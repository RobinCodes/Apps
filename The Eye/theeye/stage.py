"""The stage: the surface a picture is actually seen on.

One widget for stills and for video, because by the time anything reaches
here it is a `GdkPaintable` and the difference has stopped mattering. That is
what makes the grade, the zoom, the fit modes, the rotation and the
before-and-after split work identically on a photograph and on a frame of
film, rather than being implemented twice and behaving differently.

What it does:

* fits the picture to the window, or to its width, or shows it at its own
  size, and lets it be zoomed and dragged around;
* keeps the point under the pointer under the pointer while zooming, which is
  the difference between a zoom that feels like a tool and one that feels
  like a lottery;
* animates every change of zoom and position, with a time constant rather
  than a duration, so that a second gesture arriving mid-animation is
  absorbed instead of fighting it;
* draws the grade from `effects.py` over whatever is on screen;
* crossfades between pictures, so moving through a folder does not flash;
* and draws the before-and-after divider the editor uses.

Nothing here knows what a file is, which folder it came from or what plays
next. `window.py` hands it a paintable and a grade; everything else is this
module's own business.

The smoothing deserves a note. Every animated value moves towards its target
by a fixed *proportion of the remaining distance per second* — an exponential
approach — rather than along a fixed-length curve. Two consequences, both
wanted: an interruption is seamless, because there is no curve to restart,
and the motion is identical at 60Hz and at 144Hz because the proportion is
computed from the real frame interval. The cost is that it never mathematically
arrives, so there is a threshold below which it snaps and stops.
"""

from __future__ import annotations

import math
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")

from gi.repository import Gdk, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from . import effects, glass, theme as theming  # noqa: E402
from .grade import Grade  # noqa: E402

FITS = ("window", "width", "height", "fill", "actual")

# How far a zoom can go. The lower bound is "too small to see"; the upper is
# where a single image pixel is a 32-pixel block, which is as far into a
# photograph as anybody has ever needed to go and twice as far as is useful.
MIN_ZOOM = 0.02
MAX_ZOOM = 32.0

# The time constant of every animation here: the number of seconds it takes to
# cover about two thirds of the remaining distance. Small enough to feel
# immediate, large enough to read as motion rather than as a jump.
TAU = 0.075

# Below this, an animation has arrived.
SETTLED = 0.0015

# How long a crossfade between two pictures lasts. Long enough to be seen,
# short enough that holding an arrow key still feels like flipping through.
FADE_SECONDS = 0.16


class Stage(Gtk.Widget):
    """The picture, and everything about how it is shown."""

    __gtype_name__ = "TheEyeStage"

    __gsignals__ = {
        # the user zoomed or panned, so the chrome can show the figure
        "view-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # a plain click, which on a video means play or pause
        "activated": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # a scroll or a swipe that means "the next picture" / "the previous"
        "stepped": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        # a scroll that means something to the player: seconds, or volume
        "scrubbed": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
        "volume-rolled": (GObject.SignalFlags.RUN_FIRST, None, (float,)),
    }

    def __init__(self, scheme=None, config=None):
        super().__init__()
        self.set_focusable(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self._scheme = scheme or theming.get(theming.DEFAULT)
        self._config = config or {}

        self._paintable: Gdk.Paintable | None = None
        self._grade = Grade()
        self._fit = "window"
        self._zoom = 1.0            # the live value, which is what is drawn
        self._zoom_target = 1.0
        self._focus = [0.5, 0.5]    # image-space fraction at the widget centre
        self._focus_target = [0.5, 0.5]
        self._tick = 0
        self._last_frame = 0.0
        self._playing = False
        self._variant = 0
        self._compare: float | None = None
        self._dragging = False
        self._drag_from = (0.5, 0.5)

        self._fade_from: Gdk.Paintable | None = None
        self._fade_started = 0.0
        self._fade_zoom = 1.0
        self._fade_focus = [0.5, 0.5]

        self._handler = 0
        self._install_gestures()

    # -- content ----------------------------------------------------------

    def set_scheme(self, scheme):
        self._scheme = scheme
        self.queue_draw()

    def set_config(self, config):
        self._config = config
        self.queue_draw()

    def set_content(self, paintable: Gdk.Paintable | None, fade: bool = True):
        """Show a different picture.

        The outgoing one is kept for the length of the crossfade, at the zoom
        and position it was being shown at — not at the new one's, which would
        make the old picture jump as it faded.
        """
        if paintable is self._paintable:
            return
        if self._handler and self._paintable is not None:
            self._paintable.disconnect(self._handler)
            self._handler = 0

        animate = fade and self._paintable is not None and self.get_mapped()
        if animate:
            self._fade_from = self._paintable
            self._fade_started = time.monotonic()
            self._fade_zoom = self._zoom
            self._fade_focus = list(self._focus)
        else:
            self._fade_from = None

        self._paintable = paintable
        if paintable is not None:
            # A video frame arrives sixty times a second and a GIF rather less
            # often; either way the widget has to be told.
            self._handler = paintable.connect("invalidate-contents",
                                              lambda *_: self.queue_draw())
        self._variant = 0
        self.reset_view(animate=False)
        self.queue_draw()

    def content(self):
        return self._paintable

    def set_grade(self, settings: Grade):
        self._grade = settings
        self.queue_draw()

    def grade(self) -> Grade:
        return self._grade

    def set_playing(self, playing: bool):
        """Whether the picture is moving, which the grain needs to know.

        Film grain is a different crystal on every frame. Holding one pattern
        still over moving pictures looks like dirt on the lens, so the variant
        is cycled while something is playing and frozen while it is not —
        frozen matters just as much, because grain that crawls on a still
        photograph is worse than grain that does not move on a film.
        """
        self._playing = playing

    def set_compare(self, position: float | None):
        """Show the ungraded picture left of `position`, 0..1 — or nothing."""
        self._compare = position
        self.queue_draw()

    def compare(self):
        return self._compare

    # -- geometry ---------------------------------------------------------

    def natural_size(self) -> tuple[float, float]:
        """The picture's own size, with the grade's rotation applied."""
        if self._paintable is None:
            return (0.0, 0.0)
        width = float(self._paintable.get_intrinsic_width())
        height = float(self._paintable.get_intrinsic_height())
        if width <= 0 or height <= 0:
            aspect = self._paintable.get_intrinsic_aspect_ratio()
            if aspect > 0:
                width, height = aspect * 1000.0, 1000.0
            else:
                return (0.0, 0.0)
        return self._grade.oriented(width, height)

    def fit_scale(self, mode: str | None = None) -> float:
        """The scale a fit mode asks for, at the current window size."""
        width, height = self.natural_size()
        view_w, view_h = float(self.get_width()), float(self.get_height())
        if width <= 0 or height <= 0 or view_w <= 0 or view_h <= 0:
            return 1.0
        mode = mode or self._fit
        if mode == "actual":
            return 1.0
        if mode == "width":
            return view_w / width
        if mode == "height":
            return view_h / height
        if mode == "fill":
            return max(view_w / width, view_h / height)
        # "window", and anything unrecognised: fit inside, and never enlarge
        # a picture smaller than the window — blowing a thumbnail up to fill a
        # 4K display is not what "fit" means to anybody.
        return min(1.0, min(view_w / width, view_h / height))

    def scale(self) -> float:
        return self._zoom

    def set_fit(self, mode: str, animate: bool = True):
        if mode not in FITS:
            mode = "window"
        self._fit = mode
        self._zoom_target = self.fit_scale(mode)
        self._focus_target = [0.5, 0.5]
        self._begin(animate)
        self.emit("view-changed")

    def fit(self) -> str:
        return self._fit

    def reset_view(self, animate: bool = True):
        self._zoom_target = self.fit_scale()
        self._focus_target = [0.5, 0.5]
        if not animate:
            self._zoom = self._zoom_target
            self._focus = list(self._focus_target)
        self._begin(animate)
        self.emit("view-changed")

    def is_fitted(self) -> bool:
        """Is the picture at the size the fit mode asks for?"""
        return abs(self._zoom_target - self.fit_scale()) < 0.001

    def zoom_to(self, scale: float, at=None, animate: bool = True):
        """Zoom to `scale`, optionally keeping the point `at` where it is.

        `at` is in widget coordinates — the pointer, usually. Keeping the
        image point under it fixed is one line of algebra and the whole
        difference between a usable zoom and an infuriating one.
        """
        width, height = self.natural_size()
        if width <= 0 or height <= 0:
            return
        scale = max(MIN_ZOOM, min(MAX_ZOOM, scale))
        if at is not None:
            before = self._image_at(at, self._zoom_target, self._focus_target)
            self._zoom_target = scale
            after = self._image_at(at, self._zoom_target, self._focus_target)
            self._focus_target = [
                _clamp01(self._focus_target[0] + (before[0] - after[0])),
                _clamp01(self._focus_target[1] + (before[1] - after[1])),
            ]
        else:
            self._zoom_target = scale
        self._begin(animate)
        self.emit("view-changed")

    def zoom_by(self, factor: float, at=None, animate: bool = True):
        self.zoom_to(self._zoom_target * factor, at, animate)

    def pan_by(self, dx: float, dy: float, animate: bool = True):
        """Move the picture by a number of widget pixels."""
        width, height = self.natural_size()
        if width <= 0 or height <= 0 or self._zoom_target <= 0:
            return
        self._focus_target = [
            _clamp01(self._focus_target[0] - dx / (width * self._zoom_target)),
            _clamp01(self._focus_target[1] - dy / (height * self._zoom_target)),
        ]
        self._begin(animate)
        self.emit("view-changed")

    def _image_at(self, position, zoom, focus) -> tuple[float, float]:
        """Which point of the picture, in 0..1, is under a widget point."""
        width, height = self.natural_size()
        view_w, view_h = float(self.get_width()), float(self.get_height())
        drawn_w, drawn_h = width * zoom, height * zoom
        left, top = self._origin(drawn_w, drawn_h, view_w, view_h, focus)
        if drawn_w <= 0 or drawn_h <= 0:
            return (0.5, 0.5)
        return ((position[0] - left) / drawn_w, (position[1] - top) / drawn_h)

    def _origin(self, drawn_w, drawn_h, view_w, view_h, focus):
        """Top-left of the drawn picture, in widget coordinates.

        An axis on which the picture is smaller than the window is centred and
        the focus ignored. Honouring the focus there is how a picture ends up
        stuck against one edge with a gap on the other, which looks broken
        even though every number involved is correct.
        """
        if drawn_w <= view_w:
            left = (view_w - drawn_w) / 2
        else:
            left = view_w / 2 - focus[0] * drawn_w
            left = min(0.0, max(view_w - drawn_w, left))
        if drawn_h <= view_h:
            top = (view_h - drawn_h) / 2
        else:
            top = view_h / 2 - focus[1] * drawn_h
            top = min(0.0, max(view_h - drawn_h, top))
        return left, top

    def picture_rect(self) -> Graphene.Rect:
        """Where the picture is on screen now — what a click is tested against."""
        width, height = self.natural_size()
        view_w, view_h = float(self.get_width()), float(self.get_height())
        drawn_w, drawn_h = width * self._zoom, height * self._zoom
        left, top = self._origin(drawn_w, drawn_h, view_w, view_h, self._focus)
        return glass.rect(left, top, drawn_w, drawn_h)

    # -- the animation ----------------------------------------------------

    def _begin(self, animate: bool):
        if not animate or not self.get_mapped() or not self._animations_on():
            self._zoom = self._zoom_target
            self._focus = list(self._focus_target)
            self.queue_draw()
            return
        if self._tick == 0:
            self._last_frame = 0.0
            self._tick = self.add_tick_callback(self._advance)

    def _animations_on(self) -> bool:
        settings = self.get_settings()
        return settings is None or settings.get_property("gtk-enable-animations")

    def _advance(self, _widget, clock):
        now = clock.get_frame_time() / 1e6
        if self._last_frame <= 0:
            self._last_frame = now
        delta = max(0.0, min(0.1, now - self._last_frame))
        self._last_frame = now

        # Exponential approach, framed in terms of the real interval so that
        # the motion is the same on any display.
        step = 1.0 - math.exp(-delta / TAU) if delta > 0 else 0.0

        # Zoom is interpolated in log space. Linear interpolation from 1x to
        # 8x spends most of its time near 8x and arrives in a rush; in log
        # space each frame is the same *ratio*, which is how a zoom is
        # perceived and what makes it look smooth.
        if self._zoom > 0 and self._zoom_target > 0:
            moved = math.log(self._zoom) + (
                math.log(self._zoom_target) - math.log(self._zoom)) * step
            self._zoom = math.exp(moved)
        for axis in (0, 1):
            self._focus[axis] += (self._focus_target[axis]
                                  - self._focus[axis]) * step

        settled = (abs(math.log(max(self._zoom, 1e-6))
                       - math.log(max(self._zoom_target, 1e-6))) < SETTLED
                   and all(abs(self._focus[a] - self._focus_target[a]) < SETTLED
                           for a in (0, 1)))
        fading = self._fade_from is not None

        self.queue_draw()
        if settled and not fading:
            self._zoom = self._zoom_target
            self._focus = list(self._focus_target)
            self._tick = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def do_unmap(self):
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0
        Gtk.Widget.do_unmap(self)

    # -- gestures ---------------------------------------------------------

    def _install_gestures(self):
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.BOTH_AXES
            | Gtk.EventControllerScrollFlags.DISCRETE)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        # The middle button drags too, which is the convention everywhere that
        # the left button might be doing something else.
        middle = Gtk.GestureDrag.new()
        middle.set_button(Gdk.BUTTON_MIDDLE)
        middle.connect("drag-begin", self._on_drag_begin)
        middle.connect("drag-update", self._on_drag_update)
        middle.connect("drag-end", self._on_drag_end)
        self.add_controller(middle)

        zoom = Gtk.GestureZoom.new()
        zoom.connect("begin", self._on_pinch_begin)
        zoom.connect("scale-changed", self._on_pinch)
        self.add_controller(zoom)

        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_click)
        self.add_controller(click)

    def _modifier(self, controller) -> Gdk.ModifierType:
        event = controller.get_current_event()
        return event.get_modifier_state() if event is not None else 0

    def _on_scroll(self, controller, dx, dy):
        state = self._modifier(controller)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        step = dy or dx
        if step == 0:
            return False

        if shift:
            # Through the folder, which is what a trackpad's horizontal swipe
            # does as well.
            self.emit("stepped", 1 if step > 0 else -1)
            return True
        if ctrl and self._playing:
            self.emit("volume-rolled", -0.05 if step > 0 else 0.05)
            return True
        if self._playing or self._is_video():
            # A video scrolls through time. A photograph has no time to scroll
            # through, so it zooms instead — the two are different enough
            # jobs that giving them the same gesture would serve neither.
            self.emit("scrubbed", -5.0 if step > 0 else 5.0)
            return True

        factor = float(self._config.get("zoom_steps", 1.25))
        pointer = self._pointer(controller)
        self.zoom_by(1 / factor if step > 0 else factor, at=pointer)
        if self._fit != "free":
            self._fit = "free"
        return True

    def _pointer(self, controller):
        """Where the pointer is, in this widget's coordinates, or None.

        The event carries its position in the surface's coordinates, so it has
        to be brought into ours — a stage inset from the window edge would
        otherwise zoom towards a point some way off from the pointer, which is
        subtle enough to be maddening.
        """
        event = controller.get_current_event()
        if event is None:
            return None
        position = event.get_position()
        if position is None:
            return None
        found, x, y = position
        if not found:
            return None
        root = self.get_root()
        if root is None:
            return (x, y)
        ok, local_x, local_y = root.translate_coordinates(self, x, y)
        return (local_x, local_y) if ok else (x, y)

    def _is_video(self):
        return hasattr(self._paintable, "aspect")

    def _on_drag_begin(self, gesture, x, y):
        self._dragging = True
        self._drag_from = tuple(self._focus_target)
        self.set_cursor(Gdk.Cursor.new_from_name("grabbing", None))

    def _on_drag_update(self, gesture, dx, dy):
        width, height = self.natural_size()
        if width <= 0 or self._zoom_target <= 0:
            return
        drawn_w, drawn_h = width * self._zoom_target, height * self._zoom_target
        if drawn_w <= self.get_width() and drawn_h <= self.get_height():
            return                      # nothing to drag; it all fits
        # Straight to the live value as well as the target: a drag that lags
        # behind the finger is a drag that feels broken, however smooth it is.
        self._focus_target = [
            _clamp01(self._drag_from[0] - dx / drawn_w),
            _clamp01(self._drag_from[1] - dy / drawn_h),
        ]
        self._focus = list(self._focus_target)
        self.queue_draw()
        self.emit("view-changed")

    def _on_drag_end(self, gesture, dx, dy):
        self._dragging = False
        self.set_cursor(None)

    def _on_pinch_begin(self, gesture, sequence):
        self._pinch_from = self._zoom_target

    def _on_pinch(self, gesture, scale):
        if scale <= 0:
            return
        ok, x, y = gesture.get_bounding_box_center()
        self.zoom_to(getattr(self, "_pinch_from", self._zoom) * scale,
                     at=(x, y) if ok else None, animate=False)
        self._fit = "free"

    def _on_click(self, gesture, count, x, y):
        self.grab_focus()
        if count == 2:
            # Between fitting the window and actual size, which is the pair
            # worth toggling: everything else is a deliberate choice.
            self.set_fit("actual" if self.is_fitted() and self._fit != "actual"
                         else "window")
            return
        if count == 1:
            self.emit("activated")

    # -- drawing ----------------------------------------------------------

    def do_snapshot(self, snapshot):
        width, height = float(self.get_width()), float(self.get_height())
        if width <= 0 or height <= 0:
            return
        bounds = glass.rect(0, 0, width, height)

        if self._config.get("background_matte", True):
            snapshot.append_color(glass.rgba(self._scheme.matte, 1.0), bounds)

        if self._paintable is None:
            return

        fade = self._fade_progress()
        if fade is not None and self._fade_from is not None:
            # The outgoing picture, at the geometry it had, under the
            # incoming one. Both are drawn graded: fading from an ungraded
            # picture to a graded one would read as the grade snapping on.
            self._draw_picture(snapshot, self._fade_from, 1.0 - fade,
                               self._fade_zoom, self._fade_focus)
        self._draw_picture(snapshot, self._paintable,
                           1.0 if fade is None else fade,
                           self._zoom, self._focus)

        if self._compare is not None:
            self._draw_divider(snapshot, width, height)

    def _fade_progress(self):
        if self._fade_from is None:
            return None
        elapsed = time.monotonic() - self._fade_started
        if elapsed >= FADE_SECONDS or not self._animations_on():
            self._fade_from = None
            return None
        self._begin(True)
        return max(0.0, min(1.0, elapsed / FADE_SECONDS))

    def _draw_picture(self, snapshot, paintable, alpha, zoom, focus):
        width, height = self._paintable_size(paintable)
        if width <= 0 or height <= 0:
            return
        view_w, view_h = float(self.get_width()), float(self.get_height())
        rotation = int(self._grade["rotation"]) % 360
        oriented_w, oriented_h = ((height, width) if rotation % 180
                                  else (width, height))
        drawn_w, drawn_h = oriented_w * zoom, oriented_h * zoom
        left, top = self._origin(drawn_w, drawn_h, view_w, view_h, focus)

        if alpha < 0.999:
            snapshot.push_opacity(alpha)

        if self._config.get("checkerboard", False):
            self._draw_checker(snapshot, left, top, drawn_w, drawn_h)

        # The picture itself, drawn into a node of its own so that the grade
        # can use it several times over without decoding or uploading it
        # again, and so that the rotation is a transform on the node rather
        # than something every stage has to know about.
        inner = Gtk.Snapshot()
        paintable.snapshot(inner, width * zoom, height * zoom)
        node = inner.to_node()
        if node is None:
            if alpha < 0.999:
                snapshot.pop()
            return

        if self._playing:
            self._variant = (self._variant + 1) % 997

        graded = effects.apply_grade(
            node, glass.rect(0, 0, width * zoom, height * zoom), self._grade,
            self._config.get("effect_quality", "high"), self._variant)
        plain = node if self._compare is not None else None

        snapshot.save()
        snapshot.translate(glass.point(left + drawn_w / 2, top + drawn_h / 2))
        if rotation:
            snapshot.rotate(rotation)
        if self._grade["flip_h"] or self._grade["flip_v"]:
            snapshot.scale(-1.0 if self._grade["flip_h"] else 1.0,
                           -1.0 if self._grade["flip_v"] else 1.0)
        snapshot.translate(glass.point(-width * zoom / 2, -height * zoom / 2))

        radius = float(self._config.get("corner_radius", 0.0))
        if radius > 0.5:
            box = glass.rounded(0, 0, width * zoom, height * zoom,
                                min(radius, min(drawn_w, drawn_h) / 2))
            snapshot.push_rounded_clip(box)

        if plain is not None:
            # Before and after, split at the divider. The ungraded half is
            # clipped rather than drawn smaller, so the two halves line up
            # exactly and the join is invisible except for the line.
            split = max(0.0, min(1.0, self._compare)) * width * zoom
            snapshot.push_clip(glass.rect(0, 0, split, height * zoom))
            snapshot.append_node(plain)
            snapshot.pop()
            snapshot.push_clip(glass.rect(split, 0, width * zoom - split,
                                          height * zoom))
            snapshot.append_node(graded)
            snapshot.pop()
        else:
            snapshot.append_node(graded)

        if radius > 0.5:
            snapshot.pop()
        snapshot.restore()
        if alpha < 0.999:
            snapshot.pop()

    def _paintable_size(self, paintable) -> tuple[float, float]:
        width = float(paintable.get_intrinsic_width())
        height = float(paintable.get_intrinsic_height())
        if width > 0 and height > 0:
            return width, height
        aspect = paintable.get_intrinsic_aspect_ratio()
        if aspect > 0:
            return aspect * 1000.0, 1000.0
        return 0.0, 0.0

    def _draw_checker(self, snapshot, x, y, width, height):
        """The transparency chequerboard, for a picture with holes in it.

        Two squares and a repeat node, which GSK draws as one tiled texture —
        the obvious loop over every square would be thousands of nodes on a
        large picture.
        """
        size = 12.0
        light = glass.white(0.14) if self._scheme.dark else glass.black(0.07)
        tile = Gtk.Snapshot()
        tile.append_color(light, glass.rect(0, 0, size, size))
        tile.append_color(light, glass.rect(size, size, size, size))
        node = tile.to_node()
        if node is None:
            return
        snapshot.append_node(Gsk.RepeatNode.new(
            glass.rect(x, y, width, height), node,
            glass.rect(x, y, size * 2, size * 2)))

    def _draw_divider(self, snapshot, width, height):
        """The before-and-after line, with a grip in the middle of it."""
        picture = self.picture_rect()
        if picture.size.width <= 0:
            return
        x = picture.origin.x + picture.size.width * max(0.0, min(1.0, self._compare))
        top, bottom = picture.origin.y, picture.origin.y + picture.size.height
        snapshot.append_color(glass.white(0.85), glass.rect(x - 1, top, 2, bottom - top))
        mid = (top + bottom) / 2
        grip = glass.rounded(x - 13, mid - 20, 26, 40, 13)
        snapshot.append_outset_shadow(grip, glass.black(0.35), 0, 2, 0, 8)
        snapshot.push_rounded_clip(grip)
        snapshot.append_color(glass.white(0.92), glass.rect(x - 13, mid - 20, 26, 40))
        snapshot.pop()
        for offset in (-4, 0, 4):
            snapshot.append_color(glass.black(0.35),
                                  glass.rect(x + offset - 0.5, mid - 7, 1, 14))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
