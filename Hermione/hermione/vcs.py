"""Git's opinion of the folder being looked at.

A source tree is the folder this app will be pointed at most, and the one
question always worth answering there is "what have I changed?". So a listing
inside a repository carries a mark per row: modified, staged, untracked,
ignored, clean.

Done with one `git status` per folder rather than one per file. `git status
--porcelain -z` on the directory gives every interesting path under it in a
single process; asking per row would be forty processes for a folder of forty
files and would make scrolling feel like wading.

The repository root is found by walking up, and cached, because the answer
changes only when the folder does.
"""

from __future__ import annotations

import os
import subprocess

from . import winenv

# git's two-letter codes, reduced to the one letter a row can show.
MODIFIED = "M"
ADDED = "A"
DELETED = "D"
RENAMED = "R"
UNTRACKED = "?"
IGNORED = "!"
CONFLICT = "U"
CLEAN = ""

LABELS = {
    MODIFIED: "Modified",
    ADDED: "Staged",
    DELETED: "Deleted",
    RENAMED: "Renamed",
    UNTRACKED: "Untracked",
    IGNORED: "Ignored",
    CONFLICT: "Conflicted",
}

# The colours these marks are drawn in. Warm for "you changed this", cool for
# "git knows about it", grey for "git is ignoring it".
COLOURS = {
    MODIFIED: "#f5a97f",
    ADDED: "#a6da95",
    DELETED: "#ed8796",
    RENAMED: "#8aadf4",
    UNTRACKED: "#8aadf4",
    IGNORED: "#6e738d",
    CONFLICT: "#ee99a0",
}

_roots: dict[str, str | None] = {}


def repo_root(path: str) -> str | None:
    """The top of the repository `path` is in, or None.

    Walked by looking for `.git` rather than by asking git, because this runs
    on every folder change and a process is a thousand times the cost of six
    `stat` calls. A `.git` *file* counts: that is what a worktree and a
    submodule have.
    """
    path = os.path.abspath(path)
    if path in _roots:
        return _roots[path]
    walker = path
    answer = None
    while True:
        if os.path.exists(os.path.join(walker, ".git")):
            answer = walker
            break
        parent = os.path.dirname(walker)
        if parent == walker:
            break
        walker = parent
    _roots[path] = answer
    return answer


def forget(path: str = "") -> None:
    """Drop what is remembered, for one folder or all of them."""
    if path:
        _roots.pop(os.path.abspath(path), None)
    else:
        _roots.clear()


def status(directory: str, include_ignored: bool = False,
           timeout: float = 4.0) -> dict[str, str]:
    """Path -> mark, for everything interesting in `directory`.

    The keys are absolute paths. A folder gets the strongest mark of anything
    under it, so a collapsed `src/` says "something in here changed" — which
    is the only thing a one-line folder row can usefully say.
    """
    root = repo_root(directory)
    if root is None or not winenv.tool("git"):
        return {}

    args = ["git", "-C", directory, "status", "--porcelain", "-z",
            "--untracked-files=normal", "--no-renames"]
    if include_ignored:
        args.append("--ignored=matching")
    try:
        done = subprocess.run(args, capture_output=True, timeout=timeout,
                              creationflags=winenv.NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return {}
    if done.returncode != 0:
        return {}

    marks: dict[str, str] = {}
    here = os.path.abspath(directory)
    for record in done.stdout.split(b"\0"):
        if len(record) < 4:
            continue
        try:
            text = record.decode("utf-8")
        except UnicodeDecodeError:
            text = record.decode("utf-8", "replace")
        code, name = text[:2], text[3:]
        if not name:
            continue
        full = os.path.normpath(os.path.join(root, name.rstrip("/")))
        mark = _reduce(code)
        if not mark:
            continue
        # Anything below this folder marks the folder it is in, all the way
        # back up to the folder being shown.
        walker = full
        while True:
            parent = os.path.dirname(walker)
            if walker == here or parent == walker:
                break
            if parent == here:
                marks[walker] = _stronger(marks.get(walker, ""), mark)
                break
            walker = parent
        else:
            continue
        if os.path.dirname(full) == here:
            marks[full] = _stronger(marks.get(full, ""), mark)
    return marks


# Which mark wins when a folder holds several kinds of change. A conflict is
# the one you must deal with; an ignored file is the one you need not know
# about, so it loses to everything.
_ORDER = (CONFLICT, MODIFIED, ADDED, DELETED, RENAMED, UNTRACKED, IGNORED)


def _stronger(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    return left if _ORDER.index(left) <= _ORDER.index(right) else right


def _reduce(code: str) -> str:
    """git's XY status pair, as the one letter a row shows."""
    if code == "??":
        return UNTRACKED
    if code == "!!":
        return IGNORED
    if "U" in code or code in ("AA", "DD"):
        return CONFLICT
    index, work = code[0], code[1]
    if work == "M" or work == "T":
        return MODIFIED
    if work == "D":
        return DELETED
    if index == "R":
        return RENAMED
    if index in ("A", "M", "T"):
        return ADDED
    if index == "D":
        return DELETED
    return CLEAN


def branch(directory: str, timeout: float = 2.0) -> str:
    """The branch name, for the status bar. Empty outside a repository."""
    if repo_root(directory) is None or not winenv.tool("git"):
        return ""
    try:
        done = subprocess.run(["git", "-C", directory, "rev-parse",
                               "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True, timeout=timeout,
                              creationflags=winenv.NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return ""
    name = done.stdout.strip()
    if done.returncode != 0 or not name:
        return ""
    return "detached" if name == "HEAD" else name
