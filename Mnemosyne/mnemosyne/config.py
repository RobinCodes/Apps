"""Settings, in two places, because they follow Robin to two different extents.

`Config` is the window's: the theme, the glass, the size it was closed at, the
page it was on. That is about this machine's screen, so it lives where every
other app here keeps its settings — ~/.config/mnemosyne/config.json, or
%APPDATA%\\mnemosyne on Windows.

`Learning` is the method's: which folders hold cards, how many new ones a day,
the retention FSRS aims for. Those have to be the same on every machine or two
machines would schedule the same log differently, so they live in the brain —
`SecondBrain/config/learning.yaml`, as SYSTEM.md §7 says — and go wherever the
brain goes. Preferences writes them back there. Keys this app does not know
are kept when it does, so another tool can keep its own settings in the same
file.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import date, datetime
from pathlib import Path

from . import brain, theme as theming, winenv, yamlite

DIR = winenv.config_home("mnemosyne")
PATH = os.path.join(DIR, "config.json")

DEFAULTS = {
    # -- the house style --------------------------------------------------
    # The themes, the glass, and whether to follow the desktop between light
    # and dark. The keys and their defaults come from theme.py, which every
    # app here has the same copy of.
    **theming.SETTINGS,

    # The window.
    "window_width": 760,
    "window_height": 820,
    "window_maximized": False,
    "page": "today",

    # Studying on screen. The card text is a ratio of this, in points.
    "card_font_size": 17,
    # Show the context breadcrumb above a question.
    "show_context": True,

    # The paper. Points, for the PDF; 9.5 fits about twenty-five rows of
    # ordinary cards on a side of A4 and is still comfortable to read.
    "sheet_font_size": 9.5,
    "sheet_include_new": True,
    # Where the observation trainer looks for photographs, home-relative.
    "photos_folder": "Pictures",
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
                    # int and float are interchangeable here; a size written
                    # as 10 rather than 10.0 is still a size.
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


# ------------------------------------------------------------- learning ----

# SYSTEM.md §7, word for word.
LEARNING_DEFAULTS = {
    "roots": [
        "SecondBrain/learning",
        "Projects/Studies",
        "Projects/Documents",
        "Projects/Programming/Websites/Digitalization/Data",
        "Projects/Programming/Websites/Digitalization/DataHU",
    ],
    "extensions": ["md", "tex"],
    "new_per_day": 20,
    "desired_retention": 0.9,
    # Beyond §7, from HANDBOOK.md §5.2. Left out of the file while empty.
    #
    # Decks switched off, by name or by folder. An entry matches a deck that
    # is it or is below it, and any card whose path runs through it, so
    # `Digitalization/DataHU/Humán tárgyak` switches off a whole subject of
    # `.tex` files, which have no front matter to say `cards: false` in.
    "off": [],
    # Test dates, deck → YYYY-MM-DD. Until the day, that deck's new cards come
    # first and the day's ration rises to meet them all in time.
    "tests": {},
    # FSRS fitted to this log (fit.py), and how many reviews it was fitted on.
    "weights": None,
    "weights_reviews": 0,
}

# Written to the file only when they say something.
OPTIONAL = ("off", "tests", "weights", "weights_reviews")

# Below 0.7 FSRS spaces cards so far apart that most are forgotten before they
# come back, and above 0.97 the number of reviews a day explodes for very
# little gain. The preference row offers the same range.
RETENTION_RANGE = (0.70, 0.97)
NEW_RANGE = (0, 200)

LEARNING_HEADER = (
    "How Mnemosyne learns (SYSTEM.md §7). The same on every machine, because\n"
    "it lives in the brain; edited by hand or from Mnemosyne's preferences.\n"
    "roots are home-relative.")


class Learning:
    """`config/learning.yaml`: the settings the schedule itself depends on."""

    def __init__(self, where: brain.Places | None = None):
        self.places = where or brain.places()
        self.values = copy.deepcopy(LEARNING_DEFAULTS)
        self.extra: dict = {}
        self.load()

    @property
    def path(self) -> Path:
        return self.places.brain / "config" / "learning.yaml"

    def __getitem__(self, key):
        return self.values[key]

    # -- reading ----------------------------------------------------------
    def load(self) -> "Learning":
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return self
        data = yamlite.load(text)
        if not isinstance(data, dict):
            return self
        for key, value in data.items():
            if key is False or key == "False":
                key = "off"              # `off:` unquoted, as YAML 1.1 reads it
            if key in LEARNING_DEFAULTS:
                taken = _valid(key, value)
                if taken is not None:
                    self.values[key] = taken
            else:
                self.extra[key] = value
        return self

    # -- writing ----------------------------------------------------------
    def set(self, key, value) -> bool:
        """Change one setting and write the file. False if the value is no good."""
        taken = _valid(key, value)
        if taken is None:
            return False
        self.values[key] = taken
        return self.save()

    def save(self) -> bool:
        """Write the file back, keeping whatever keys other tools put in it.

        Re-read first: the file may have arrived from another machine since
        this one was opened, and a preference changed here should not quietly
        put back a value changed there.
        """
        current: dict = {}
        try:
            loaded = yamlite.load(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current = loaded
        except OSError:
            pass
        merged = {("off" if key is False else key): value for key, value in current.items()}
        for key in LEARNING_DEFAULTS:
            value = self.values[key]
            if key in OPTIONAL and not value:
                merged.pop(key, None)
            elif key == "tests":
                merged[key] = {deck: day.isoformat() for deck, day in value.items()}
            else:
                merged[key] = value
        if not self.places.exists():
            return False                 # no brain here: nowhere that follows
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".yaml.tmp")
            tmp.write_text(yamlite.dump(merged, LEARNING_HEADER), encoding="utf-8",
                           newline="\n")
            os.replace(tmp, self.path)
        except OSError:
            return False
        return True


def _valid(key, value):
    """The value if it is a sensible one for `key`, clamped; None if not."""
    if key == "off":
        if value is None:
            return []
        key = "roots"                    # the same shape: a list of paths
    if key == "tests":
        if value is None:
            return {}
        if not isinstance(value, dict):
            return None
        tests = {}
        for deck, day in value.items():
            if isinstance(day, datetime):
                day = day.date()
            if not isinstance(day, date):
                try:
                    day = date.fromisoformat(str(day).strip())
                except ValueError:
                    continue
            if str(deck).strip():
                tests[str(deck).strip()] = day
        return tests
    if key == "weights":
        if value is None:
            return None
        try:
            weights = tuple(float(v) for v in value)
        except (TypeError, ValueError):
            return None
        # A fitted set is only trusted whole and sane: the scheduler divides
        # by some of these and takes the logarithm of others.
        if len(weights) != 19 or not all(0 < w < 100 for w in weights):
            return None
        return [round(w, 5) for w in weights]
    if key == "weights_reviews":
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return None
    if key in ("roots", "extensions"):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return None
        items = [str(v).strip().strip("/").replace("\\", "/") for v in value
                 if v is not None and str(v).strip()]
        if key == "extensions":
            items = [item.lstrip(".").lower() for item in items]
        out = []
        for item in items:
            if item not in out:
                out.append(item)
        return out if (out or key == "roots") else None
    if key == "new_per_day":
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return max(NEW_RANGE[0], min(NEW_RANGE[1], number))
    if key == "desired_retention":
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number > 1:                   # written as a percentage
            number /= 100
        return round(max(RETENTION_RANGE[0], min(RETENTION_RANGE[1], number)), 3)
    return None
