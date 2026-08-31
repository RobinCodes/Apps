"""Persisted settings — scan roots, window geometry, and the safety toggles."""

from __future__ import annotations

import json
import os

from . import winenv

DIR = winenv.config_home("git-manager")
PATH = os.path.join(DIR, "config.json")

DEFAULTS = {
    "roots": [os.path.expanduser("~")],
    # Repositories added by hand. The scan stops at the first repo it finds and
    # never descends into a working tree, so a repo nested inside another one
    # is unreachable by scanning alone and has to be remembered here.
    "extra_repos": [],
    # Repositories dropped with "Forget this folder". A scan has no memory and
    # would find one inside a scan root again on the very next walk, so the
    # decision to be rid of it has to be kept somewhere the scan cannot undo.
    "hidden_repos": [],
    "max_depth": 8,
    "confirm_destructive": True,   # answered "include, behind confirmation"
    "fetch_on_open": False,        # off by default: 19 repos of network on launch
    "status_poll_seconds": 15,
    "clone_dir": os.path.expanduser("~/Projects"),
    # Folder audited by the file system view for work that isn't backed up.
    "backup_root": os.path.expanduser("~/Projects"),
    "backup_max_depth": 3,
    "window_width": 1360,
    "window_height": 860,
    "last_repo": "",
    "diff_context": 3,
}


# Every list of paths below is one of these, and they arrive from three places
# that spell them differently -- see winenv.canonical.
PATH_LISTS = ("roots", "extra_repos", "hidden_repos")


def _unique(paths):
    """Order-preserving dedupe, case-insensitive where the filesystem is."""
    seen, out = set(), []
    for path in paths:
        key = winenv.path_key(path)
        if key and key not in seen:
            seen.add(key)
            out.append(path)
    return out


class Config(dict):
    def __init__(self):
        super().__init__(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(PATH) as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in DEFAULTS and isinstance(v, type(DEFAULTS[k])):
                        self[k] = v
        except (OSError, ValueError):
            pass
        # Settle the spelling on the way in, so a config written before this
        # -- with git's forward slashes in it -- starts matching the scan.
        for key in PATH_LISTS:
            self[key] = _unique(winenv.canonical(p) for p in self[key] if p)
        self["last_repo"] = winenv.canonical(self["last_repo"])
        if not self["roots"]:
            self["roots"] = [os.path.expanduser("~")]
        return self

    def save(self):
        try:
            os.makedirs(DIR, exist_ok=True)
            tmp = PATH + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(dict(self), fh, indent=2)
            os.replace(tmp, PATH)
        except OSError:
            pass
