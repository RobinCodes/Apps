"""The reel: the folder, laid out as a strip of thumbnails.

Drawn as one widget rather than built as one widget per file. A folder of
twenty thousand photographs would be twenty thousand widgets — each with its
own allocation, its own CSS node and its own share of every layout pass — and
GTK would spend longer measuring the strip than decoding the pictures in it.
Drawing it directly means the cost is the number of thumbnails *visible*,
which is about a dozen, however large the folder is.

What the strip shows, beyond the picture:

* a border and a lift on the one being viewed, so the eye can find it;
* a badge and a length on anything with sound or motion in it, because a
  thumbnail of a video otherwise looks exactly like a photograph;
* a bar across the bottom of anything half-watched, from the resume positions
  in `state.json`, which is the fastest way to answer "where was I";
* and the name, under the pointer only — a strip of filenames is a list, and
  a list is not what a strip is for;
* a check on the ones marked, for doing one thing to several: Ctrl+click
  marks or unmarks one, Shift+click every one from the last as well, and a
  right-click offers what can be done to them — or to the one under the
  pointer, when it is not one of them.

Scrolling has momentum and the selection is kept in view by an animation
rather than a jump, for the same reason as everywhere else in this app: a
viewer is looked at for hours, and things that move instantly are tiring.
"""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")

from gi.repository import Gdk, GLib, GObject, Gsk, Gtk  # noqa: E402

from . import glass, metadata, theme as theming  # noqa: E402

GAP = 8.0
PADDING = 10.0

# The same exponential approach the stage uses, for the same reasons.
TAU = 0.09
SETTLED = 0.4


class Filmstrip(Gtk.Widget):
    """A scrollable strip of what is in the folder."""

    __gtype_name__ = "TheEyeFilmstrip"

    __gsignals__ = {
        "chosen": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        # the strip wants a thumbnail it has not been given yet
        "wants": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # the marks changed
        "marks-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # a right-click on a thumbnail: its index, and where
        "menu-wanted": (GObject.SignalFlags.RUN_FIRST, None, (int, float, float)),
    }

    def __init__(self, scheme=None, size=108, vertical=False):
        super().__init__()
        self._scheme = scheme or theming.get(theming.DEFAULT)
        self._size = float(size)
        self._vertical = vertical
        self._items: list = []
        self._selected = -1
        self._offset = 0.0
        self._offset_target = 0.0
        self._tick = 0
        self._last_frame = 0.0
        self._hover = -1
        self._thumbs = None          # a thumbs.Cache, set by the window
        self._progress = None        # a callable: path -> 0..1
        self._durations: dict = {}
        # Marked by path rather than by index, so a re-sort keeps them.
        self._marked: set[str] = set()
        self._anchor = -1
        self.set_hexpand(True)
        self._apply_size()

        click = Gtk.GestureClick.new()
        click.connect("pressed", self._on_press)
        self.add_controller(click)

        menu = Gtk.GestureClick.new()
        menu.set_button(Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._on_menu)
        self.add_controller(menu)

        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag)
        self.add_controller(drag)

    # -- what is in it -----------------------------------------------------

    def _apply_size(self):
        # A thumbnail is at most `size` on its long side, so a cell is square
        # and the picture is centred in it. The alternative — cells as wide as
        # their pictures — makes the strip jitter as thumbnails arrive.
        extent = int(self._size + PADDING * 2)
        if self._vertical:
            self.set_size_request(extent, -1)
        else:
            self.set_size_request(-1, extent)

    def set_scheme(self, scheme):
        self._scheme = scheme
        self.queue_draw()

    def set_size(self, size):
        self._size = float(size)
        self._apply_size()
        self.queue_draw()

    def set_vertical(self, vertical: bool):
        self._vertical = bool(vertical)
        self._apply_size()
        self.queue_draw()

    def set_sources(self, cache, progress_for=None):
        """Where to get thumbnails, and how far through each file one is."""
        self._thumbs = cache
        self._progress = progress_for

    def set_items(self, items, selected=-1):
        self._items = list(items)
        present = {item.path for item in self._items}
        if self._marked - present:
            self._marked &= present
            self.emit("marks-changed")
        self._selected = selected
        self._offset = self._offset_target = 0.0
        self.queue_draw()
        if selected >= 0:
            self.reveal(selected, animate=False)

    def set_selected(self, index: int, animate: bool = True):
        if index == self._selected:
            return
        self._selected = index
        self.reveal(index, animate)
        self.queue_draw()

    # -- marks -------------------------------------------------------------

    def marked(self) -> list[str]:
        """The marked paths, in the strip's order."""
        return [item.path for item in self._items if item.path in self._marked]

    def is_marked(self, index: int) -> bool:
        return 0 <= index < len(self._items) and self._items[index].path in self._marked

    def toggle_mark(self, index: int):
        if not 0 <= index < len(self._items):
            return
        path = self._items[index].path
        if path in self._marked:
            self._marked.discard(path)
        else:
            self._marked.add(path)
        self._anchor = index
        self.queue_draw()
        self.emit("marks-changed")

    def mark_through(self, index: int):
        """Every one from the last marked (or the one on screen) to `index`."""
        if not 0 <= index < len(self._items):
            return
        start = self._anchor if self._anchor >= 0 else max(0, self._selected)
        low, high = sorted((start, index))
        self._marked.update(item.path for item in self._items[low:high + 1])
        self._anchor = index
        self.queue_draw()
        self.emit("marks-changed")

    def clear_marks(self):
        if self._marked:
            self._marked.clear()
            self._anchor = -1
            self.queue_draw()
            self.emit("marks-changed")

    def note_duration(self, path: str, seconds: float):
        """A length discovered elsewhere, so the badge can show it."""
        if seconds > 0:
            self._durations[path] = seconds
            self.queue_draw()

    # -- geometry ----------------------------------------------------------

    def cell(self) -> float:
        return self._size + GAP

    def content_length(self) -> float:
        return max(0.0, len(self._items) * self.cell() - GAP + PADDING * 2)

    def view_length(self) -> float:
        return float(self.get_height() if self._vertical else self.get_width())

    def _max_offset(self) -> float:
        return max(0.0, self.content_length() - self.view_length())

    def index_at(self, x: float, y: float) -> int:
        along = (y if self._vertical else x) + self._offset - PADDING
        if along < 0:
            return -1
        index = int(along // self.cell())
        if index >= len(self._items):
            return -1
        # Inside the cell rather than in the gap after it, so that clicking
        # between two thumbnails selects neither instead of the one before.
        if along - index * self.cell() > self._size:
            return -1
        return index

    def reveal(self, index: int, animate: bool = True):
        """Bring one item into view, centred when there is room to centre it."""
        if index < 0 or not self._items:
            return
        view = self.view_length()
        if view <= 1:
            # Not laid out yet. Remember the intent; do_size_allocate will
            # come back for it.
            self._pending_reveal = index
            return
        middle = PADDING + index * self.cell() + self._size / 2
        self._offset_target = max(0.0, min(self._max_offset(), middle - view / 2))
        if not animate or not self._animations_on():
            self._offset = self._offset_target
            self.queue_draw()
        else:
            self._start()

    _pending_reveal = -1

    def do_size_allocate(self, width, height, baseline):
        Gtk.Widget.do_size_allocate(self, width, height, baseline)
        if self._pending_reveal >= 0:
            index, self._pending_reveal = self._pending_reveal, -1
            self.reveal(index, animate=False)
        else:
            self._offset = min(self._offset, self._max_offset())
            self._offset_target = min(self._offset_target, self._max_offset())

    # -- animation ---------------------------------------------------------

    def _animations_on(self):
        settings = self.get_settings()
        return settings is None or settings.get_property("gtk-enable-animations")

    def _start(self):
        if self._tick == 0 and self.get_mapped():
            self._last_frame = 0.0
            self._tick = self.add_tick_callback(self._advance)

    def _advance(self, _widget, clock):
        now = clock.get_frame_time() / 1e6
        if self._last_frame <= 0:
            self._last_frame = now
        delta = max(0.0, min(0.1, now - self._last_frame))
        self._last_frame = now
        self._offset += (self._offset_target - self._offset) * (
            1.0 - math.exp(-delta / TAU))
        self.queue_draw()
        if abs(self._offset_target - self._offset) < SETTLED:
            self._offset = self._offset_target
            self._tick = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def do_unmap(self):
        if self._tick:
            self.remove_tick_callback(self._tick)
            self._tick = 0
        Gtk.Widget.do_unmap(self)

    # -- input -------------------------------------------------------------

    def _on_press(self, gesture, count, x, y):
        index = self.index_at(x, y)
        if index < 0:
            return
        state = gesture.get_current_event_state()
        if state & Gdk.ModifierType.SHIFT_MASK:
            self.mark_through(index)
        elif state & Gdk.ModifierType.CONTROL_MASK:
            self.toggle_mark(index)
        else:
            self.emit("chosen", index)

    def _on_menu(self, gesture, _count, x, y):
        index = self.index_at(x, y)
        if index >= 0:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.emit("menu-wanted", index, x, y)

    def _on_scroll(self, _controller, dx, dy):
        # Either axis moves the strip, because a mouse has one wheel and a
        # trackpad swiped sideways over a horizontal strip means this.
        step = (dy if self._vertical else (dx or dy)) * self.cell()
        if step == 0:
            return False
        self._offset_target = max(0.0, min(self._max_offset(),
                                           self._offset_target + step))
        self._start()
        return True

    def _on_motion(self, _controller, x, y):
        index = self.index_at(x, y)
        if index != self._hover:
            self._hover = index
            self.queue_draw()

    def _on_leave(self, _controller):
        if self._hover != -1:
            self._hover = -1
            self.queue_draw()

    def _on_drag_begin(self, gesture, x, y):
        self._drag_from = self._offset_target

    def _on_drag(self, gesture, dx, dy):
        delta = dy if self._vertical else dx
        if abs(delta) < 3:
            return
        self._offset_target = max(0.0, min(
            self._max_offset(), getattr(self, "_drag_from", 0.0) - delta))
        self._offset = self._offset_target
        self.queue_draw()

    # -- drawing -----------------------------------------------------------

    def do_snapshot(self, snapshot):
        width, height = float(self.get_width()), float(self.get_height())
        if width <= 1 or height <= 1 or not self._items:
            return
        scheme = self._scheme
        view = self.view_length()
        cell = self.cell()

        # Only what is on screen, plus one either side so that a thumbnail is
        # never seen appearing at the edge.
        first = max(0, int((self._offset - PADDING) // cell) - 1)
        last = min(len(self._items) - 1, int((self._offset + view) // cell) + 1)

        for index in range(first, last + 1):
            item = self._items[index]
            along = PADDING + index * cell - self._offset
            across = (width - self._size) / 2 if self._vertical else PADDING
            x, y = (across, along) if self._vertical else (along, across)
            self._draw_cell(snapshot, item, index, x, y)

        # The edges fade out, so that a thumbnail leaving the strip dissolves
        # rather than being cut in half by the panel's own border.
        self._draw_fades(snapshot, width, height)

    def _draw_cell(self, snapshot, item, index, x, y):
        size = self._size
        scheme = self._scheme
        selected = index == self._selected
        hovered = index == self._hover
        texture = None
        if self._thumbs is not None:
            texture = self._thumbs.get(item.path, int(size),
                                       lambda *_a: self.queue_draw())

        if texture is not None:
            tw, th = texture.get_width(), texture.get_height()
            scale = min(size / max(1, tw), size / max(1, th))
            dw, dh = tw * scale, th * scale
            px, py = x + (size - dw) / 2, y + (size - dh) / 2
            box = glass.rounded(px, py, dw, dh, 6.0)
            if selected or hovered:
                snapshot.append_outset_shadow(
                    box, glass.black(0.55 if selected else 0.35), 0, 2, 0,
                    9 if selected else 5)
            snapshot.push_rounded_clip(box)
            snapshot.append_scaled_texture(texture, Gsk.ScalingFilter.LINEAR,
                                           glass.rect(px, py, dw, dh))
            snapshot.pop()
            frame = (px, py, dw, dh)
        else:
            # A placeholder of the right shape, so the strip does not reflow
            # when the real thumbnail lands.
            inset = size * 0.08
            px, py = x + inset, y + inset
            dw = dh = size - inset * 2
            box = glass.rounded(px, py, dw, dh, 6.0)
            snapshot.push_rounded_clip(box)
            snapshot.append_color(glass.white(0.07) if scheme.dark
                                  else glass.black(0.05),
                                  glass.rect(px, py, dw, dh))
            snapshot.pop()
            frame = (px, py, dw, dh)

        if selected:
            glass.ring(snapshot, glass.rgba(scheme.accent, 1.0),
                       frame[0] - 2, frame[1] - 2, frame[2] + 4, frame[3] + 4,
                       radius=8.0, thickness=2.0)

        if item.path in self._marked:
            self._draw_mark(snapshot, *frame)
        if item.is_playable():
            self._draw_badge(snapshot, item, *frame)
        if self._progress is not None:
            self._draw_progress(snapshot, item, *frame)
        if hovered and not selected:
            self._draw_name(snapshot, item, *frame)

    def _draw_mark(self, snapshot, px, py, dw, dh):
        """A tick in the accent at the top left of a marked thumbnail."""
        size = 18.0
        bx, by = px + 4, py + 4
        box = glass.rounded(bx, by, size, size, size / 2)
        snapshot.append_outset_shadow(box, glass.black(0.4), 0, 1, 0, 4)
        snapshot.push_rounded_clip(box)
        snapshot.append_color(glass.rgba(self._scheme.accent, 1.0),
                              glass.rect(bx, by, size, size))
        snapshot.pop()
        tick = Gsk.PathBuilder.new()
        tick.move_to(bx + 4.8, by + 9.4)
        tick.line_to(bx + 7.8, by + 12.4)
        tick.line_to(bx + 13.4, by + 6.0)
        stroke = Gsk.Stroke.new(2.2)
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        stroke.set_line_join(Gsk.LineJoin.ROUND)
        on_accent = (glass.black(0.85) if theming.luminance(self._scheme.accent) > 0.45
                     else glass.white(0.98))
        snapshot.append_stroke(tick.to_path(), stroke, on_accent)

    def _draw_badge(self, snapshot, item, px, py, dw, dh):
        """A play triangle on a video, and its length when that is known."""
        scheme = self._scheme
        text = ""
        duration = self._durations.get(item.path, 0.0)
        if duration > 0:
            text = metadata.human_time(duration)
        size = 18.0
        bx, by = px + dw - size - 4, py + 4
        box = glass.rounded(bx, by, size, size, size / 2)
        snapshot.push_rounded_clip(box)
        snapshot.append_color(glass.black(0.55), glass.rect(bx, by, size, size))
        snapshot.pop()
        # The triangle, as a path: three lines and a fill, which GSK draws
        # without an offscreen.
        builder = Gsk.PathBuilder.new()
        cx, cy = bx + size / 2, by + size / 2
        builder.move_to(cx - 3.2, cy - 4.4)
        builder.line_to(cx + 4.6, cy)
        builder.line_to(cx - 3.2, cy + 4.4)
        builder.close()
        snapshot.append_fill(builder.to_path(), Gsk.FillRule.WINDING,
                             glass.white(0.95) if item.is_video()
                             else glass.rgba(scheme.accent, 0.95))
        if text:
            layout = self.create_pango_layout(text)
            layout.set_attributes(_small())
            tw, th = layout.get_pixel_size()
            lx, ly = px + dw - tw - 5, py + dh - th - 4
            pill = glass.rounded(lx - 4, ly - 1, tw + 8, th + 2, (th + 2) / 2)
            snapshot.push_rounded_clip(pill)
            snapshot.append_color(glass.black(0.58),
                                  glass.rect(lx - 4, ly - 1, tw + 8, th + 2))
            snapshot.pop()
            snapshot.save()
            snapshot.translate(glass.point(lx, ly))
            snapshot.append_layout(layout, glass.white(0.95))
            snapshot.restore()

    def _draw_progress(self, snapshot, item, px, py, dw, dh):
        fraction = self._progress(item.path)
        if not fraction or fraction <= 0.001:
            return
        height = 3.0
        y = py + dh - height
        snapshot.append_color(glass.black(0.45), glass.rect(px, y, dw, height))
        snapshot.append_color(glass.rgba(self._scheme.accent, 0.95),
                              glass.rect(px, y, dw * min(1.0, fraction), height))

    def _draw_name(self, snapshot, item, px, py, dw, dh):
        layout = self.create_pango_layout(item.name)
        layout.set_attributes(_small())
        layout.set_width(int(dw * 1024))
        layout.set_ellipsize(3)
        tw, th = layout.get_pixel_size()
        y = py + dh - th - 3
        snapshot.append_color(glass.black(0.6), glass.rect(px, y - 2, dw, th + 4))
        snapshot.save()
        snapshot.translate(glass.point(px + 2, y))
        snapshot.append_layout(layout, glass.white(0.95))
        snapshot.restore()

    def _draw_fades(self, snapshot, width, height):
        """A short gradient to transparent at each end of the strip."""
        length = 26.0
        if self._offset > 1:
            if self._vertical:
                snapshot.append_linear_gradient(
                    glass.rect(0, 0, width, length),
                    glass.point(0, 0), glass.point(0, length),
                    glass.stops((0.0, glass.rgba(self._scheme.glass, 0.85)),
                                (1.0, glass.rgba(self._scheme.glass, 0.0))))
            else:
                snapshot.append_linear_gradient(
                    glass.rect(0, 0, length, height),
                    glass.point(0, 0), glass.point(length, 0),
                    glass.stops((0.0, glass.rgba(self._scheme.glass, 0.85)),
                                (1.0, glass.rgba(self._scheme.glass, 0.0))))
        if self._offset < self._max_offset() - 1:
            if self._vertical:
                snapshot.append_linear_gradient(
                    glass.rect(0, height - length, width, length),
                    glass.point(0, height), glass.point(0, height - length),
                    glass.stops((0.0, glass.rgba(self._scheme.glass, 0.85)),
                                (1.0, glass.rgba(self._scheme.glass, 0.0))))
            else:
                snapshot.append_linear_gradient(
                    glass.rect(width - length, 0, length, height),
                    glass.point(width, 0), glass.point(width - length, 0),
                    glass.stops((0.0, glass.rgba(self._scheme.glass, 0.85)),
                                (1.0, glass.rgba(self._scheme.glass, 0.0))))


_small_attrs = None


def _small():
    """Pango attributes for the badges — built once, they are immutable."""
    global _small_attrs
    if _small_attrs is None:
        from gi.repository import Pango

        _small_attrs = Pango.AttrList()
        _small_attrs.insert(Pango.attr_scale_new(0.78))
    return _small_attrs
