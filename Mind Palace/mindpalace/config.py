"""Persisted settings, in ~/.config/mind-palace/config.json.

The palace's own, and only those: the look, the window, which room was open.
Every room keeps its settings where its app keeps them, so a room changed in
the palace is changed in the app too, and the other way round.
"""

from __future__ import annotations

import json
import os

from . import theme as theming, winenv

DIR = winenv.config_home("mind-palace")
PATH = os.path.join(DIR, "config.json")

DEFAULTS = {
    # The themes, the glass, following the desktop: the house's keys, from the
    # same theme.py every app has, so the palace's Look page is everyone's.
    **theming.SETTINGS,
    "window_width": 1100,
    "window_height": 760,
    "window_maximized": False,
    "last_room": "Home",          # the room to come back to
    "reopen_last_room": True,     # or always open on Home
    "sidebar_shown": True,        # in a wide window; a narrow one folds it away
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


def _copy(value):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value
