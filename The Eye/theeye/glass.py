"""The glass: what the chrome is made of, and the light behind it.

None of this comes from the compositor. There is no backdrop-blur protocol on
Wayland that an application can ask for, and a window that is merely
translucent shows whatever happens to be behind it — which is not a material,
it is a hole. Worse here than in most apps: the thing behind the chrome is
usually the picture, and a transport bar that is a hole onto a photograph is a
transport bar you cannot read.

So The Eye paints its own light. Every panel sits on a slab this module draws,
and the slab is nearly opaque over its own tint rather than transparent onto
what is underneath. The window is still genuinely translucent at its edges,
and behind the whole interface is an `Ambient` layer of slow-moving colour, so
the depth is made rather than borrowed and looks the same over a bright
photograph as over a dark one.

What makes a rounded rectangle read as glass, in the order it is drawn:

* a wide, soft shadow underneath, so it is a slab and not a painted-on panel;
* a body gradient, because the light reaching the top of a piece of glass has
  travelled less far than the light reaching the bottom;
* a faint grain, which hides the banding a large gradient gets on an 8-bit
  display and reads as frosting;
* a bright inner edge along the top, fading over about twenty pixels, which is
  the *thickness* of the material and the single most convincing part;
* and a rim: one pixel, bright along the top, dimmer at the sides, dark along
  the bottom. Light comes from above, so that is where an edge catches it.

Behind each slab, where the machine will do it, is a real blurred backdrop.
CSS `backdrop-filter: blur()` blurs what the application has already drawn
beneath a widget — measured on GTK 4.22, and the one thing in this module that
is not hand-painted. It only ever reaches what *this app* drew, never the
desktop behind a translucent window, which is the compositor's business and
not on offer under niri; but what this app drew is exactly the picture, and the
picture is what a transport bar sits on top of.

It changes the whole design of the material, which is why it is worth the
paragraph. A bar over an unblurred photograph has to be nearly opaque or the
photograph shows through and the labels become unreadable. A bar over a
*blurred* photograph does not: the detail that was competing with the text has
been destroyed, and the colour that is left reads as the picture's own light
coming through glass. So when the blur is on, `Material` is built far thinner
— and that thinness, over a real blur, is what actually looks like glass
rather than like a dark panel. `blur_radius()` is the one number that controls
it, and turning it off in the preferences falls back to the thicker material,
which is the correct look for a machine that cannot spare the blur.

Costs, because this has to run on a laptop: the ambient layer animates only
while something is happening and settles to a still gradient about twelve
seconds after the last input, it repaints at 30fps rather than at the
display's rate, and the whole module can be turned off in the preferences — at
which point every panel is a flat rounded rectangle and the app still works.
"""

from __future__ import annotations

import math
import random
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")

from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from . import theme as theming  # noqa: E402

# How long the ambient layer keeps moving after the last sign of life, and the
# most often it is allowed to repaint. Thirty is not the display's rate: the
# layer is four soft gradients drifting on a minute-long cycle, and nobody can
# tell thirty from sixty on something that slow, so the other half of those
# frames is given back to the machine — which, while a video is playing, has
# somewhere better to put them.
AWAKE_SECONDS = 12.0
MOTION_FPS = 30.0


# ------------------------------------------------------------------ colour ----

def rgba(colour: str, alpha: float = 1.0) -> Gdk.RGBA:
    r, g, b = theming.rgb(colour)
    return Gdk.RGBA(red=r, green=g, blue=b, alpha=max(0.0, min(1.0, alpha)))


def white(alpha: float) -> Gdk.RGBA:
    return Gdk.RGBA(red=1.0, green=1.0, blue=1.0, alpha=max(0.0, min(1.0, alpha)))


def black(alpha: float) -> Gdk.RGBA:
    return Gdk.RGBA(red=0.0, green=0.0, blue=0.0, alpha=max(0.0, min(1.0, alpha)))


def shade(colour: str, amount: float) -> str:
    """Lighter for a positive amount, darker for a negative one.

    Towards white or black rather than by a fixed step, so a colour that is
    already nearly white does not overshoot and come back grey.
    """
    r, g, b = theming.rgb(colour)
    target = 1.0 if amount > 0 else 0.0
    t = abs(amount)
    return theming.hexed(*(c + (target - c) * t for c in (r, g, b)))


def mix(one: str, two: str, amount: float) -> str:
    a, b = theming.rgb(one), theming.rgb(two)
    return theming.hexed(*(x + (y - x) * amount for x, y in zip(a, b)))


def stops(*pairs) -> list[Gsk.ColorStop]:
    """[(offset, Gdk.RGBA), ...] as the list the gradient calls want.

    Gsk.ColorStop takes no keyword arguments from Python, so each one has to
    be built and then filled in.
    """
    out = []
    for offset, colour in pairs:
        stop = Gsk.ColorStop()
        stop.offset = offset
        stop.color = colour
        out.append(stop)
    return out


def rect(x: float, y: float, w: float, h: float) -> Graphene.Rect:
    return Graphene.Rect().init(x, y, w, h)


def point(x: float, y: float) -> Graphene.Point:
    return Graphene.Point().init(x, y)


def rounded(x: float, y: float, w: float, h: float, radius) -> Gsk.RoundedRect:
    """A rounded rectangle; `radius` is one number or four, clockwise from
    the top left."""
    box = Gsk.RoundedRect()
    if isinstance(radius, (int, float)):
        box.init_from_rect(rect(x, y, w, h), radius)
        return box
    tl, tr, br, bl = radius
    box.init(rect(x, y, w, h),
             *(Graphene.Size().init(r, r) for r in (tl, tr, br, bl)))
    return box


# ---------------------------------------------------------------- material ----

class Material:
    """The numbers one slab of glass is drawn with.

    Built from a theme and the user's translucency, so the drawing code holds
    no colours of its own and a new theme needs no changes here. The
    constructors are the jobs glass does in this app, and they differ mostly
    in how much light they let through: a bar with controls on it has to stay
    legible over a bright photograph, a strip of thumbnails can afford to be
    thinner because its content is opaque anyway, and a panel floating over
    the picture wants to be nearly solid so that what is behind it stops
    competing for attention.
    """

    def __init__(self, scheme: theming.Theme, alpha: float, radius=14.0,
                 rim=1.0, sheen=1.0, lift=1.0, blurred=False):
        self.scheme = scheme
        self.blurred = blurred
        self.alpha = max(0.0, min(1.0, alpha))
        self.radius = radius
        self.light = not scheme.dark

        tint = scheme.glass
        # Glass is lighter where the light enters it. On a light theme the
        # gradient runs the other way: a white material lit from above gets
        # its shadow at the top, from its own bezel.
        if self.light:
            self.top = rgba(shade(tint, -0.05), self.alpha)
            self.bottom = rgba(shade(tint, 0.02), self.alpha)
        else:
            self.top = rgba(shade(tint, 0.08), self.alpha)
            self.bottom = rgba(shade(tint, -0.06), self.alpha)

        edge = scheme.rim * rim
        self.rim_top = white(0.95 * edge) if self.light else white(0.40 * edge)
        self.rim_side = white(0.45 * edge) if self.light else white(0.13 * edge)
        # A dark hairline under light glass, because white on white has no edge
        # at all; under dark glass the base catches a little light instead.
        self.rim_bottom = black(0.17 * edge) if self.light else white(0.06 * edge)

        self.sheen = (0.08 if self.light else 0.17) * scheme.rim * sheen
        self.bounce = (0.03 if self.light else 0.05) * scheme.rim * sheen
        self.pool = (0.05 if self.light else 0.07) * scheme.rim * sheen

        self.shadow = black((0.17 if self.light else 0.42) * lift)
        self.shadow_blur = 26.0 * lift
        self.shadow_dy = 8.0 * lift
        self.grain = 0.014 if self.light else 0.022

    # -- the jobs ---------------------------------------------------------

    @classmethod
    def bar(cls, scheme, translucency, radius=18.0, blurred=False):
        """A floating bar with controls on it.

        Two materials, and which one is right depends entirely on whether
        there is a blur behind it. Without one, the bar sits on an unaltered
        photograph and has to be nearly opaque to stay readable — the floor in
        `_legible` is not decoration, it is the difference between a transport
        bar and a smear. With a blur behind it, the photograph has already
        been reduced to soft colour, so the bar can be thin enough to show it
        through the glass, which is the look this is all for.
        """
        if blurred:
            return cls(scheme, _legible(scheme.glass_alpha * THIN_BAR,
                                        translucency, 0.08),
                       radius=radius, rim=1.25, sheen=1.3, lift=1.2, blurred=True)
        return cls(scheme, _legible(scheme.glass_alpha, translucency),
                   radius=radius, rim=1.1, sheen=1.15, lift=1.2)

    @classmethod
    def panel(cls, scheme, translucency, radius=16.0, blurred=False):
        """A side panel — the editor, the information list."""
        base = scheme.glass_alpha * (THIN_PANEL if blurred else 1.0)
        return cls(scheme, _legible(base, translucency, 0.10 if blurred else 0.5),
                   radius=radius, lift=0.9, blurred=blurred,
                   rim=1.2 if blurred else 1.0)

    @classmethod
    def strip(cls, scheme, translucency, radius=0.0, blurred=False):
        """The reel of thumbnails, which is mostly covered by its own content."""
        return cls(scheme, scheme.glass_alpha * translucency
                   * (0.55 if blurred else 0.92),
                   radius=radius, rim=0.9, sheen=0.9, lift=0.5, blurred=blurred)

    @classmethod
    def card(cls, scheme, translucency, radius=20.0, blurred=False):
        """Something floating clear of everything else: a toast, the on-screen
        display, the frame under the pointer on the seek bar.

        Nearly solid whether or not there is a blur. A card is in front of
        everything and short-lived, and the one thing it must not do is make
        the reader work — so this is the one material that does not get
        thinner when the blur is available.
        """
        return cls(scheme, min(1.0, scheme.glass_alpha * (0.6 + 0.4 * translucency)
                               + 0.08),
                   radius=radius, rim=1.2, sheen=1.2, lift=1.6, blurred=blurred)

    @classmethod
    def well(cls, scheme, translucency, radius=10.0):
        """A recess rather than a slab — a slider's trough, an entry.

        Drawn with the gradient inverted and the rim dark at the top, which is
        the whole of what makes a shape read as sunk into the surface rather
        than sitting on it.
        """
        material = cls(scheme, min(1.0, scheme.glass_alpha * translucency + 0.1),
                       radius=radius, rim=0.6, sheen=0.4, lift=0.0)
        material.top, material.bottom = material.bottom, material.top
        material.rim_top, material.rim_bottom = (
            black(0.22) if material.light else black(0.30),
            white(0.30) if material.light else white(0.10))
        material.sheen = 0.0
        return material


# How far the backdrop is blurred, in CSS pixels. Large enough that no detail
# of the picture survives to compete with a label — a smaller radius leaves
# recognisable shapes, which is worse than no blur at all because it looks
# like a mistake rather than like a material.
BLUR_RADIUS = 22

# How much of its opacity each material gives up when there is a blur behind
# it. These are the numbers that decide whether the chrome looks like glass or
# like a panel, and they were chosen by looking: much above these the blur
# cannot be seen at all, much below and the picture's own brightness starts to
# move the text's contrast around as the picture changes.
THIN_BAR = 0.60
THIN_PANEL = 0.64


def blur_css(radius=BLUR_RADIUS) -> str:
    """The CSS a blurred panel needs, for `widgets.stylesheet` to include."""
    return f"backdrop-filter: blur({int(radius)}px);"


def _legible(base: float, translucency: float, floor: float = 0.62) -> float:
    """Opacity for something with text or controls on it.

    The user's setting is honoured but not taken literally. At 0.5 the chrome
    reads as glass; text on glass that thin, over a photograph that might be
    any colour at all, is not readable. Pulling it back towards solid by
    `floor` keeps the look without the squint — and the look is mostly in the
    edges and the gradient, which do not get thinner.
    """
    wanted = base * translucency
    return min(1.0, wanted + (1.0 - wanted) * floor)


# ------------------------------------------------------------------- grain ----

_grain_tiles: dict[int, Gdk.Texture] = {}
_GRAIN_TILE = 128


def grain(alpha: float) -> Gdk.Texture:
    """A tile of white noise at a given strength, built once per strength.

    Two jobs. A large smooth gradient on an 8-bit display bands into visible
    steps, and a per-pixel wobble of a few levels breaks the steps up. And
    frosted glass is not smooth — a little noise is most of what separates
    "frosted" from "tinted".

    The strength is baked into the texture rather than applied by an opacity
    node around the repeat node that tiles it. Partly that is one node fewer
    per panel, on something drawn for every panel on every frame. Mostly it is
    that GSK's renderers have been seen to disagree about an opacity node
    wrapped directly around a repeat node — the symptom being noise at full
    strength, which is a window of television static rather than a subtle
    frosting. Both renderers agree on this code today, and baking the
    coverage in means there is nothing left for them to disagree about.

    The strength is quantised to a thousandth so that a theme change does not
    build a new tile for a figure that rounds to the same bytes.
    """
    key = max(0, min(1000, int(round(alpha * 1000))))
    if key not in _grain_tiles:
        rng = random.Random(0x9E3779B9)      # fixed, so it never shimmers
        size = _GRAIN_TILE
        coverage = key / 1000.0
        data = bytearray(size * size * 4)
        for i in range(0, len(data), 4):
            # Premultiplied white: every channel is the coverage, so the tile
            # is white at a per-pixel alpha and needs no opacity node.
            value = int(rng.randrange(0, 256) * coverage)
            data[i] = data[i + 1] = data[i + 2] = data[i + 3] = value
        _grain_tiles[key] = Gdk.MemoryTexture.new(
            size, size, Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED,
            GLib.Bytes.new(bytes(data)), size * 4)
    return _grain_tiles[key]


def append_grain(snapshot: Gtk.Snapshot, bounds: Graphene.Rect, alpha: float):
    if alpha <= 0.001:
        return
    tile = rect(0, 0, _GRAIN_TILE, _GRAIN_TILE)
    node = Gsk.TextureNode.new(grain(alpha), tile)
    snapshot.append_node(Gsk.RepeatNode.new(bounds, node, tile))


# -------------------------------------------------------------------- slab ----

def slab(snapshot: Gtk.Snapshot, material: Material, x: float, y: float,
         width: float, height: float, radius=None, shadow=True, grain_on=True):
    """One piece of glass, drawn in the order described at the top of the file.

    `radius` overrides the material's own, and takes one number or four
    clockwise from the top left — which is what a bar flush against the
    bottom of the window needs, so that its lower corners are square and its
    upper ones are not.
    """
    if width <= 0.5 or height <= 0.5:
        return
    radius = material.radius if radius is None else radius
    box = rounded(x, y, width, height, radius)
    bounds = rect(x, y, width, height)

    if shadow and material.shadow.alpha > 0.002:
        # Outset rather than a blurred copy of the shape: it is one node, and
        # GSK draws it without an offscreen.
        snapshot.append_outset_shadow(
            box, material.shadow, 0.0, material.shadow_dy,
            0.0, material.shadow_blur)

    snapshot.push_rounded_clip(box)
    try:
        # The body.
        snapshot.append_linear_gradient(
            bounds, point(x, y), point(x, y + height),
            stops((0.0, material.top), (1.0, material.bottom)))

        if grain_on:
            append_grain(snapshot, bounds, material.grain)

        # Two pools of light inside the slab, offset from each other, as if
        # the ambient layer behind were refracting through the material. Cheap
        # — two gradients — and most of what stops a large panel looking flat.
        if material.pool > 0.002:
            tint = material.scheme.accent
            snapshot.append_radial_gradient(
                bounds, point(x + width * 0.22, y + height * 0.1),
                width * 0.5, height * 1.4, 0.0, 1.0,
                stops((0.0, rgba(tint, material.pool)), (1.0, rgba(tint, 0.0))))
            snapshot.append_radial_gradient(
                bounds, point(x + width * 0.86, y + height * 0.95),
                width * 0.42, height * 1.2, 0.0, 1.0,
                stops((0.0, rgba(material.scheme.aurora[0], material.pool * 0.8)),
                      (1.0, rgba(material.scheme.aurora[0], 0.0))))

        # The thickness of the material: a bright band just inside the top
        # edge, fading over about twenty pixels. This is the part that makes
        # it glass rather than paint.
        if material.sheen > 0.002:
            depth = min(22.0, height * 0.5)
            snapshot.append_linear_gradient(
                rect(x, y, width, depth), point(x, y), point(x, y + depth),
                stops((0.0, white(material.sheen)),
                      (0.55, white(material.sheen * 0.28)),
                      (1.0, white(0.0))))
        if material.bounce > 0.002:
            # And a dimmer one at the bottom, which is light that has been
            # through the slab and come back off whatever it is sitting on.
            depth = min(14.0, height * 0.4)
            snapshot.append_linear_gradient(
                rect(x, y + height - depth, width, depth),
                point(x, y + height), point(x, y + height - depth),
                stops((0.0, white(material.bounce)), (1.0, white(0.0))))
    finally:
        snapshot.pop()

    # The rim, last, so nothing is drawn over it. Three separate borders
    # rather than one: a single colour all the way round is what a flat
    # rectangle has, and light comes from above.
    snapshot.append_border(box, (1.0, 1.0, 1.0, 1.0),
                           (material.rim_top, material.rim_side,
                            material.rim_bottom, material.rim_side))


def ring(snapshot: Gtk.Snapshot, colour: Gdk.RGBA, x, y, width, height,
         radius=10.0, thickness=2.0):
    """A focus ring, or the border on the selected thumbnail."""
    box = rounded(x, y, width, height, radius)
    snapshot.append_border(box, (thickness,) * 4, (colour,) * 4)


# ----------------------------------------------------------------- ambient ----

class Blob:
    """One drifting light behind the glass.

    Two sine waves of incommensurate period for each axis, so the path never
    repeats anywhere near often enough to be recognised as a loop, and a third
    for the size — light that moves without changing shape reads as a sprite
    being slid around rather than as something luminous.
    """

    def __init__(self, colour: str, seed: int):
        rng = random.Random(seed)
        self.colour = colour
        self.home = (rng.uniform(0.12, 0.88), rng.uniform(0.08, 0.92))
        self.travel = (rng.uniform(0.10, 0.26), rng.uniform(0.08, 0.20))
        self.period = (rng.uniform(47, 93), rng.uniform(53, 101))
        self.phase = (rng.uniform(0, math.tau), rng.uniform(0, math.tau))
        self.size = rng.uniform(0.48, 0.78)
        self.breath = rng.uniform(71, 113)
        self.strength = rng.uniform(0.75, 1.0)

    def at(self, t: float) -> tuple[float, float, float]:
        """Centre and scale at time `t`, in fractions of the widget."""
        x = self.home[0] + self.travel[0] * math.sin(
            math.tau * t / self.period[0] + self.phase[0])
        y = self.home[1] + self.travel[1] * math.sin(
            math.tau * t / self.period[1] + self.phase[1])
        scale = self.size * (1.0 + 0.16 * math.sin(math.tau * t / self.breath))
        return x, y, scale


class Ambient(Gtk.Widget):
    """The light behind everything, and the clock that moves it.

    Sits at the back of the window with the whole interface drawn over the
    top, so it is the one widget that has to cover every pixel. In this app
    most of it is hidden by the picture — which is the point: what shows is
    the border around a photograph that does not fill the window, and that
    border is the difference between a window with a picture in it and a
    window that is about the picture.

    It animates only while the window is being used. `wake()` is called from
    the keyboard and the pointer; twelve seconds after the last of those the
    tick callback removes itself and the layer holds its position. Waking
    again picks the clock up where it left off rather than snapping, because
    the alternative is a visible jump every time you touch the keyboard after
    reading something.

    It also stops while a video is playing, which is not a compromise: the
    picture covers it, nothing of it can be seen, and the frames are wanted
    elsewhere.
    """

    __gtype_name__ = "TheEyeAmbient"

    def __init__(self, scheme: theming.Theme, translucency: float = 0.9,
                 motion: bool = True):
        super().__init__()
        self.set_can_target(False)          # never in the way of a click
        self._scheme = scheme
        self._alpha = translucency
        self._motion = motion
        self._suspended = False
        self._blobs: list[Blob] = []
        self._tick = 0
        self._clock = 0.0
        self._last_frame: float | None = None
        self._last_draw = 0.0
        self._awake_until = 0.0
        self._rebuild()

    # -- theme and settings ------------------------------------------------

    def _rebuild(self):
        self._blobs = [Blob(colour, 1013 + index * 7717)
                       for index, colour in enumerate(self._scheme.aurora)]
        self.queue_draw()

    def set_scheme(self, scheme: theming.Theme):
        if scheme is not self._scheme:
            self._scheme = scheme
            self._rebuild()

    def set_translucency(self, alpha: float):
        if abs(alpha - self._alpha) > 0.001:
            self._alpha = alpha
            self.queue_draw()

    def set_motion(self, motion: bool):
        self._motion = motion
        if not motion:
            self._stop()
        self.queue_draw()

    def suspend(self, suspended: bool):
        """Hold still — called while a video is playing, and on unmap."""
        self._suspended = suspended
        if suspended:
            self._stop()
        else:
            self.wake()

    # -- the clock ---------------------------------------------------------

    def wake(self):
        """Something happened; drift for a while longer."""
        self._awake_until = time.monotonic() + AWAKE_SECONDS
        if self._suspended or not self._motion or self._tick or not self.get_mapped():
            return
        settings = self.get_settings()
        if settings is not None and not settings.get_property("gtk-enable-animations"):
            return
        self._last_frame = None
        self._tick = self.add_tick_callback(self._advance)

    def _stop(self):
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0

    def _advance(self, _widget, frame_clock):
        now = frame_clock.get_frame_time() / 1e6
        if self._last_frame is None:
            self._last_frame = now
        delta = max(0.0, min(0.25, now - self._last_frame))
        self._last_frame = now
        self._clock += delta

        # The frame clock and time.monotonic() are different clocks, so the
        # deadline is only ever compared against the one it was set from.
        if time.monotonic() > self._awake_until:
            self._stop()
            self.queue_draw()
            return GLib.SOURCE_REMOVE

        if now - self._last_draw >= 1.0 / MOTION_FPS:
            self._last_draw = now
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def do_map(self):
        Gtk.Widget.do_map(self)
        self.wake()

    def do_unmap(self):
        self._stop()
        Gtk.Widget.do_unmap(self)

    def do_unroot(self):
        self._stop()
        Gtk.Widget.do_unroot(self)

    # -- drawing -----------------------------------------------------------

    def do_snapshot(self, snapshot):
        width, height = float(self.get_width()), float(self.get_height())
        if width <= 0 or height <= 0:
            return
        scheme = self._scheme
        bounds = rect(0, 0, width, height)
        base = scheme.bg
        alpha = self._alpha

        # The ground. Two stops either side of the background colour rather
        # than the colour itself, so even with every effect off the window has
        # a direction to it.
        snapshot.append_linear_gradient(
            bounds, point(0, 0), point(0, height),
            stops((0.0, rgba(shade(base, 0.06 if scheme.dark else -0.02), alpha)),
                  (1.0, rgba(shade(base, -0.04 if scheme.dark else -0.06), alpha))))

        if self._motion or self._clock:
            t = self._clock
            for blob in self._blobs:
                fx, fy, scale = blob.at(t)
                peak = blob.strength * (0.40 if scheme.dark else 0.28) * alpha
                snapshot.append_radial_gradient(
                    bounds, point(width * fx, height * fy),
                    max(1.0, width * scale), max(1.0, height * scale * 1.05),
                    0.0, 1.0,
                    stops((0.0, rgba(blob.colour, peak)),
                          (0.45, rgba(blob.colour, peak * 0.42)),
                          (1.0, rgba(blob.colour, 0.0))))

        # A vignette, so the corners settle and the middle is where the eye
        # goes — which in this app is where the picture is. Light themes get a
        # far weaker one: a grey corner on white looks like dirt, not depth.
        corner = 0.28 if scheme.dark else 0.10
        snapshot.append_radial_gradient(
            bounds, point(width * 0.5, height * 0.46),
            width * 0.78, height * 0.86, 0.0, 1.0,
            stops((0.55, black(0.0)), (1.0, black(corner * alpha))))

        append_grain(snapshot, bounds, 0.018 if scheme.dark else 0.012)
