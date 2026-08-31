"""Finding every git repository on the machine.

A plain `find` over a home directory spends most of its time inside
node_modules and package caches, so the walk prunes those by name and stops
descending as soon as it finds a repo — a repo's own subdirectories can't
contain anything this app should list separately (submodules excepted, which
are reached through their parent).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from . import winenv

# Where the last scan is kept, so the window has rows before the walk ends.
# Computed at import, because a test points XDG_CACHE_HOME at a scratch
# directory before importing this module. winenv is what knows where a cache
# belongs on each platform; reading XDG_CACHE_HOME directly, as this did, put
# the file in a Unix-shaped ~/.cache on Windows that nothing else in the app
# uses. Under an XDG_CACHE_HOME override the path is unchanged, and so is it
# on Linux.
CACHE = os.path.join(winenv.cache_home("git-manager"), "repos.json")

# Directory names never worth descending into. These are where the time goes.
PRUNE_NAMES = {
    "node_modules", "__pycache__", ".cache", ".npm", ".yarn", ".pnpm-store",
    ".venv", "venv", "env", ".tox", "site-packages", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "target", ".gradle", ".m2", ".cargo", ".rustup", ".nvm",
    ".steam", ".wine", ".mozilla", ".thunderbird", "Trash", ".Trash",
    ".local/share/Trash", "snap", ".flatpak", ".var", ".docker", ".vagrant.d",
    "vendor", "bower_components", ".terraform", "dist-newstyle", ".stack-work",
    ".conda", "anaconda3", "miniconda3", ".ollama", ".ccache",
}

# Absolute paths to skip outright — pseudo-filesystems and mount noise that
# only matter once someone adds "/" as a scan root.
PRUNE_PATHS = {
    "/proc", "/sys", "/dev", "/run", "/tmp/.X11-unix", "/var/lib/docker",
    "/lost+found", "/boot",
}

# The Windows equivalents. These are the directories that make scanning "~" or
# a whole drive take minutes instead of seconds, and none of them has ever
# held a repository worth finding. Built from the environment rather than
# hardcoded, because none of these paths is guaranteed to be on C:.
if os.name == "nt":
    _env = os.environ.get
    _home = os.path.expanduser("~")
    for _p in (
        _env("SystemRoot"), _env("ProgramData"),
        _env("ProgramFiles"), _env("ProgramFiles(x86)"), _env("ProgramW6432"),
        _env("TEMP"), _env("TMP"),
        os.path.join(_home, "AppData", "Local", "Temp"),
        os.path.join(_home, "AppData", "Local", "Microsoft"),
        os.path.join(_home, "AppData", "Local", "Packages"),
        os.path.join(_home, "AppData", "LocalLow"),
        os.path.join(_home, "OneDriveTemp"),
        r"C:\$Recycle.Bin", r"C:\System Volume Information", r"C:\msys64",
    ):
        if _p:
            PRUNE_PATHS.add(os.path.normcase(os.path.normpath(_p)))

    PRUNE_NAMES |= {"$Recycle.Bin", "System Volume Information", "MSOCache",
                    "Recovery", "WinSxS", "assembly"}


@dataclass
class RepoRef:
    path: str
    name: str
    bare: bool = False

    def to_json(self):
        return {"path": self.path, "name": self.name, "bare": self.bare}


def _is_repo_dir(entry_path):
    """Return (is_repo, is_bare) for a candidate directory."""
    dotgit = os.path.join(entry_path, ".git")
    # A .git file (not directory) means a worktree or submodule — still a repo.
    if os.path.isdir(dotgit) or os.path.isfile(dotgit):
        return True, False
    # Bare repo: has HEAD + objects + refs but no worktree.
    if all(os.path.exists(os.path.join(entry_path, n)) for n in ("HEAD", "objects", "refs")):
        return True, True
    return False, False


def scan(roots, max_depth=8, on_found=None, should_stop=None, follow_symlinks=False):
    """Walk `roots` and return the repositories found, shallowest first.

    on_found(RepoRef) fires as each is discovered so the UI can populate live;
    should_stop() is polled so a rescan can be cancelled from the GUI.
    """
    found, seen = [], set()
    stack = []
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        if os.path.isdir(root):
            stack.append((root, 0))

    while stack:
        if should_stop and should_stop():
            break
        path, depth = stack.pop(0)  # breadth-first: shallow repos surface first
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)

        is_repo, bare = _is_repo_dir(path)
        if is_repo:
            path = winenv.canonical(path)
            ref = RepoRef(path=path, name=os.path.basename(path) or path, bare=bare)
            found.append(ref)
            if on_found:
                on_found(ref)
            continue  # don't descend into a repo's working tree

        if depth >= max_depth:
            continue
        try:
            with os.scandir(path) as it:
                for entry in it:
                    if (entry.name in PRUNE_NAMES
                            or os.path.normcase(entry.path) in PRUNE_PATHS):
                        continue
                    try:
                        if not entry.is_dir(follow_symlinks=follow_symlinks):
                            continue
                    except OSError:
                        continue
                    stack.append((entry.path, depth + 1))
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
            continue  # unreadable directories are normal outside $HOME

    found.sort(key=lambda r: r.name.lower())
    return found


def save_cache(repos):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w") as fh:
            json.dump({"when": time.time(), "repos": [r.to_json() for r in repos]}, fh)
    except OSError:
        pass


def load_cache():
    """Repos from the last scan, so the window has content before the walk ends."""
    try:
        with open(CACHE) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    out = []
    for r in data.get("repos", []):
        # Drop anything deleted or moved since the scan.
        if isinstance(r, dict) and r.get("path") and os.path.isdir(r["path"]):
            path = winenv.canonical(r["path"])
            out.append(RepoRef(path=path, name=r.get("name") or os.path.basename(path),
                               bare=bool(r.get("bare"))))
    return out
