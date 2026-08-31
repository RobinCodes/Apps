"""Persisted settings, in ~/.config/larenderer/config.json."""

from __future__ import annotations

import json
import os

from . import winenv

DIR = winenv.config_home("larenderer")
PATH = os.path.join(DIR, "config.json")

DEFAULTS = {
    "engine": "pdflatex",
    "auto_compile": True,
    "compile_delay_ms": 400,     # after the last keystroke
    "shell_escape": False,       # off: it lets a document run shell commands
    "bibliography": True,
    "timeout_seconds": 40,
    "editor_font_size": 11,
    "zoom": "fit-width",
    "pdf_theme": "auto",         # auto | light | dark — the pages, not the app
    "sync_preview": True,        # follow the cursor via synctex
    "show_problems": False,      # it opens itself when something is wrong
    "window_width": 1500,
    "window_height": 940,
    "window_maximized": False,
    "split_position": 620,
    "last_file": "",
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
                for key, value in data.items():
                    if key in DEFAULTS and isinstance(value, type(DEFAULTS[key])):
                        self[key] = value
        except (OSError, ValueError):
            pass
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
