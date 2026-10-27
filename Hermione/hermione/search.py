"""Finding things: by name, by what is inside them, and by what they cost.

All of it runs on a worker thread and reports hits as they are found, because
the useful property of a search is that the first answer arrives immediately
even when the last one takes a minute. Nothing here returns a list and then
hands it over; everything calls `on_hit` as it goes and watches a stop flag.

Content search is written in Python rather than shelled out to `grep`. It is
slower per megabyte and it is the only version that behaves identically on
every machine this app runs on — and the files it is pointed at are usually a
source tree, where the limit is `stat`, not `memchr`. `ripgrep` is used
instead when the machine has it, which is a straight win and a fallback away.

The three space-finders at the bottom — duplicates, the biggest files, the
empty folders — are the ones people actually open a file manager to do and
then do by hand.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat as statmod
import subprocess
import threading
import time

from . import entries as entrymod
from . import filetypes, winenv

# Directories never worth walking into for a search started from the home
# folder or the root. Each one is either not the user's files or a hall of
# mirrors.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".cache", ".venv", "venv", ".tox",
    ".gradle", ".m2", "target", ".next", ".nuxt", ".terraform",
    "/proc", "/sys", "/dev", "/run",
}

# Absolute paths that are not files at all and will hang or flood a walk.
SKIP_ROOTS = ("/proc", "/sys", "/dev", "/run", "/snap")


class Query:
    """What is being looked for."""

    def __init__(self, text: str = "", *, regex: bool = False,
                 case_sensitive: bool = False, in_contents: bool = False,
                 categories: set[str] | None = None,
                 min_size: int = -1, max_size: int = -1,
                 newer_than: float = 0.0, older_than: float = 0.0,
                 include_hidden: bool = False, follow_links: bool = False,
                 max_results: int = 5000, max_depth: int = -1,
                 content_limit_mb: int = 16, skip_noise: bool = True):
        self.text = text
        self.regex = regex
        self.case_sensitive = case_sensitive
        self.in_contents = in_contents
        self.categories = categories or set()
        self.min_size = min_size
        self.max_size = max_size
        self.newer_than = newer_than
        self.older_than = older_than
        self.include_hidden = include_hidden
        self.follow_links = follow_links
        self.max_results = max_results
        self.max_depth = max_depth
        self.content_limit = content_limit_mb * 1024 * 1024
        self.skip_noise = skip_noise
        self._matcher = self._build()

    def _build(self):
        """One callable that answers "does this name match?".

        Built once. A search of a hundred thousand files that compiles a
        regex per file spends most of its time in the regex compiler.
        """
        if not self.text:
            return lambda name: True
        if self.regex:
            try:
                pattern = re.compile(self.text,
                                     0 if self.case_sensitive else re.IGNORECASE)
            except re.error:
                # An unfinished regex — someone is still typing `(foo` — must
                # match nothing rather than raise into the worker.
                return lambda name: False
            return lambda name: pattern.search(name) is not None
        if any(char in self.text for char in "*?["):
            import fnmatch

            pattern = (self.text if self.case_sensitive else self.text.lower())
            compiled = re.compile(fnmatch.translate(pattern))
            if self.case_sensitive:
                return lambda name: compiled.match(name) is not None
            return lambda name: compiled.match(name.lower()) is not None
        needle = self.text if self.case_sensitive else self.text.lower()
        if self.case_sensitive:
            return lambda name: needle in name
        return lambda name: needle in name.lower()

    @property
    def valid(self) -> bool:
        """Is there anything here to search for?"""
        return bool(self.text or self.categories or self.min_size >= 0
                    or self.max_size >= 0 or self.newer_than or self.older_than)

    def name_matches(self, name: str) -> bool:
        return self._matcher(name)

    def entry_matches(self, entry: entrymod.Entry) -> bool:
        """Everything except the contents, which cost a read."""
        if self.text and not self.in_contents and not self.name_matches(entry.name):
            return False
        if self.categories and entry.category not in self.categories:
            return False
        if not entry.is_dir:
            if self.min_size >= 0 and entry.size < self.min_size:
                return False
            if self.max_size >= 0 and entry.size > self.max_size:
                return False
        elif self.min_size >= 0 or self.max_size >= 0:
            return False            # a size filter is about files
        if self.newer_than and entry.mtime < self.newer_than:
            return False
        if self.older_than and entry.mtime > self.older_than:
            return False
        return True

    def content_pattern(self):
        """The compiled pattern for a content search, or None."""
        if not self.in_contents or not self.text:
            return None
        flags = 0 if self.case_sensitive else re.IGNORECASE
        try:
            return re.compile(self.text if self.regex else re.escape(self.text),
                              flags)
        except re.error:
            return None


class Hit:
    """One answer: an entry, and where in it the text was found."""

    def __init__(self, entry: entrymod.Entry, line_no: int = 0, line: str = ""):
        self.entry = entry
        self.line_no = line_no
        self.line = line

    @property
    def path(self) -> str:
        return self.entry.path

    def __repr__(self) -> str:
        where = f":{self.line_no}" if self.line_no else ""
        return f"<Hit {self.entry.name}{where}>"


class Progress:
    """How far a search has got. Same reasoning as ops.Progress: no locks."""

    def __init__(self):
        self.folders = 0
        self.files = 0
        self.hits = 0
        self.current = ""
        self.started = time.monotonic()
        self.finished = 0.0
        self.stopped = False
        self.truncated = False      # hit max_results and gave up early

    @property
    def elapsed(self) -> float:
        return (self.finished or time.monotonic()) - self.started


class Search:
    """A running search. Start it, read `progress`, stop it when bored."""

    def __init__(self, roots, query: Query, *, on_hit=None, on_progress=None,
                 on_done=None):
        self.roots = [os.path.abspath(p) for p in (roots if isinstance(roots, (list, tuple))
                                                   else [roots])]
        self.query = query
        self.on_hit = on_hit
        self.on_progress = on_progress
        self.on_done = on_done
        self.progress = Progress()
        self.stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_report = 0.0

    def start(self) -> "Search":
        self._thread = threading.Thread(target=self.run, daemon=True,
                                        name="hermione-search")
        self._thread.start()
        return self

    def cancel(self) -> None:
        self.stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def run(self) -> Progress:
        pattern = self.query.content_pattern()
        try:
            if pattern is not None and winenv.tool("rg"):
                self._run_ripgrep(pattern)
            else:
                for root in self.roots:
                    self._walk(root, pattern, 0)
        except _Stop:
            self.progress.stopped = True
        except OSError:
            pass
        self.progress.finished = time.monotonic()
        self._report(force=True)
        if self.on_done is not None:
            self.on_done(self.progress)
        return self.progress

    # -- the walk ---------------------------------------------------------

    def _walk(self, directory: str, pattern, depth: int) -> None:
        if self.stop.is_set():
            raise _Stop()
        if self.query.max_depth >= 0 and depth > self.query.max_depth:
            return
        self.progress.folders += 1
        self.progress.current = directory
        self._report()

        try:
            scanner = os.scandir(directory)
        except OSError:
            return

        children = []
        with scanner:
            while True:
                if self.stop.is_set():
                    raise _Stop()
                try:
                    item = next(scanner)
                except StopIteration:
                    break
                except OSError:
                    break

                name = item.name
                if not self.query.include_hidden and name.startswith("."):
                    continue
                try:
                    is_link = item.is_symlink()
                    is_dir = item.is_dir(follow_symlinks=self.query.follow_links)
                except OSError:
                    is_link, is_dir = False, False
                if is_link and not self.query.follow_links:
                    is_dir = False

                if is_dir:
                    if not (self.query.skip_noise and name in SKIP_DIRS):
                        children.append(os.path.join(directory, name))

                self.progress.files += 1
                try:
                    info = item.stat(follow_symlinks=False)
                except OSError:
                    continue
                entry = entrymod.from_stat(os.path.join(directory, name), name,
                                           info if not is_link else None,
                                           info if is_link else None)
                if is_link:
                    # A link found by a search is still a result; it just has
                    # the link's own size and date.
                    entry.is_dir = is_dir
                self._consider(entry, pattern)

        for child in children:
            full = os.path.join(child)
            if any(full.startswith(bad) for bad in SKIP_ROOTS):
                continue
            self._walk(full, pattern, depth + 1)

    def _consider(self, entry: entrymod.Entry, pattern) -> None:
        if not self.query.entry_matches(entry):
            return
        if pattern is not None:
            if entry.is_dir:
                return
            line_no, line = self._grep(entry, pattern)
            if line_no == 0:
                return
            self._emit(Hit(entry, line_no, line))
            return
        self._emit(Hit(entry))

    def _grep(self, entry: entrymod.Entry, pattern) -> tuple[int, str]:
        """The first matching line of a file, or (0, "").

        Binary files are skipped by looking for a zero byte in the first block,
        which is what every grep does and is right often enough that the
        alternative — a character encoding guess per file — is not worth it.
        """
        if entry.size > self.query.content_limit:
            return 0, ""
        if entry.category in ("binary", "archive", "image", "video", "audio",
                              "font"):
            return 0, ""
        try:
            with open(entry.path, "rb") as handle:
                first = handle.read(8192)
                if b"\0" in first:
                    return 0, ""
                number = 0
                rest = first + handle.read()
        except OSError:
            return 0, ""
        try:
            text = rest.decode("utf-8")
        except UnicodeDecodeError:
            text = rest.decode("utf-8", "replace")
        for number, line in enumerate(text.splitlines(), 1):
            if self.stop.is_set():
                raise _Stop()
            if pattern.search(line):
                return number, line.strip()[:400]
        return 0, ""

    # -- ripgrep, where it exists -----------------------------------------

    def _run_ripgrep(self, pattern) -> None:
        """The same content search, handed to ripgrep.

        Only for content searches: ripgrep's own file-name filtering does not
        have our size and date predicates, and for a name-only search the walk
        above is already as fast as the disk.
        """
        args = ["rg", "--line-number", "--no-heading", "--with-filename",
                "--max-columns", "400", "--null"]
        if not self.query.case_sensitive:
            args.append("--ignore-case")
        if not self.query.regex:
            args.append("--fixed-strings")
        if self.query.include_hidden:
            args.append("--hidden")
        if self.query.follow_links:
            args.append("--follow")
        args += ["--", self.query.text] + self.roots

        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL,
                                       creationflags=winenv.NO_WINDOW)
        except (OSError, ValueError):
            for root in self.roots:
                self._walk(root, pattern, 0)
            return

        try:
            assert process.stdout is not None
            for raw in process.stdout:
                if self.stop.is_set():
                    process.terminate()
                    raise _Stop()
                path_part, _, rest = raw.partition(b"\0")
                path = path_part.decode("utf-8", "replace")
                number, _, line = rest.partition(b":")
                try:
                    line_no = int(number)
                except ValueError:
                    continue
                try:
                    info = os.lstat(path)
                except OSError:
                    continue
                entry = entrymod.from_stat(path, os.path.basename(path), info)
                self.progress.files += 1
                if not self.query.entry_matches(entry):
                    continue
                self._emit(Hit(entry, line_no,
                               line.decode("utf-8", "replace").strip()[:400]))
        finally:
            try:
                process.stdout.close() if process.stdout else None
                process.terminate()
                process.wait(timeout=1)
            except (OSError, subprocess.SubprocessError):
                pass

    # -- reporting --------------------------------------------------------

    def _emit(self, hit: Hit) -> None:
        self.progress.hits += 1
        if self.on_hit is not None:
            self.on_hit(hit)
        if self.progress.hits >= self.query.max_results:
            self.progress.truncated = True
            raise _Stop()

    def _report(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_report < 0.08:
            return
        self._last_report = now
        if self.on_progress is not None:
            self.on_progress(self.progress)


class _Stop(Exception):
    """Internal: unwind the walk when the search is cancelled or full."""


# ------------------------------------------------- the three space tools ---

def walk_files(root: str, stop=None, include_hidden: bool = False,
               follow_links: bool = False, skip_noise: bool = False):
    """Every file under `root`, as (path, stat). The shared engine below.

    A generator, so each of the three tools can stop early and none of them
    has to hold a list of a million paths.
    """
    stack = [root]
    while stack:
        if stop is not None and stop.is_set():
            return
        directory = stack.pop()
        try:
            scanner = os.scandir(directory)
        except OSError:
            continue
        with scanner:
            for item in scanner:
                if stop is not None and stop.is_set():
                    return
                if not include_hidden and item.name.startswith("."):
                    continue
                if skip_noise and item.name in SKIP_DIRS:
                    continue
                try:
                    info = item.stat(follow_symlinks=False)
                except OSError:
                    continue
                if statmod.S_ISDIR(info.st_mode):
                    stack.append(os.path.join(directory, item.name))
                elif statmod.S_ISREG(info.st_mode):
                    yield os.path.join(directory, item.name), info


def largest_files(root: str, count: int = 50, stop=None,
                  include_hidden: bool = False) -> list[tuple[str, int]]:
    """The biggest files under `root`, biggest first.

    Kept in a heap of `count`, so this costs one `stat` per file and no
    sorting of the whole tree — the same walk over a million files whether
    you asked for the top ten or the top thousand.
    """
    import heapq

    heap: list[tuple[int, str]] = []
    for path, info in walk_files(root, stop, include_hidden):
        if len(heap) < count:
            heapq.heappush(heap, (info.st_size, path))
        elif info.st_size > heap[0][0]:
            heapq.heapreplace(heap, (info.st_size, path))
    return [(path, size) for size, path in sorted(heap, reverse=True)]


def empty_folders(root: str, stop=None, include_hidden: bool = False) -> list[str]:
    """Folders holding nothing, deepest first.

    "Nothing" includes nothing but other empty folders: a chain of
    `a/b/c/d` with no file anywhere in it is four empty folders and not one,
    because that is what a person means when they ask what is empty. Walking
    bottom-up makes this one pass — a folder is empty when every child
    folder is already in the answer and there are no files.

    Deepest first, so deleting the list in the order given works: the parent
    is only removable once its children have gone.
    """
    found: list[str] = []
    empty = set()
    root = os.path.normpath(root)
    for directory, dirs, files in os.walk(root, topdown=False,
                                          onerror=lambda _e: None):
        if stop is not None and stop.is_set():
            break
        # Normalised, because the walk and `os.path.join` need not agree on
        # the separator: MSYS2's Python joins with `/` while the walk hands
        # back what Windows' own directory listing built, with `\`. Compared
        # raw, no folder ever found its empty children in the set.
        directory = os.path.normpath(directory)
        visible_dirs = [d for d in dirs if include_hidden or not d.startswith(".")]
        visible_files = [f for f in files if include_hidden or not f.startswith(".")]
        if visible_files:
            continue
        if any(os.path.join(directory, name) not in empty for name in visible_dirs):
            continue
        empty.add(directory)
        if directory != root and (include_hidden
                                  or not os.path.basename(directory).startswith(".")):
            found.append(directory)
    found.sort(key=lambda p: p.count(os.sep), reverse=True)
    return found


HASH_BLOCK = 1 << 18


def file_hash(path: str, length: int = 0, algorithm: str = "sha256",
              stop=None) -> str:
    """A digest of a file, or of its first `length` bytes.

    The partial form is what makes the duplicate finder quick: two files that
    differ at all almost always differ in their first 64 kB, so the full read
    is only ever done for files that really do look identical.
    """
    try:
        digest = hashlib.new(algorithm)
    except ValueError:
        digest = hashlib.sha256()
    read = 0
    try:
        with open(path, "rb") as handle:
            while True:
                if stop is not None and stop.is_set():
                    return ""
                want = HASH_BLOCK if not length else min(HASH_BLOCK, length - read)
                if want <= 0:
                    break
                block = handle.read(want)
                if not block:
                    break
                digest.update(block)
                read += len(block)
    except OSError:
        return ""
    return digest.hexdigest()


def duplicates(roots, stop=None, include_hidden: bool = False,
               min_size: int = 1, on_progress=None) -> list[list[str]]:
    """Groups of files with identical contents, biggest group first.

    Three passes, each cheaper than the next is expensive: group by size,
    then by the hash of the first 64 kB, then by the hash of the whole file.
    A folder of ten thousand photos is a handful of full reads rather than
    ten thousand.
    """
    if isinstance(roots, str):
        roots = [roots]
    by_size: dict[int, list[str]] = {}
    seen_inodes: set[tuple[int, int]] = set()
    for root in roots:
        for path, info in walk_files(root, stop, include_hidden):
            if info.st_size < min_size:
                continue
            # Two names for one inode are one file, not a duplicate of it.
            key = (info.st_dev, info.st_ino)
            if info.st_nlink > 1:
                if key in seen_inodes:
                    continue
                seen_inodes.add(key)
            by_size.setdefault(info.st_size, []).append(path)
            if on_progress is not None and len(by_size) % 500 == 0:
                on_progress(len(by_size))

    groups: list[list[str]] = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        if stop is not None and stop.is_set():
            break
        heads: dict[str, list[str]] = {}
        for path in paths:
            head = file_hash(path, min(size, 65536), stop=stop)
            if head:
                heads.setdefault(head, []).append(path)
        for candidates in heads.values():
            if len(candidates) < 2:
                continue
            if size <= 65536:
                groups.append(sorted(candidates))
                continue
            whole: dict[str, list[str]] = {}
            for path in candidates:
                digest = file_hash(path, stop=stop)
                if digest:
                    whole.setdefault(digest, []).append(path)
            for same in whole.values():
                if len(same) > 1:
                    groups.append(sorted(same))
    groups.sort(key=lambda group: (len(group), -len(group[0])), reverse=True)
    return groups


def folder_size(path: str, stop=None) -> tuple[int, int, int]:
    """(bytes, files, folders) under a folder. For the properties dialog."""
    total = 0
    files = 0
    folders = 0
    for directory, dirs, names in os.walk(path, onerror=lambda _e: None):
        if stop is not None and stop.is_set():
            break
        folders += len(dirs)
        for name in names:
            files += 1
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                pass
    return total, files, folders


def categories_present(paths) -> dict[str, int]:
    """How many of each category a selection holds, for the status line."""
    counts: dict[str, int] = {}
    for path in paths:
        key = filetypes.category(os.path.basename(path), os.path.isdir(path))
        counts[key] = counts.get(key, 0) + 1
    return counts
