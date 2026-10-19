"""Persisted settings, in ~/.config/hermione/config.json.

Every value has a default here, and a value read from the file is only taken
if it is the same shape as its default and — where there is a list of them —
one of the allowed answers. A settings file is a text file a person can edit
and a thing a half-finished write can truncate, so "the file disagrees" has to
mean "use the default", never "crash on startup".
"""

from __future__ import annotations

import json
import os

from . import theme as theming, winenv

DIR = winenv.config_home("hermione")
PATH = os.path.join(DIR, "config.json")
STATE = os.path.join(winenv.data_home("hermione"), "state.json")

DEFAULTS = {
    # -- the look ---------------------------------------------------------
    # The house themes, named in theme.py and the same eight in every app
    # here. With `follow_system` on, the two below are used instead of
    # `theme` and follow the desktop's own light/dark setting.
    **theming.THEME_SETTINGS,

    "glass": True,               # the translucent, blurred chrome
    "glass_blur": 36,            # how far the chrome blurs what is behind it
    "translucency": 0.80,        # 1.0 is an opaque window, 0.5 is barely there
    "rounding": 16,              # the window's corner radius, in pixels
    "accent_from_folder": True,  # tint the chrome towards the folder's own hue
    "animations": True,
    "colour_icons": True,        # icons tinted by category
    "font_scale": 1.0,

    # -- what is shown ----------------------------------------------------
    "view_mode": "list",         # list | grid | columns
    "show_hidden": False,
    "folders_first": True,
    "sort_key": "name",
    "sort_reverse": False,
    "icon_size": 72,             # the grid's tile size
    "row_height": "normal",      # compact | normal | roomy
    "show_sidebar": True,
    "show_preview": False,
    "show_status": True,
    "show_thumbnails": True,
    "thumbnail_limit_mb": 80,    # don't try to thumbnail a 4 GB video
    "folder_sizes": False,       # count what is under every folder on sight
    "git_status": True,
    "single_click": False,
    "columns": {                 # which of the detail view's columns are shown
        "size": True,
        "type": False,
        "modified": True,
        "permissions": False,
        "owner": False,
    },

    # -- behaviour --------------------------------------------------------
    "confirm_trash": False,      # the trash is already the undo
    "confirm_delete": True,      # permanent deletion always asks
    "confirm_many": 100,         # ask before acting on more than this many
    "restore_session": True,
    "terminal": "",              # empty means "whichever the machine has"
    "search_content_max_mb": 16,
    "search_follow_links": False,

    # -- remembered -------------------------------------------------------
    "window_width": 1180,
    "window_height": 760,
    "window_maximized": False,
    "sidebar_width": 232,
    "preview_width": 320,
    "split_position": 560,
    "bookmarks": [],             # [{"name": ..., "path": ...}]
    "bookmarks_seeded": False,   # GTK's own bookmarks have been imported once
    "recent_folders": [],
    "last_folder": "",
    "tabs": [],                  # the session, when restore_session is on
    "folder_views": {},          # path -> {"view_mode", "sort_key", ...}
}

VALID = {
    # The themes are not listed here: theme.py owns that list, and a name it
    # no longer knows resolves to the default when it is looked up rather
    # than being rejected on the way in — which is what lets a theme be
    # renamed without emptying everyone's settings file.
    "view_mode": ("list", "grid", "columns"),
    "row_height": ("compact", "normal", "roomy"),
    "sort_key": ("name", "size", "modified", "type", "accessed", "created",
                 "extension"),
}

RANGES = {
    "glass_blur": (0, 80),
    "translucency": (0.45, 1.0),
    "rounding": (0, 28),
    "icon_size": (40, 256),
    "font_scale": (0.8, 1.4),
    "thumbnail_limit_mb": (0, 4096),
    "confirm_many": (0, 100000),
    "search_content_max_mb": (0, 4096),
    "sidebar_width": (180, 480),
    "preview_width": (220, 700),
}

# How many of these to keep. A list that grows without a limit is a settings
# file that grows without a limit.
LIMITS = {"recent_folders": 24, "bookmarks": 64, "tabs": 24, "folder_views": 400}


class Config(dict):
    def __init__(self, path: str = ""):
        super().__init__({key: _copy(value) for key, value in DEFAULTS.items()})
        self.path = path or PATH
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return self
        if not isinstance(data, dict):
            return self
        for key, value in data.items():
            if key not in DEFAULTS:
                continue
            default = DEFAULTS[key]
            # bool is a subclass of int, so an isinstance check alone would
            # let `true` into a number and a number into a checkbox.
            if isinstance(default, bool) != isinstance(value, bool):
                continue
            if isinstance(default, float) and isinstance(value, int):
                value = float(value)
            if not isinstance(value, type(default)):
                continue
            if key in VALID and value not in VALID[key]:
                continue
            if key in RANGES:
                low, high = RANGES[key]
                value = max(low, min(high, value))
            if key == "columns" and isinstance(value, dict):
                merged = dict(DEFAULTS["columns"])
                for name, shown in value.items():
                    if name in merged and isinstance(shown, bool):
                        merged[name] = shown
                self[key] = merged
                continue
            if key in ("bookmarks", "tabs"):
                self[key] = [item for item in value if _is_place(item)][:LIMITS[key]]
                continue
            if key == "recent_folders":
                self[key] = [p for p in value
                             if isinstance(p, str)][:LIMITS[key]]
                continue
            if key == "folder_views" and isinstance(value, dict):
                self[key] = {k: v for k, v in list(value.items())[:LIMITS[key]]
                             if isinstance(k, str) and isinstance(v, dict)}
                continue
            self[key] = _copy(value)
        return self

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(dict(self), handle, indent=2)
            # Replace, so an interrupted write leaves the old file whole
            # rather than half of a new one.
            os.replace(tmp, self.path)
        except OSError:
            pass

    # -- the lists the window keeps ---------------------------------------

    def remember_folder(self, path: str):
        recent = [p for p in self["recent_folders"] if p != path]
        recent.insert(0, path)
        self["recent_folders"] = recent[:LIMITS["recent_folders"]]
        self["last_folder"] = path

    def add_bookmark(self, path: str, name: str = "") -> bool:
        if any(b.get("path") == path for b in self["bookmarks"]):
            return False
        name = name or os.path.basename(path.rstrip(os.sep)) or path
        self["bookmarks"] = (self["bookmarks"] +
                             [{"name": name, "path": path}])[:LIMITS["bookmarks"]]
        return True

    def remove_bookmark(self, path: str) -> bool:
        kept = [b for b in self["bookmarks"] if b.get("path") != path]
        changed = len(kept) != len(self["bookmarks"])
        self["bookmarks"] = kept
        return changed

    def is_bookmarked(self, path: str) -> bool:
        return any(b.get("path") == path for b in self["bookmarks"])

    def move_bookmark(self, path: str, to_index: int) -> None:
        marks = list(self["bookmarks"])
        for index, mark in enumerate(marks):
            if mark.get("path") == path:
                marks.insert(max(0, min(len(marks) - 1, to_index)),
                             marks.pop(index))
                break
        self["bookmarks"] = marks

    # -- per-folder view memory -------------------------------------------

    def folder_view(self, path: str) -> dict:
        """What this folder was last looked at with.

        A pictures folder wants the grid and a source tree wants the list, and
        being told so once per folder is better than being asked every time.
        """
        stored = self["folder_views"].get(path)
        return dict(stored) if isinstance(stored, dict) else {}

    def set_folder_view(self, path: str, **changes) -> None:
        views = dict(self["folder_views"])
        current = dict(views.get(path, {}))
        current.update({k: v for k, v in changes.items() if v is not None})
        views[path] = current
        if len(views) > LIMITS["folder_views"]:
            # Drop the oldest half rather than one at a time, so this is not
            # a re-sort of four hundred entries on every folder change.
            for key in list(views)[:LIMITS["folder_views"] // 2]:
                if key != path:
                    del views[key]
        self["folder_views"] = views


def _is_place(item) -> bool:
    return (isinstance(item, dict) and isinstance(item.get("path"), str)
            and isinstance(item.get("name", ""), str))


def _copy(value):
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return list(value)
    return value
