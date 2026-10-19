"""The glass itself: what gets drawn under the text.

None of this comes from the compositor. niri has no backdrop blur to ask for,
and there is no wallpaper daemon running behind the window to frost, so a
translucent window here would only show whatever happens to be underneath it
— which is not a material, it is a hole. So Prism paints its own light
instead. Every pane sits on a slab this module draws, and behind all the slabs
is an `Ambient` layer of slow-moving colour. The window is still genuinely
translucent at the edges, but the depth is made rather than borrowed, which
means it looks the same over a full-screen browser as it does over the
desktop.

What makes a rounded rectangle read as glass, in the order it is drawn:

* a wide, soft shadow underneath, so it is a slab and not a painted-on panel;
* a body gradient — glass is never one flat colour, because the light that
  reaches the top of it has travelled less far than the light at the bottom;
* a faint grain, which hides gradient banding and reads as frosting;
* two pools of light inside, offset from the blobs behind, as if the ambient
  layer were refracting through the material;
* a highlight that follows the pointer, because real glass has a specular
  response and a still one looks like a photograph of glass;
* a bright inner edge at the top fading over ~20px, which is the *thickness*
  of the material and the single most convincing part;
* and a rim: one pixel, bright along the top, dimmer at the sides, dark at the
  bottom. Light comes from above, so that is where the edge catches it.

Blur is used in exactly one place, and the reasoning is worth writing down
because it is not where you would expect. GTK 4.22 does support CSS
`backdrop-filter`, and it really does blur what the app has already drawn
underneath a widget — measured, not assumed. But behind a *pane* is the
ambient layer, which is four soft gradients: blurring a gradient produces the
same gradient and costs an offscreen pass for nothing. Behind a floating
*card* is the terminal, which is text, and text is the only thing here with
detail fine enough for a blur to do anything to. So `Card` frosts and nothing
else does. The blur reaches only what this app drew; the desktop behind a
translucent window belongs to the compositor, and niri has no blur to offer.

Costs, because this runs on a 1366x768 laptop: the ambient layer animates only
while something is happening and settles to a still gradient about twelve
seconds after the last keystroke, the pointer highlight repaints at 30fps
rather than at the display's rate, and every effect here can be turned off in
the preferences. With motion off the whole module is a handful of gradients
drawn once per resize.
"""

from __future__ import annotations

import math
import random
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from . import theme as theming  # noqa: E402

# How long the ambient layer keeps moving after the last sign of life, and the
# most often it is allowed to repaint. Thirty is not the display's rate: the
# layer is four soft gradients drifting on a sixty-second cycle, and nobody can
# tell thirty from sixty on something that slow, so the other half of those
# frames is given back to the machine.
AWAKE_SECONDS = 12.0
MOTION_FPS = 30.0
SPECULAR_FPS = 30.0


# ------------------------------------------------------------- colours ----

def rgba(colour: str, alpha: float = 1.0) -> Gdk.RGBA:
    r, g, b = theming.rgb(colour)
    return Gdk.RGBA(red=r, green=g, blue=b, alpha=max(0.0, min(1.0, alpha)))


def white(alpha: float) -> Gdk.RGBA:
    return Gdk.RGBA(red=1.0, green=1.0, blue=1.0, alpha=max(0.0, min(1.0, alpha)))


def black(alpha: float) -> Gdk.RGBA:
    return Gdk.RGBA(red=0.0, green=0.0, blue=0.0, alpha=max(0.0, min(1.0, alpha)))


def shade(colour: str, amount: float) -> str:
    """Lighter for a positive amount, darker for a negative one.

    Towards white rather than by a fixed step, so a colour that is already
    nearly white does not overshoot and come back grey.
    """
    r, g, b = theming.rgb(colour)
    target = 1.0 if amount > 0 else 0.0
    t = abs(amount)
    mixed = tuple(c + (target - c) * t for c in (r, g, b))
    return "#%02X%02X%02X" % tuple(round(c * 255) for c in mixed)


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
    box.init(rect(x, y, w, h), *(Graphene.Size().init(r, r) for r in (tl, tr, br, bl)))
    return box


# ------------------------------------------------------------- material ----

class Material:
    """The numbers one slab of glass is drawn with.

    Built from a theme and the user's opacity, so that the drawing code below
    holds no colours of its own and a new theme needs no changes here. The
    three constructors are the three jobs glass does in this app, and they
    differ mostly in how much light they let through: a pane carries text and
    has to stay readable, the chrome can afford to be thinner, and a card
    floating over the terminal wants to be nearly solid so that what is behind
    it stops competing for attention.
    """

    def __init__(self, scheme: theming.Theme, alpha: float, radius=14.0,
                 rim=1.0, sheen=1.0, lift=1.0, grain=True):
        self.scheme = scheme
        self.alpha = max(0.0, min(1.0, alpha))
        self.radius = radius
        self.light = not scheme.dark

        tint = scheme.glass
        # Glass is lighter where the light enters it. On a light theme the
        # gradient runs the other way: a white material lit from above gets
        # its shadow at the top, from the bezel.
        if self.light:
            self.top = rgba(shade(tint, -0.04), self.alpha)
            self.bottom = rgba(shade(tint, 0.02), self.alpha)
        else:
            self.top = rgba(shade(tint, 0.07), self.alpha)
            self.bottom = rgba(shade(tint, -0.05), self.alpha)

        edge = scheme.rim * rim
        self.rim_top = white(0.42 * edge) if not self.light else white(0.95 * edge)
        self.rim_side = white(0.14 * edge) if not self.light else white(0.45 * edge)
        # A dark hairline under light glass, because white-on-white has no
        # edge at all; under dark glass the base catches a little light.
        self.rim_bottom = black(0.16 * edge) if self.light else white(0.07 * edge)

        self.sheen = (0.07 if self.light else 0.16) * scheme.rim * sheen
        self.bounce = (0.03 if self.light else 0.05) * scheme.rim * sheen
        self.specular = (0.05 if self.light else 0.06) * scheme.rim * sheen
        self.pool = (0.05 if self.light else 0.07) * scheme.rim * sheen
        # How much of the window's light comes through this glass. Thicker,
        # more opaque glass passes less of it — and a card, which is nearly
        # solid and is meant to sit in front of everything, passes least.
        self.refraction = max(0.0, (1.0 - self.alpha) * 2.1 + 0.10) * (
            1.0 if scheme.dark else 0.72)

        self.shadow = black((0.16 if self.light else 0.40) * lift)
        self.shadow_blur = 26.0 * lift
        self.shadow_dy = 8.0 * lift
        self.grain = (0.022 if not self.light else 0.014) if grain else 0.0

    @classmethod
    def pane(cls, scheme, opacity, radius=14.0, grain=True):
        """Glass with text on it.

        Two things are held back here that the chrome does not have to hold
        back. The opacity has a floor, so the pane stays readable however
        transparent the window as a whole is asked to be; and the light
        passing through is cut to a bit over half, because a wash of colour
        that is lovely in a margin is tiring under a wall of text. The light
        is still there — it has to be, or the pane stops belonging to the
        window — but it is a tint rather than a glow.
        """
        material = cls(scheme, _readable(scheme.glass_alpha, opacity),
                       radius=radius, lift=0.75, grain=grain)
        material.refraction *= 0.58
        return material

    @classmethod
    def chrome(cls, scheme, opacity, radius=14.0, grain=True):
        return cls(scheme, scheme.glass_alpha * opacity * 0.96,
                   radius=radius, rim=1.1, sheen=1.15, lift=0.5, grain=grain)

    @classmethod
    def card(cls, scheme, opacity, radius=18.0, grain=True, frosted=False):
        """A panel floating over the terminal, lifted well clear of it.

        `frosted` says the stylesheet is blurring what is behind this card.
        When it is, the card can afford to be much thinner — the blur is
        what stops the text underneath competing, so the fill no longer has
        to, and a thin fill over a blurred background is the whole look. Set
        it solid *and* blurred and the blur is invisible, which is what the
        first attempt did.
        """
        body = (0.62 if frosted else min(
            1.0, scheme.glass_alpha * (0.55 + 0.45 * opacity) + 0.06))
        card = cls(scheme, body, radius=radius, rim=1.2, sheen=1.2,
                   lift=1.5, grain=grain)
        card.refraction *= 0.5 if not frosted else 0.25
        return card


def _readable(base: float, opacity: float) -> float:
    """Opacity for something with text on it.

    The user's setting is honoured but not taken literally: at 0.6 the window
    reads as glass, and text on glass that thin is tiring. Pulling it back
    more than half way to solid keeps the look without the squint.
    """
    wanted = base * opacity
    return min(1.0, wanted + (1.0 - wanted) * 0.55)


# ---------------------------------------------------------------- grain ----

_grain_tiles: dict[int, Gdk.Texture] = {}
_GRAIN_TILE = 128


def grain(alpha: float) -> Gdk.Texture:
    """A tile of white noise, already faded to `alpha`.

    Two jobs. Large smooth gradients on an 8-bit display band into visible
    steps, and a per-pixel wobble of a few levels breaks the steps up. And
    frosted glass is not smooth — a little noise is most of what separates
    "frosted" from "tinted".

    The fade is baked into the tile rather than applied with an opacity node
    around it, which is not a micro-optimisation: GSK's GL renderer draws a
    repeat node at full strength no matter what opacity it is wrapped in, so
    the version that asked for two per cent of this noise got all of it. The
    Cairo renderer composes it correctly, which is exactly why this had to be
    checked under the renderer the app will actually run on. Baking the alpha
    in needs no opacity node and so cannot be ignored by one.

    Tiles are cached per alpha, quantised, because there are only ever a
    handful of distinct values in use and each one is 64KB.
    """
    key = max(0, min(255, round(alpha * 1000)))
    texture = _grain_tiles.get(key)
    if texture is None:
        rng = random.Random(0x9E3779B9)      # fixed, so it never shimmers
        size = _GRAIN_TILE
        faded = key / 1000.0
        data = bytearray(size * size * 4)
        for i in range(0, len(data), 4):
            # Premultiplied white: every channel is the coverage, and the
            # coverage is the noise already scaled by the alpha wanted.
            value = round(rng.randrange(0, 256) * faded)
            data[i] = data[i + 1] = data[i + 2] = data[i + 3] = value
        texture = Gdk.MemoryTexture.new(
            size, size, Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED,
            GLib.Bytes.new(bytes(data)), size * 4)
        _grain_tiles[key] = texture
    return texture


def append_grain(snapshot: Gtk.Snapshot, bounds: Graphene.Rect, alpha: float) -> None:
    if alpha <= 0.001:
        return
    tile = rect(0, 0, _GRAIN_TILE, _GRAIN_TILE)
    node = Gsk.TextureNode.new(grain(alpha), tile)
    snapshot.append_node(Gsk.RepeatNode.new(bounds, node, tile))


# -------------------------------------------------------------- ambient ----

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
        """Centre and scale at time `t`, in fractions of the window."""
        x = self.home[0] + self.travel[0] * math.sin(
            math.tau * t / self.period[0] + self.phase[0])
        y = self.home[1] + self.travel[1] * math.sin(
            math.tau * t / self.period[1] + self.phase[1])
        scale = self.size * (1.0 + 0.16 * math.sin(math.tau * t / self.breath))
        return x, y, scale


class Light:
    """The moving light the whole window shares.

    This is the part that makes the glass read as glass rather than as a dark
    rectangle with a shiny edge. The light is defined once, in the window's
    own coordinates, and then *every* slab samples it at its own position and
    paints it again, dimmer and larger, inside itself — so a glow that is
    halfway under the edge of a pane appears on both sides of that edge, in
    the right place, slightly diffused where it passes through the material.
    That continuity is what the eye reads as one light source behind one sheet
    of glass. Give each pane its own independent glow instead and it reads as
    a row of separate lamps, which was the first thing this looked like.

    No widget of its own: `Ambient` owns the clock and drives this, and
    anything that wants to be repainted as the light moves registers here.
    """

    def __init__(self, scheme: theming.Theme, motion: bool = True):
        self.scheme = scheme
        self.motion = motion
        self.clock = 0.0
        self.blobs: list[Blob] = []
        self._listeners: list = []
        self.rebuild()

    def rebuild(self):
        self.blobs = [Blob(colour, 1013 + index * 7717)
                      for index, colour in enumerate(self.scheme.aurora)]

    def set_scheme(self, scheme: theming.Theme):
        if scheme is not self.scheme:
            self.scheme = scheme
            self.rebuild()
            self.redraw()

    def listen(self, widget):
        """Repaint `widget` whenever the light moves."""
        if widget not in self._listeners:
            self._listeners.append(widget)

    def forget(self, widget):
        if widget in self._listeners:
            self._listeners.remove(widget)

    def redraw(self):
        for widget in list(self._listeners):
            if widget.get_mapped():
                widget.queue_draw()

    def wake(self):
        """Something happened; drift for a while longer."""
        for widget in self._listeners:
            if isinstance(widget, Ambient):
                widget.wake()

    def sample(self, width: float, height: float, dx: float = 0.0, dy: float = 0.0):
        """Where every blob is, in the coordinates of a widget at (dx, dy).

        `width` and `height` are the *window's*, not the widget's: the light
        belongs to the window, and a pane that scaled it to itself would show
        a different part of the sky from the pane beside it.
        """
        out = []
        for blob in self.blobs:
            fx, fy, scale = blob.at(self.clock)
            out.append((width * fx - dx, height * fy - dy,
                        max(1.0, width * scale), max(1.0, height * scale * 1.05),
                        blob.colour, blob.strength))
        return out


def window_offset(widget: Gtk.Widget) -> tuple[float, float, float, float]:
    """Where `widget` sits inside its window, and how big that window is.

    Returns zeroes and the widget's own size when it is not rooted yet, which
    makes the first frame draw the light as though the widget were the whole
    window — wrong, but only until the frame after it, and better than not
    drawing.
    """
    root = widget.get_root()
    if root is None:
        return 0.0, 0.0, float(widget.get_width()), float(widget.get_height())
    ok, local = widget.compute_point(root, point(0, 0))
    if not ok:
        return 0.0, 0.0, float(root.get_width()), float(root.get_height())
    return (-local.x, -local.y, float(root.get_width()), float(root.get_height()))


class Ambient(Gtk.Widget):
    """The light behind everything, and the clock that moves it.

    Sits at the back of the window with the whole interface drawn over the
    top, so it is the one widget that has to cover every pixel — including the
    gaps between panes, which is where it is seen most directly.

    It animates only while the window is being used. `wake()` is called from
    the keyboard, the pointer, and from each terminal when output arrives;
    twelve seconds after the last of those the tick callback removes itself
    and the layer holds its position. Waking again picks up the clock where it
    left off rather than snapping, because the alternative is a visible jump
    every time you touch the keyboard after reading something.
    """

    __gtype_name__ = "PrismAmbient"

    def __init__(self, light: Light, opacity: float = 0.94, grain: bool = True):
        super().__init__()
        self.set_can_target(False)          # never in the way of a click
        self._light = light
        self._opacity = opacity
        self._grain = grain
        self._tick = 0
        self._last_frame: float | None = None
        self._last_draw = 0.0
        self._awake_until = 0.0
        light.listen(self)

    @property
    def light(self) -> Light:
        return self._light

    def set_opacity_level(self, opacity: float):
        if abs(opacity - self._opacity) > 0.001:
            self._opacity = opacity
            self.queue_draw()

    def set_grain(self, grain: bool):
        if grain != self._grain:
            self._grain = grain
            self.queue_draw()

    def set_motion(self, motion: bool):
        self._light.motion = motion
        if not motion:
            self._stop()
        else:
            self.wake()
        self.queue_draw()

    # -- the clock ---------------------------------------------------------

    def wake(self):
        self._awake_until = time.monotonic() + AWAKE_SECONDS
        if self._light.motion and self._tick == 0 and self.get_mapped():
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
        self._light.clock += delta

        # The frame clock and time.monotonic() are not the same clock, so the
        # deadline is only ever compared against the one it was set from.
        if time.monotonic() > self._awake_until:
            self._stop()
            self._light.redraw()
            return GLib.SOURCE_REMOVE

        # Thirty frames a second, not sixty: see MOTION_FPS.
        if now - self._last_draw >= 1.0 / MOTION_FPS:
            self._last_draw = now
            self._light.redraw()
        return GLib.SOURCE_CONTINUE

    def do_map(self):
        Gtk.Widget.do_map(self)
        self.wake()

    def do_unmap(self):
        self._stop()
        Gtk.Widget.do_unmap(self)

    def do_unroot(self):
        self._stop()
        self._light.forget(self)
        Gtk.Widget.do_unroot(self)

    # -- drawing -----------------------------------------------------------

    def do_snapshot(self, snapshot):
        width = float(self.get_width())
        height = float(self.get_height())
        if width <= 0 or height <= 0:
            return

        scheme = self._light.scheme
        bounds = rect(0, 0, width, height)
        base = scheme.bg
        alpha = self._opacity

        # The ground. Two stops either side of the background colour rather
        # than the colour itself, so even with every effect off the window has
        # a direction to it.
        snapshot.append_linear_gradient(
            bounds, point(0, 0), point(0, height),
            stops((0.0, rgba(shade(base, 0.05 if scheme.dark else -0.02), alpha)),
                  (1.0, rgba(shade(base, -0.03 if scheme.dark else -0.05), alpha))))

        peak_scale = (0.55 if scheme.dark else 0.34) * alpha
        for cx, cy, rx, ry, colour, strength in self._light.sample(width, height):
            peak = strength * peak_scale
            snapshot.append_radial_gradient(
                bounds, point(cx, cy), rx, ry, 0.0, 1.0,
                stops((0.0, rgba(colour, peak)),
                      (0.45, rgba(colour, peak * 0.42)),
                      (1.0, rgba(colour, 0.0))))

        # A vignette, so the corners settle and the middle is where the eye
        # goes. Light themes get a far weaker one: a grey corner on white
        # looks like dirt rather than depth.
        corner = 0.26 if scheme.dark else 0.10
        snapshot.append_radial_gradient(
            bounds, point(width * 0.5, height * 0.46),
            width * 0.78, height * 0.86, 0.0, 1.0,
            stops((0.55, black(0.0)), (1.0, black(corner * alpha))))

        if self._grain:
            append_grain(snapshot, bounds, 0.018 if scheme.dark else 0.012)


# ----------------------------------------------------------------- slabs ----

class Slab(Gtk.Box):
    """A pane of glass with widgets on it.

    A Gtk.Box rather than a bare Gtk.Widget on purpose: the box already knows
    how to measure, allocate and dispose of its children, and all this needs
    on top of that is to draw the material underneath them. So `do_snapshot`
    paints the glass and then hands over to the inherited implementation,
    which draws the children on top.

    The material is set rather than computed here, because a window changing
    theme has to update every slab at once and the slabs should not each be
    reading the settings.
    """

    __gtype_name__ = "PrismSlab"

    def __init__(self, material: Material, radius=None, specular=True,
                 light: "Light | None" = None, **kwargs):
        kwargs.setdefault("orientation", Gtk.Orientation.VERTICAL)
        super().__init__(**kwargs)
        self._material = material
        self._radius = radius
        self._light = light
        if light is not None:
            light.listen(self)
        self._specular_on = specular
        self._pointer: tuple[float, float] | None = None
        self._pointer_drawn = (0.0, 0.0)
        self._pointer_at = 0.0
        self._flash = 0.0
        self._flash_tick = 0
        self._flash_seconds = 0.28
        self._flash_started = 0.0
        self._focused = True

        if specular:
            # Capture, not bubble: the terminal inside is interested in the
            # same motion events for its selection, and a highlight that
            # stops as soon as the pointer is over the text would be worse
            # than no highlight at all.
            motion = Gtk.EventControllerMotion()
            motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            motion.connect("motion", self._on_motion)
            motion.connect("leave", self._on_leave)
            self.add_controller(motion)

    # -- state -------------------------------------------------------------

    def set_material(self, material: Material):
        self._material = material
        self.queue_draw()

    @property
    def material(self) -> Material:
        return self._material

    def set_light(self, light: "Light | None"):
        if self._light is not None:
            self._light.forget(self)
        self._light = light
        if light is not None:
            light.listen(self)
        self.queue_draw()

    def do_unroot(self):
        if self._light is not None:
            self._light.forget(self)
        Gtk.Box.do_unroot(self)

    def set_focused(self, focused: bool):
        """Which pane the keyboard is talking to, when there is more than one.

        Said with light rather than with a coloured border: the rim of the
        focused pane keeps its full brightness and the others are dimmed and
        pushed back. It is the same thing real glass does when one piece is
        nearer the lamp, so it needs no explaining, and it survives being
        looked at out of the corner of an eye — which a one-pixel accent line
        does not.
        """
        if focused != self._focused:
            self._focused = focused
            self.queue_draw()

    def set_specular(self, enabled: bool):
        self._specular_on = enabled
        if not enabled:
            self._pointer = None
        self.queue_draw()

    def radius(self) -> float:
        return self._material.radius if self._radius is None else self._radius

    def _on_motion(self, _controller, x, y):
        if not self._specular_on:
            return
        self._pointer = (x, y)
        now = time.monotonic()
        moved = math.dist(self._pointer, self._pointer_drawn)
        # Repainting on every motion event would redraw the whole pane at the
        # pointer's report rate, which on this hardware is most of a core. A
        # highlight this soft does not need better than 30fps, and below six
        # pixels there is nothing to see.
        if moved >= 6.0 and now - self._pointer_at >= 1.0 / SPECULAR_FPS:
            self._pointer_at = now
            self._pointer_drawn = self._pointer
            self.queue_draw()

    # -- the bell ----------------------------------------------------------

    def flash(self, seconds: float = 0.28):
        """Light the glass up briefly: the visible form of the bell.

        A rim that brightens and fades rather than the whole pane inverting,
        which is the traditional visual bell and is genuinely unpleasant on a
        large screen. This one is noticeable out of the corner of an eye and
        does not make the text unreadable while it happens.
        """
        self._flash = 1.0
        self._flash_seconds = max(0.05, seconds)
        if self._flash_tick == 0:
            self._flash_started = time.monotonic()
            self._flash_tick = self.add_tick_callback(self._fade_flash)
        else:
            self._flash_started = time.monotonic()
        self.queue_draw()

    def _fade_flash(self, _widget, _clock):
        gone = (time.monotonic() - self._flash_started) / self._flash_seconds
        self._flash = max(0.0, 1.0 - gone)
        self.queue_draw()
        if self._flash <= 0.0:
            self._flash_tick = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _on_leave(self, _controller):
        if self._pointer is not None:
            self._pointer = None
            self.queue_draw()

    # -- drawing -----------------------------------------------------------

    def do_snapshot(self, snapshot):
        width = float(self.get_width())
        height = float(self.get_height())
        if width > 0 and height > 0:
            self.draw_glass(snapshot, width, height)
        Gtk.Widget.do_snapshot(self, snapshot)

    def draw_glass(self, snapshot, width, height):
        material = self._material
        dim = 1.0 if self._focused else 0.32
        radius = self.radius()
        bounds = rect(0, 0, width, height)
        box = rounded(0, 0, width, height, radius)

        # 1. The lift. Drawn first and outside the clip, so it falls on
        #    whatever is behind the slab rather than on the slab itself.
        if material.shadow.alpha > 0.002:
            snapshot.append_outset_shadow(
                box, material.shadow, 0, material.shadow_dy, 0, material.shadow_blur)

        snapshot.push_rounded_clip(box)
        try:
            # 2. The body.
            snapshot.append_linear_gradient(
                bounds, point(0, 0), point(0, height),
                stops((0.0, material.top), (1.0, material.bottom)))

            # 3. The frosting.
            append_grain(snapshot, bounds, material.grain)

            # 4. The window's light, seen through this piece of glass: the
            #    same blobs the ambient layer is drawing, at the same place on
            #    screen, but larger and much fainter. Larger because glass
            #    diffuses what passes through it, and a glow that kept its
            #    edges would look like a sticker on the front of the pane
            #    rather than like something behind it.
            if self._light is not None and material.refraction * dim > 0.004:
                dx, dy, root_w, root_h = window_offset(self)
                for cx, cy, rx, ry, colour, strength in self._light.sample(
                        root_w, root_h, dx, dy):
                    peak = strength * material.refraction * dim
                    snapshot.append_radial_gradient(
                        bounds, point(cx, cy), rx * 1.25, ry * 1.25, 0.0, 1.0,
                        stops((0.0, rgba(colour, peak)),
                              (0.5, rgba(colour, peak * 0.4)),
                              (1.0, rgba(colour, 0.0))))
            elif material.pool * dim > 0.002:
                # No shared light (a slab used on its own): two still pools,
                # so it is never a flat rectangle.
                snapshot.append_radial_gradient(
                    bounds, point(width * 0.18, height * 0.04),
                    width * 0.62, height * 0.75, 0.0, 1.0,
                    stops((0.0, white(material.pool * dim)), (1.0, white(0.0))))
                snapshot.append_radial_gradient(
                    bounds, point(width * 0.92, height * 1.02),
                    width * 0.52, height * 0.6, 0.0, 1.0,
                    stops((0.0, white(material.pool * 0.55 * dim)), (1.0, white(0.0))))

            # 5. The specular response, where the pointer is.
            if self._pointer is not None and material.specular > 0.002:
                px, py = self._pointer
                reach = max(width, height) * 0.42
                snapshot.append_radial_gradient(
                    bounds, point(px, py), reach, reach, 0.0, 1.0,
                    stops((0.0, white(material.specular)),
                          (0.42, white(material.specular * 0.35)),
                          (1.0, white(0.0))))

            # 6. The thickness: the top 22px of the glass seen edge-on, and a
            #    weaker version at the bottom where light bounces back up.
            depth = min(22.0, height * 0.5)
            if material.sheen * dim > 0.002 and depth > 1:
                snapshot.append_linear_gradient(
                    rect(0, 0, width, depth), point(0, 0), point(0, depth),
                    stops((0.0, white(material.sheen * dim)), (1.0, white(0.0))))
            if material.bounce * dim > 0.002 and depth > 1:
                snapshot.append_linear_gradient(
                    rect(0, height - depth, width, depth),
                    point(0, height), point(0, height - depth),
                    stops((0.0, white(material.bounce * dim)), (1.0, white(0.0))))

            # 6a. A pane that does not have the keyboard is pushed back:
            #     it loses the light above, and then this takes it down. The
            #     wash is black rather than the theme's background, which was
            #     the first thing tried and does nothing — the background and
            #     the glass tint are near enough the same colour that washing
            #     one over the other is a no-op. Held at 0.18 so the text in
            #     an unfocused pane still reads at about 10:1.
            if dim < 1.0:
                snapshot.append_color(black(0.26 * (1.0 - dim)), bounds)

            # 6b. The bell.
            if self._flash > 0.004:
                snapshot.append_color(
                    rgba(material.scheme.accent, 0.10 * self._flash), bounds)
        finally:
            snapshot.pop()

        # 7. The rim, drawn after the clip is popped so the hairline lands on
        #    the edge itself and is not half cut away by it.
        def dimmed(colour):
            if dim >= 1.0:
                return colour
            return Gdk.RGBA(red=colour.red, green=colour.green, blue=colour.blue,
                            alpha=colour.alpha * dim)

        snapshot.append_border(box, [1.0, 1.0, 1.0, 1.0],
                              [dimmed(material.rim_top), dimmed(material.rim_side),
                               dimmed(material.rim_bottom), dimmed(material.rim_side)])
        if self._flash > 0.004:
            lit = rgba(material.scheme.accent, 0.75 * self._flash)
            snapshot.append_border(box, [1.5] * 4, [lit] * 4)


class Card(Slab):
    """A panel that floats over the terminal: the palette, the find bar.

    Lifted much further than a pane, and frosted: the `.glass-card` class
    carries the `backdrop-filter` that blurs the terminal behind it, which is
    the one place in this app where a blur has anything to work on. The
    material is built with `frosted=True` to match, so the fill stays thin
    enough to see the blur through. With the blur turned off the card goes
    nearly solid instead and the `Scrim` does the work.
    """

    __gtype_name__ = "PrismCard"

    def __init__(self, material: Material, **kwargs):
        kwargs.setdefault("specular", True)
        super().__init__(material, **kwargs)
        self.add_css_class("glass-card")


class Scrim(Gtk.Widget):
    """The dimming behind a card.

    A plain wash rather than a blur, and a radial one so that it is heaviest
    at the edges of the window and lightest behind the card itself — which
    puts the attention where the card is without hiding what you were looking
    at, since half of using a command palette is reading the screen behind it.
    """

    __gtype_name__ = "PrismScrim"

    def __init__(self, scheme: theming.Theme, strength=0.42):
        super().__init__()
        self.set_can_target(False)
        self._scheme = scheme
        self._strength = strength

    def set_scheme(self, scheme):
        self._scheme = scheme
        self.queue_draw()

    def set_strength(self, strength: float):
        if abs(strength - self._strength) > 0.001:
            self._strength = strength
            self.queue_draw()

    def do_snapshot(self, snapshot):
        width, height = float(self.get_width()), float(self.get_height())
        if width <= 0 or height <= 0:
            return
        wash = black(self._strength) if self._scheme.dark else black(self._strength * 0.55)
        edge = Gdk.RGBA(red=wash.red, green=wash.green, blue=wash.blue,
                        alpha=wash.alpha)
        middle = Gdk.RGBA(red=wash.red, green=wash.green, blue=wash.blue,
                          alpha=wash.alpha * 0.45)
        snapshot.append_radial_gradient(
            rect(0, 0, width, height), point(width * 0.5, height * 0.42),
            width * 0.85, height * 0.95, 0.0, 1.0,
            stops((0.0, middle), (1.0, edge)))
