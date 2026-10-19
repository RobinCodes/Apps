"""Persisted settings, in ~/.config/prism/config.json.

Same shape as the other apps here: a dict with defaults, a load that ignores
anything it does not recognise or that arrives with the wrong type, and an
atomic save. The type check matters more than it looks — a hand-edited file
with `"opacity": 1` instead of `1.0` should still work, and one with
`"opacity": "high"` should be ignored rather than crash the window on the
line that multiplies by it.

Numbers that have a sensible range are clamped on the way in rather than
trusted, because every one of them ends up in a drawing call.
"""

from __future__ import annotations

import json
import os

from . import paths, theme as theming

DIR = paths.config_home()
PATH = os.path.join(DIR, "config.json")

DEFAULTS = {
    # --- the look -------------------------------------------------------
    # The house themes, named in house/theme.py and the same eight in every
    # app here. The two defaults are the pair the house is tuned for.
    # With `follow_system` on, `dark_theme` and `light_theme` are used
    # instead of `theme` and follow the desktop's own setting.
    **theming.THEME_SETTINGS,

    "opacity": 0.94,            # of the window as a whole, 0.55 to 1.0
    "motion": True,             # the slow light behind the glass
    "specular": True,           # the highlight that follows the pointer
    "grain": True,              # the frosting, and what stops gradient banding
    # How far a floating panel blurs the terminal behind it. 0 turns it off,
    # and then the panel goes back to being nearly solid so the text under it
    # still does not compete.
    "panel_blur": 26,
    "corner_radius": 14,
    "pane_padding": 12,         # between the glass edge and the first glyph

    # --- the text -------------------------------------------------------
    "font_family": "",          # "" = whatever the desktop keeps for monospace
    "font_size": 11.0,
    "line_height": 1.0,         # VTE's cell height scale, 1.0 to 2.0
    "letter_spacing": 1.0,      # cell width scale
    "bold_is_bright": False,
    "cursor_shape": "beam",     # beam | block | underline
    "cursor_blink": True,

    # --- behaviour ------------------------------------------------------
    "scrollback_lines": 10000,  # 0 = none, -1 = unlimited
    "scroll_on_output": False,
    "scroll_on_keystroke": True,
    "mouse_autohide": True,
    "copy_on_select": False,
    "paste_guard": True,        # ask before pasting something that runs itself
    "confirm_close": True,      # when something is still running in the tab
    "audible_bell": False,
    "visual_bell": True,
    "hyperlinks": True,
    "sixel": True,
    "word_chars": "-A-Za-z0-9,./?%&#:_=+@~",

    # --- the shell ------------------------------------------------------
    "shell": "",                # "" = the one in /etc/passwd
    "login_shell": False,
    "startup_command": "",      # run in each new tab, before the prompt

    # --- the window -----------------------------------------------------
    "window_width": 940,
    "window_height": 620,
    "window_maximized": False,
    "tab_bar": "auto",          # auto | always | never
    "restore_session": True,
    "cwd_in_title": True,
}

# Everything with a range, and what that range is. Checked on load and on
# every set, so no drawing code has to defend itself against a bad number.
LIMITS = {
    "opacity": (0.55, 1.0),
    "corner_radius": (0, 28),
    "panel_blur": (0, 80),
    "pane_padding": (0, 48),
    "font_size": (6.0, 48.0),
    "line_height": (0.8, 2.0),
    "letter_spacing": (0.8, 2.0),
    "scrollback_lines": (-1, 1000000),
    "window_width": (320, 10000),
    "window_height": (200, 10000),
}

CHOICES = {
    "cursor_shape": ("beam", "block", "underline"),
    "tab_bar": ("auto", "always", "never"),
}


def clamp(key, value):
    """`value` brought inside whatever range `key` has, if it has one."""
    if key in LIMITS:
        low, high = LIMITS[key]
        return max(low, min(high, value))
    if key in CHOICES and value not in CHOICES[key]:
        return DEFAULTS[key]
    return value


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
                    # int and float are interchangeable here; an opacity
                    # written as 1 rather than 1.0 is still an opacity.
                    wanted = type(DEFAULTS[key])
                    if wanted is float and isinstance(value, int) and not isinstance(value, bool):
                        value = float(value)
                    if isinstance(value, bool) != (wanted is bool):
                        continue
                    if isinstance(value, wanted):
                        self[key] = clamp(key, value)
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

    def set(self, key, value):
        """A setting changed, clamped and saved. True when it really changed."""
        if key not in DEFAULTS:
            return False
        value = clamp(key, value)
        if self.get(key) == value:
            return False
        self[key] = value
        self.save()
        return True

    # The theme in force, which is not always the `theme` key: with
    # follow_system on, the desktop chooses which of the two it is.
    def theme_key(self, dark: bool) -> str:
        if self["follow_system"]:
            return self["dark_theme"] if dark else self["light_theme"]
        return self["theme"]
