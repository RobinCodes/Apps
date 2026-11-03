"""Settings, in ~/.config/clio/config.json (%APPDATA%\\clio on Windows).

Only the window's: the theme, the glass, its size, whether the list of boards is
showing. The boards are in the brain, and go where it goes.
"""

from __future__ import annotations

import json
import os

from . import theme as theming, winenv

DIR = winenv.config_home("clio")
PATH = os.path.join(DIR, "config.json")

DEFAULTS = {
    **theming.SETTINGS,
    "window_width": 1100,
    "window_height": 720,
    "window_maximized": False,
    "sidebar_visible": True,
    "list_mode": False,
    # What was open, so it is open again: a board's file.
    "last_open": "",
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
                    wanted = type(DEFAULTS[key])
                    if isinstance(value, wanted) or (
                            wanted is float and isinstance(value, int)):
                        self[key] = wanted(value) if wanted is float else value
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
