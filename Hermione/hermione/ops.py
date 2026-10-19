"""Copying, moving, deleting — and being able to take it back.

The whole of this module runs on a worker thread and touches no GTK. What it
reports, it reports by calling a function the caller gave it; turning that into
a progress bar is the window's business.

Four things make the difference between a file manager that can be trusted
with a thousand files and one that cannot:

*It knows what it is about to do before it starts.* A job counts its files and
bytes first, as its own phase, so the progress bar means something and so
"there is not enough room on the disk" is said before the first byte is
written rather than nine minutes in.

*It never silently overwrites.* A conflict asks, through a resolver the caller
supplies, and the answer can be "and all the rest like it". A resolver that
blocks is expected — the worker is meant to wait for the person.

*It copies properly.* Big files are copied in chunks so progress moves,
permissions and timestamps come across, a move across filesystems becomes a
copy and a delete, and a directory cannot be copied into itself.

*It is reversible.* Every job records what it did in the form its undo needs:
pairs for a move, created paths for a copy, trash items for a trash. Only a
permanent delete is not undoable, which is why it is the one that always asks.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat as statmod
import threading
import time

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib  # noqa: E402

from . import trash as trashmod  # noqa: E402

# What to do about a name that is already taken.
SKIP = "skip"
REPLACE = "replace"
RENAME = "rename"
MERGE = "merge"            # directories only: keep both, recurse
CANCEL = "cancel"

COPY = "copy"
MOVE = "move"
TRASH = "trash"
DELETE = "delete"
LINK = "link"
# Two kinds that only an undo asks for: putting moved things back where each
# one came from, and taking things out of the trash again.
PUT_BACK = "put-back"
UNTRASH = "untrash"

# Big enough that the syscall overhead disappears, small enough that progress
# moves on a slow disk and a cancel is noticed promptly.
CHUNK = 1 << 20


class Cancelled(Exception):
    """Raised inside the worker when the job is told to stop."""


class Progress:
    """What a job has done so far. Read from the GTK thread, written by the worker.

    Plain attributes under no lock: every one of them is a single assignment
    of an int, a float or a string, each of which is atomic under the GIL, and
    a progress bar that reads a count one frame late is a progress bar that is
    right one frame later. A lock here would be a lock taken a thousand times
    a second to protect nothing.
    """

    def __init__(self):
        self.phase = "counting"        # counting | working | done
        self.files_total = 0
        self.files_done = 0
        self.bytes_total = 0
        self.bytes_done = 0
        self.current = ""
        self.started = time.monotonic()
        self.finished = 0.0
        self.cancelled = False
        self.errors: list[str] = []

    @property
    def fraction(self) -> float:
        """How far along, by bytes where there are any and by files otherwise.

        Bytes are the honest measure for a copy of a few big files and a
        hopeless one for a copy of ten thousand empty ones, so whichever
        total is larger gets to drive the bar.
        """
        if self.bytes_total > 0:
            return min(1.0, self.bytes_done / self.bytes_total)
        if self.files_total > 0:
            return min(1.0, self.files_done / self.files_total)
        return 0.0

    @property
    def elapsed(self) -> float:
        return (self.finished or time.monotonic()) - self.started

    @property
    def rate(self) -> float:
        """Bytes per second, averaged over the job so far."""
        seconds = self.elapsed
        return self.bytes_done / seconds if seconds > 0.2 else 0.0

    @property
    def remaining(self) -> float:
        """Seconds left, or -1 when there is no honest way to guess."""
        rate = self.rate
        if rate <= 0 or self.bytes_total <= 0:
            return -1.0
        left = self.bytes_total - self.bytes_done
        return left / rate if left > 0 else 0.0


class Result:
    """What a job did, in the shape its undo needs."""

    def __init__(self, kind: str):
        self.kind = kind
        self.moved: list[tuple[str, str]] = []     # (from, to)
        self.created: list[str] = []               # paths a copy brought into being
        self.trashed: list[str] = []               # original paths now in the trash
        self.deleted: list[str] = []               # gone for good
        self.skipped: list[str] = []
        self.errors: list[str] = []
        self.cancelled = False

    @property
    def ok(self) -> bool:
        return not self.errors and not self.cancelled

    @property
    def count(self) -> int:
        return len(self.moved) + len(self.created) + len(self.trashed) + len(self.deleted)


# ------------------------------------------------------------- planning ---

class Plan:
    """How many files and bytes a job is about to touch."""

    def __init__(self):
        self.files = 0
        self.dirs = 0
        self.bytes = 0
        self.unreadable: list[str] = []

    def fits(self, destination: str) -> tuple[bool, int]:
        """Is there room? Returns (fits, free bytes) — (True, -1) if unknowable."""
        try:
            usage = shutil.disk_usage(destination)
        except (OSError, AttributeError):
            return True, -1
        return usage.free >= self.bytes, usage.free


def measure(paths, follow_links: bool = False, stop=None) -> Plan:
    """Walk `paths` and add up what is there.

    Hard links are not deduplicated: a copy of two names for one inode really
    does write the bytes twice, which is the number the progress bar needs.
    """
    plan = Plan()
    for path in paths:
        if stop is not None and stop.is_set():
            raise Cancelled()
        try:
            info = os.lstat(path)
        except OSError:
            plan.unreadable.append(path)
            continue
        if statmod.S_ISLNK(info.st_mode) and not follow_links:
            plan.files += 1
            plan.bytes += info.st_size
            continue
        if statmod.S_ISDIR(info.st_mode):
            plan.dirs += 1
            for walk_root, dirs, files in os.walk(path, followlinks=follow_links,
                                                  onerror=plan.unreadable.append):
                if stop is not None and stop.is_set():
                    raise Cancelled()
                plan.dirs += len(dirs)
                for name in files:
                    plan.files += 1
                    try:
                        plan.bytes += os.lstat(os.path.join(walk_root, name)).st_size
                    except OSError:
                        pass
        else:
            plan.files += 1
            plan.bytes += info.st_size
    return plan


# ------------------------------------------------------------ the naming ---

def split_name(name: str) -> tuple[str, str]:
    """A name as (stem, suffix), where the suffix includes a double extension.

    `archive.tar.gz` has to come apart as `archive` + `.tar.gz`, or a copy of
    it is called `archive.tar (copy).gz`, which no longer extracts by name.
    """
    from . import filetypes

    lowered = name.lower()
    for double in filetypes.DOUBLE:
        if lowered.endswith("." + double) and len(name) > len(double) + 1:
            return name[: -(len(double) + 1)], name[-(len(double) + 1):]
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        return name, ""
    return stem, "." + ext


def unique_name(directory: str, name: str, suffix: str = "copy") -> str:
    """A name in `directory` that nothing is using yet.

    `notes.txt` becomes `notes (copy).txt`, then `notes (copy 2).txt`. The
    word goes before the extension so the file still opens in the same
    program, and the number goes after the word so a folder of copies sorts
    together rather than interleaving with the originals.
    """
    if not os.path.lexists(os.path.join(directory, name)):
        return name
    stem, ext = split_name(name)
    # Taking an existing "(copy)" apart first, so copying a copy gives
    # "(copy 2)" rather than "(copy) (copy)".
    import re

    match = re.match(r"^(.*) \(" + re.escape(suffix) + r"(?: (\d+))?\)$", stem)
    if match:
        stem = match.group(1)
    candidate = f"{stem} ({suffix}){ext}"
    index = 2
    while os.path.lexists(os.path.join(directory, candidate)):
        candidate = f"{stem} ({suffix} {index}){ext}"
        index += 1
        if index > 10000:                     # a folder this pathological wins
            candidate = f"{stem} ({suffix} {int(time.time())}){ext}"
            break
    return candidate


def is_inside(child: str, parent: str) -> bool:
    """Is `child` the same as `parent`, or under it?

    The check that stops `cp -r dir dir/sub` from filling the disk. Done on
    real paths so a symlinked route in does not get past it.
    """
    try:
        child_real = os.path.realpath(child)
        parent_real = os.path.realpath(parent)
    except OSError:
        child_real, parent_real = child, parent
    if child_real == parent_real:
        return True
    return child_real.startswith(parent_real.rstrip(os.sep) + os.sep)


# ----------------------------------------------------------------- jobs ----

class Job:
    """One operation, run on a thread of its own.

    `resolver(source, destination, is_dir)` is called when a name is taken and
    must return one of SKIP, REPLACE, RENAME, MERGE or CANCEL. It is called on
    the worker thread and may block for as long as a person takes to answer.
    `on_progress(progress)` is called often and `on_done(result)` once; both
    arrive on the worker thread, so a GTK caller hands them to GLib.idle_add.
    """

    def __init__(self, kind: str, sources, destination: str = "", *,
                 resolver=None, on_progress=None, on_done=None,
                 follow_links: bool = False, rename_to: str = "",
                 pairs=None):
        self.kind = kind
        self.pairs = list(pairs or [])      # (where it is now, where it belongs)
        self.sources = [os.path.abspath(p) for p in sources]
        self.destination = os.path.abspath(destination) if destination else ""
        self.resolver = resolver
        self.on_progress = on_progress
        self.on_done = on_done
        self.follow_links = follow_links
        self.rename_to = rename_to     # a one-source copy/move that also renames
        self.progress = Progress()
        self.result = Result(kind)
        self.stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_report = 0.0
        # A conflict answer that was given "for all the rest" as well.
        self._blanket: str = ""

    # -- running ----------------------------------------------------------

    def start(self) -> "Job":
        self._thread = threading.Thread(target=self.run, daemon=True,
                                        name=f"hermione-{self.kind}")
        self._thread.start()
        return self

    def cancel(self) -> None:
        self.stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def run(self) -> Result:
        try:
            self._run()
        except Cancelled:
            self.result.cancelled = True
            self.progress.cancelled = True
        except OSError as exc:
            self.result.errors.append(_explain(exc))
        except Exception as exc:                    # noqa: BLE001
            # A bug in here must not take the app down with it; the job
            # reports itself failed and the window says so.
            self.result.errors.append(f"{type(exc).__name__}: {exc}")
        self.progress.phase = "done"
        self.progress.finished = time.monotonic()
        self.result.errors.extend(self.progress.errors)
        self._report(force=True)
        if self.on_done is not None:
            self.on_done(self.result)
        return self.result

    def _run(self) -> None:
        if self.kind == PUT_BACK:
            self._run_put_back()
            return
        if self.kind == UNTRASH:
            self._run_untrash()
            return
        if self.kind in (COPY, MOVE, LINK):
            self._check_destination()

        if self.kind in (COPY, MOVE, DELETE):
            plan = measure(self.sources, self.follow_links, self.stop)
            self.progress.files_total = plan.files + plan.dirs
            self.progress.bytes_total = plan.bytes
            if self.kind == COPY:
                # Said before the first byte is written rather than nine
                # minutes in, and said in bytes a person can compare.
                fits, free = plan.fits(self.destination)
                if not fits and free >= 0:
                    from .units import human_size

                    raise OSError(
                        errno.ENOSPC,
                        f"that needs {human_size(plan.bytes)} and there is "
                        f"{human_size(free)} free",
                        self.destination)
        else:
            self.progress.files_total = len(self.sources)

        self.progress.phase = "working"
        self._report(force=True)

        for source in self.sources:
            self._tick()
            name = self.rename_to or os.path.basename(source.rstrip(os.sep))
            if self.kind == TRASH:
                self._do_trash(source)
            elif self.kind == DELETE:
                self._do_delete(source)
            elif self.kind == LINK:
                self._do_link(source, name)
            else:
                target = os.path.join(self.destination, name)
                if self.kind == COPY:
                    self._copy_any(source, target)
                else:
                    self._move_any(source, target)

    def _run_put_back(self) -> None:
        """Move each thing back to the path it was moved from.

        One destination per item, which is why this cannot be an ordinary
        move: undoing a move of forty files out of twelve folders puts each
        one back in its own folder.
        """
        self.progress.files_total = len(self.pairs)
        self.progress.phase = "working"
        for now, before in self.pairs:
            self._tick()
            self._note(now)
            if not os.path.lexists(now):
                self.progress.errors.append(
                    f"{os.path.basename(now)}: it is no longer there")
                continue
            try:
                os.makedirs(os.path.dirname(before), exist_ok=True)
                if os.path.lexists(before):
                    # Something has taken the old name since. Put it beside
                    # rather than over: an undo must not destroy anything.
                    directory = os.path.dirname(before)
                    before = os.path.join(
                        directory,
                        unique_name(directory, os.path.basename(before), "restored"))
                shutil.move(now, before)
            except OSError as exc:
                self._fail(now, exc)
                continue
            self.result.moved.append((now, before))
            self.progress.files_done += 1

    def _run_untrash(self) -> None:
        """Take things out of the trash, by the path they were deleted from."""
        self.progress.files_total = len(self.sources)
        self.progress.phase = "working"
        wanted = {os.path.abspath(p) for p in self.sources}
        found = {}
        for item in trashmod.list_items():
            # Newest first from list_items, so the first match for a path is
            # the deletion being undone and not an older one of the same name.
            found.setdefault(os.path.abspath(item.original), item)
        for path in self.sources:
            self._tick()
            self._note(path)
            item = found.get(os.path.abspath(path))
            if item is None:
                self.progress.errors.append(
                    f"{os.path.basename(path)}: it is not in the trash")
                continue
            ok, why = trashmod.restore(item)
            if not ok:
                self.progress.errors.append(f"{item.display_name}: {why}")
                continue
            self.result.moved.append((item.files_path, item.original))
            self.progress.files_done += 1
        _ = wanted

    def _check_destination(self) -> None:
        if not self.destination:
            raise OSError(errno.EINVAL, "No destination given")
        if not os.path.isdir(self.destination):
            raise OSError(errno.ENOTDIR, "Not a folder", self.destination)
        for source in self.sources:
            if os.path.isdir(source) and not os.path.islink(source):
                if is_inside(self.destination, source):
                    raise OSError(errno.EINVAL,
                                  f"{os.path.basename(source)} cannot be "
                                  "copied into itself")

    # -- reporting --------------------------------------------------------

    def _tick(self) -> None:
        if self.stop.is_set():
            raise Cancelled()

    def _report(self, force: bool = False) -> None:
        """Tell the caller, but not more than twenty times a second.

        A copy of ten thousand small files would otherwise post ten thousand
        idle callbacks and the progress dialog would spend the whole job
        relayouting instead of the copy getting on with it.
        """
        now = time.monotonic()
        if not force and now - self._last_report < 0.05:
            return
        self._last_report = now
        if self.on_progress is not None:
            self.on_progress(self.progress)

    def _note(self, path: str) -> None:
        self.progress.current = os.path.basename(path.rstrip(os.sep)) or path
        self._report()

    def _fail(self, path: str, exc: OSError) -> None:
        name = os.path.basename(path.rstrip(os.sep)) or path
        message = f"{name}: {_explain(exc)}"
        if message not in self.progress.errors:
            self.progress.errors.append(message)

    # -- conflicts --------------------------------------------------------

    def _resolve(self, source: str, target: str) -> tuple[str, str]:
        """Settle a taken name. Returns (what to do, possibly new target)."""
        is_dir = os.path.isdir(source) and not os.path.islink(source)
        # Two directories merging is the ordinary case and not worth a
        # question of its own when the user already said "merge all".
        answer = self._blanket or (
            self.resolver(source, target, is_dir) if self.resolver else RENAME)
        if isinstance(answer, tuple):               # (answer, "for all of them")
            answer, blanket = answer
            if blanket:
                self._blanket = answer
        if answer == CANCEL:
            raise Cancelled()
        if answer == RENAME:
            directory = os.path.dirname(target)
            return RENAME, os.path.join(directory,
                                        unique_name(directory,
                                                    os.path.basename(target)))
        return answer, target

    # -- the operations ---------------------------------------------------

    def _copy_any(self, source: str, target: str) -> None:
        if os.path.lexists(target):
            if os.path.realpath(source) == os.path.realpath(target):
                # Copying a thing onto itself means "duplicate it".
                directory = os.path.dirname(target)
                target = os.path.join(directory,
                                      unique_name(directory,
                                                  os.path.basename(target)))
            else:
                action, target = self._resolve(source, target)
                if action == SKIP:
                    self.result.skipped.append(source)
                    self._skip_counts(source)
                    return
                if action == REPLACE:
                    self._remove_tree(target)
        try:
            if os.path.islink(source) and not self.follow_links:
                self._copy_link(source, target)
            elif os.path.isdir(source):
                self._copy_dir(source, target)
            else:
                self._copy_file(source, target)
        except Cancelled:
            raise
        except OSError as exc:
            self._fail(source, exc)

    def _copy_dir(self, source: str, target: str) -> None:
        self._tick()
        self._note(source)
        fresh = not os.path.isdir(target)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as exc:
            self._fail(source, exc)
            return
        if fresh:
            self.result.created.append(target)
        self.progress.files_done += 1

        try:
            names = sorted(os.listdir(source))
        except OSError as exc:
            self._fail(source, exc)
            names = []

        for name in names:
            self._tick()
            child = os.path.join(source, name)
            child_target = os.path.join(target, name)
            if os.path.lexists(child_target):
                action, child_target = self._resolve(child, child_target)
                if action == SKIP:
                    self._skip_counts(child)
                    continue
                if action == REPLACE:
                    self._remove_tree(child_target)
            try:
                if os.path.islink(child) and not self.follow_links:
                    self._copy_link(child, child_target)
                elif os.path.isdir(child):
                    self._copy_dir(child, child_target)
                else:
                    self._copy_file(child, child_target)
            except Cancelled:
                raise
            except OSError as exc:
                self._fail(child, exc)
        _copy_metadata(source, target)

    def _copy_file(self, source: str, target: str) -> None:
        self._tick()
        self._note(source)
        try:
            with open(source, "rb") as reader, open(target, "wb") as writer:
                while True:
                    self._tick()
                    block = reader.read(CHUNK)
                    if not block:
                        break
                    writer.write(block)
                    self.progress.bytes_done += len(block)
                    self._report()
        except Cancelled:
            # A file stopped halfway is not a copy of anything, and leaving it
            # there is how a cancelled backup becomes a truncated file nobody
            # knows is truncated.
            try:
                os.remove(target)
            except OSError:
                pass
            raise
        _copy_metadata(source, target)
        self.progress.files_done += 1
        if target not in self.result.created:
            self.result.created.append(target)

    def _copy_link(self, source: str, target: str) -> None:
        self._tick()
        self._note(source)
        where = os.readlink(source)
        if os.path.lexists(target):
            self._remove_tree(target)
        os.symlink(where, target)
        self.progress.files_done += 1
        self.result.created.append(target)

    def _move_any(self, source: str, target: str) -> None:
        if os.path.realpath(source) == os.path.realpath(target):
            return                                  # moving home is nothing
        merge_into = False
        if os.path.lexists(target):
            action, new_target = self._resolve(source, target)
            if action == SKIP:
                self.result.skipped.append(source)
                self._skip_counts(source)
                return
            if action == REPLACE:
                self._remove_tree(target)
            elif action == MERGE and os.path.isdir(source) and os.path.isdir(target):
                merge_into = True
            else:
                target = new_target

        self._tick()
        self._note(source)
        if merge_into:
            self._merge_move(source, target)
            return
        try:
            os.rename(source, target)
        except OSError as exc:
            if exc.errno not in (errno.EXDEV, errno.ENOTEMPTY, errno.EEXIST):
                self._fail(source, exc)
                return
            # Across filesystems a rename cannot work, so the move becomes a
            # copy and a delete — and the delete only happens if the copy did.
            before = len(self.progress.errors)
            if os.path.isdir(source) and not os.path.islink(source):
                self._copy_dir(source, target)
            elif os.path.islink(source):
                self._copy_link(source, target)
            else:
                self._copy_file(source, target)
            if len(self.progress.errors) != before:
                return
            try:
                self._remove_tree(source)
            except OSError as inner:
                self._fail(source, inner)
                return
            self.result.moved.append((source, target))
            return
        self.result.moved.append((source, target))
        self.progress.files_done += 1
        try:
            self.progress.bytes_done += os.lstat(target).st_size
        except OSError:
            pass

    def _merge_move(self, source: str, target: str) -> None:
        """Move a folder's contents into a folder that is already there."""
        try:
            names = sorted(os.listdir(source))
        except OSError as exc:
            self._fail(source, exc)
            return
        for name in names:
            self._tick()
            self._move_any(os.path.join(source, name), os.path.join(target, name))
        try:
            os.rmdir(source)            # only if the merge emptied it
        except OSError:
            pass

    def _do_trash(self, source: str) -> None:
        self._tick()
        self._note(source)
        try:
            Gio.File.new_for_path(source).trash(None)
        except GLib.Error as exc:
            # No trash on this filesystem is the common one, and it is worth
            # saying so plainly rather than as a GIO error code.
            message = exc.message or "could not be moved to the trash"
            self.progress.errors.append(
                f"{os.path.basename(source)}: {message}")
        else:
            self.result.trashed.append(source)
        self.progress.files_done += 1
        self._report()

    def _do_delete(self, source: str) -> None:
        self._tick()
        self._note(source)
        try:
            if os.path.isdir(source) and not os.path.islink(source):
                self._delete_tree(source)
            else:
                size = _size_of(source)
                os.remove(source)
                self.progress.bytes_done += size
                self.progress.files_done += 1
            self.result.deleted.append(source)
        except OSError as exc:
            self._fail(source, exc)

    def _delete_tree(self, root: str) -> None:
        """rmtree, but cancellable and reporting as it goes."""
        for directory, dirs, files in os.walk(root, topdown=False):
            for name in files:
                self._tick()
                path = os.path.join(directory, name)
                try:
                    size = _size_of(path)
                    os.remove(path)
                    self.progress.bytes_done += size
                except OSError as exc:
                    self._fail(path, exc)
                self.progress.files_done += 1
                self._note(path)
            for name in dirs:
                self._tick()
                path = os.path.join(directory, name)
                try:
                    if os.path.islink(path):
                        os.remove(path)
                    else:
                        os.rmdir(path)
                except OSError as exc:
                    self._fail(path, exc)
                self.progress.files_done += 1
        os.rmdir(root)
        self.progress.files_done += 1

    def _do_link(self, source: str, name: str) -> None:
        self._tick()
        self._note(source)
        target = os.path.join(self.destination, name)
        if os.path.lexists(target):
            directory = os.path.dirname(target)
            target = os.path.join(directory, unique_name(directory, name, "link"))
        try:
            os.symlink(source, target)
        except (OSError, NotImplementedError) as exc:
            self._fail(source, exc if isinstance(exc, OSError)
                       else OSError(errno.EPERM, "Links are not supported here"))
            return
        self.result.created.append(target)
        self.progress.files_done += 1

    # -- bookkeeping ------------------------------------------------------

    def _skip_counts(self, source: str) -> None:
        """Take a skipped thing off the totals, so the bar still reaches the end."""
        try:
            plan = measure([source], self.follow_links, self.stop)
        except Cancelled:
            raise
        except OSError:
            return
        self.progress.files_total = max(0, self.progress.files_total
                                        - plan.files - plan.dirs)
        self.progress.bytes_total = max(0, self.progress.bytes_total - plan.bytes)
        self._report()

    def _remove_tree(self, path: str) -> None:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass


def _size_of(path: str) -> int:
    try:
        return os.lstat(path).st_size
    except OSError:
        return 0


def _copy_metadata(source: str, target: str) -> None:
    """Permissions and timestamps across; owner only where we are allowed.

    Failures are swallowed on purpose: a copy onto an exFAT stick cannot keep
    a Unix mode, and refusing to copy the file over it would be absurd.
    """
    try:
        info = os.stat(source)
    except OSError:
        return
    try:
        os.chmod(target, statmod.S_IMODE(info.st_mode))
    except OSError:
        pass
    try:
        os.utime(target, (info.st_atime, info.st_mtime))
    except OSError:
        pass


def _explain(exc: OSError) -> str:
    code = getattr(exc, "errno", None)
    if code == errno.EACCES:
        return "no permission"
    if code == errno.ENOSPC:
        return "the disk is full"
    if code == errno.EROFS:
        return "the disk is read-only"
    if code == errno.ENOENT:
        return "it is no longer there"
    if code == errno.EEXIST:
        return "something is already there"
    if code == errno.ENOTEMPTY:
        return "the folder is not empty"
    if code == errno.EXDEV:
        return "that is a different filesystem"
    if code == errno.EINVAL and exc.strerror:
        return exc.strerror
    if code == errno.EDQUOT:
        return "the disk quota is used up"
    if code == errno.EMLINK or code == errno.ELOOP:
        return "too many links"
    if code == errno.EBUSY:
        return "it is in use"
    if code == errno.ENAMETOOLONG:
        return "the name is too long"
    return (exc.strerror or str(exc) or "failed").lower()


# ---------------------------------------------------- the small, instant ---
#
# Operations that are one syscall and need no thread, progress or dialog.
# They return (ok, message) so the caller can show a toast and carry on.

def rename(path: str, new_name: str) -> tuple[bool, str]:
    if not new_name or new_name in (".", ".."):
        return False, "That is not a name"
    if os.sep in new_name or (os.altsep and os.altsep in new_name):
        return False, "A name cannot contain a slash"
    target = os.path.join(os.path.dirname(path), new_name)
    if target == path:
        return True, ""
    # A case-only rename on a case-insensitive filesystem looks like a
    # collision with itself; the rename is still right and still safe.
    if os.path.lexists(target) and os.path.normcase(target) != os.path.normcase(path):
        return False, f"{new_name} already exists"
    try:
        os.rename(path, target)
    except OSError as exc:
        return False, _explain(exc)
    return True, ""


def make_folder(parent: str, name: str = "New Folder") -> tuple[str, str]:
    """Create a folder. Returns (path, error) — the path is "" on failure."""
    name = unique_name(parent, name)
    path = os.path.join(parent, name)
    try:
        os.mkdir(path)
    except OSError as exc:
        return "", _explain(exc)
    return path, ""


def make_file(parent: str, name: str = "New File", content: bytes = b"") -> tuple[str, str]:
    name = unique_name(parent, name)
    path = os.path.join(parent, name)
    try:
        with open(path, "xb") as handle:
            if content:
                handle.write(content)
    except OSError as exc:
        return "", _explain(exc)
    return path, ""


def set_mode(path: str, mode: int, recursive: bool = False) -> tuple[bool, str]:
    """Change permissions, optionally all the way down.

    The recursive form does not copy the mode onto directories verbatim: a
    directory without `x` cannot be entered, so anything with `r` gets the
    matching `x`. Applying `644` to a tree and locking yourself out of it is
    the classic way to ruin an afternoon.
    """
    # A directory needs `x` wherever it has `r`, or it cannot be entered —
    # including this one, which has to stay walkable long enough to finish
    # the walk.
    dir_mode = mode
    for shift in (6, 3, 0):
        if mode & (0o4 << shift):
            dir_mode |= 0o1 << shift
    is_dir = os.path.isdir(path) and not os.path.islink(path)
    try:
        os.chmod(path, dir_mode if is_dir else mode)
    except OSError as exc:
        return False, _explain(exc)
    if not recursive or not is_dir:
        return True, ""
    failures = 0
    for directory, dirs, files in os.walk(path):
        for name in dirs:
            try:
                os.chmod(os.path.join(directory, name), dir_mode)
            except OSError:
                failures += 1
        for name in files:
            full = os.path.join(directory, name)
            if os.path.islink(full):
                continue
            try:
                os.chmod(full, mode)
            except OSError:
                failures += 1
    if failures:
        return False, f"{failures} item(s) could not be changed"
    return True, ""


# ---------------------------------------------------------------- undoing ---

class Change:
    """One thing the user did, and enough about it to take it back.

    What is kept is the job's *parameters* as well as its result, because an
    undo and a redo want different halves of that: undoing a copy means
    deleting what appeared, redoing it means running the same copy again.

    A permanent delete is never recorded. There is nothing to record.
    """

    def __init__(self, kind: str, sources, destination: str = "",
                 result: Result | None = None, label: str = "",
                 rename_from: str = "", rename_to: str = ""):
        self.kind = kind
        self.sources = list(sources)
        self.destination = destination
        self.result = result
        self.rename_from = rename_from
        self.rename_to = rename_to
        self.label = label or self._describe()
        self.when = time.time()

    def _describe(self) -> str:
        count = len(self.sources)
        name = (os.path.basename(self.sources[0].rstrip(os.sep))
                if self.sources else "")
        thing = name if count == 1 else f"{count} items"
        return {
            COPY: f"Copy of {thing}",
            MOVE: f"Move of {thing}",
            TRASH: f"Moving {thing} to the trash",
            LINK: f"Link to {thing}",
            "rename": f"Renaming {self.rename_from} to {self.rename_to}",
            "create": f"Creating {thing}",
        }.get(self.kind, f"{self.kind} of {thing}")

    @property
    def undo_label(self) -> str:
        return {
            COPY: "Undo copy",
            MOVE: "Undo move",
            TRASH: "Put back",
            LINK: "Undo link",
            "rename": "Undo rename",
            "create": "Undo create",
        }.get(self.kind, "Undo")

    # -- the two directions ------------------------------------------------

    def undo(self, **kwargs) -> Job | tuple[bool, str]:
        """A Job that reverses this, or the (ok, message) of an instant one."""
        if self.kind == "rename":
            path = os.path.join(os.path.dirname(self.sources[0]), self.rename_to)
            return rename(path, self.rename_from)
        if self.kind == MOVE:
            return Job(PUT_BACK, [], pairs=[(new, old) for old, new
                                            in (self.result.moved if self.result
                                                else [])], **kwargs)
        if self.kind == TRASH:
            return Job(UNTRASH, self.result.trashed if self.result else [], **kwargs)
        if self.kind in (COPY, LINK, "create"):
            return Job(DELETE, self._top_level_created(), **kwargs)
        return (False, "That cannot be undone")

    def redo(self, **kwargs) -> Job | tuple[bool, str]:
        if self.kind == "rename":
            return rename(self.sources[0], self.rename_to)
        if self.kind == "create":
            return (False, "")       # handled by the window, which knows what kind
        return Job(self.kind, self.sources, self.destination, **kwargs)

    def _top_level_created(self) -> list[str]:
        """What a copy brought into being, without its own children.

        `created` lists every file and folder a copy made, nested ones
        included. Deleting the folder deletes the children with it, and
        deleting a child twice is an error to explain to somebody.
        """
        if self.kind == "create":
            return list(self.sources)
        made = list(self.result.created) if self.result else []
        tops = []
        for path in made:
            if not any(path != other and is_inside(path, other) for other in made):
                tops.append(path)
        return tops

    @property
    def undoable(self) -> bool:
        if self.kind == "rename":
            return bool(self.rename_from and self.rename_to)
        if self.kind == "create":
            return bool(self.sources)
        if self.result is None:
            return False
        if self.kind == MOVE:
            return bool(self.result.moved)
        if self.kind == TRASH:
            return bool(self.result.trashed)
        if self.kind in (COPY, LINK):
            return bool(self.result.created)
        return False


class UndoStack:
    """The last few things that happened, newest last.

    Bounded, because the alternative is remembering every copy of every file
    for the life of the process, and because an undo from four hundred
    operations ago is not an undo anybody wants.
    """

    def __init__(self, limit: int = 40):
        self.limit = limit
        self.done: list[Change] = []
        self.undone: list[Change] = []

    def push(self, change: Change) -> Change | None:
        if not change.undoable:
            return None
        self.done.append(change)
        del self.done[:-self.limit]
        # Doing something new is the end of the redo line, as everywhere else.
        self.undone.clear()
        return change

    def record(self, kind: str, sources, destination: str = "",
               result: Result | None = None, **kwargs) -> Change | None:
        return self.push(Change(kind, sources, destination, result, **kwargs))

    @property
    def can_undo(self) -> bool:
        return bool(self.done)

    @property
    def can_redo(self) -> bool:
        return bool(self.undone)

    def peek(self) -> Change | None:
        return self.done[-1] if self.done else None

    def peek_redo(self) -> Change | None:
        return self.undone[-1] if self.undone else None

    def pop_undo(self) -> Change | None:
        if not self.done:
            return None
        change = self.done.pop()
        self.undone.append(change)
        del self.undone[:-self.limit]
        return change

    def pop_redo(self) -> Change | None:
        if not self.undone:
            return None
        change = self.undone.pop()
        self.done.append(change)
        return change

    def clear(self) -> None:
        self.done.clear()
        self.undone.clear()
