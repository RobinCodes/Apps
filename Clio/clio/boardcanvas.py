"""The board: the axis, the events hanging above it, the `}` braces below.

Drawn in one pass with cairo rather than built from widgets, for the same reason
as Ariadne's canvas: a board of three hundred events is then three hundred marks
and a few dozen pieces of text, of which only what is on screen is ever laid out,
and a zoom is one multiplication. Where things go is decided in layout.py; this
paints it and answers the hands.

What you can do, and how:

* **drag** the board to move along it (and up and down, when the braces hang off
  the bottom); **scroll** does the same; **Ctrl+scroll** or a pinch zooms, at the
  pointer, smoothly;
* **click** something to select it, **Shift**- or **Ctrl**-click to add to the
  selection, **Shift+drag** to sweep up everything across a stretch;
* **double-click** an empty place to add an event at that date, as precise as the
  zoom can tell; double-click anything to edit it;
* **Delete** takes away what is selected, **Escape** lets go of it, and the arrow
  keys move along the axis.

The canvas changes nothing itself. It tells its owner what happened through the
callbacks it was given, and the owner saves.
"""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, Graphene, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import housestyle, layout  # noqa: E402
from .axis import MAX_PPD, MIN_PPD, YEAR, Axis, age_at  # noqa: E402
from .timelines import When, today_days  # noqa: E402

FLIGHT_US = 240_000       # how long a jump or a fit takes to arrive
ZOOM_EASE = 0.32          # how much of the remaining zoom each frame does
EDGE = 40.0               # how far past the screen's edge anything is actually drawn


def _rgba(colour: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    parsed = Gdk.RGBA()
    if not parsed.parse(colour):
        parsed.parse("#7f7f7f")
    return parsed.red, parsed.green, parsed.blue, alpha


def describe(item) -> str:
    """What a hover says: the name (which may not have fitted), the dates, the notes."""
    if item.span:
        until = item.end.label() if item.end else "now"
        when = f"{item.start.label()} – {until}"
    else:
        when = item.start.label()
    head = f"{item.title or 'Untitled'}\n{when}"
    if item.categories:
        head += f"  ·  {', '.join(item.categories)}"
    if item.notes:
        head += "\n" + "\n".join(item.notes[:6])
    return head


class BoardCanvas(Gtk.Widget):
    """A board, drawn and handled. See the module docstring for the gestures.

    Callbacks, each optional:
      on_add(when)        an empty place was double-clicked
      on_edit(item)       something was double-clicked, or Enter was pressed on it
      on_delete(items)    Delete was pressed with something selected
      on_select(items)    the selection changed
    """

    __gtype_name__ = "ClioBoardCanvas"

    def __init__(self, **callbacks):
        super().__init__(hexpand=True, vexpand=True, focusable=True)
        self.add_css_class("clio-canvas")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.set_has_tooltip(True)
        self.connect("query-tooltip", self._tooltip)
        self.callbacks = callbacks
        self.board = None
        self.axis = Axis()
        self.hidden: set[str] = set()
        self.ghosts: list = []
        self.selection: list = []
        self.pin: When | None = None       # where a jump landed, until the next click
        self.lift = 0.0                    # how far the board is scrolled up
        self.scene = layout.Scene()
        self._fitted = False
        self._widths: dict[tuple[str, bool], float] = {}
        self._hover = None
        self._last = None
        self._drag = None                  # how far the drag had got, last time it moved
        self._sweep = None
        self._goal = None                  # the zoom being eased towards
        self._around = None
        self._flight = None                # (centre, log ppd, centre, log ppd, started)
        self._ticking = 0
        self._pinch_from = 1.0

        drag = Gtk.GestureDrag(button=1)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)

        click = Gtk.GestureClick(button=1)
        click.connect("pressed", self._pressed)
        click.connect("released", self._released)
        self.add_controller(click)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._scrolled)
        self.add_controller(scroll)

        pinch = Gtk.GestureZoom()
        pinch.connect("begin", lambda *_: setattr(self, "_pinch_from", self.axis.ppd))
        pinch.connect("scale-changed", self._pinched)
        self.add_controller(pinch)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._moved)
        motion.connect("leave", lambda *_: self._hover_over(None))
        self.add_controller(motion)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    # -- what it shows -------------------------------------------------------

    def set_board(self, board, view=None):
        """Show a board, where it was last looked at (`view` = (centre, ppd)), or all of it."""
        self.board = board
        self.selection = []
        self.ghosts = []
        self.pin = None
        self.lift = 0.0
        self._goal = self._flight = None
        self._widths = {}
        if view is not None:
            self.axis.centre, self.axis.ppd = view
            self._fitted = True
        else:
            self._fitted = False
            if self.get_width():
                self.fit(glide=False)
        self.queue_draw()

    def view_state(self):
        return self.axis.centre, self.axis.ppd

    def refresh(self):
        """The board changed: whatever was selected and is gone is let go of."""
        if self.board is not None:
            present = {id(item) for item in self.board.items} | {id(g) for g in self.ghosts}
            kept = [item for item in self.selection if id(item) in present]
            if len(kept) != len(self.selection):
                self.selection = kept
                self._told("on_select", kept)
        self.queue_draw()

    def set_hidden(self, hidden):
        self.hidden = set(hidden)
        self.selection = [i for i in self.selection if layout.shown(i, self.hidden)]
        self._told("on_select", self.selection)
        self.queue_draw()

    def set_ghosts(self, items):
        """Show what Claude proposes, dashed, and bring it into view."""
        self.ghosts = list(items)
        self.selection = [i for i in self.selection if i not in self.ghosts]
        if self.ghosts:
            self.fit(self.ghosts)
        self.queue_draw()

    def clear_ghosts(self):
        self.selection = [i for i in self.selection if not self.is_ghost(i)]
        self.ghosts = []
        self._told("on_select", self.selection)
        self.queue_draw()

    def is_ghost(self, item) -> bool:
        return any(item is ghost for ghost in self.ghosts)

    def select(self, items):
        self.selection = list(items)
        self._told("on_select", self.selection)
        self.queue_draw()

    def arranged(self) -> layout.Scene:
        """Where everything is now, for this width and height."""
        self.axis.width = float(self.get_width() or self.axis.width)
        height = float(self.get_height() or 480)
        if self.board is None:
            return layout.Scene(axis_y=height / 2)
        return layout.arrange(self.board, self.axis, height, self._measure, self.hidden,
                              self.ghosts)

    def _measure(self, text: str, bold: bool) -> float:
        key = (text, bold)
        width = self._widths.get(key)
        if width is None:
            piece = self._piece(text, bold=bold)
            width = float(piece.get_pixel_size()[0])
            self._widths[key] = width
        return width

    def _piece(self, text, scale=0.92, bold=False, italic=False):
        piece = self.create_pango_layout(text)
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_scale_new(scale))
        if bold:
            attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
        if italic:
            attrs.insert(Pango.attr_style_new(Pango.Style.ITALIC))
        piece.set_attributes(attrs)
        return piece

    # -- moving about --------------------------------------------------------

    def fit(self, items=None, glide=True):
        """Everything (or `items`) in view; a board with nothing on it shows the last ten years."""
        if items is None and self.board is not None:
            items = [i for i in self.board.items if layout.shown(i, self.hidden)] + self.ghosts
        items = list(items or [])
        self.pin = None
        today = today_days()
        if items:
            first = min(item.first_day() for item in items)
            last = max(item.end_day() for item in items)
        else:
            born = self.board.born if self.board is not None else None
            first = born.first_day() if born is not None else today - 10 * YEAR
            last = today
        if last - first < YEAR:
            middle = (first + last) / 2
            first, last = middle - YEAR / 2, middle + YEAR / 2
        self._fitted = True
        goal = Axis(width=float(self.get_width() or self.axis.width))
        goal.show(first, last)
        self.fly(goal.centre, goal.ppd, glide)

    def jump(self, when: When):
        """Go to a date, and leave a pin in it."""
        goal = Axis(width=float(self.get_width() or self.axis.width))
        goal.jump(when)
        self.pin = when
        self.fly(goal.centre, goal.ppd)

    def fly(self, centre, ppd, glide=True):
        """Glide to a view, so that you can see where you went."""
        self._goal = None
        if not glide or not self.get_mapped():
            self.axis.centre, self.axis.ppd = centre, ppd
            self._flight = None
            self.queue_draw()
            return
        self._flight = (self.axis.centre, math.log(self.axis.ppd), centre, math.log(ppd), None)
        self._start_ticking()

    def zoom(self, factor, around_x=None):
        """Zoom by a factor, keeping the day under `around_x` where it is on the screen."""
        width = float(self.get_width() or self.axis.width)
        self.axis.width = width
        around_x = width / 2 if around_x is None else around_x
        if not self.get_mapped():
            self.axis.zoom(factor, around_x)
            self.queue_draw()
            return
        goal = (self._goal if self._goal is not None else self.axis.ppd) * factor
        self._goal, self._around = max(MIN_PPD, min(MAX_PPD, goal)), around_x
        self._flight = None
        self._start_ticking()

    def _start_ticking(self):
        if not self._ticking:
            self._ticking = self.add_tick_callback(self._tick)

    def _tick(self, _widget, clock):
        if self._flight is not None:
            centre, zoom, to_centre, to_zoom, started = self._flight
            now = clock.get_frame_time()
            if started is None:
                started = now
                self._flight = (centre, zoom, to_centre, to_zoom, started)
            t = min(1.0, (now - started) / FLIGHT_US)
            eased = 1 - (1 - t) ** 3
            self.axis.centre = centre + (to_centre - centre) * eased
            self.axis.ppd = math.exp(zoom + (to_zoom - zoom) * eased)
            if t >= 1.0:
                self._flight = None
        elif self._goal is not None:
            left = math.log(self._goal) - math.log(self.axis.ppd)
            if abs(left) < 0.004:
                self.axis.zoom(self._goal / self.axis.ppd, self._around)
                self._goal = None
            else:
                self.axis.zoom(math.exp(left * ZOOM_EASE), self._around)
        self.queue_draw()
        if self._flight is None and self._goal is None:
            self._ticking = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _pan(self, dx, dy=0.0):
        self.axis.pan(dx)
        self.lift = max(0.0, min(self.scene.overflow, self.lift - dy))
        self.queue_draw()

    # -- drawing -------------------------------------------------------------

    def _accent(self) -> str:
        try:
            return housestyle.current().accent
        except Exception:  # noqa: BLE001 - a colour is not worth a crash
            return "#3584e4"

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        self.axis.width = float(width or self.axis.width)
        if not self._fitted and width and self.board is not None:
            self.fit(glide=False)
        self.scene = scene = self.arranged()
        self.lift = max(0.0, min(scene.overflow, self.lift))

        cr = snapshot.append_cairo(Graphene.Rect().init(0, 0, width, height))
        ink = self.get_color()
        fg = (ink.red, ink.green, ink.blue)
        accent = _rgba(self._accent())
        cr.translate(0, -self.lift)

        self._draw_axis(cr, scene, width, height, fg, accent)
        for brace in scene.braces:
            self._draw_brace(cr, brace, scene, width, fg, accent)
        for mark in scene.marks:
            self._draw_stem(cr, mark, scene, fg)
        for mark in scene.marks:
            self._draw_mark(cr, mark, scene, width, fg, accent)
        if self._sweep is not None:
            left, right = sorted(self._sweep)
            cr.rectangle(left, self.lift, right - left, height)
            cr.set_source_rgba(*accent[:3], 0.14)
            cr.fill()

    def _draw_axis(self, cr, scene, width, height, fg, accent):
        y = scene.axis_y
        bottom = height + self.lift
        for tick in self.axis.ticks():
            x = self.axis.x(tick.day)
            if tick.major:
                cr.set_source_rgba(*fg, 0.06)
                cr.set_line_width(1)
                cr.move_to(x, self.lift)
                cr.line_to(x, bottom)
                cr.stroke()
                self._text(cr, tick.label, x + 3, y + 4, (*fg, 0.62), 0.8)
            cr.set_source_rgba(*fg, 0.5 if tick.major else 0.3)
            cr.set_line_width(1)
            cr.move_to(x, y)
            cr.line_to(x, y + (6 if tick.major else 3))
            cr.stroke()

        cr.set_source_rgba(*fg, 0.5)
        cr.set_line_width(1.3)
        cr.move_to(0, y)
        cr.line_to(width, y)
        cr.stroke()

        board = self.board
        if board is not None and board.born is not None:
            self._draw_ages(cr, board.born, y + layout.TICKS - 1, width, fg, accent)

        today = self.axis.x(today_days() + 0.5)
        if -EDGE <= today <= width + EDGE:
            cr.set_source_rgba(*accent[:3], 0.7)
            cr.set_line_width(1.2)
            cr.move_to(today, y - 12)
            cr.line_to(today, y + 12)
            cr.stroke()
        if self.pin is not None:
            x = self.axis.x(self.pin.middle())
            if -EDGE <= x <= width + EDGE:
                cr.save()
                cr.set_dash([4, 4])
                cr.set_source_rgba(*accent[:3], 0.8)
                cr.set_line_width(1.2)
                cr.move_to(x, self.lift)
                cr.line_to(x, bottom)
                cr.stroke()
                cr.restore()
                self._text(cr, self.pin.label(), x + 4, self.lift + 3, accent, 0.8)

    def _draw_ages(self, cr, born, y, width, fg, accent):
        ages = self.axis.ages(born)
        for index, (day, age) in enumerate(ages):
            x = self.axis.x(day)
            label = "born" if age == 0 else f"age {age}" if index == 0 else str(age)
            cr.set_source_rgba(*accent[:3], 0.55)
            cr.set_line_width(1)
            cr.move_to(x, y)
            cr.line_to(x, y + 4)
            cr.stroke()
            self._text(cr, label, x + 3, y - 1, (*accent[:3], 0.9), 0.74)
        if not ages and self.axis.centre >= born.first_day():
            # Zoomed in between two birthdays: still say how old, at the edge.
            self._text(cr, f"age {age_at(born, self.axis.centre)}", 6, y - 1,
                       (*accent[:3], 0.9), 0.74)

    def _draw_stem(self, cr, mark, scene, fg):
        if mark.row < 0:
            return
        colour = self.board.colour_of(mark.item)
        cr.set_source_rgba(*_rgba(colour, 0.38 if mark.ghost else 0.55))
        cr.set_line_width(1)
        cr.move_to(mark.centre, scene.axis_y)
        top = mark.label_y + layout.ROW - 3
        cr.line_to(mark.centre, top)
        # Along the foot of the name, when the name had to move off the mark.
        if mark.centre < mark.label_x:
            cr.line_to(mark.label_x + 2, top)
        elif mark.centre > mark.label_x + mark.label_w:
            cr.line_to(mark.label_x + mark.label_w - 2, top)
        cr.stroke()

    def _draw_mark(self, cr, mark, scene, width, fg, accent):
        item = mark.item
        y = scene.axis_y
        chosen = any(item is other for other in self.selection)
        strength = 0.45 if mark.ghost else 0.55 if item.start.approx else 1.0
        colour = _rgba(self.board.colour_of(item), strength)
        cr.new_path()                    # or the arc would start from the last name drawn
        cr.save()
        if mark.band:
            left, right = max(mark.x1, -EDGE), min(mark.x2, width + EDGE)
            half = mark.radius
            _rounded(cr, left, y - half, right - left, half * 2, 3)
            cr.set_source_rgba(*colour[:3], colour[3] * (0.28 if mark.ghost else 0.5))
            cr.fill_preserve()
            if mark.ghost or item.start.approx:
                cr.set_dash([3, 3])
            cr.set_source_rgba(*colour)
            cr.set_line_width(1.2)
            cr.stroke()
        else:
            cr.arc(mark.centre, y, mark.radius, 0, 2 * math.pi)
            if mark.ghost:
                cr.set_dash([3, 2])
                cr.set_source_rgba(*colour)
                cr.set_line_width(1.5)
                cr.stroke()
            elif item.start.approx:
                # Soft: a pale middle in a ring that does not quite close.
                cr.set_source_rgba(*colour[:3], colour[3] * 0.5)
                cr.fill_preserve()
                cr.set_dash([2, 2])
                cr.set_source_rgba(*colour)
                cr.set_line_width(1.2)
                cr.stroke()
            else:
                cr.set_source_rgba(*colour)
                cr.fill()
        cr.restore()
        if chosen:
            cr.set_source_rgba(*accent)
            cr.set_line_width(2)
            if mark.band:
                left, right = max(mark.x1, -EDGE), min(mark.x2, width + EDGE)
                _rounded(cr, left - 2, y - mark.radius - 2, right - left + 4,
                         mark.radius * 2 + 4, 4)
            else:
                cr.new_path()
                cr.arc(mark.centre, y, mark.radius + 2.5, 0, 2 * math.pi)
            cr.stroke()
        if mark.row >= 0:
            alpha = 0.6 if mark.ghost else 0.92 if not item.start.approx else 0.72
            if chosen or self._hover is item:
                alpha = 1.0
            piece = self._piece(item.title or "Untitled", bold=item.milestone, italic=mark.ghost)
            piece.set_width(int(mark.label_w * Pango.SCALE))
            piece.set_ellipsize(Pango.EllipsizeMode.END)
            self._halo(cr, piece, mark.label_x, mark.label_y)
            cr.move_to(mark.label_x, mark.label_y)
            cr.set_source_rgba(*(accent[:3] if chosen else fg), alpha)
            PangoCairo.show_layout(cr, piece)

    def _draw_brace(self, cr, brace, scene, width, fg, accent):
        item = brace.item
        chosen = any(item is other for other in self.selection)
        colour = _rgba(self.board.colour_of(item), 0.5 if brace.ghost else 1.0)
        y = brace.y
        hook_left = brace.x1 >= -EDGE
        hook_right = brace.x2 <= width + EDGE
        left, right = max(brace.x1, -EDGE), min(brace.x2, width + EDGE)

        # Faint lines up to the axis, to show what the brace holds.
        for x, hook in ((left, hook_left), (right, hook_right)):
            if hook:
                cr.set_source_rgba(*colour[:3], 0.14)
                cr.set_line_width(1)
                cr.move_to(x, scene.axis_y + 1)
                cr.line_to(x, y)
                cr.stroke()

        cr.save()
        if brace.ghost or item.start.approx or (item.end is not None and item.end.approx):
            cr.set_dash([4, 3])
        cr.set_source_rgba(*(accent if chosen else colour))
        cr.set_line_width(2.4 if chosen else 1.6)
        _brace(cr, left, right, brace.point, y, layout.BRACE, hook_left, hook_right)
        cr.stroke()
        cr.restore()

        if item.open_end and hook_right:
            # Still running: it goes on past today.
            cr.save()
            cr.set_dash([2, 3])
            cr.set_source_rgba(*colour[:3], 0.8)
            cr.set_line_width(1.6)
            cr.move_to(right, y)
            cr.line_to(right + 16, y)
            cr.stroke()
            cr.restore()

        if brace.label_w <= 0:
            return
        piece = self._piece(item.title or "Untitled", italic=brace.ghost)
        piece.set_width(int(brace.label_w * Pango.SCALE))
        piece.set_ellipsize(Pango.EllipsizeMode.END)
        piece.set_alignment(Pango.Alignment.CENTER)
        self._halo(cr, piece, brace.label_x, brace.label_y)
        cr.move_to(brace.label_x, brace.label_y)
        hovering = self._hover is item
        cr.set_source_rgba(*(accent[:3] if chosen else fg),
                           1.0 if chosen or hovering else 0.65 if brace.ghost else 0.85)
        PangoCairo.show_layout(cr, piece)

    def _halo(self, cr, piece, x, y):
        """A soft edge of the paper's colour around a name, for stems that pass behind it."""
        cr.save()
        cr.move_to(x, y)
        PangoCairo.layout_path(cr, piece)
        cr.set_source_rgba(*self._paper())
        cr.set_line_width(3)
        cr.set_line_join(1)              # round
        cr.stroke()
        cr.restore()

    def _paper(self):
        try:
            return _rgba(housestyle.current().bg, 0.8)
        except Exception:  # noqa: BLE001 - a colour is not worth a crash
            ink = self.get_color()
            return (1.0, 1.0, 1.0, 0.8) if ink.red + ink.green + ink.blue < 1.5                 else (0.0, 0.0, 0.0, 0.8)

    def _text(self, cr, text, x, y, colour, scale=1.0):
        piece = self._piece(text, scale=scale)
        cr.move_to(x, y)
        cr.set_source_rgba(*colour)
        PangoCairo.show_layout(cr, piece)

    # -- the hands -----------------------------------------------------------

    def _told(self, name, *args):
        callback = self.callbacks.get(name)
        if callback:
            callback(*args)

    def item_at(self, x, y):
        """What is at a point on the widget: an event, a span, or None."""
        y += self.lift
        mark = self.scene.mark_at(x, y)
        if mark is not None:
            return mark.item
        brace = self.scene.brace_at(x, y)
        return brace.item if brace is not None else None

    def _click_select(self, item, adding):
        chosen = list(self.selection)
        if adding:
            if any(item is other for other in chosen):
                chosen = [other for other in chosen if other is not item]
            else:
                chosen.append(item)
        else:
            chosen = [item]
        self.select(chosen)

    def _drag_begin(self, gesture, x, y):
        self._drag = (0.0, 0.0)
        if gesture.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK:
            self._sweep = (x, x)

    def _drag_update(self, gesture, dx, dy):
        if self._drag is None:
            return
        if self._sweep is not None:
            self._sweep = (self._sweep[0], self._sweep[0] + dx)
            self.queue_draw()
            return
        # Not until it is clearly a drag: a click's small wobble moves nothing.
        if self._drag == (0.0, 0.0) and abs(dx) + abs(dy) < 3:
            return
        self._pan(dx - self._drag[0], dy - self._drag[1])
        self._drag = (dx, dy)

    def _drag_end(self, gesture, dx, dy):
        self._drag = None
        if self._sweep is not None:
            left, right = sorted(self._sweep)
            self._sweep = None
            if right - left > 4:
                self.sweep(left, right)
            self.queue_draw()

    def sweep(self, left, right):
        """Select every event across a stretch of the axis, and add to what was selected."""
        found = list(self.selection)
        for mark in self.scene.marks:
            if left <= mark.centre <= right and not any(mark.item is o for o in found):
                found.append(mark.item)
        self.select(found)

    def _pressed(self, gesture, presses, x, y):
        if presses != 2:
            return
        item = self.item_at(x, y)
        if item is not None:
            self._told("on_edit", item)
            return
        precision = self.axis.precision()
        self._told("on_add", When.from_days(int(math.floor(self.axis.day(x))), precision))

    def _released(self, gesture, presses, x, y):
        if presses != 1:
            return
        self.grab_focus()
        state = gesture.get_current_event_state()
        adding = bool(state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK))
        self.pin = None
        item = self.item_at(x, y)
        if item is not None:
            self._click_select(item, adding)
        elif not adding:
            self.select([])

    def _scrolled(self, controller, dx, dy):
        state = controller.get_current_event_state()
        surface = controller.get_unit() == Gdk.ScrollUnit.SURFACE
        if state & Gdk.ModifierType.CONTROL_MASK:
            # A wheel's notch is a step; a touchpad's slide is a stream of small ones.
            factor = math.exp(-dy * 0.008) if surface else (1.25 if dy < 0 else 1 / 1.25)
            self.zoom(factor, self._last[0] if self._last else None)
            return True
        step = 1.0 if surface else 40.0
        if state & Gdk.ModifierType.SHIFT_MASK:
            self._pan(0, -dy * step)
            return True
        self._pan(-(dx + dy) * step)
        return True

    def _pinched(self, gesture, scale):
        ppd = self._pinch_from * scale
        self.axis.zoom(ppd / self.axis.ppd, self._last[0] if self._last else None)
        self.queue_draw()

    def _moved(self, _controller, x, y):
        self._last = (x, y)
        item = self.item_at(x, y)
        self._hover_over(item)
        self.set_cursor_from_name("pointer" if item is not None else None)

    def _hover_over(self, item):
        if item is not self._hover:
            self._hover = item
            self.queue_draw()

    def _tooltip(self, _widget, x, y, _keyboard, tooltip):
        item = self.item_at(x, y)
        if item is None:
            return False
        tooltip.set_text(describe(item))
        return True

    def _key(self, _controller, keyval, _keycode, state):
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete, Gdk.KEY_BackSpace) and self.selection:
            self._told("on_delete", list(self.selection))
            return True
        if keyval == Gdk.KEY_Escape and (self.selection or self.pin):
            self.pin = None
            self.select([])
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and len(self.selection) == 1:
            self._told("on_edit", self.selection[0])
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            distance = self.axis.width / 5
            self._pan(distance if keyval == Gdk.KEY_Left else -distance)
            return True
        return False


def _rounded(cr, x, y, width, height, radius):
    radius = min(radius, width / 2, height / 2)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 1.5 * math.pi)
    cr.close_path()


def _brace(cr, left, right, point, y, depth, hook_left=True, hook_right=True):
    """A `}` on its side: its ends turned up to the axis, its point down at the name.

    Where the span runs off the screen that end is not drawn turning, because the
    span does not end there.
    """
    middle = y + depth / 2
    tip = y + depth
    width = right - left
    curl = min(depth, max(width / 4, 1.0))
    point = max(left + curl, min(point, right - curl)) if width > 2 * curl else (left + right) / 2
    if hook_left:
        cr.move_to(left, y)
        cr.curve_to(left, middle, left + curl / 2, middle, left + curl, middle)
    else:
        cr.move_to(left, middle)
    cr.line_to(point - curl, middle)
    cr.curve_to(point - curl / 2, middle, point, middle, point, tip)
    cr.curve_to(point, middle, point + curl / 2, middle, point + curl, middle)
    cr.line_to(right - curl if hook_right else right, middle)
    if hook_right:
        cr.curve_to(right - curl / 2, middle, right, middle, right, y)

