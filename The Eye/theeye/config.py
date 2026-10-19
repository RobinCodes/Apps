"""Persisted settings, in ~/.config/the-eye/config.json.

Two files, not one, because two kinds of thing are being remembered and they
do not deserve the same treatment.

*Settings* are choices — the theme, how transparent the glass is, whether to
loop. They are small, they are worth carrying to another machine, and they go
in the config directory.

*State* is what the app noticed — where you stopped watching each of four
hundred videos, which folders you have opened, the last window size. It grows
without bound, it is meaningless on another machine, and it goes in the data
directory where a synchronised profile will leave it alone. `resume` is the
part that earns its keep: stopping a film forty minutes in and having it
start there next time is the single feature most often missed from a viewer,
and it costs one JSON file.

Both are written atomically, through a temporary file and a rename, because
the alternative is a truncated settings file after a power cut and an app that
comes back with its defaults.
"""

from __future__ import annotations

import json
import os
import time

from . import theme as theming, winenv

APP = "the-eye"
CONFIG_DIR = winenv.config_home(APP)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
STATE_DIR = winenv.data_home(APP)
STATE_PATH = os.path.join(STATE_DIR, "state.json")

DEFAULTS = {
    # look
    # The house themes, and whether to follow the desktop between the pair.
    **theming.THEME_SETTINGS,
    "translucency": 0.86,     # 0.5..1.0; how much of the glass is glass
    "motion": True,           # the drifting light behind the chrome
    "glass": True,            # the whole hand-painted material, or flat panels
    "backdrop_blur": True,    # really blur the picture behind the chrome
    "corner_radius": 10.0,    # on the picture itself, 0 for square
    "chrome_autohide": True,  # the bars get out of the way when playing
    "autohide_seconds": 2.5,
    "effect_quality": "high",  # high | balanced | draft

    # viewing
    "fit": "window",          # window | width | height | fill | actual
    "zoom_steps": 1.25,       # the factor one notch of the wheel moves
    "smooth_scaling": True,   # off for pixel art
    "background_matte": True,  # the theme's neutral matte behind the picture
    "checkerboard": False,     # chequers rather than matte behind transparency
    "show_strip": True,
    "strip_size": 108,
    "strip_at": "bottom",     # bottom | top | start | end
    "show_info": False,       # the metadata panel
    "slideshow_seconds": 4.0,
    "slideshow_shuffle": False,
    "slideshow_loop": True,

    # playing
    "volume": 0.8,
    "muted": False,
    "loop": "off",            # off | one | all
    "autoplay": True,
    "resume_playback": True,
    "resume_threshold": 30.0,   # don't bother remembering less than this
    "resume_tail": 15.0,        # nor the last few seconds, which means "done"
    "speed": 1.0,
    "subtitles": True,
    "subtitle_size": 1.0,
    "hardware_decoding": True,
    "seek_short": 5.0,          # the arrow keys
    "seek_long": 60.0,          # page up and down
    "frame_rate_hint": 25.0,    # for frame stepping before the real rate is known
    "keep_above_while_playing": False,

    # sorting and walking
    "sort": "name",           # name | date | size | kind | random
    "sort_descending": False,
    "recurse": False,          # take subfolders into the reel as well
    "show_hidden": False,
    "wrap_around": True,       # the end of the folder goes back to the start

    # editing
    "editor_open": False,
    "editor_live": True,       # grade the video while it plays, not just paused
    "remember_grades": True,   # a sidecar beside each picture
    "export_quality": 95,
    "export_format": "same",   # same | png | jpeg | webp

    # the window
    "window_width": 1280,
    "window_height": 800,
    "window_maximized": False,
    "fullscreen_on_open": False,
}

VALID = {
    "theme": None,            # checked against theme.THEMES, which imports GTK
    "fit": ("window", "width", "height", "fill", "actual"),
    "effect_quality": ("high", "balanced", "draft"),
    "loop": ("off", "one", "all"),
    "sort": ("name", "date", "size", "kind", "random"),
    "strip_at": ("bottom", "top", "start", "end"),
    "export_format": ("same", "png", "jpeg", "webp"),
}

LIMITS = {
    "translucency": (0.45, 1.0),
    "corner_radius": (0.0, 32.0),
    "autohide_seconds": (0.5, 30.0),
    "zoom_steps": (1.05, 4.0),
    "strip_size": (56, 260),
    "slideshow_seconds": (0.5, 600.0),
    "volume": (0.0, 1.0),
    "speed": (0.1, 8.0),
    "subtitle_size": (0.4, 3.0),
    "seek_short": (0.5, 60.0),
    "seek_long": (5.0, 600.0),
    "frame_rate_hint": (1.0, 480.0),
    "resume_threshold": (0.0, 3600.0),
    "resume_tail": (0.0, 600.0),
    "export_quality": (40, 100),
    "window_width": (360, 16384),
    "window_height": (240, 16384),
}


def _copy(value):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


class Store(dict):
    """A dict that knows its own defaults and will not accept nonsense.

    Every value read from disk is checked against the type of its default —
    `bool` before `int`, because bool is a subclass of int and otherwise
    `true` would be accepted into a numeric setting — then against the list of
    allowed values or the numeric range. A setting that fails any of those is
    dropped, not corrected, so a file hand-edited into an odd state loses only
    the line that is wrong.
    """

    path = ""
    defaults: dict = {}
    valid: dict = {}
    limits: dict = {}

    def __init__(self):
        super().__init__({k: _copy(v) for k, v in self.defaults.items()})
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return self
        if isinstance(data, dict):
            self.take(data)
        return self

    def take(self, data):
        for key, value in data.items():
            if key not in self.defaults:
                continue
            want = self.defaults[key]
            if isinstance(want, bool) != isinstance(value, bool):
                continue
            if isinstance(want, (int, float)) and not isinstance(want, bool):
                if not isinstance(value, (int, float)):
                    continue
                low, high = self.limits.get(key, (None, None))
                if low is not None:
                    value = max(low, min(high, value))
                value = type(want)(value)
            elif not isinstance(value, type(want)):
                continue
            allowed = self.valid.get(key)
            if allowed and value not in allowed:
                continue
            self[key] = _copy(value)
        return self

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.trimmed(), fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def trimmed(self):
        return dict(self)

    def reset(self, key):
        if key in self.defaults:
            self[key] = _copy(self.defaults[key])


class Config(Store):
    path = CONFIG_PATH
    defaults = DEFAULTS
    valid = VALID
    limits = LIMITS

    def trimmed(self):
        """Only what differs from the defaults.

        A settings file that holds nothing but the two things you changed is a
        settings file you can read, and — more to the point — one where a new
        default in a later version actually reaches you instead of being
        shadowed by a value you never chose.
        """
        return {k: v for k, v in self.items() if v != self.defaults.get(k)}


# ------------------------------------------------------------------ state ----

STATE_DEFAULTS = {
    "resume": {},          # path -> {"at": seconds, "of": seconds, "when": epoch}
    "recent_folders": [],
    "recent_files": [],
    "grades": {},          # path -> a grade, for anything without a sidecar
    "window": {},
}

RESUME_LIMIT = 600
RECENT_LIMIT = 24
GRADE_LIMIT = 400


class State(Store):
    """What the app noticed, as opposed to what was chosen.

    Everything here is capped, because all of it is a list that only ever
    grows. The caps drop the oldest entries, which is right for resume
    positions and recent folders alike: a film you have not touched in two
    years is one you will start again anyway.
    """

    path = STATE_PATH
    defaults = STATE_DEFAULTS
    valid = {}
    limits = {}

    # -- where you stopped watching ---------------------------------------

    def remember_position(self, path: str, at: float, of: float,
                          threshold: float = 30.0, tail: float = 15.0):
        """Note where a video was stopped, if it is worth noting.

        Three cases are not: a clip shorter than `threshold`, a position in
        the first few seconds, and a position within `tail` of the end. The
        last is the important one — stopping a film in its credits means you
        finished it, and offering to resume at 1:58:40 next time would be
        worse than useless.
        """
        if of <= 0 or of < threshold:
            self.forget_position(path)
            return
        if at < min(threshold, 10.0) or at > of - tail:
            self.forget_position(path)
            return
        resume = dict(self["resume"])
        resume[path] = {"at": round(float(at), 2), "of": round(float(of), 2),
                        "when": int(time.time())}
        if len(resume) > RESUME_LIMIT:
            oldest = sorted(resume.items(),
                            key=lambda kv: kv[1].get("when", 0))
            for key, _ in oldest[:len(resume) - RESUME_LIMIT]:
                resume.pop(key, None)
        self["resume"] = resume

    def position_for(self, path: str) -> float:
        entry = self["resume"].get(path)
        if not isinstance(entry, dict):
            return 0.0
        try:
            return max(0.0, float(entry.get("at", 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def progress_for(self, path: str) -> float:
        """How far through, 0..1 — what the strip draws as a bar on a poster."""
        entry = self["resume"].get(path)
        if not isinstance(entry, dict):
            return 0.0
        try:
            at, of = float(entry.get("at", 0)), float(entry.get("of", 0))
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, at / of)) if of > 0 else 0.0

    def forget_position(self, path: str):
        if path in self["resume"]:
            resume = dict(self["resume"])
            resume.pop(path, None)
            self["resume"] = resume

    # -- what has been opened ---------------------------------------------

    def remember_folder(self, path: str):
        self["recent_folders"] = _push(self["recent_folders"], path, RECENT_LIMIT)

    def remember_file(self, path: str):
        self["recent_files"] = _push(self["recent_files"], path, RECENT_LIMIT)

    def recent_folders(self) -> list[str]:
        """The ones still there, so a menu never offers an unplugged disk."""
        return [p for p in self["recent_folders"] if os.path.isdir(p)]

    def recent_files(self) -> list[str]:
        return [p for p in self["recent_files"] if os.path.isfile(p)]

    # -- grades, for files we cannot write a sidecar beside ---------------

    def remember_grade(self, path: str, values: dict):
        grades = dict(self["grades"])
        if values:
            grades[path] = dict(values)
        else:
            grades.pop(path, None)
        if len(grades) > GRADE_LIMIT:
            for key in list(grades)[:len(grades) - GRADE_LIMIT]:
                grades.pop(key, None)
        self["grades"] = grades

    def grade_for(self, path: str) -> dict:
        found = self["grades"].get(path)
        return dict(found) if isinstance(found, dict) else {}


def _push(items, value, limit):
    kept = [item for item in items if item != value and isinstance(item, str)]
    kept.insert(0, value)
    return kept[:limit]
