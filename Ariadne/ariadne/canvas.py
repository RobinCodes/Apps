"""The thread canvas: thoughts as cards, arrows between them, and the hands to move them.

Everything is drawn in one pass with cairo, in world coordinates under a
pan and a zoom, rather than built from widgets. A thread of forty thoughts is
then forty rectangles and forty layouts, not forty widget trees, which is
what keeps it smooth on a machine with three gigabytes of memory — and a
zoom is one multiplication rather than a relayout.

What you can do, and how:

* **double-click** on empty canvas to add a thought there; on a thought to
  edit it;
* **drag** a thought to move it; drag from the **handle** on its right edge
  to another thought to draw an arrow; drag empty canvas to pan;
* **click** an arrow to label it; click a `[[link]]` in a thought to follow
  it; **Delete** takes away whatever is selected;
* scroll to pan, Ctrl+scroll or pinch to zoom.

The canvas changes nothing on disk itself. It tells its owner what happened
through the callbacks it was given, and the owner saves.
"""

from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, Graphene, Gtk, Pango, PangoCairo  # noqa: E402

from . import housestyle, inline, layout  # noqa: E402

WIDTH = 230            # a card, in world units
PAD = 12
GAP = 6
HANDLE = 7
TEXT_LINES = 9         # past this a thought's text is ellipsised on the card
MIN_ZOOM, MAX_ZOOM = 0.25, 2.5


def _rgba(hex_colour: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    colour = Gdk.RGBA()
    colour.parse(hex_colour)
    return colour.red, colour.green, colour.blue, alpha


class Card:
    """A thought as drawn: where it is, how big, and its laid-out text."""

    def __init__(self, node, ghost=False):
        self.node = node
        self.ghost = ghost
        self.x = self.y = 0.0
        self.height = 60.0
        self.title = None
        self.text = None
        self.runs = []
        self.text_top = 0.0

    @property
    def id(self):
        return self.node.id

    def contains(self, x, y) -> bool:
        return self.x <= x <= self.x + WIDTH and self.y <= y <= self.y + self.height

    def handle(self) -> tuple[float, float]:
        return self.x + WIDTH, self.y + self.height / 2

    def centre(self) -> tuple[float, float]:
        return self.x + WIDTH / 2, self.y + self.height / 2

    def border_towards(self, x, y) -> tuple[float, float]:
        """Where a line from the centre towards (x, y) leaves the card."""
        cx, cy = self.centre()
        dx, dy = x - cx, y - cy
        if dx == 0 and dy == 0:
            return cx, cy
        sx = (WIDTH / 2) / abs(dx) if dx else math.inf
        sy = (self.height / 2) / abs(dy) if dy else math.inf
        scale = min(sx, sy)
        return cx + dx * scale, cy + dy * scale


class Canvas(Gtk.Widget):
    """A thread, drawn and handled. See the module docstring for the gestures.

    Callbacks, each optional:
      on_moved()                    a thought was dragged somewhere new
      on_connect(source, target)    an arrow was drawn between two thoughts
      on_edit(node_id)              a thought was double-clicked
      on_add(x, y)                  empty canvas was double-clicked (world coords)
      on_edge(source_id, edge)      an arrow was clicked
      on_link(target, wiki)         a link in a thought was clicked
      on_delete(selection)          Delete was pressed with something selected
      on_select(selection)          the selection changed
    """

    __gtype_name__ = "AriadneCanvas"

    def __init__(self, **callbacks):
        super().__init__(hexpand=True, vexpand=True, focusable=True)
        self.add_css_class("thread-canvas")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.callbacks = callbacks
        self.thread = None
        self.cards: dict[str, Card] = {}
        self.ghosts: dict[str, Card] = {}
        self.ghost_edges: list = []
        self.auto = False              # positions made up here, not read from the file
        self.scale = 1.0
        self.offset = [0.0, 0.0]
        self.selection = None          # ("node", id) | ("edge", (source, Edge)) | None
        self._drag = None
        self._pointer = None
        self._hover = None
        self._fitted = False
        self._last = None
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
        pinch.connect("begin", lambda *_: setattr(self, "_pinch_from", self.scale))
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

    def set_thread(self, thread, keep_view=False):
        """Show a thread, where its `layout:` says, or arranged when it says nothing."""
        self.thread = thread
        self.selection = None
        self.ghosts, self.ghost_edges = {}, []
        places = thread.layout if thread is not None else {}
        nodes = thread.nodes if thread is not None else []
        self.auto = bool(nodes) and not places
        self.cards = {node.id: Card(node) for node in nodes}
        self._measure_all()
        if self.auto:
            places = layout.arrange([n.id for n in nodes],
                                    [(n.id, e.target) for n in nodes for e in n.edges],
                                    heights=self._heights())
        placed = {}
        for node in nodes:
            card = self.cards[node.id]
            where = places.get(node.id)
            if where is None:
                where = layout.free_spot(placed, max(placed.values(), default=layout.ORIGIN))
            card.x, card.y = where
            placed[node.id] = where
        if not keep_view:
            self._fitted = False
            self.fit()
        self.queue_draw()

    def refresh(self):
        """The thread changed under the same cards: titles, texts, arrows."""
        if self.thread is None:
            return
        present = {node.id for node in self.thread.nodes}
        for gone in set(self.cards) - present:
            del self.cards[gone]
        for node in self.thread.nodes:
            if node.id not in self.cards:
                card = Card(node)
                card.x, card.y = layout.free_spot(self.places(), layout.ORIGIN)
                self.cards[node.id] = card
            else:
                self.cards[node.id].node = node
        if self.selection and self.selection[0] == "node" and self.selection[1] not in self.cards:
            self.selection = None
        self._measure_all()
        self.queue_draw()

    def places(self) -> dict[str, tuple[float, float]]:
        """Where every real thought is, for `layout:`."""
        return {key: (card.x, card.y) for key, card in self.cards.items()}

    def ghost_places(self) -> dict[str, tuple[float, float]]:
        return {key: (card.x, card.y) for key, card in self.ghosts.items()}

    def place(self, node_id, x, y):
        card = self.cards.get(node_id)
        if card is not None:
            card.x, card.y = x, y
            self.queue_draw()

    def arrange(self):
        """Lay the thread out afresh, left to right."""
        if self.thread is None:
            return
        places = layout.arrange([n.id for n in self.thread.nodes],
                                [(n.id, e.target) for n in self.thread.nodes for e in n.edges],
                                heights=self._heights())
        for key, (x, y) in places.items():
            self.cards[key].x, self.cards[key].y = x, y
        self.auto = False
        self.fit()

    def set_ghosts(self, proposal, near=None):
        """Show what Claude proposes, beside `near` (a thought id) or after everything."""
        self.ghosts = {}
        nodes = list(proposal.nodes) if proposal else []
        edges = [(s, e.target) for s, e in (proposal.edges if proposal else [])
                 if s in {n.id for n in nodes} and e.target in {n.id for n in nodes}]
        if near in self.cards:
            origin = (self.cards[near].x + layout.COLUMN, self.cards[near].y)
        elif self.cards:
            origin = (max(c.x for c in self.cards.values()) + layout.COLUMN,
                      min(c.y for c in self.cards.values()))
        else:
            origin = layout.ORIGIN
        for node in nodes:
            self.ghosts[node.id] = Card(node, ghost=True)
            self._measure(self.ghosts[node.id])
        places = layout.arrange([n.id for n in nodes], edges, origin=origin,
                                heights={k: c.height for k, c in self.ghosts.items()})
        for key, card in self.ghosts.items():
            card.x, card.y = places[key]
        self.ghost_edges = list(proposal.edges) if proposal else []
        self.queue_draw()

    def clear_ghosts(self):
        self.ghosts, self.ghost_edges = {}, []
        self.queue_draw()

    # -- measuring -----------------------------------------------------------

    def _heights(self):
        return {key: card.height for key, card in self.cards.items()}

    def _measure_all(self):
        for card in [*self.cards.values(), *self.ghosts.values()]:
            self._measure(card)

    def _measure(self, card):
        title = self.create_pango_layout(card.node.title or "Untitled")
        title.set_width((WIDTH - 2 * PAD) * Pango.SCALE)
        title.set_wrap(Pango.WrapMode.WORD_CHAR)
        bold = Pango.AttrList()
        bold.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
        title.set_attributes(bold)
        card.title = title
        height = PAD + title.get_pixel_size()[1]
        card.text = None
        card.runs = []
        if card.node.text.strip():
            plain, runs = inline.render(card.node.text.strip())
            text = self.create_pango_layout(plain)
            text.set_width((WIDTH - 2 * PAD) * Pango.SCALE)
            text.set_wrap(Pango.WrapMode.WORD_CHAR)
            text.set_height(-TEXT_LINES)
            text.set_ellipsize(Pango.EllipsizeMode.END)
            attrs = _attributes(runs, self._accent())
            attrs.insert(Pango.attr_scale_new(0.92))
            text.set_attributes(attrs)
            card.text = text
            card.runs = runs
            card.text_top = height + GAP
            height = card.text_top + text.get_pixel_size()[1]
        card.height = height + PAD

    # -- coordinates ---------------------------------------------------------

    def to_world(self, x, y) -> tuple[float, float]:
        return (x - self.offset[0]) / self.scale, (y - self.offset[1]) / self.scale

    def to_screen(self, x, y) -> tuple[float, float]:
        return x * self.scale + self.offset[0], y * self.scale + self.offset[1]

    def zoom(self, factor, around=None):
        width, height = self.get_width() or 600, self.get_height() or 400
        around = around or (width / 2, height / 2)
        world = self.to_world(*around)
        self.scale = max(MIN_ZOOM, min(MAX_ZOOM, self.scale * factor))
        self.offset = [around[0] - world[0] * self.scale, around[1] - world[1] * self.scale]
        self.queue_draw()

    def fit(self):
        """Everything in view, at no more than full size."""
        cards = [*self.cards.values(), *self.ghosts.values()]
        width, height = self.get_width(), self.get_height()
        if not width or not height:
            self._fitted = False
            return
        self._fitted = True
        if not cards:
            self.scale, self.offset = 1.0, [40.0, 40.0]
            self.queue_draw()
            return
        left = min(c.x for c in cards) - 40
        top = min(c.y for c in cards) - 40
        right = max(c.x + WIDTH for c in cards) + 40
        bottom = max(c.y + c.height for c in cards) + 40
        self.scale = max(MIN_ZOOM, min(1.0, width / (right - left), height / (bottom - top)))
        self.offset = [(width - (right - left) * self.scale) / 2 - left * self.scale,
                       (height - (bottom - top) * self.scale) / 2 - top * self.scale]
        self.queue_draw()

    def centre_world(self) -> tuple[float, float]:
        width, height = self.get_width() or 600, self.get_height() or 400
        x, y = self.to_world(width / 2, height / 2)
        return x - WIDTH / 2, y - 30

    # -- hit testing ---------------------------------------------------------

    def card_at(self, x, y, ghosts=False):
        pool = list(self.cards.values()) + (list(self.ghosts.values()) if ghosts else [])
        for card in reversed(pool):
            if card.contains(x, y):
                return card
        return None

    def handle_at(self, x, y):
        reach = (HANDLE + 5) / self.scale
        for card in reversed(list(self.cards.values())):
            hx, hy = card.handle()
            if math.hypot(x - hx, y - hy) <= reach:
                return card
        return None

    def edge_at(self, x, y):
        reach = 7 / self.scale
        for source, edge, (sx, sy), (tx, ty), label_box in self._edge_geometry():
            if label_box and label_box[0] <= x <= label_box[2] and label_box[1] <= y <= label_box[3]:
                return source, edge
            if _distance_to_segment(x, y, sx, sy, tx, ty) <= reach:
                return source, edge
        return None

    def link_at(self, x, y):
        card = self.card_at(x, y)
        if card is None or card.text is None:
            return None
        inside, index, _ = card.text.xy_to_index(int((x - card.x - PAD) * Pango.SCALE),
                                                 int((y - card.y - card.text_top) * Pango.SCALE))
        return inline.link_at(card.runs, index) if inside else None

    def _edge_geometry(self):
        """(source id, edge, start, end, label box) for every real arrow."""
        out = []
        if self.thread is None:
            return out
        for node in self.thread.nodes:
            source = self.cards.get(node.id)
            if source is None:
                continue
            for edge in node.edges:
                target = self.cards.get(edge.target)
                if target is None:
                    start = (source.x + WIDTH, source.y + source.height / 2)
                    end = (start[0] + 90, start[1] + 26)
                else:
                    start = source.border_towards(*target.centre())
                    end = target.border_towards(*source.centre())
                out.append((node.id, edge, start, end, self._label_box(edge, start, end)))
        return out

    def _label_box(self, edge, start, end):
        if not edge.label:
            return None
        mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
        width = 8 * len(edge.label) + 14
        return mx - width / 2, my - 11, mx + width / 2, my + 11

    # -- drawing -------------------------------------------------------------

    def _accent(self) -> str:
        try:
            return housestyle.current().accent
        except Exception:  # noqa: BLE001 - a colour is not worth a crash
            return "#3584e4"

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if not self._fitted and width and height:
            self.fit()
        rect = Graphene.Rect().init(0, 0, width, height)
        cr = snapshot.append_cairo(rect)
        ink = self.get_color()
        fg = (ink.red, ink.green, ink.blue)
        accent = _rgba(self._accent())

        cr.translate(*self.offset)
        cr.scale(self.scale, self.scale)

        # The arrows first, so the cards sit over their ends.
        ids = set(self.cards)
        for source, edge, start, end, box in self._edge_geometry():
            chosen = self.selection == ("edge", (source, edge))
            dangling = edge.target not in ids
            colour = accent if chosen else (*fg, 0.55)
            self._arrow(cr, start, end, colour, dashed=dangling, width=2.2 if chosen else 1.4)
            if dangling:
                self._text(cr, f"{edge.target}?", end[0] + 4, end[1] - 8, (*fg, 0.6), 0.85)
            if box:
                self._pill(cr, box, edge.label, colour, fg)

        for source, edge in self.ghost_edges:
            start_card = self.cards.get(source) or self.ghosts.get(source)
            end_card = self.cards.get(edge.target) or self.ghosts.get(edge.target)
            if start_card is None or end_card is None:
                continue
            start = start_card.border_towards(*end_card.centre())
            end = end_card.border_towards(*start_card.centre())
            self._arrow(cr, start, end, (*accent[:3], 0.6), dashed=True, width=1.4)
            box = self._label_box(edge, start, end)
            if box:
                self._pill(cr, box, edge.label, (*accent[:3], 0.6), fg)

        for card in [*self.cards.values(), *self.ghosts.values()]:
            self._card(cr, card, fg, accent)

        if self._drag and self._drag[0] == "connect" and self._pointer:
            source = self.cards[self._drag[1]]
            self._arrow(cr, source.handle(), self._pointer, accent, dashed=False, width=1.6)

    def _card(self, cr, card, fg, accent):
        chosen = self.selection == ("node", card.id)
        alpha = 0.55 if card.ghost else 1.0
        _rounded(cr, card.x, card.y, WIDTH, card.height, 10)
        cr.set_source_rgba(*fg, 0.07 if not card.ghost else 0.03)
        cr.fill_preserve()
        if card.ghost:
            cr.set_dash([5, 4])
            cr.set_source_rgba(*accent[:3], 0.8)
            cr.set_line_width(1.4)
        elif chosen:
            cr.set_source_rgba(*accent)
            cr.set_line_width(2.2)
        else:
            cr.set_source_rgba(*fg, 0.18)
            cr.set_line_width(1.0)
        cr.stroke()
        cr.set_dash([])

        cr.move_to(card.x + PAD, card.y + PAD)
        cr.set_source_rgba(*fg, 0.95 * alpha)
        PangoCairo.show_layout(cr, card.title)
        if card.text is not None:
            cr.move_to(card.x + PAD, card.y + card.text_top)
            cr.set_source_rgba(*fg, 0.78 * alpha)
            PangoCairo.show_layout(cr, card.text)

        if not card.ghost:
            hx, hy = card.handle()
            shown = 0.9 if (chosen or self._hover == card.id) else 0.3
            cr.arc(hx, hy, HANDLE, 0, 2 * math.pi)
            cr.set_source_rgba(*accent[:3], shown)
            cr.fill()

    def _arrow(self, cr, start, end, colour, dashed=False, width=1.4):
        cr.set_source_rgba(*colour)
        cr.set_line_width(width)
        if dashed:
            cr.set_dash([5, 4])
        cr.move_to(*start)
        cr.line_to(*end)
        cr.stroke()
        cr.set_dash([])
        angle = math.atan2(end[1] - start[1], end[0] - start[0])
        size = 9
        cr.move_to(*end)
        cr.line_to(end[0] - size * math.cos(angle - 0.4), end[1] - size * math.sin(angle - 0.4))
        cr.line_to(end[0] - size * math.cos(angle + 0.4), end[1] - size * math.sin(angle + 0.4))
        cr.close_path()
        cr.fill()

    def _pill(self, cr, box, text, colour, fg):
        _rounded(cr, box[0], box[1], box[2] - box[0], box[3] - box[1], 10)
        cr.set_source_rgba(*colour[:3], 0.16)
        cr.fill()
        self._text(cr, text, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, (*fg, 0.85),
                   0.85, centred=True)

    def _text(self, cr, text, x, y, colour, scale=1.0, centred=False):
        piece = self.create_pango_layout(text)
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_scale_new(scale))
        piece.set_attributes(attrs)
        w, h = piece.get_pixel_size()
        cr.move_to(x - w / 2 if centred else x, y - h / 2 if centred else y)
        cr.set_source_rgba(*colour)
        PangoCairo.show_layout(cr, piece)

    # -- the hands -----------------------------------------------------------

    def _select(self, selection):
        if selection != self.selection:
            self.selection = selection
            callback = self.callbacks.get("on_select")
            if callback:
                callback(selection)
        self.queue_draw()

    def _drag_begin(self, gesture, x, y):
        self.grab_focus()
        wx, wy = self.to_world(x, y)
        handle = self.handle_at(wx, wy)
        if handle is not None:
            self._drag = ("connect", handle.id)
            self._pointer = (wx, wy)
            return
        card = self.card_at(wx, wy, ghosts=True)
        if card is not None:
            self._drag = ("move", card, card.x, card.y)
            return
        self._drag = ("pan", list(self.offset))

    def _drag_update(self, gesture, dx, dy):
        if not self._drag:
            return
        kind = self._drag[0]
        if kind == "move":
            card, x0, y0 = self._drag[1:]
            card.x, card.y = x0 + dx / self.scale, y0 + dy / self.scale
        elif kind == "pan":
            start = self._drag[1]
            self.offset = [start[0] + dx, start[1] + dy]
        elif kind == "connect":
            _ok, sx, sy = gesture.get_start_point()
            self._pointer = self.to_world(sx + dx, sy + dy)
        self.queue_draw()

    def _drag_end(self, gesture, dx, dy):
        drag, self._drag = self._drag, None
        if not drag:
            return
        if drag[0] == "move" and (abs(dx) > 2 or abs(dy) > 2):
            if not drag[1].ghost:
                self.auto = False
                callback = self.callbacks.get("on_moved")
                if callback:
                    callback()
        elif drag[0] == "connect":
            _ok, sx, sy = gesture.get_start_point()
            target = self.card_at(*self.to_world(sx + dx, sy + dy))
            if target is not None and target.id != drag[1]:
                callback = self.callbacks.get("on_connect")
                if callback:
                    callback(drag[1], target.id)
        self._pointer = None
        self.queue_draw()

    def _pressed(self, gesture, presses, x, y):
        if presses != 2:
            return
        wx, wy = self.to_world(x, y)
        card = self.card_at(wx, wy)
        if card is not None:
            callback = self.callbacks.get("on_edit")
            if callback:
                callback(card.id)
        elif self.edge_at(wx, wy) is None and self.card_at(wx, wy, ghosts=True) is None:
            callback = self.callbacks.get("on_add")
            if callback:
                callback(wx - WIDTH / 2, wy - 20)

    def _released(self, gesture, presses, x, y):
        if presses != 1:
            return
        self.grab_focus()
        wx, wy = self.to_world(x, y)
        link = self.link_at(wx, wy)
        if link is not None:
            callback = self.callbacks.get("on_link")
            if callback:
                callback(link.target, link.wiki)
            return
        card = self.card_at(wx, wy)
        if card is not None:
            self._select(("node", card.id))
            return
        edge = self.edge_at(wx, wy)
        if edge is not None:
            self._select(("edge", edge))
            callback = self.callbacks.get("on_edge")
            if callback:
                callback(*edge)
            return
        self._select(None)

    def _scrolled(self, controller, dx, dy):
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            self.zoom(1.1 if dy < 0 else 1 / 1.1, self._last)
            return True
        step = 1.0 if controller.get_unit() == Gdk.ScrollUnit.SURFACE else 40.0
        if state & Gdk.ModifierType.SHIFT_MASK:
            dx, dy = dy, dx
        self.offset = [self.offset[0] - dx * step, self.offset[1] - dy * step]
        self.queue_draw()
        return True

    def _pinched(self, gesture, scale):
        target = self._pinch_from * scale
        self.zoom(target / self.scale)

    def _moved(self, _controller, x, y):
        self._last = (x, y)
        wx, wy = self.to_world(x, y)
        card = self.card_at(wx, wy)
        self._hover_over(card.id if card else None)
        link = self.link_at(wx, wy)
        self.set_cursor_from_name("pointer" if link or self.handle_at(wx, wy)
                                  or self.edge_at(wx, wy) else None)

    def _hover_over(self, card_id):
        if card_id != self._hover:
            self._hover = card_id
            self.queue_draw()

    def _key(self, _controller, keyval, _keycode, state):
        if keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace, Gdk.KEY_KP_Delete) and self.selection:
            callback = self.callbacks.get("on_delete")
            if callback:
                callback(self.selection)
            return True
        if keyval == Gdk.KEY_Escape and self.selection:
            self._select(None)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.selection \
                and self.selection[0] == "node" and not state & Gdk.ModifierType.CONTROL_MASK:
            callback = self.callbacks.get("on_edit")
            if callback:
                callback(self.selection[1])
            return True
        return False


def _attributes(runs, accent: str) -> Pango.AttrList:
    attrs = Pango.AttrList()
    red, green, blue, _ = _rgba(accent)
    for run in runs:
        if run.kind == "strong":
            items = [Pango.attr_weight_new(Pango.Weight.BOLD)]
        elif run.kind == "em":
            items = [Pango.attr_style_new(Pango.Style.ITALIC)]
        elif run.kind == "code":
            items = [Pango.attr_family_new("monospace")]
        elif run.kind == "link":
            items = [Pango.attr_underline_new(Pango.Underline.SINGLE),
                     Pango.attr_foreground_new(int(red * 65535), int(green * 65535),
                                               int(blue * 65535))]
        else:
            continue
        for item in items:
            item.start_index, item.end_index = run.start, run.end
            attrs.insert(item)
    return attrs


def _rounded(cr, x, y, width, height, radius):
    radius = min(radius, width / 2, height / 2)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _distance_to_segment(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))
