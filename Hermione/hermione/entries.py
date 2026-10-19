"""One line of a listing, and how a directory becomes a list of them.

Everything here runs off the GTK thread. A directory of forty thousand files
is a quarter of a second of `stat` calls on a warm cache and several seconds
on a cold one, and a file manager that stops painting while it reads is a file
manager that feels broken — so `read_dir` is written to be called from a
worker, touches no GTK, and hands back plain objects.

`Entry` is a `GObject.Object` because the views put it in a `Gio.ListStore`,
but it keeps its fields as ordinary Python attributes rather than GObject
properties: a property read through the C layer costs twenty times what an
attribute read costs, and every cell of every visible row does several.

What a symlink *is* takes some care. A link to a directory should open like a
directory and sort with the directories, but its size and its date are the
link's own, and a link to nothing at all has to be visible as broken rather
than as a zero-byte file.
"""

from __future__ import annotations

import os
import stat as statmod

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GObject  # noqa: E402

from . import filetypes, winenv  # noqa: E402

# Windows' own idea of hidden, which has nothing to do with a leading dot.
_FILE_ATTRIBUTE_HIDDEN = 0x02
_FILE_ATTRIBUTE_SYSTEM = 0x04


class Entry(GObject.Object):
    """A file, a directory, or a link to one of them."""

    __gtype_name__ = "HermioneEntry"

    __gsignals__ = {
        # Something about this entry changed after it was listed — a folder's
        # size finished counting, a thumbnail arrived, git had an opinion.
        # The cells bound to it listen, so one row redraws instead of the view
        # being rebuilt underneath the user's selection.
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, path: str, name: str = ""):
        super().__init__()
        self.path = path
        self.name = name or os.path.basename(path) or path
        self.is_dir = False
        self.is_link = False
        self.broken = False
        self.link_target = ""
        self.size = 0                # the link's own size, for a link
        self.mtime = 0.0
        self.atime = 0.0
        self.ctime = 0.0
        self.mode = 0
        self.uid = -1
        self.gid = -1
        self.inode = 0
        self.nlink = 1
        self.device = 0
        self.blocks = 0              # 512-byte units, as st_blocks reports
        self.hidden = False
        self.category = "other"
        self.unreadable = False      # stat failed; the name is all we have
        # Filled in later, by whoever is interested.
        self.dir_size: int | None = None      # bytes under a folder, once counted
        self.dir_items: tuple[int, int] | None = None   # (files, folders)
        self.vcs = ""                # git's one-letter opinion, if any
        self.thumb = None            # Gdk.Texture, once one exists
        self.thumb_tried = False

    # -- what the views ask of it -------------------------------------------

    @property
    def sort_size(self) -> int:
        """The number the size column sorts on.

        A folder sorts on what is under it *once that is known*; until then it
        sorts as nothing, which keeps folders together at one end rather than
        shuffling as the counts arrive.
        """
        if self.is_dir:
            return self.dir_size if self.dir_size is not None else -1
        return self.size

    @property
    def display_size(self) -> int:
        return self.sort_size

    @property
    def is_hidden(self) -> bool:
        return self.hidden

    @property
    def executable(self) -> bool:
        return bool(self.mode & 0o111) and not self.is_dir

    @property
    def extension(self) -> str:
        return filetypes.extension(self.name)

    def type_label(self) -> str:
        return filetypes.type_label(self.path, self.is_dir, self.is_link)

    def touch(self) -> None:
        self.emit("changed")

    def __repr__(self) -> str:      # for test failures that have to be readable
        kind = "dir" if self.is_dir else "file"
        return f"<Entry {self.name!r} {kind} {self.size}>"


def from_stat(path: str, name: str, info, link_info=None) -> Entry:
    """An Entry from a stat result (and the link's own stat, when it is one)."""
    entry = Entry(path, name)
    source = link_info if link_info is not None else info
    entry.is_link = link_info is not None
    if info is None:
        # A link whose target is gone, or a file we may not stat.
        entry.broken = entry.is_link
        entry.unreadable = not entry.is_link
        if source is not None:
            entry.size = source.st_size
            entry.mtime = source.st_mtime
            entry.mode = statmod.S_IMODE(source.st_mode)
        entry.category = filetypes.category(name, False)
        entry.hidden = _is_hidden(name, source)
        return entry

    entry.is_dir = statmod.S_ISDIR(info.st_mode)
    entry.size = source.st_size
    entry.mtime = source.st_mtime
    entry.atime = getattr(source, "st_atime", 0.0)
    entry.ctime = getattr(source, "st_ctime", 0.0)
    entry.mode = statmod.S_IMODE(info.st_mode)
    entry.uid = getattr(info, "st_uid", -1)
    entry.gid = getattr(info, "st_gid", -1)
    entry.inode = getattr(info, "st_ino", 0)
    entry.nlink = getattr(info, "st_nlink", 1)
    entry.device = getattr(info, "st_dev", 0)
    entry.blocks = getattr(source, "st_blocks", 0)
    entry.category = filetypes.category(name, entry.is_dir)
    entry.hidden = _is_hidden(name, source)
    return entry


def _is_hidden(name: str, info) -> bool:
    if name.startswith("."):
        return True
    if winenv.WINDOWS and info is not None:
        attrs = getattr(info, "st_file_attributes", 0)
        return bool(attrs & (_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM))
    return False


def read_hidden_list(directory: str) -> set[str]:
    """Names the directory itself asks to have hidden.

    A `.hidden` file holding one name per line is the freedesktop way for a
    directory to say "not this one" without renaming anything, and it is the
    only reason a file manager ever hides something that is not a dotfile.
    """
    try:
        with open(os.path.join(directory, ".hidden"), encoding="utf-8",
                  errors="replace") as handle:
            return {line.strip() for line in handle if line.strip()}
    except OSError:
        return set()


def read_dir(directory: str, show_hidden: bool = False,
             on_batch=None, batch_size: int = 400) -> tuple[list[Entry], str]:
    """Everything in `directory`, as Entries. Returns (entries, error message).

    Runs on a worker thread. `on_batch`, if given, is called with each batch of
    entries as they are read — the first few hundred rows can be on screen
    while a slow network mount is still being walked.

    An error is returned rather than raised: "you may not read this" is a
    thing to show in the window, not a crash, and a directory can also be
    half-readable, in which case there are entries *and* a message.
    """
    entries: list[Entry] = []
    error = ""
    hide_list = set() if show_hidden else read_hidden_list(directory)
    batch: list[Entry] = []

    try:
        scanner = os.scandir(directory)
    except (OSError, ValueError) as exc:
        return [], _explain(exc, directory)

    with scanner:
        while True:
            try:
                item = next(scanner)
            except StopIteration:
                break
            except OSError as exc:
                error = error or _explain(exc, directory)
                break

            name = item.name
            try:
                is_link = item.is_symlink()
            except OSError:
                is_link = False

            if not show_hidden:
                if name.startswith(".") or name in hide_list:
                    continue

            link_info = None
            try:
                if is_link:
                    link_info = item.stat(follow_symlinks=False)
                    try:
                        info = item.stat()
                    except OSError:
                        info = None             # broken link
                else:
                    info = item.stat(follow_symlinks=False)
            except OSError:
                info = None
                if not is_link:
                    link_info = None

            entry = from_stat(os.path.join(directory, name), name, info, link_info)
            if is_link and info is None:
                entry.link_target = _read_link(entry.path)
            elif is_link:
                entry.link_target = _read_link(entry.path)

            if not show_hidden and entry.hidden:
                continue

            entries.append(entry)
            if on_batch is not None:
                batch.append(entry)
                if len(batch) >= batch_size:
                    on_batch(batch)
                    batch = []

    if on_batch is not None and batch:
        on_batch(batch)
    return entries, error


def _read_link(path: str) -> str:
    try:
        return os.readlink(path)
    except OSError:
        return ""


def _explain(exc: OSError, path: str) -> str:
    """An error message a person can act on, rather than an errno."""
    import errno

    code = getattr(exc, "errno", None)
    name = os.path.basename(path.rstrip(os.sep)) or path
    if code == errno.EACCES:
        return f"No permission to read {name}"
    if code == errno.ENOENT:
        return f"{name} no longer exists"
    if code == errno.ENOTDIR:
        return f"{name} is not a folder"
    if code == errno.ELOOP:
        return f"{name} is a loop of links"
    if code == errno.ENAMETOOLONG:
        return "That path is too long"
    if code == errno.EIO:
        return f"The disk reported an error reading {name}"
    return getattr(exc, "strerror", None) or f"Could not read {name}"


# --------------------------------------------------------------- sorting ---

def natural_key(name: str):
    """A sort key that puts `file2` before `file10`.

    Digits are compared as numbers and everything else case-insensitively,
    which is the order every file manager and most file *listings* use, and
    the only one that does not look broken on a folder of episode numbers.
    The lower-cased text is kept as a tiebreak so `A` and `a` have a stable,
    reproducible order rather than whichever the filesystem returned first.
    """
    parts: list = []
    digits = ""
    for char in name:
        if char.isdigit():
            digits += char
        else:
            if digits:
                parts.append((1, int(digits), ""))
                digits = ""
            parts.append((0, 0, char.lower()))
    if digits:
        parts.append((1, int(digits), ""))
    return (parts, name)


SORT_KEYS = ("name", "size", "modified", "type", "accessed", "created", "extension")


def sort_entries(entries: list[Entry], key: str = "name", reverse: bool = False,
                 folders_first: bool = True) -> list[Entry]:
    """The listing, in the order it should be shown.

    `folders_first` is applied outside the reverse, not inside it: reversing a
    folders-first sort so that the folders land at the bottom is not what
    anybody means by "sort by name, descending".
    """
    if key == "size":
        def sorter(e):
            return (e.sort_size, natural_key(e.name))
    elif key == "modified":
        def sorter(e):
            return (e.mtime, natural_key(e.name))
    elif key == "accessed":
        def sorter(e):
            return (e.atime, natural_key(e.name))
    elif key == "created":
        def sorter(e):
            return (e.ctime, natural_key(e.name))
    elif key == "type":
        def sorter(e):
            return (e.category, e.extension, natural_key(e.name))
    elif key == "extension":
        def sorter(e):
            return (e.extension, natural_key(e.name))
    else:
        def sorter(e):
            return natural_key(e.name)

    ordered = sorted(entries, key=sorter, reverse=reverse)
    if folders_first:
        ordered.sort(key=lambda e: 0 if e.is_dir else 1)
    return ordered


# ------------------------------------------------------------- filtering ---

def matches(entry: Entry, needle: str) -> bool:
    """Does this entry answer to what was typed in the filter box?

    Plain substring, case-insensitively, unless the text looks like a glob —
    `*.png`, `report-??.pdf` — in which case it is one. Nobody types a `*`
    into a filter box meaning a literal asterisk.
    """
    if not needle:
        return True
    lowered = needle.lower()
    name = entry.name.lower()
    if any(char in needle for char in "*?["):
        import fnmatch

        return fnmatch.fnmatch(name, lowered)
    return lowered in name


def filter_entries(entries: list[Entry], needle: str = "",
                   categories: set[str] | None = None) -> list[Entry]:
    out = entries
    if needle:
        out = [e for e in out if matches(e, needle)]
    if categories:
        out = [e for e in out if e.category in categories or e.is_dir]
    return out
