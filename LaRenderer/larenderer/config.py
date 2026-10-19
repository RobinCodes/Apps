"""Persisted settings, in ~/.config/larenderer/config.json."""

from __future__ import annotations

import json
import os

from . import theme as theming, winenv

DIR = winenv.config_home("larenderer")
PATH = os.path.join(DIR, "config.json")

DEFAULTS = {
    # -- the house style --------------------------------------------------
    # The themes, the glass, and whether to follow the desktop between light
    # and dark. The keys and their defaults come from theme.py, which every
    # app here has the same copy of, so that adding a theme or changing the
    # default pair is one edit rather than nine.
    **theming.SETTINGS,

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
    # The editor's share of the editor+preview width. The pixel position
    # above stays for a first start from an older config; this is what is
    # used, because a split kept in pixels squeezes the preview to nothing
    # as soon as the folder side bar takes 250 of them.
    "split_fraction": 0.0,
    "last_file": "",
    # The tabs that were open when the window last closed, reopened in order
    # on the next start. Files that have since been deleted are skipped
    # silently rather than opening an empty tab named after a missing one.
    "open_files": [],

    # The folder in the side bar, reopened with the tabs.
    "open_folder": "",
    "recent_folders": [],
    "sidebar_visible": True,
    "sidebar_width": 250,
    "show_hidden_files": True,
    # Main documents chosen by hand ("Use as the main document"), by folder.
    # A folder not in here gets the one project.find_main picks.
    "project_mains": {},

    # Overleaf, through its git bridge. Projects are cloned under this folder.
    "overleaf_dir": os.path.join(os.path.expanduser("~"), "Projects", "Overleaf"),
    "overleaf_pull_on_open": True,   # fetch collaborators' edits when a project opens
    "overleaf_sync_on_save": False,  # commit, pull and push after every save
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
                    if key in DEFAULTS and isinstance(value, type(DEFAULTS[key])):
                        self[key] = value
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
