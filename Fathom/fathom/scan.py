"""The walk, and the tree it builds.

Everything this app shows comes from one pass over the filesystem, so this
module decides what the app can be. Three choices are worth explaining.

*One thread.* The obvious way to make a directory walk faster is to run
several at once, and on this machine it makes it slower: `os.scandir` and
`stat` are microseconds on an NVMe SSD with a warm page cache, so the threads
spend their time queueing for the GIL rather than waiting on the disk. Measured
over a home directory of 356k files: 1.5s on one thread, 3.2s on four, 4.2s on
eight. So the walk is a single iterative loop — it runs on a worker thread to
keep the window alive, and that is the only thread it needs. What would change
the answer is a filesystem where a stat really does have to wait: a spinning
disk, NFS, a fuse mount. If Fathom ever grows a "this scan is crawling" case,
that is the one.

*Files are not objects.* A directory is a `Dir`, but the files inside it live
in flat parallel arrays on their parent — names in a list, sizes and times in
`array.array`. A Python object per file costs about 200 bytes; this costs
about 70, and on a 3.4 GB machine scanning a million files that is the
difference between a tool and a swap storm. Nothing above this module sees the
arrays: `Dir.file(i)` hands back a `FileRef`, and only the rows actually on
screen ever become one.

*Two sizes, and the truth about hardlinks.* `st_size` is how long the file is;
`st_blocks * 512` is what it costs, which is not the same for a sparse file,
a compressed btrfs extent, or anything smaller than a block. Both are kept.
And a file with more than one link is one file: counting it once per name
inflated this machine's home directory by 9 GB — 72,000 hardlinked files, most
of them package-manager and pacman cache — so the second and later sightings
of an inode are recorded as a per-directory deduction rather than a saving
spread invisibly through the tree. That keeps the file's own row honest about
its size while the totals agree with `du`.
"""

from __future__ import annotations

import os
import stat as statmod
import threading
import time
from array import array

BLOCK = 512

# Never descend into these, whatever the settings say. They are kernel
# bookkeeping rather than storage: /proc alone contains a nominal several
# terabytes of files that occupy nothing, and a treemap of it means nothing.
# `one_filesystem` catches them too, but only when the scan started somewhere
# else on the real root, so this is the belt to that's braces.
PSEUDO_PATHS = frozenset({
    "/proc", "/sys", "/dev", "/run", "/tmp/.X11-unix", "/var/run", "/var/lock",
})


class Dir:
    """A directory, and everything under it once the walk has rolled up.

    `size`/`alloc`/`nfiles`/`ndirs` describe the whole subtree; `own_*`
    describe only what is immediately inside.
    """

    __slots__ = (
        "name", "parent", "subdirs",
        "fnames", "fsizes", "fallocs", "fmtimes",
        "size", "alloc", "nfiles", "ndirs",
        "own_size", "own_alloc", "dup_size", "dup_alloc",
        "self_alloc", "mtime", "unreadable",
    )

    def __init__(self, name: str, parent: "Dir | None"):
        self.name = name
        self.parent = parent
        self.subdirs: list[Dir] = []
        self.fnames: list[str] = []
        self.fsizes = array("q")
        self.fallocs = array("q")
        self.fmtimes = array("q")     # seconds; "q" not "i", so 2038 is fine
        # Subtree totals, filled in by rollup().
        self.size = 0
        self.alloc = 0
        self.nfiles = 0
        self.ndirs = 0
        # What is immediately inside, and what of it was already counted
        # elsewhere because another name points at the same inode.
        self.own_size = 0
        self.own_alloc = 0
        self.dup_size = 0
        self.dup_alloc = 0
        self.self_alloc = 0           # the directory entry's own blocks
        self.mtime = 0.0
        self.unreadable = False

    # -------------------------------------------------------------- shape --

    @property
    def is_dir(self) -> bool:
        return True

    @property
    def path(self) -> str:
        """Absolute path, rebuilt from the chain of names.

        Not stored: a string per directory is another 60 bytes each, and this
        is only ever asked for rows the user can see or is about to act on.
        """
        parts = []
        node: Dir | None = self
        while node is not None and node.parent is not None:
            parts.append(node.name)
            node = node.parent
        base = node.name if node is not None else ""
        return os.path.join(base, *reversed(parts)) if parts else base

    def file_count(self) -> int:
        return len(self.fnames)

    def file(self, index: int) -> "FileRef":
        return FileRef(self, index)

    def files(self):
        for index in range(len(self.fnames)):
            yield FileRef(self, index)

    def child_count(self) -> int:
        return len(self.subdirs) + len(self.fnames)

    def walk(self):
        """Every directory in the subtree, this one first."""
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(node.subdirs)

    def find(self, name: str) -> "Dir | None":
        for sub in self.subdirs:
            if sub.name == name:
                return sub
        return None


class FileRef:
    """One file, made on demand out of its parent's arrays.

    Equality is by position, so a reference held by a list row and one made
    fresh by the treemap are the same thing — which is what lets clicking a
    rectangle select the right line.
    """

    __slots__ = ("parent", "index")

    def __init__(self, parent: Dir, index: int):
        self.parent = parent
        self.index = index

    @property
    def is_dir(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return self.parent.fnames[self.index]

    @property
    def size(self) -> int:
        return self.parent.fsizes[self.index]

    @property
    def alloc(self) -> int:
        return self.parent.fallocs[self.index]

    @property
    def mtime(self) -> float:
        return self.parent.fmtimes[self.index]

    @property
    def path(self) -> str:
        return os.path.join(self.parent.path, self.name)

    def __eq__(self, other):
        return (isinstance(other, FileRef)
                and other.parent is self.parent and other.index == self.index)

    def __hash__(self):
        return hash((id(self.parent), self.index))


class Progress:
    """What the window shows while the walk is running."""

    __slots__ = ("files", "dirs", "bytes", "where", "elapsed", "done")

    def __init__(self, files=0, dirs=0, nbytes=0, where="", elapsed=0.0, done=False):
        self.files = files
        self.dirs = dirs
        self.bytes = nbytes
        self.where = where
        self.elapsed = elapsed
        self.done = done


class Result:
    """A finished scan: the tree, and what the walk learned on the way."""

    __slots__ = ("root", "path", "elapsed", "cancelled",
                 "unreadable", "examples", "hardlinks", "hardlink_bytes",
                 "device", "total_bytes", "free_bytes", "reserved_bytes")

    def __init__(self, root, path):
        self.root = root
        self.path = path
        self.elapsed = 0.0
        self.cancelled = False
        self.unreadable = 0
        self.examples: list[str] = []
        self.hardlinks = 0
        self.hardlink_bytes = 0
        self.device = ""
        self.total_bytes = 0
        self.free_bytes = 0
        self.reserved_bytes = 0


class Scan:
    """One walk. Make it, run it on a thread, cancel it if it outstays."""

    # Enough entries between progress reports that the check itself is free,
    # few enough that the count on screen still moves on a slow filesystem.
    REPORT_EVERY = 4096

    def __init__(self, path: str, *, one_filesystem: bool = True,
                 dedup_hardlinks: bool = True, excludes=()):
        self.path = os.path.abspath(os.path.expanduser(path))
        self.one_filesystem = one_filesystem
        self.dedup_hardlinks = dedup_hardlinks
        self.excludes = frozenset(excludes)
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ---------------------------------------------------------- the walk ----

    def run(self, on_progress=None) -> Result:
        started = time.monotonic()
        root = Dir(self.path, None)
        result = Result(root, self.path)

        try:
            st = os.stat(self.path)
            root.mtime = st.st_mtime
            root.self_alloc = st.st_blocks * BLOCK
            root_dev = st.st_dev
        except OSError:
            root.unreadable = True
            result.unreadable = 1
            result.examples = [self.path]
            result.elapsed = time.monotonic() - started
            return result

        # Local names: this loop runs once per directory entry on the machine,
        # and an attribute lookup in it is not free at that count.
        scandir = os.scandir
        seen_inodes: set[tuple[int, int]] = set()
        dedup = self.dedup_hardlinks
        one_fs = self.one_filesystem
        excludes = self.excludes
        cancelled = self._cancel.is_set

        files = dirs = nbytes = 0
        unreadable = 0
        examples: list[str] = []
        hardlinks = 0
        hardlink_bytes = 0
        since_report = 0
        last_report = started

        stack: list[tuple[Dir, str]] = [(root, self.path)]
        while stack:
            node, here = stack.pop()
            if cancelled():
                break

            fnames = node.fnames
            fsizes = node.fsizes
            fallocs = node.fallocs
            fmtimes = node.fmtimes
            own_size = own_alloc = 0
            dup_size = dup_alloc = 0

            try:
                iterator = scandir(here)
            except OSError:
                node.unreadable = True
                unreadable += 1
                if len(examples) < 24:
                    examples.append(here)
                continue

            with iterator:
                while True:
                    try:
                        entry = next(iterator)
                    except StopIteration:
                        break
                    except OSError:
                        # A directory that vanished or turned unreadable
                        # mid-listing. Keep what was already counted.
                        node.unreadable = True
                        unreadable += 1
                        break

                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        unreadable += 1
                        continue

                    mode = st.st_mode
                    if statmod.S_ISDIR(mode):
                        if entry.name in excludes:
                            continue
                        child_path = entry.path
                        if child_path in PSEUDO_PATHS or (
                                one_fs and st.st_dev != root_dev):
                            continue
                        sub = Dir(entry.name, node)
                        sub.mtime = st.st_mtime
                        sub.self_alloc = st.st_blocks * BLOCK
                        node.subdirs.append(sub)
                        stack.append((sub, child_path))
                        dirs += 1
                        continue

                    # Symlinks are counted as what they are — a name and a
                    # path's worth of bytes — and never followed. Following
                    # them is how a disk tool reports a home directory as
                    # infinite, or counts /usr twice.
                    size = st.st_size
                    alloc = st.st_blocks * BLOCK
                    fnames.append(entry.name)
                    fsizes.append(size)
                    fallocs.append(alloc)
                    fmtimes.append(int(st.st_mtime))
                    own_size += size
                    own_alloc += alloc
                    files += 1
                    nbytes += alloc

                    if st.st_nlink > 1 and statmod.S_ISREG(mode):
                        key = (st.st_dev, st.st_ino)
                        if key in seen_inodes:
                            hardlinks += 1
                            hardlink_bytes += alloc
                            if dedup:
                                dup_size += size
                                dup_alloc += alloc
                        else:
                            seen_inodes.add(key)

                    since_report += 1
                    if since_report >= self.REPORT_EVERY:
                        since_report = 0
                        if cancelled():
                            break
                        now = time.monotonic()
                        # Time as well as count: 4096 entries is instant on an
                        # SSD and a visible pause on a fuse mount, and the
                        # window wants a steady tick either way.
                        if on_progress is not None and now - last_report >= 0.10:
                            last_report = now
                            on_progress(Progress(files, dirs, nbytes, here,
                                                 now - started))

            node.own_size = own_size
            node.own_alloc = own_alloc
            node.dup_size = dup_size
            node.dup_alloc = dup_alloc

        rollup(root)

        result.elapsed = time.monotonic() - started
        result.cancelled = self._cancel.is_set()
        result.unreadable = unreadable
        result.examples = examples
        result.hardlinks = hardlinks
        result.hardlink_bytes = hardlink_bytes
        try:
            usage = os.statvfs(self.path) if hasattr(os, "statvfs") else None
            if usage is not None:
                result.total_bytes = usage.f_blocks * usage.f_frsize
                # f_bavail is what this user may have; f_bfree is what is
                # actually empty. ext4 keeps 5% back for root, and the
                # difference is why a scan of a full disk comes up short
                # against `df`: 84 GB of files, 25.7 GB free, 5.9 GB that
                # belongs to neither. Naming it is the only way the three
                # numbers on screen add up to the size of the disk.
                result.free_bytes = usage.f_bavail * usage.f_frsize
                result.reserved_bytes = max(
                    0, (usage.f_bfree - usage.f_bavail) * usage.f_frsize)
            else:
                import shutil

                total, _used, free = shutil.disk_usage(self.path)
                result.total_bytes, result.free_bytes = total, free
        except OSError:
            pass

        if on_progress is not None:
            on_progress(Progress(files, dirs, nbytes, "", result.elapsed, done=True))
        return result


def rollup(root: Dir) -> Dir:
    """Add every directory's contents to it, deepest first.

    Iterative rather than recursive: a path can be deeper than Python's
    recursion limit, and a tree walker that dies on `.venv/lib/python3.14/...`
    is not one worth having.
    """
    order = []
    stack = [root]
    while stack:
        node = stack.pop()
        order.append(node)
        stack.extend(node.subdirs)

    for node in reversed(order):
        size = node.own_size - node.dup_size
        alloc = node.own_alloc - node.dup_alloc + node.self_alloc
        nfiles = len(node.fnames)
        ndirs = len(node.subdirs)
        for child in node.subdirs:
            size += child.size
            alloc += child.alloc
            nfiles += child.nfiles
            ndirs += child.ndirs
        node.size = size
        node.alloc = alloc
        node.nfiles = nfiles
        node.ndirs = ndirs
    return root


def subtract(start: Dir | None, size: int, alloc: int, nfiles: int, ndirs: int):
    """Take a deleted thing back out of `start` and every directory above it.

    Cheaper than rolling the whole tree up again, and the only way the numbers
    can stay right while the user deletes things one at a time.
    """
    node = start
    while node is not None:
        node.size -= size
        node.alloc -= alloc
        node.nfiles -= nfiles
        node.ndirs -= ndirs
        node = node.parent


def remove_file(ref: FileRef) -> tuple[int, int]:
    """Drop a file from its parent's arrays and from the totals above it."""
    parent = ref.parent
    index = ref.index
    size = parent.fsizes[index]
    alloc = parent.fallocs[index]
    del parent.fnames[index]
    del parent.fsizes[index]
    del parent.fallocs[index]
    del parent.fmtimes[index]
    parent.own_size -= size
    parent.own_alloc -= alloc
    parent.size -= size
    parent.alloc -= alloc
    parent.nfiles -= 1
    subtract(parent.parent, size, alloc, 1, 0)
    return size, alloc


def remove_dir(node: Dir) -> tuple[int, int]:
    """Drop a directory and everything under it."""
    parent = node.parent
    if parent is None:
        return 0, 0
    size, alloc = node.size, node.alloc
    nfiles, ndirs = node.nfiles, node.ndirs + 1
    parent.subdirs.remove(node)
    parent.size -= size
    parent.alloc -= alloc
    parent.nfiles -= nfiles
    parent.ndirs -= ndirs
    subtract(parent.parent, size, alloc, nfiles, ndirs)
    node.parent = None
    return size, alloc


def value_of(node, basis: str) -> int:
    """The number every view sizes things by — allocated blocks, or length."""
    return node.alloc if basis == "alloc" else node.size


def largest_files(root: Dir, limit: int = 5000, basis: str = "alloc") -> list[FileRef]:
    """The biggest files in the tree, biggest first.

    A full pass and a partial sort: `heapq.nlargest` over a million files is
    about a third of a second and needs no second copy of the tree, which a
    sorted list of every file would.
    """
    import heapq

    use_alloc = basis == "alloc"

    def entries():
        for node in root.walk():
            values = node.fallocs if use_alloc else node.fsizes
            for index in range(len(node.fnames)):
                yield values[index], index, node

    best = heapq.nlargest(limit, entries(), key=lambda item: item[0])
    return [FileRef(node, index) for _value, index, node in best]


def name_matcher(text: str):
    """A test for "does this filename match what the user typed".

    A plain word is a substring, because that is what people mean by typing
    one. Anything with a wildcard in it is a glob, because someone who types
    `*.iso` means a glob and would be baffled to be told there is no file
    called literally that.
    """
    text = text.strip().lower()
    if not text:
        return None
    if any(ch in text for ch in "*?["):
        import fnmatch

        pattern = text if "*" in text or "?" in text else f"*{text}*"
        compiled = fnmatch.translate(pattern)
        import re

        matches = re.compile(compiled).match
        return lambda name: matches(name.lower()) is not None
    return lambda name: text in name.lower()


def search(root: Dir, text: str, limit: int = 5000,
           basis: str = "alloc") -> list[FileRef]:
    """Every file in the tree whose name matches, biggest first.

    Over the whole scan, not over the biggest-files table: a search box that
    quietly only looked at the five thousand largest files would answer "no
    matches" for a thousand small ones, which is worse than not having it.
    A pass over a million names is about a third of a second.
    """
    import heapq

    matches = name_matcher(text)
    if matches is None:
        return []
    use_alloc = basis == "alloc"

    def entries():
        for node in root.walk():
            values = node.fallocs if use_alloc else node.fsizes
            names = node.fnames
            for index, name in enumerate(names):
                if matches(name):
                    yield values[index], index, node

    best = heapq.nlargest(limit, entries(), key=lambda item: item[0])
    return [FileRef(node, index) for _value, index, node in best]


def by_extension(root: Dir, basis: str = "alloc") -> list[tuple[str, int, int, int]]:
    """Every extension in the tree: (extension, files, size, alloc), biggest first."""
    from .filetypes import extension_of

    totals: dict[str, list[int]] = {}
    for node in root.walk():
        names = node.fnames
        sizes = node.fsizes
        allocs = node.fallocs
        for index in range(len(names)):
            key = extension_of(names[index])
            row = totals.get(key)
            if row is None:
                totals[key] = [1, sizes[index], allocs[index]]
            else:
                row[0] += 1
                row[1] += sizes[index]
                row[2] += allocs[index]

    # rows are (extension, files, size, alloc); sort on whichever of the two
    # sizes the rest of the app is currently showing.
    column = 3 if basis == "alloc" else 2
    rows = [(ext, row[0], row[1], row[2]) for ext, row in totals.items()]
    rows.sort(key=lambda row: row[column], reverse=True)
    return rows
