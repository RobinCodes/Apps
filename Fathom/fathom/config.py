"""Persisted settings, in ~/.config/fathom/config.json."""

from __future__ import annotations

import json
import os

from . import theme as theming, winenv

DIR = winenv.config_home("fathom")
PATH = os.path.join(DIR, "config.json")

DEFAULTS = {
    # -- the house style --------------------------------------------------
    # The themes, the glass, and whether to follow the desktop between light
    # and dark. The keys and their defaults come from theme.py, which every
    # app here has the same copy of, so that adding a theme or changing the
    # default pair is one edit rather than nine.
    **theming.SETTINGS,

    # "alloc" — what the file costs the disk, which is what you get back by
    # deleting it and what `du` and `df` agree on. "size" is how long the file
    # is, which is bigger for a directory of tiny files and smaller for a
    # sparse one.
    "size_basis": "alloc",
    "si_units": False,           # 1000-byte kB rather than 1024-byte KB
    "one_filesystem": True,      # don't wander off the mount you started on
    "dedup_hardlinks": True,     # one inode, one file, however many names
    "show_free_space": True,     # free space as a block of its own in the map
    "treemap_colour": "type",    # type | folder | depth
    "treemap_labels": True,
    "max_file_rows": 5000,       # how many of the biggest files to list
    "auto_expand": True,         # open the root's biggest branch after a scan
    "window_width": 1440,
    "window_height": 900,
    "window_maximized": False,
    "split_position": 780,
    "last_path": "",
    "recent_paths": [],
    "columns": {                 # which of the tree's columns are shown
        "alloc": True,
        "size": False,
        "percent": True,
        "items": True,
        "files": False,
        "folders": False,
        "modified": True,
    },
}

VALID = {
    "size_basis": ("alloc", "size"),
    "treemap_colour": ("type", "folder", "depth"),
}


class Config(dict):
    def __init__(self):
        super().__init__({key: _copy(value) for key, value in DEFAULTS.items()})
        self.load()

    def load(self):
        try:
            with open(PATH, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return self
        if not isinstance(data, dict):
            return self
        for key, value in data.items():
            if key not in DEFAULTS:
                continue
            # bool is a subclass of int, so an isinstance check alone would let
            # `true` into an integer setting and a number into a checkbox.
            if isinstance(DEFAULTS[key], bool) != isinstance(value, bool):
                continue
            if not isinstance(value, type(DEFAULTS[key])):
                continue
            if key in VALID and value not in VALID[key]:
                continue
            if key == "columns" and isinstance(value, dict):
                merged = dict(DEFAULTS["columns"])
                for name, shown in value.items():
                    if name in merged and isinstance(shown, bool):
                        merged[name] = shown
                self[key] = merged
                continue
            self[key] = _copy(value)
        # The house-style keys have their own rules — see theming.take.
        theming.take(self, data)
        return self

    def save(self):
        try:
            os.makedirs(DIR, exist_ok=True)
            tmp = PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(dict(self), fh, indent=2)
            os.replace(tmp, PATH)
        except OSError:
            pass

    def remember_path(self, path: str, limit: int = 12):
        """Keep a short list of what has been scanned, most recent first."""
        recent = [p for p in self["recent_paths"] if p != path]
        recent.insert(0, path)
        self["recent_paths"] = recent[:limit]
        self["last_path"] = path


def _copy(value):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value
