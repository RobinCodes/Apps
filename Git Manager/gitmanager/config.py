"""Persisted settings — scan roots, window geometry, and the safety toggles."""

from __future__ import annotations

import json
import os

DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "git-manager"
)
PATH = os.path.join(DIR, "config.json")

DEFAULTS = {
    "roots": [os.path.expanduser("~")],
    # Repositories added by hand. The scan stops at the first repo it finds and
    # never descends into a working tree, so a repo nested inside another one
    # is unreachable by scanning alone and has to be remembered here.
    "extra_repos": [],
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
