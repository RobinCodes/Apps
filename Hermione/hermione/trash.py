"""The trash: what is in it, where it came from, and how to put it back.

Deleting goes through GIO — `g_file_trash` writes the `.trashinfo` file, finds
the right trash directory for the volume the file is on, and on Windows uses
the Recycle Bin — so nothing here has to know the rules for *putting* things
in the trash. Reading it back is the other way round: the freedesktop spec is
a directory of files and a directory of little ini files beside it, and parsing
those directly works whether or not gvfs is installed, which `trash:///` does
not. A restore is then a plain move, which is all it ever was.

The spec, in brief: `$XDG_DATA_HOME/Trash` holds `files/` and `info/`, with
one `NAME.trashinfo` per deleted thing giving its original Path and
DeletionDate. A file deleted from another filesystem goes to `.Trash-$uid` at
the root of *that* filesystem instead, because the trash is a rename and a
rename cannot cross a device.
"""

from __future__ import annotations

import os
import shutil
import time
import urllib.parse

from . import winenv


class TrashItem:
    """One deleted thing, and where it was."""

    def __init__(self, name: str, files_path: str, info_path: str,
                 original: str, deleted_at: float, trash_root: str):
        self.name = name                  # the name inside files/
        self.files_path = files_path       # where the data is now
        self.info_path = info_path         # the .trashinfo beside it
        self.original = original           # where it was deleted from
        self.deleted_at = deleted_at
        self.trash_root = trash_root
        try:
            info = os.lstat(files_path)
            self.is_dir = os.path.isdir(files_path) and not os.path.islink(files_path)
            self.size = info.st_size
        except OSError:
            self.is_dir = False
            self.size = 0

    @property
    def display_name(self) -> str:
        """What it was called, which is not always what it is called now.

        The trash renames to avoid collisions — two `notes.txt` from two
        folders cannot both be `files/notes.txt` — so the name worth showing
        is the last component of the original path.
        """
        return os.path.basename(self.original) or self.name

    @property
    def original_folder(self) -> str:
        return os.path.dirname(self.original)


def home_trash() -> str:
    """The user's own trash — $XDG_DATA_HOME/Trash, by the spec.

    Not `winenv.data_home("hermione")`: this is the shared trash every program
    on the machine uses, not somewhere of ours.
    """
    return os.path.join(_data_root(), "Trash")


def _data_root() -> str:
    override = os.environ.get("XDG_DATA_HOME")
    if override:
        return override
    if winenv.WINDOWS:
        return os.environ.get("LOCALAPPDATA") or os.path.expanduser(
            os.path.join("~", "AppData", "Local"))
    return os.path.expanduser("~/.local/share")


def volume_trashes() -> list[str]:
    """`.Trash-$uid` directories on the other filesystems that are mounted.

    Only ones that already exist are returned: creating them is the business
    of whatever deleted the file, and a file manager that makes a `.Trash-1000`
    at the root of every mount it looks at is a vandal.
    """
    if winenv.WINDOWS:
        return []
    uid = os.getuid()
    found = []
    for mount in _mount_points():
        for candidate in (os.path.join(mount, f".Trash-{uid}"),
                          os.path.join(mount, ".Trash", str(uid))):
            if os.path.isdir(os.path.join(candidate, "files")):
                found.append(candidate)
    return found


def _mount_points() -> list[str]:
    try:
        with open("/proc/mounts", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    points = []
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        kind = parts[2]
        if kind in ("proc", "sysfs", "devtmpfs", "devpts", "cgroup", "cgroup2",
                    "tmpfs", "securityfs", "debugfs", "tracefs", "configfs",
                    "pstore", "bpf", "autofs", "mqueue", "hugetlbfs", "fusectl",
                    "efivarfs", "ramfs", "binfmt_misc", "overlay", "squashfs"):
            continue
        point = parts[1].replace("\\040", " ").replace("\\011", "\t")
        if point != "/" and os.path.isdir(point):
            points.append(point)
    return points


def roots() -> list[str]:
    """Every trash directory worth reading, the home one first."""
    return [home_trash()] + volume_trashes()


def list_items() -> list[TrashItem]:
    """Everything in the trash, newest deletion first."""
    items: list[TrashItem] = []
    for root in roots():
        files_dir = os.path.join(root, "files")
        info_dir = os.path.join(root, "info")
        try:
            names = os.listdir(files_dir)
        except OSError:
            continue
        for name in names:
            info_path = os.path.join(info_dir, name + ".trashinfo")
            original, deleted_at = _read_info(info_path)
            if not original:
                # No info file: still in the trash, still deletable, we just
                # cannot say where it came from. Showing it is better than
                # pretending the trash is emptier than it is.
                original = name
            # A relative Path in a volume trash is relative to that volume.
            if not os.path.isabs(original):
                original = os.path.join(os.path.dirname(root), original)
            items.append(TrashItem(name, os.path.join(files_dir, name), info_path,
                                   original, deleted_at, root))
    items.sort(key=lambda item: item.deleted_at, reverse=True)
    return items


def _read_info(path: str) -> tuple[str, float]:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return "", 0.0
    original = ""
    when = 0.0
    for line in text.splitlines():
        if line.startswith("Path="):
            # The spec says the path is URL-encoded, and most implementations
            # agree, but a `%` in a filename from one that did not would turn
            # into mojibake — so decode and keep the result only if it still
            # names something we can see.
            raw = line[5:]
            original = urllib.parse.unquote(raw)
        elif line.startswith("DeletionDate="):
            stamp = line[len("DeletionDate="):].strip()
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    when = time.mktime(time.strptime(stamp, fmt))
                    break
                except ValueError:
                    continue
    return original, when


def size_of_trash() -> tuple[int, int]:
    """(items, bytes) in the trash, for the sidebar and the empty button."""
    total = 0
    count = 0
    for item in list_items():
        count += 1
        if item.is_dir:
            for walk_root, _dirs, files in os.walk(item.files_path):
                for name in files:
                    try:
                        total += os.lstat(os.path.join(walk_root, name)).st_size
                    except OSError:
                        pass
        else:
            total += item.size
    return count, total


def restore(item: TrashItem, overwrite: bool = False) -> tuple[bool, str]:
    """Put one item back where it came from.

    The folder it came from may be gone — deleting a folder and then one of
    the files that used to be in it is ordinary — so it is recreated. A name
    that is taken is not overwritten unless asked: restoring a file over the
    newer file that replaced it is a data loss nobody asked for.
    """
    target = item.original
    if not target or not os.path.isabs(target):
        return False, "Nowhere to restore it to"
    if os.path.lexists(target) and not overwrite:
        return False, f"{os.path.basename(target)} already exists"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.lexists(target) and overwrite:
            _remove(target)
        shutil.move(item.files_path, target)
    except OSError as exc:
        return False, exc.strerror or "Could not restore it"
    try:
        os.remove(item.info_path)
    except OSError:
        pass
    return True, ""


def delete_item(item: TrashItem) -> tuple[bool, str]:
    """Remove one item from the trash for good."""
    try:
        _remove(item.files_path)
    except OSError as exc:
        return False, exc.strerror or "Could not delete it"
    try:
        os.remove(item.info_path)
    except OSError:
        pass
    return True, ""


def empty(on_item=None) -> tuple[int, list[str]]:
    """Empty the trash. Returns (how many went, what would not go)."""
    gone = 0
    errors = []
    for item in list_items():
        ok, why = delete_item(item)
        if ok:
            gone += 1
            if on_item is not None:
                on_item(item)
        else:
            errors.append(f"{item.display_name}: {why}")
    return gone, errors


def _remove(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)
