"""The house palettes, and the glass each one is made of.

This file is the house style's colour half, and it is the same file in every
app here — byte for byte, like `updater.py`. One copy per app, kept in step by
`house/sync.py`, so that Porcelain is the *same* Porcelain in the terminal, the
file manager, the viewer and the editors, and so that adding a theme is one
edit rather than nine.

A theme carries more than the colours of text. It carries the material the
chrome is made of: the tint of the glass, how much light comes through it, how
bright its edges catch that light, and the colours of the slow-moving light
behind it. Both halves have to be designed together — a rose-tinted glass over
a cold blue palette looks like two themes fighting, and the amount of light a
dark glass can take before text stops being readable is not the same as for a
light one.

Four fields exist for one app each, and are filled in for every theme anyway,
because a theme that only half exists in one of the apps is not a house style:

* `ansi`, the usual sixteen terminal colours, which only Prism renders. They
  are drawn rather than borrowed: eight hues at roughly even spacing, held at
  one chroma and one lightness so no single colour jumps out of a line of
  output, with the bright variants a fixed step lighter rather than by eye.
  The light themes need much darker colours than the dark ones to stay
  readable, which is why they are not the same numbers with the background
  swapped.
* `matte`, the surround a picture sits on, which only The Eye shows. It is
  never decorative: the eye judges a colour against its surroundings, so a
  warm matte makes every photograph on it look cold. Every matte here is a
  *neutral* grey or black to within rounding, and the test suites fail a theme
  whose matte has a hue. It is why print shops are painted grey.
* `cursor`, which only a text caret uses.
* `dim`, for anything deliberately quiet — a timestamp, a file size, the
  unfilled half of a slider.

`contrast()` at the bottom is the check that keeps all of this honest. Every
theme's text and every one of its meaningful colours is measured against its
own background, and each app's test suite fails a theme whose text would not
meet WCAG's readable threshold. A palette here is arithmetic that happens to
look nice, not the other way round.
"""

from __future__ import annotations


def rgb(value: str) -> tuple[float, float, float]:
    """"#rrggbb" as the three 0..1 floats that Gdk and Gsk both want."""
    text = value.lstrip("#")
    return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def hexed(r: float, g: float, b: float) -> str:
    """Three 0..1 floats back as "#RRGGBB"."""
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(c * 255))) for c in (r, g, b))


def shade(colour: str, amount: float) -> str:
    """Lighter for a positive amount, darker for a negative one.

    Towards white and towards black rather than by multiplying, because
    multiplying a nearly-black tint by 1.2 gives back a nearly-black tint and
    the whole point of a shade is that the step is visible.
    """
    r, g, b = rgb(colour)
    if amount >= 0:
        return hexed(r + (1.0 - r) * amount, g + (1.0 - g) * amount,
                     b + (1.0 - b) * amount)
    keep = 1.0 + amount
    return hexed(r * keep, g * keep, b * keep)


def mix(one: str, two: str, amount: float) -> str:
    """`amount` of the way from `one` to `two`, 0..1."""
    a, b = rgb(one), rgb(two)
    t = max(0.0, min(1.0, amount))
    return hexed(*(a[i] + (b[i] - a[i]) * t for i in range(3)))


# What a label on a filled surface is drawn in. Near-black rather than black
# so that it reads as ink on the colour rather than as a hole in it.
INK = "#0A0A0C"
PAPER = "#FFFFFF"


def on(colour: str) -> str:
    """Text that stays readable *on* `colour`.

    One decision, made once: the label on an accent-coloured button, the
    figure in a filled badge, the text on a selected row. Measured rather
    than thresholded — "is it lighter than half?" is the wrong question in
    sRGB, where a mid periwinkle and a mid olive have the same lightness and
    not the same answer. Whichever of the two has more contrast wins, so a
    theme cannot be given an accent whose label is unreadable.
    """
    return INK if contrast(INK, colour) >= contrast(PAPER, colour) else PAPER


class Theme:
    """One complete look: the text colours, the glass, and the light behind it.

    `fg` is body text, `dim` anything deliberately quiet, `accent` the one
    colour the chrome picks up — the filled half of a slider, the ring on a
    focused control, the selected row.

    `glass` is the tint of the slab the interface sits on and `glass_alpha`
    how much of the light behind it comes through: a figure each window
    multiplies by the user's own translucency setting rather than using
    directly, so that turning the glass down never makes a theme illegible.
    `rim` scales the specular edges — glass that is nearly black can take a
    brighter edge than glass that is nearly white before it starts to look
    like plastic.

    `aurora` is the drifting light behind the glass: a handful of hues that
    are mixed, blurred by distance and never seen sharply, so they can be far
    more saturated than anything else in the theme.
    """

    def __init__(self, key, name, dark, bg, fg, dim, accent, cursor, matte,
                 ansi, glass, glass_alpha, rim, aurora, blurb=""):
        self.key = key
        self.name = name
        self.dark = dark
        self.bg = bg
        self.fg = fg
        self.dim = dim
        self.accent = accent            # the one colour the chrome picks up
        self.cursor = cursor
        self.matte = matte              # the neutral surround, for The Eye
        self.ansi = ansi                # sixteen: normal 0-7, bright 8-15
        self.glass = glass
        self.glass_alpha = glass_alpha
        self.rim = rim                  # how bright the specular edge gets
        self.aurora = aurora
        self.blurb = blurb

    # The selection and the faint text are derived rather than given, so a
    # theme cannot be defined with a selection that does not match its accent.
    def selection(self) -> str:
        return self.accent

    def faint(self) -> str:
        """ANSI 8 doubles as the colour of anything deliberately quiet."""
        return self.ansi[8]

    def neutral_matte(self) -> float:
        """How far the matte is from grey, 0.0 for a perfectly neutral one.

        The spread of the three channels, which is what the eye notices: a
        matte of #1A1A1C is neutral enough that nobody could name the hue,
        and #1A1A28 is a blue surround that will make every photograph on it
        look warm.
        """
        channels = rgb(self.matte)
        return max(channels) - min(channels)


THEMES: dict[str, Theme] = {}

# Keys a settings file may still be carrying from before the palettes were
# shared. The Eye's blue theme was called Vitreous and Prism's was called
# Aurora; they were the same theme drawn twice, so now they are one and the
# old name resolves to it rather than falling back to the default.
ALIASES = {"vitreous": "aurora", "nocturne": "moss", "noir": "moss"}


def _add(theme: Theme) -> Theme:
    THEMES[theme.key] = theme
    return theme


# ---------------------------------------------------------------- dark ----

_add(Theme(
    key="lacquer", name="Lacquer", dark=True,
    blurb="Black lacquer and brass. Porcelain with the light turned down.",
    # The dark half of the house and the counterpart to Porcelain, which is
    # the point of it: same warmth, same restraint, the light turned down
    # rather than a different taste in a darker room.
    #
    # Three things keep it from being another warm dark theme. The ground is
    # warm but barely — #141210 is a neutral black you would call warm only
    # beside a cold one — so the warmth is something the glass carries and
    # the text never fights. The palette is held well below the chroma of a
    # jewel-tone theme, at one lightness, so a line of coloured output reads
    # as a line of text rather than as a row of lights. And the accent is
    # brass rather than gold: #D9B779 is the colour of an unpolished fitting,
    # which is what lets it sit on a page without becoming the page.
    #
    # The aurora is almost still — four browns and ochres a step apart, so
    # what it does is stop a large pane of one tint reading as paint, and
    # nothing else. That restraint is the whole theme.
    bg="#141210", fg="#EDE7DD", dim="#A09486", accent="#D9B779",
    cursor="#D9B779", matte="#0B0B0B",
    ansi=["#201C18", "#E8837A", "#9CC98E", "#E0BE72",
          "#8FB6CE", "#C79CBE", "#79C7BE", "#C9BEB0",
          "#6B6055", "#F2A49C", "#B9DEAC", "#F2D694",
          "#B0CFE2", "#DCBAD4", "#9FDDD6", "#F6F1E8"],
    glass="#1C1813", glass_alpha=0.91, rim=0.95,
    aurora=["#3A2A16", "#2A1F14", "#44331C", "#211913"]))

_add(Theme(
    key="petrol", name="Petrol", dark=True,
    blurb="Deep blue-green glass, lit by copper. A cold room and a warm lamp.",
    # A cold ground under a warm accent, which is the oldest arrangement
    # there is and the reason it keeps being used: the eye reads the copper
    # as light falling on the petrol rather than as a second colour fighting
    # it. Blue-green rather than blue, so it is not Aurora in another key.
    bg="#081316", fg="#DEE9E8", dim="#8CA3A3", accent="#E2906A",
    cursor="#E2906A", matte="#0A0A0B",
    ansi=["#101F22", "#F08271", "#58CFA5", "#E8C277",
          "#6FB8D6", "#C09BDA", "#58CFCF", "#C2D2D1",
          "#52696A", "#FFA594", "#86E6C3", "#F7D89C",
          "#9BD3EA", "#D8BCEC", "#8FE6E6", "#EFF6F5"],
    glass="#0C1B1F", glass_alpha=0.90, rim=1.0,
    aurora=["#12565F", "#0E3F52", "#8A4A2A", "#154A44"]))

_add(Theme(
    key="moss", name="Moss", dark=True,
    blurb="Forest black under sage. The quiet one, and the default.",
    # Nothing else in the house is green, and green is the hue the eye
    # resolves most sharply, which is exactly why a *desaturated* one makes
    # such a restful ground: all the acuity and none of the pull. The accent
    # is sage rather than a leaf green — bright enough to find, dull enough
    # to live with for a working day.
    bg="#0D1310", fg="#E3EADF", dim="#93A390", accent="#C4D39A",
    cursor="#C4D39A", matte="#0A0A0A",
    ansi=["#161E19", "#EC8A80", "#7FD093", "#E0C277",
          "#8FB9D2", "#C7A0CE", "#6FCDBE", "#C3CDBE",
          "#5D6B5E", "#FFA79D", "#A4E3B2", "#F2D898",
          "#B2D2E6", "#DFBDE5", "#99DED3", "#F0F5EC"],
    glass="#121A15", glass_alpha=0.91, rim=0.9,
    aurora=["#2A4A32", "#1C3A2E", "#3E5A33", "#15302A"]))

_add(Theme(
    key="aurora", name="Aurora", dark=True,
    blurb="Deep blue glass over slow northern light.",
    bg="#0A0D14", fg="#DCE3F0", dim="#8A93A8", accent="#6FB4FF",
    cursor="#7FD1FF", matte="#0B0B0C",
    ansi=["#171C28", "#FF6B81", "#5CE2A7", "#F2C97D",
          "#6FB4FF", "#C792FF", "#5BDCE0", "#C6CEDE",
          "#566382", "#FF93A3", "#8BF0C4", "#FFDCA0",
          "#9BCBFF", "#DCB4FF", "#8FEDF0", "#F0F4FB"],
    glass="#0C1322", glass_alpha=0.90, rim=1.0,
    aurora=["#2B4FA8", "#1E6F8C", "#5B3C9E", "#175F6B"]))

_add(Theme(
    key="obsidian", name="Obsidian", dark=True,
    blurb="Neutral black glass. Nothing at all competes with the content.",
    bg="#0B0B0C", fg="#E4E6EA", dim="#8C8F96", accent="#B9C6D8",
    cursor="#E4E6EA", matte="#000000",
    ansi=["#191B1F", "#F0796E", "#7FD08C", "#E7C36B",
          "#7FAEF0", "#C49BEE", "#70CFD4", "#BFC4CC",
          "#5E636E", "#FF9D93", "#A6E4AF", "#F6D893",
          "#A6C9F7", "#D9BCF6", "#9BE2E6", "#F3F5F8"],
    glass="#131416", glass_alpha=0.92, rim=0.82,
    aurora=["#272A2F", "#35383E", "#202226", "#3F4249"]))

_add(Theme(
    key="ember", name="Ember", dark=True,
    blurb="Warm smoked glass, lit from below. Kind at night.",
    bg="#13100E", fg="#EDE2D8", dim="#A2958A", accent="#FFBE5C",
    cursor="#FFBE5C", matte="#0C0B0A",
    ansi=["#221B17", "#FF7A66", "#B8CE6A", "#FFBE5C",
          "#86B2C9", "#E092A0", "#7FCBB8", "#D4C6B8",
          "#756154", "#FF9E8D", "#CFE08F", "#FFD489",
          "#A8CBDD", "#F0B3BD", "#A5DDCF", "#F7EDE2"],
    glass="#1B1512", glass_alpha=0.90, rim=0.95,
    aurora=["#8C4A1C", "#6B2F28", "#9A6420", "#44201B"]))

_add(Theme(
    key="studio", name="Studio", dark=True,
    blurb="An 18% neutral grey surround — the condition a print is judged "
          "in — with the panels kept dark. For colour work.",
    # 18% reflectance is about #7C7C7C in sRGB, and that is the matte only.
    # The chrome stays dark, for two reasons: a mid grey cannot carry readable
    # text in either direction — the arithmetic says no label on #7C7C7C
    # reaches 4.5:1, black or white — and panels darker than the surround keep
    # the eye on the picture, which is where every grading suite ends up.
    # The palette is accordingly paler than the other dark themes': #202020
    # is light enough that a saturated colour on it would not clear 4.5:1.
    bg="#202020", fg="#ECECEC", dim="#A4A4A4", accent="#D6D6D6",
    cursor="#D6D6D6", matte="#7C7C7C",
    ansi=["#2B2B2B", "#F09A95", "#9AD6A6", "#E3C98A",
          "#A9C4EC", "#D0AEE8", "#95D2D6", "#C8C8C8",
          "#787878", "#F8B6B2", "#BCE6C4", "#F2DCA8",
          "#C6D8F3", "#E3CBF2", "#B4E2E5", "#F4F4F4"],
    glass="#272727", glass_alpha=0.95, rim=0.5,
    aurora=["#2E2E2E", "#242424", "#303030", "#2A2A2A"]))

_add(Theme(
    key="mono", name="Monochrome", dark=True,
    blurb="Grey glass. Colour only where the content depends on it.",
    bg="#0D0D0D", fg="#E8E8E8", dim="#9B9B9B", accent="#C4C4C4",
    cursor="#E8E8E8", matte="#0A0A0A",
    ansi=["#1A1A1A", "#C9C9C9", "#A8A8A8", "#DADADA",
          "#8E8E8E", "#B8B8B8", "#9B9B9B", "#C4C4C4",
          "#636363", "#F2F2F2", "#CFCFCF", "#FFFFFF",
          "#B2B2B2", "#DCDCDC", "#C0C0C0", "#FAFAFA"],
    glass="#141414", glass_alpha=0.94, rim=0.8,
    aurora=["#2B2B2B", "#1E1E1E", "#343434", "#242424"]))

# --------------------------------------------------------------- light ----

_add(Theme(
    key="porcelain", name="Porcelain", dark=False,
    blurb="Warm paper under glass, the colour of gallery walls. The default.",
    bg="#FBF7F2", fg="#2A2522", dim="#665D53", accent="#A4561A",
    cursor="#A4561A", matte="#EDEDED",
    ansi=["#2E2823", "#B4323C", "#3F6B2A", "#8A5A05",
          "#2F5C96", "#7B3A86", "#1C6560", "#7A6F64",
          "#574E46", "#95222C", "#32571F", "#6E4703",
          "#234878", "#632B6C", "#14504C", "#9C9084"],
    glass="#FFFCF7", glass_alpha=0.87, rim=0.5,
    aurora=["#E8C9A0", "#D9B9C6", "#C2CFA8", "#EFD9B4"]))

_add(Theme(
    key="daylight", name="Daylight", dark=False,
    blurb="Frosted white glass. For a bright room.",
    bg="#F7F8FB", fg="#1E2430", dim="#5B6472", accent="#2A5FB8",
    cursor="#2A5FB8", matte="#EDEDEE",
    ansi=["#262C36", "#C8324B", "#1E7A52", "#8A5B0C",
          "#2A5FB8", "#8338A8", "#0F6E78", "#6E7883",
          "#4D5560", "#A62238", "#13613F", "#6E4708",
          "#1E4A96", "#6B2A8C", "#0A555D", "#8F98A3"],
    glass="#FFFFFF", glass_alpha=0.86, rim=0.55,
    aurora=["#9CC4F0", "#C3B4EC", "#A8DCDA", "#E8CBDA"]))


# The two the house is built on, and what `follow_system` reaches for. Both
# were chosen rather than inherited: Porcelain is the light theme, and Moss
# is the dark one — picked from three that were drawn for the job, which is
# why the other two are still here rather than deleted.
LIGHT_DEFAULT = "porcelain"
DARK_DEFAULT = "moss"
DEFAULT = DARK_DEFAULT


# --------------------------------------------------------- the settings ----

# The keys every app keeps for the house style, with their defaults, so that
# adding one is an edit here rather than nine. A config spreads them into its
# own DEFAULTS; the names are the ones Hermione used first, and the three apps
# that had their own names for the glass before this file existed — Prism's
# `opacity`, The Eye's `backdrop_blur` — keep them rather than orphan
# everyone's settings file for a rename nobody asked for.
THEME_SETTINGS = {
    "theme": DARK_DEFAULT,       # used when follow_system is off
    "follow_system": True,       # the two below instead, tracking the desktop
    "dark_theme": DARK_DEFAULT,
    "light_theme": LIGHT_DEFAULT,
}

GLASS_SETTINGS = {
    "glass": True,               # the translucent, blurred chrome
    "glass_blur": 26,            # how far a floating panel blurs what is under it
    "translucency": 0.86,        # 1.0 is an opaque window, 0.45 is barely there
    "rounding": 16,              # the window's corner radius, in pixels
    "animations": True,
}

SETTINGS = {**THEME_SETTINGS, **GLASS_SETTINGS}

# What each of them that has a range is clamped to on the way in. Every one of
# these numbers ends up in a drawing call, so a hand-edited file with a
# translucency of 99 should come back as 1.0 rather than as a window nobody
# can see — and a theme name this file no longer knows is *not* here, because
# that one resolves to the default in `get()` instead of being rejected,
# which is what lets a theme be renamed without emptying the settings file.
RANGES = {
    "glass_blur": (0, 80),
    "translucency": (0.45, 1.0),
    "rounding": (0, 28),
}


def clamp(key, value):
    """`value` brought inside whatever range `key` has, if it has one."""
    if key in RANGES:
        low, high = RANGES[key]
        try:
            return max(low, min(high, value))
        except TypeError:
            return SETTINGS.get(key, value)
    return value


def take(config, data) -> None:
    """Read the house-style keys out of a loaded settings file into `config`.

    A pass of its own rather than a few more lines in each app's loop,
    because the rules are the same in every app and are not quite the rules
    for an app's own settings:

    * A translucency written as `1` rather than `1.0` is still a
      translucency. A settings file is a text file a person edits, and an app
      that ignored that one would silently come back at 0.86 and look broken.
    * `bool` is a subclass of `int`, so an `isinstance` check alone lets
      `true` into a number and a number into a switch. Both are rejected.
    * Every number here ends up in a drawing call, so it is clamped rather
      than trusted — see RANGES.
    * A theme name is *not* checked against the list. One this file no longer
      knows resolves to the default when it is looked up, which is what lets
      a theme be renamed without emptying everybody's settings file.
    """
    if not isinstance(data, dict):
        return
    for key, default in SETTINGS.items():
        if key not in data:
            continue
        value = data[key]
        if isinstance(default, bool):
            if isinstance(value, bool):
                config[key] = value
            continue
        if isinstance(value, bool):
            continue
        if isinstance(default, float) and isinstance(value, int):
            value = float(value)
        if isinstance(value, type(default)):
            config[key] = clamp(key, value)


class Settings(dict):
    """The house keys as a mapping, for an app whose config is not one.

    Claude Desk keeps its settings as attributes on a class rather than in a
    dict, and both the preference rows and the `Glazing` want `config[key]`
    and a `save()`. So it keeps one of these and hands it over: a plain dict
    of the house keys with the owner's own save wired to the back of it, which
    is less code than teaching either side about the other.
    """

    def __init__(self, save=None):
        super().__init__(SETTINGS)
        self._save = save

    def save(self) -> None:
        if callable(self._save):
            self._save()

    def take(self, data) -> None:
        take(self, data)

    def payload(self) -> dict:
        """Just the house keys, for writing into a larger settings file."""
        return {key: self[key] for key in SETTINGS}


def get(key: str) -> Theme:
    """The named theme, or the default.

    A settings file can name a theme that has since been renamed or removed,
    and the answer to that is the default rather than an exception on the
    first line of the first window's constructor.
    """
    return THEMES.get(ALIASES.get(key, key)) or THEMES[DEFAULT]


def ordered() -> list[Theme]:
    """Dark first, then light — the order the preferences list them in.

    Within each half, the house default leads and the rest follow by name, so
    the two themes everything is tuned for are the two at the top.
    """
    def rank(theme: Theme) -> tuple:
        preferred = theme.key in (DARK_DEFAULT, LIGHT_DEFAULT)
        return (not theme.dark, not preferred, theme.name)

    return sorted(THEMES.values(), key=rank)


def keys() -> list[str]:
    """Every theme key, in the order `ordered()` gives them."""
    return [theme.key for theme in ordered()]


def for_system(dark: bool) -> Theme:
    """Whichever of the two house defaults the desktop is asking for."""
    return get(DARK_DEFAULT if dark else LIGHT_DEFAULT)


# ------------------------------------------------------------- contrast ----

def _channel(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    r, g, b = (_channel(c) for c in rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(one: str, two: str) -> float:
    """WCAG contrast ratio, 1.0 (identical) to 21.0 (black on white)."""
    a, b = luminance(one), luminance(two)
    light, dark = max(a, b), min(a, b)
    return (light + 0.05) / (dark + 0.05)


# The sixteen split into the twelve hues, which carry meaning and have to be
# legible, and the four greys, which are a ramp from "nearly the background"
# to "nearly the foreground" and are judged differently.
CHROMATIC = (1, 2, 3, 4, 5, 6, 9, 10, 11, 12, 13, 14)
GREYS = (0, 7, 8, 15)


def grey_ramp(theme: "Theme") -> list[tuple[int, float]]:
    """The four greys against the background, dimmest first.

    One end of this ramp is supposed to disappear: ANSI 0 on a dark theme and
    ANSI 15 on a light one both mean "the colour of the background", and a box
    drawn in it is meant to read as a hole rather than as text. The other
    three have to stay visible — which on a light theme is the whole reason
    its "white" is a mid grey rather than white. Returning them in order lets
    a test suite exempt whichever end went to the background without having to
    know which way round the theme is.
    """
    measured = [(i, contrast(theme.ansi[i], theme.bg)) for i in GREYS]
    return sorted(measured, key=lambda pair: pair[1])
