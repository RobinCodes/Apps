"""The two pieces of the look that CSS cannot do.

*Clipping.* GTK's CSS has no `overflow`, so a rounded container does not
round what its children paint: a selected row at the bottom of the list
squares off the corner of the window, and the whole illusion of one slab of
glass goes with it. `Slab` rounds its children by pushing a rounded clip in
its own snapshot, which is both the correct way and a single GSK node.

*The highlight that follows the pointer.* Real glass has a bright spot where
the light is, and it moves when you move. `Sheen` paints one very faint
radial gradient under everything else and follows the pointer, which is what
makes the surface read as a material rather than as a flat translucent
rectangle. It is one gradient node, redrawn only when the pointer moves, and
it switches itself off while the window has no focus — a highlight that keeps
animating in a window nobody is using is just a waste of a GPU.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402


class Slab(Gtk.Box):
    """The window's one pane of glass: rounded, and rounded all the way down.

    The radius itself lives in the stylesheet, which is also what paints the
    background, so it is set in one place. What this adds is the clip:
    `Gtk.Overflow.HIDDEN` honours the CSS corner radius, so a selected row at
    the bottom of the list stops squaring off the corner.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.add_css_class("slab")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self._aurora = None

    def set_light(self, scheme, translucency: float = 1.0,
                  enabled: bool = True) -> None:
        """Which theme's light to draw behind the cards, or none at all.

        The texture comes from `housestyle.aurora_texture`, which is the
        same light every other app here draws, and the reason it is a
        texture rather than the gradients it used to be is in that
        function's docstring: five gradients in a CSS `background-image`
        are five full-slab blends on every repaint.
        """
        from . import housestyle

        self._aurora = (housestyle.aurora_texture(scheme, translucency)
                        if enabled else None)
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        """The light first, then the cards laid on it.

        GTK has drawn the CSS background and the rim by the time this runs,
        so the texture lands on the slab's own tint and under every card —
        which is where light coming through glass belongs.
        """
        texture = self._aurora
        if texture is not None:
            width, height = self.get_width(), self.get_height()
            if width > 0 and height > 0:
                snapshot.append_scaled_texture(
                    texture, Gsk.ScalingFilter.LINEAR,
                    Graphene.Rect().init(0, 0, width, height))
        Gtk.Box.do_snapshot(self, snapshot)

    def set_square(self, square: bool) -> None:
        """Corners off, for a window that covers the whole screen.

        A rounded corner against the edge of the display is a transparent
        notch showing the wallpaper, which reads as a bug rather than a style.
        Tiled in a column it reads as intended, so only the maximised and
        fullscreen cases square themselves off.
        """
        if square:
            self.add_css_class("squared")
        else:
            self.remove_css_class("squared")


class Sheen(Gtk.Widget):
    """A soft highlight under the content, where the pointer is.

    Sized to the widget it is overlaid on and drawn with one radial gradient.
    `strength` is deliberately tiny: this is meant to be noticed only when it
    is taken away.
    """

    def __init__(self, strength: float = 0.055, radius: int = 420):
        super().__init__()
        self._strength = strength
        self._radius = radius
        self._x = -1.0
        self._y = -1.0
        self._active = True
        self.set_can_target(False)         # never eats a click
        self.set_can_focus(False)

    def set_strength(self, strength: float) -> None:
        self._strength = max(0.0, min(0.5, strength))
        self.queue_draw()

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.queue_draw()

    def track(self, widget: Gtk.Widget) -> None:
        """Follow the pointer over `widget` — normally the window.

        The controller goes on the other widget rather than this one, because
        this one cannot be a target: it has to be transparent to clicks, and a
        widget that cannot be targeted gets no motion events of its own.
        """
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        widget.add_controller(motion)
        self._source = widget

    def _on_motion(self, controller, x: float, y: float) -> None:
        widget = controller.get_widget()
        point = Graphene.Point().init(x, y)
        ok, local = widget.compute_point(self, point)
        if not ok:
            return
        # A pixel of pointer movement is not worth a frame; a dozen is.
        if abs(local.x - self._x) < 8 and abs(local.y - self._y) < 8:
            return
        self._x, self._y = local.x, local.y
        self.queue_draw()

    def _on_leave(self, _controller) -> None:
        self._x = self._y = -1.0
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if not self._active or self._strength <= 0 or self._x < 0:
            return
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            return
        radius = float(self._radius)
        bounds = Graphene.Rect().init(self._x - radius, self._y - radius,
                                      radius * 2, radius * 2)
        centre = Graphene.Point().init(self._x, self._y)
        bright = Gdk.RGBA()
        bright.red = bright.green = bright.blue = 1.0
        bright.alpha = self._strength
        clear = Gdk.RGBA()
        clear.red = clear.green = clear.blue = 1.0
        clear.alpha = 0.0
        snapshot.append_radial_gradient(
            bounds, centre, radius, radius, 0.0, 1.0,
            [_stop(0.0, bright),
             _stop(0.55, _fade(bright, 0.35)),
             _stop(1.0, clear)])


def _stop(offset: float, colour: Gdk.RGBA) -> Gsk.ColorStop:
    """One gradient stop.

    Built field by field rather than as `Gsk.ColorStop(offset=…, color=…)`:
    it is a plain C struct with no constructor, so PyGObject takes no
    keywords for it and the keyword form raises a TypeError — inside a
    `do_snapshot`, which swallows it into a warning on stderr and draws
    nothing. The highlight was silently absent for exactly that reason.
    """
    stop = Gsk.ColorStop()
    stop.offset = offset
    stop.color = colour
    return stop


def _fade(colour: Gdk.RGBA, factor: float) -> Gdk.RGBA:
    out = Gdk.RGBA()
    out.red, out.green, out.blue = colour.red, colour.green, colour.blue
    out.alpha = colour.alpha * factor
    return out


def rounded_clip(snapshot: Gtk.Snapshot, width: float, height: float,
                 radius: float) -> None:
    """Push a rounded clip of this size. For the other widgets that need one."""
    bounds = Graphene.Rect().init(0, 0, width, height)
    rounded = Gsk.RoundedRect()
    rounded.init_from_rect(bounds, radius)
    snapshot.push_rounded_clip(rounded)


def tint(hex_colour: str, alpha: float) -> Gdk.RGBA:
    """A Gdk.RGBA from `#rrggbb` and an alpha, for the widgets that draw."""
    colour = Gdk.RGBA()
    if not colour.parse(hex_colour):
        colour.parse("#808080")
    colour.alpha = alpha
    return colour
