"""Persisted settings, in ~/.config/notedown/config.json."""

from __future__ import annotations

import json
import os

from . import theme as theming, winenv

DIR = winenv.config_home("notedown")
PATH = os.path.join(DIR, "config.json")

DEFAULTS = {
    # -- the house style --------------------------------------------------
    # The themes, the glass, and whether to follow the desktop between light
    # and dark. The keys and their defaults come from theme.py, which every
    # app here has the same copy of, so that adding a theme or changing the
    # default pair is one edit rather than nine.
    **theming.SETTINGS,

    # The editor. A family of "" is whatever GTK would have used anyway, and
    # "monospace" is the one the desktop keeps for code rather than a font of
    # that name.
    "editor_font_family": "monospace",
    "editor_font_size": 13,
    "wrap": True,
    "show_gutter": True,
    "focus_mode": False,        # dim everything but the current paragraph
    "typewriter": False,        # keep the current line in the middle
    "spell_check": False,

    # The preview.
    "preview_font_family": "",      # "" = the desktop's own document font
    "preview_font_size": 13,        # points, the size the rest is a ratio of
    "reading_width": 820,       # 0 = the whole pane, else a measure in pixels
    "sync_scroll": True,
    "load_remote_images": False,
    "preview_visible": True,
    "editor_visible": True,

    # Saving.
    "auto_save": False,
    "auto_save_delay_ms": 1500,
    "trim_trailing_space": False,   # off: two spaces are a line break here
    "final_newline": True,
    "backup_on_save": False,

    # The window.
    "window_width": 1180,
    "window_height": 820,
    "window_maximized": False,
    "split_fraction": 0.5,
    "sidebar_visible": False,
    "sidebar_width": 230,

    # What was open last time.
    "open_files": [],
    "last_folder": "",
    "recent_files": [],
    "recent_limit": 15,

    # Export.
    "export_style": "clean",    # clean | plain | github
    "export_standalone": True,  # one file with the CSS inside it
}


class Config(dict):
    def __init__(self):
        super().__init__(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for key, value in data.items():
                    if key not in DEFAULTS:
                        continue
                    # int and float are interchangeable here; a scale written
                    # as 1 rather than 1.0 is still a scale.
                    wanted = type(DEFAULTS[key])
                    if isinstance(value, wanted) or (
                            wanted is float and isinstance(value, int)):
                        self[key] = wanted(value) if wanted is float else value
                # The house-style keys have their own rules — see theming.take.
                theming.take(self, data)
        except (OSError, ValueError):
            pass
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

    def remember_file(self, path: str):
        """Put a path at the top of the recent list, without duplicating it."""
        if not path:
            return
        recent = [p for p in self["recent_files"] if p != path]
        recent.insert(0, path)
        self["recent_files"] = recent[:max(1, int(self["recent_limit"]))]

    def forget_file(self, path: str):
        self["recent_files"] = [p for p in self["recent_files"] if p != path]
