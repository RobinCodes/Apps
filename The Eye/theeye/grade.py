"""The grade: what the editor's sliders mean, in arithmetic.

This module holds no drawing code and imports no GTK. It turns a set of
named adjustments into two things:

* a 4x4 colour matrix and an offset, which is everything that can be done to
  a pixel without looking at its neighbours or at itself twice — exposure,
  white balance, contrast, saturation, hue, the black and white points;
* a list of `Stage` records describing the passes that cannot be a matrix —
  the gamma curve, the masked highlight and shadow lifts, bloom, glow,
  clarity, grain, the vignette.

`effects.py` walks that list and makes GSK render nodes out of it. Keeping the
two apart is what lets the whole colour pipeline be tested with plain
arithmetic and no display, and it is why the preview and the export cannot
drift apart: they are the same numbers, and the export renders the same node
tree the screen does.

Everything here works on sRGB values as they are encoded — 0 to 1, with the
display's gamma still in them — because that is what GSK hands a colour
matrix node, and converting to linear light and back would cost two more
passes over every frame. Three consequences are worth knowing:

* An exposure change is still exactly right. Multiplying *encoded* values by
  s is multiplying *linear* light by s^2.2, because a power function is
  multiplicative — so one stop is a scale of 2^(1/2.2), not of 2. That is
  what `EXPOSURE_GAMMA` below is for, and it is the only reason exposure in
  this app behaves like exposure in a camera rather than like a brightness
  knob.
* Desaturation uses Rec.709 luma weights applied to encoded values, which is
  what every GPU-side desaturate does. It is not a true luminance, and a
  saturated blue goes slightly darker than a colourimetrist would like.
* Blending and blurring also happen in encoded space. For a viewer this is
  the right trade: it is how every other tool on the desktop does it, so a
  picture graded here looks the same when it is opened elsewhere.

Ranges: every slider is 0 at its neutral position, so an untouched grade is
all zeroes and `is_neutral()` is a cheap check that skips the entire pipeline.
"""

from __future__ import annotations

import json
import math

# sRGB's transfer function is a power of 2.4 with a linear toe, which over the
# range that matters behaves like a plain gamma of about 2.2. Exposure is the
# one control where the difference between 2.2 and 1.0 is the difference
# between "a stop" and "an arbitrary amount", so it gets the real figure.
EXPOSURE_GAMMA = 2.2

# Photographic middle grey — 18% reflectance — is this value once sRGB has
# encoded it. Contrast pivots here rather than at 0.5 so that a mid-grey card
# in the picture does not move when the slider does.
MIDDLE_GREY = 0.4564

# Rec.709 luma weights, at the precision the SVG filter specification uses for
# its saturate and hueRotate matrices. Those two matrices are taken from that
# specification rather than derived here, because they are the ones every
# browser implements and a picture graded in The Eye should not shift when it
# is viewed somewhere else.
LUMA = (0.213, 0.715, 0.072)


# -------------------------------------------------------- matrix algebra ----
#
# A matrix is four rows of four numbers, and the convention is the ordinary
# one: out[j] = sum over i of M[j][i] * in[i]. Row 3 is alpha, and nothing
# here ever touches it — a grade changes how a picture looks, never how
# opaque it is. `effects.py` transposes on the way into Graphene, which wants
# the other convention.

IDENTITY = ((1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0),
            (0.0, 0.0, 0.0, 1.0))

Matrix = tuple
Offset = tuple


def diagonal(r: float, g: float, b: float) -> Matrix:
    return ((r, 0.0, 0.0, 0.0),
            (0.0, g, 0.0, 0.0),
            (0.0, 0.0, b, 0.0),
            (0.0, 0.0, 0.0, 1.0))


def from_rows(rows) -> Matrix:
    """A 3x3 colour mixer as a 4x4 with alpha left alone."""
    return (tuple(rows[0]) + (0.0,),
            tuple(rows[1]) + (0.0,),
            tuple(rows[2]) + (0.0,),
            (0.0, 0.0, 0.0, 1.0))


def compose(first: Matrix, first_off: Offset,
            second: Matrix, second_off: Offset) -> tuple[Matrix, Offset]:
    """One matrix and offset doing `first` and then `second`.

    Both are affine, so the composition is too: applying B·(A·x + a) + b is
    applying (B·A)·x + (B·a + b). Collapsing the whole chain into one pair is
    the point of the exercise — a grade with eight colour controls turned up
    still costs the renderer exactly one colour matrix node.
    """
    product = tuple(
        tuple(sum(second[j][k] * first[k][i] for k in range(4)) for i in range(4))
        for j in range(4))
    offset = tuple(
        sum(second[j][k] * first_off[k] for k in range(4)) + second_off[j]
        for j in range(4))
    return product, offset


def apply(matrix: Matrix, offset: Offset, colour) -> tuple:
    """A colour through a matrix, clamped the way GSK clamps it.

    GSK's colour matrix node clamps each channel to 0..1 after the matrix and
    before compositing — which is not a detail to work around but a tool:
    `(value - threshold) * slope` with the negatives clamped away is a
    bright-pass filter, and that is how `bloom` and `halation` find the
    highlights without a shader of their own.
    """
    out = []
    for j in range(4):
        total = sum(matrix[j][i] * colour[i] for i in range(4)) + offset[j]
        out.append(max(0.0, min(1.0, total)))
    return tuple(out)


ZERO = (0.0, 0.0, 0.0, 0.0)


# ----------------------------------------------------- the colour controls ----

def exposure_matrix(stops: float) -> Matrix:
    """`stops` stops of exposure, as a scale of the encoded values.

    See EXPOSURE_GAMMA: the encoded scale for one stop of light is 2^(1/2.2),
    about 1.37, not 2.
    """
    scale = 2.0 ** (stops / EXPOSURE_GAMMA)
    return diagonal(scale, scale, scale)


def contrast_matrix(amount: float) -> tuple[Matrix, Offset]:
    """Contrast about middle grey, `amount` from -1 to +1.

    An affine stretch, so it is honest about what it does: at the top of the
    range the brightest and darkest parts of the picture clip. `filmic` in
    the stage list is the one that does not clip, and most pictures want a
    little of each.
    """
    factor = 2.0 ** (amount * 1.5)
    shift = MIDDLE_GREY * (1.0 - factor)
    return diagonal(factor, factor, factor), (shift, shift, shift, 0.0)


def levels_matrix(blacks: float, whites: float) -> tuple[Matrix, Offset]:
    """The black and white points, `blacks` and `whites` from -1 to +1.

    Positive blacks lift the shadows off the floor, which is the faded-film
    look and also the honest way to show a scan with no true black in it.
    Positive whites pull the highlights up towards clipping.
    """
    low = blacks * 0.12
    high = 1.0 + whites * 0.25
    span = high - low
    if abs(span) < 1e-6:
        span = 1e-6
    # Map low..high onto 0..1 for negative values of either, and the reverse
    # for positive ones: out = (in - low) / span.
    scale = 1.0 / span
    shift = -low * scale
    return diagonal(scale, scale, scale), (shift, shift, shift, 0.0)


def balance_matrix(temperature: float, tint: float) -> Matrix:
    """White balance, as per-channel gains that keep a grey's brightness.

    `temperature` runs cool (-1) to warm (+1) and trades red against blue;
    `tint` runs green (-1) to magenta (+1) and trades green against the other
    two. The three gains are then divided by their own luma, so that warming
    a picture does not also brighten it — which is the difference between a
    white balance control and two brightness controls in a trenchcoat.
    """
    red = 1.0 + 0.30 * temperature + 0.06 * tint
    green = 1.0 - 0.10 * tint
    blue = 1.0 - 0.30 * temperature + 0.06 * tint
    weight = LUMA[0] * red + LUMA[1] * green + LUMA[2] * blue
    if weight <= 1e-6:
        return IDENTITY
    return diagonal(red / weight, green / weight, blue / weight)


def saturation_matrix(amount: float) -> Matrix:
    """Saturation, `amount` -1 (grey) to +1 (double).

    The SVG `feColorMatrix type="saturate"` matrix. Each row sums to one, so a
    grey stays exactly the same grey however far the slider goes — which is
    the property that makes it safe to leave in the chain at any strength.
    """
    s = 1.0 + amount
    lr, lg, lb = LUMA
    return from_rows((
        (lr + (1 - lr) * s, lg - lg * s, lb - lb * s),
        (lr - lr * s, lg + (1 - lg) * s, lb - lb * s),
        (lr - lr * s, lg - lg * s, lb + (1 - lb) * s),
    ))


def hue_matrix(degrees: float) -> Matrix:
    """A rotation of every hue by `degrees`, leaving greys alone.

    The SVG `feColorMatrix type="hueRotate"` matrix. It is a rotation about
    the grey axis of RGB space rather than a true hue rotation in a perceptual
    space — so large angles move some hues further than others — but it is
    what the rest of the world implements, and at the few degrees anyone
    actually uses the difference is invisible.
    """
    radians = math.radians(degrees)
    c, s = math.cos(radians), math.sin(radians)
    return from_rows((
        (0.213 + c * 0.787 - s * 0.213,
         0.715 - c * 0.715 - s * 0.715,
         0.072 - c * 0.072 + s * 0.928),
        (0.213 - c * 0.213 + s * 0.143,
         0.715 + c * 0.285 + s * 0.140,
         0.072 - c * 0.072 - s * 0.283),
        (0.213 - c * 0.213 - s * 0.787,
         0.715 - c * 0.715 + s * 0.715,
         0.072 + c * 0.928 + s * 0.072),
    ))


def bright_pass(threshold: float, slope: float = 1.0) -> tuple[Matrix, Offset]:
    """Keep what is brighter than `threshold` and throw the rest away.

    Not a matrix operation in spirit — it is a threshold — but GSK clamps a
    colour matrix node's result to 0..1, so `(value - threshold) * slope`
    leaves nothing below the threshold and a linear ramp above it. This is
    what bloom and halation are built on, and the clamp doing the work is
    verified in the test suite rather than assumed.
    """
    k = max(1e-6, slope)
    shift = -threshold * k
    return diagonal(k, k, k), (shift, shift, shift, 0.0)


def monochrome_matrix(toward=None) -> Matrix:
    """Everything to grey, or to one colour's own ramp.

    With no colour this is desaturation. With one it is a duotone: the
    picture's luminance drives a ramp from black to that colour, which is how
    a selenium or a cyanotype print behaves, and is far more convincing than
    desaturating and then tinting.
    """
    lr, lg, lb = LUMA
    if toward is None:
        return from_rows(((lr, lg, lb), (lr, lg, lb), (lr, lg, lb)))
    r, g, b = toward
    return from_rows(((lr * r, lg * r, lb * r),
                      (lr * g, lg * g, lb * g),
                      (lr * b, lg * b, lb * b)))


# ---------------------------------------------------------------- colours ----

def rgb(value: str) -> tuple[float, float, float]:
    text = str(value).lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    try:
        return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except (ValueError, IndexError):
        return (0.5, 0.5, 0.5)


# ------------------------------------------------------------- the stages ----

class Stage:
    """One pass the renderer has to make, beyond the single colour matrix.

    `kind` says which; the rest of the fields are whatever that kind needs.
    They are plain data on purpose: the editor builds them, the test suite
    reads them, and `effects.py` is the only code that knows what a GSK node
    is.
    """

    __slots__ = ("kind", "amount", "colour", "radius", "threshold", "detail")

    def __init__(self, kind, amount=0.0, colour=None, radius=0.0,
                 threshold=0.0, detail=None):
        self.kind = kind
        self.amount = amount
        self.colour = colour
        self.radius = radius
        self.threshold = threshold
        self.detail = detail

    def __repr__(self):
        return (f"Stage({self.kind!r}, amount={self.amount:.3f}"
                f"{', colour=' + repr(self.colour) if self.colour else ''}"
                f"{f', radius={self.radius:.1f}' if self.radius else ''})")


# How the wash colour can be laid over the picture. Each is a GSK blend mode;
# `effects.py` maps the names. "Wash" is the one control here that is meant to
# be used at full strength — the others are seasoning.
WASH_MODES = ("soft-light", "multiply", "screen", "overlay", "colour",
              "hue", "luminosity")


DEFAULTS = {
    # light
    "exposure": 0.0,          # stops, -3..3
    "contrast": 0.0,          # -1..1, affine about middle grey
    "filmic": 0.0,            # -1..1, an S-curve that cannot clip
    "midtones": 0.0,          # -1..1, a gamma curve pinned at both ends
    "blacks": 0.0,            # -1..1, where black sits
    "whites": 0.0,            # -1..1, where white sits
    "highlights": 0.0,        # -1..1, masked by the picture's own brightness
    "shadows": 0.0,           # -1..1, masked by the inverse
    # colour
    "temperature": 0.0,       # -1 cool .. 1 warm
    "tint": 0.0,              # -1 green .. 1 magenta
    "saturation": 0.0,        # -1 grey .. 1 double
    "hue": 0.0,               # degrees, -180..180
    "monochrome": False,
    "duotone": "",            # the colour a monochrome ramp runs to, if any
    "shadow_tint": "",        # split toning: a colour into the shadows
    "shadow_tint_amount": 0.0,
    "highlight_tint": "",     # and one into the highlights
    "highlight_tint_amount": 0.0,
    "wash": "",               # a colour laid over the whole picture
    "wash_amount": 0.0,
    "wash_mode": "soft-light",
    # texture
    "clarity": 0.0,           # -1..1, local contrast; negative softens
    "sharpen": 0.0,           # 0..1, fine detail
    "bloom": 0.0,             # 0..1, light spilling out of the highlights
    "bloom_threshold": 0.72,
    "glow": 0.0,              # 0..1, diffusion over the whole frame
    "halation": 0.0,          # 0..1, the red fringe film gets around highlights
    "grain": 0.0,             # 0..1
    "grain_size": 1.0,        # 0.5..3, how coarse
    # frame
    "vignette": 0.0,          # -1 brighter corners .. 1 darker
    # geometry, which is not a grade but travels with one
    "rotation": 0,            # degrees clockwise, a multiple of 90
    "flip_h": False,
    "flip_v": False,
}

# Everything that is a number, with the range the editor clamps it to.
RANGES = {
    "exposure": (-3.0, 3.0), "contrast": (-1.0, 1.0), "filmic": (-1.0, 1.0),
    "midtones": (-1.0, 1.0), "blacks": (-1.0, 1.0), "whites": (-1.0, 1.0),
    "highlights": (-1.0, 1.0), "shadows": (-1.0, 1.0),
    "temperature": (-1.0, 1.0), "tint": (-1.0, 1.0),
    "saturation": (-1.0, 1.0), "hue": (-180.0, 180.0),
    "shadow_tint_amount": (0.0, 1.0), "highlight_tint_amount": (0.0, 1.0),
    "wash_amount": (0.0, 1.0),
    "clarity": (-1.0, 1.0), "sharpen": (0.0, 1.0),
    "bloom": (0.0, 1.0), "bloom_threshold": (0.0, 1.0),
    "glow": (0.0, 1.0), "halation": (0.0, 1.0),
    "grain": (0.0, 1.0), "grain_size": (0.5, 3.0),
    "vignette": (-1.0, 1.0),
}


class Grade(dict):
    """A set of adjustments, and the pipeline it compiles to.

    A dict so that saving it is `json.dump` and a preset is a literal. The
    interesting methods are `matrix()`, which collapses every affine control
    into one pair, and `stages()`, which lists what is left in the order it
    has to happen.
    """

    def __init__(self, values=None):
        super().__init__(DEFAULTS)
        if values:
            self.update_checked(values)

    # -- housekeeping ------------------------------------------------------

    def update_checked(self, values):
        """Take what is recognisable and ignore the rest.

        Grades arrive from a settings file, a sidecar next to a picture and
        the clipboard, so none of them can be trusted to hold what this
        version of the app expects.
        """
        for key, value in dict(values).items():
            if key not in DEFAULTS:
                continue
            want = DEFAULTS[key]
            if isinstance(want, bool):
                if isinstance(value, bool):
                    self[key] = value
            elif isinstance(want, str):
                if isinstance(value, str):
                    if key == "wash_mode" and value not in WASH_MODES:
                        continue
                    self[key] = value
            elif isinstance(want, int) and not isinstance(want, bool):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self[key] = int(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                low, high = RANGES.get(key, (-1e9, 1e9))
                self[key] = max(low, min(high, float(value)))
        return self

    def copy(self) -> "Grade":
        return Grade(self)

    def reset(self):
        self.update(DEFAULTS)
        return self

    def is_neutral(self) -> bool:
        """True when nothing would change, so the whole pipeline can be skipped.

        Geometry is excluded: a rotated picture with no grade on it still has
        to be rotated, but it does not need a single render node of colour
        work, and on a video that is the difference between a frame costing
        one textured quad and costing nine.
        """
        for key, default in DEFAULTS.items():
            if key in ("rotation", "flip_h", "flip_v"):
                continue
            if key in ("bloom_threshold", "grain_size", "wash_mode"):
                continue        # only meaningful when their amount is non-zero
            if self[key] != default:
                return False
        return True

    def touched(self) -> list[str]:
        """Which controls are away from neutral — what the editor badges."""
        return [key for key, default in DEFAULTS.items()
                if key not in ("bloom_threshold", "grain_size", "wash_mode")
                and self[key] != default]

    def to_json(self) -> str:
        """Only what differs from neutral, so a sidecar stays readable."""
        return json.dumps({k: self[k] for k in sorted(self)
                           if self[k] != DEFAULTS[k]}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Grade":
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return cls()
        return cls(data if isinstance(data, dict) else None)

    # -- the affine half --------------------------------------------------

    def matrix(self) -> tuple[Matrix, Offset]:
        """Every affine control, collapsed into one matrix and one offset.

        The order is the order a darkroom would use and a camera raw
        converter still does: get the exposure right, then the white balance,
        then the black and white points, and only then touch contrast and
        colour. Doing saturation before white balance, for instance, would
        make the balance control's effect depend on the saturation slider,
        which is the kind of coupling that makes a set of sliders feel broken
        without anyone being able to say why.
        """
        matrix, offset = IDENTITY, ZERO

        if self["exposure"]:
            matrix, offset = compose(matrix, offset,
                                     exposure_matrix(self["exposure"]), ZERO)
        if self["temperature"] or self["tint"]:
            matrix, offset = compose(
                matrix, offset,
                balance_matrix(self["temperature"], self["tint"]), ZERO)
        if self["blacks"] or self["whites"]:
            levels, levels_off = levels_matrix(self["blacks"], self["whites"])
            matrix, offset = compose(matrix, offset, levels, levels_off)
        if self["contrast"]:
            contrast, contrast_off = contrast_matrix(self["contrast"])
            matrix, offset = compose(matrix, offset, contrast, contrast_off)
        if self["hue"]:
            matrix, offset = compose(matrix, offset, hue_matrix(self["hue"]), ZERO)
        if self["monochrome"]:
            toward = rgb(self["duotone"]) if self["duotone"] else None
            matrix, offset = compose(matrix, offset, monochrome_matrix(toward), ZERO)
        elif self["saturation"]:
            matrix, offset = compose(matrix, offset,
                                     saturation_matrix(self["saturation"]), ZERO)
        return matrix, offset

    # -- everything else --------------------------------------------------

    def stages(self) -> list[Stage]:
        """The passes that are not a matrix, in the order they must happen.

        Tone before colour before texture before the frame, because each
        group reads the result of the one before it: bloom has to find the
        highlights *after* the exposure has been set, or turning the exposure
        down would leave the bloom where the highlights used to be. The
        vignette and the grain come last for the same reason a photographer
        burns the edges last — they are about the print, not the picture.
        """
        out: list[Stage] = []

        # Tone curves. These are blends against a flat grey, which is a gamma
        # or an S-curve pinned at black and white, so they shape the midtones
        # without ever clipping an end.
        if self["midtones"]:
            out.append(Stage("midtones", self["midtones"]))
        if self["filmic"]:
            out.append(Stage("filmic", self["filmic"]))

        # Masked tone. The mask is the picture's own luminance, so these two
        # reach only the part of the range they are named after.
        if self["shadows"]:
            out.append(Stage("shadows", self["shadows"]))
        if self["highlights"]:
            out.append(Stage("highlights", self["highlights"]))

        # Split toning: a colour into one end of the range and usually its
        # complement into the other. Nearly every graded film in the last
        # twenty years is some version of this.
        if self["shadow_tint"] and self["shadow_tint_amount"]:
            out.append(Stage("shadow_tint", self["shadow_tint_amount"],
                             colour=self["shadow_tint"]))
        if self["highlight_tint"] and self["highlight_tint_amount"]:
            out.append(Stage("highlight_tint", self["highlight_tint_amount"],
                             colour=self["highlight_tint"]))
        if self["wash"] and self["wash_amount"]:
            out.append(Stage("wash", self["wash_amount"], colour=self["wash"],
                             detail=self["wash_mode"]))

        # Texture. Clarity is a wide unsharp mask and sharpen a narrow one —
        # the same operation at two scales, which is why they are one stage
        # kind with a radius. Clarity also runs backwards: a negative amount
        # blends towards the blurred copy instead of away from it, which is
        # a diffusion filter and is what Tape and Dream are mostly made of.
        if self["clarity"]:
            out.append(Stage("unsharp", self["clarity"], radius=18.0,
                             detail="clarity"))
        if self["sharpen"]:
            out.append(Stage("unsharp", self["sharpen"], radius=2.0,
                             detail="sharpen"))
        if self["bloom"]:
            out.append(Stage("bloom", self["bloom"], radius=28.0,
                             threshold=self["bloom_threshold"]))
        if self["halation"]:
            # Film's halation is light scattering back off the base through
            # the red-sensitive layer, so it is always red-orange and always
            # wider than bloom.
            out.append(Stage("bloom", self["halation"], radius=46.0,
                             threshold=0.78, colour="#FF5A1E", detail="halation"))
        if self["glow"]:
            out.append(Stage("glow", self["glow"], radius=34.0))
        if self["grain"]:
            out.append(Stage("grain", self["grain"], radius=self["grain_size"]))

        if self["vignette"]:
            out.append(Stage("vignette", self["vignette"]))
        return out

    def cost(self) -> int:
        """Roughly how many times the picture has to be drawn for this grade.

        The transport bar uses it to warn before a video is played with a
        grade heavy enough to drop frames, and the editor uses it to decide
        whether to preview at full resolution or at half. The blurred stages
        count for more because a blur is the only thing here that is not a
        single textured quad.
        """
        total = 1
        for stage in self.stages():
            if stage.kind in ("bloom", "glow", "unsharp"):
                total += 3
            elif stage.kind in ("shadows", "highlights", "shadow_tint",
                                "highlight_tint"):
                total += 2
            else:
                total += 1
        return total

    # -- geometry ---------------------------------------------------------

    def rotate(self, degrees: int):
        self["rotation"] = (int(self["rotation"]) + degrees) % 360
        return self

    def oriented(self, width: float, height: float) -> tuple[float, float]:
        """`width` and `height` the right way round for this grade's rotation."""
        return (height, width) if self["rotation"] % 180 else (width, height)
