"""The command palette: everything the app can do, found by typing.

A terminal accumulates commands faster than a menu can hold them, and the
answer most editors settled on is to stop trying — put every action in one
list, match it on a few letters, and let the menu be for the handful of
things people look for with the pointer. That is what this is.

The matcher is a subsequence match rather than a substring one, so "nt"
finds "New tab" and "spv" finds "Split vertically". Scoring is what makes
that usable: a match at the start of a word counts for much more than one in
the middle, a run of consecutive letters counts for more than the same
letters scattered, and a shorter label wins a tie. Without the bonuses an
abbreviation matches almost everything and the ordering is noise.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Gdk, GLib, Graphene, Gtk, Pango  # noqa: E402

from . import glass  # noqa: E402

MAX_ROWS = 9


class Command:
    """One line in the palette."""

    def __init__(self, key, label, group, run, accel="", hint=""):
        self.key = key
        self.label = label
        self.group = group
        self.run = run
        self.accel = accel
        self.hint = hint

    @property
    def haystack(self) -> str:
        return f"{self.label} {self.group} {self.hint}"


def score(query: str, text: str) -> int | None:
    """How well `query` matches `text`, or None when it does not at all.

    Higher is better. The weights are not arrived at by theory: they are the
    smallest set that makes the obvious abbreviation win for every command in
    this app, which is the only test that matters for a list this size.
    """
    if not query:
        return 0
    needle = query.lower()
    hay = text.lower()

    total = 0
    at = 0
    run = 0
    for char in needle:
        found = hay.find(char, at)
        if found < 0:
            return None
        # Prefer the same letter where it begins a word. "nw" should find
        # "New window", and taking the first `w` available finds the one in
        # "New" — which scores "New tab" higher than the window, because the
        # tab's label is shorter. The one exception is a letter that carries
        # straight on from the last one: that is a run, runs are the strongest
        # signal there is, and jumping away to a word start would break it.
        if found != at or at == 0:
            step = at
            while True:
                word_start = hay.find(char, step)
                if word_start < 0:
                    break
                if word_start == 0 or not hay[word_start - 1].isalnum():
                    found = word_start
                    break
                step = word_start + 1
        starts_word = found == 0 or not hay[found - 1].isalnum()
        total += 18 if starts_word else 4
        if found == at and at > 0:
            run += 1
            total += 6 + run * 2      # consecutive letters compound
        else:
            run = 0
        total -= min(6, found - at)   # distance costs, but not unboundedly
        at = found + 1

    # Two labels that both match get ordered by how much of each was used.
    total += max(0, 14 - len(text) // 4)
    if hay.startswith(needle):
        total += 30
    return total


# A subsequence match of three or four letters will find something in almost
# any sentence — "split" is a subsequence of "Specular highlight: on" — so a
# list that showed every match would bury the two commands that were meant.
# Anything scoring below this fraction of the best match is dropped.
CUTOFF = 0.55


def rank(query: str, commands) -> list[Command]:
    scored = []
    for command in commands:
        value = score(query, command.label)
        if value is None:
            value = score(query, command.haystack)
            if value is None:
                continue
            value -= 25          # matched on the group or the hint, not the name
        scored.append((value, command))
    if not scored:
        return []
    scored.sort(key=lambda pair: (-pair[0], pair[1].label))
    best = scored[0][0]
    floor = best * CUTOFF if best > 0 else best
    return [command for value, command in scored if value >= floor]


class Palette(Gtk.Box):
    """The card, the entry and the list — shown over the terminal.

    Not a dialog. A dialog would take the keyboard away from the terminal
    through the compositor and come back with its own frame and its own
    background, and the whole point is that this floats on the same sheet of
    glass as everything else. So it is an overlay child that grabs focus, and
    Escape gives the focus back to the terminal underneath.
    """

    __gtype_name__ = "PrismPalette"

    def __init__(self, scheme, light, config, on_run):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.START)
        self.set_margin_top(64)
        self.set_size_request(520, -1)
        self._on_run = on_run
        self._commands: list[Command] = []
        self._shown: list[Command] = []
        self._chosen = 0

        self.card = glass.Card(glass.Material.card(
            scheme, config["opacity"], grain=config["grain"],
            frosted=bool(config["panel_blur"])),
            radius=int(config["corner_radius"]) + 4, light=light)
        self.card.set_size_request(520, -1)
        self.append(self.card)

        self.entry = Gtk.Entry(placeholder_text="Type a command…")
        self.entry.set_margin_start(12)
        self.entry.set_margin_end(12)
        self.entry.set_margin_top(12)
        self.entry.set_margin_bottom(4)
        self.entry.connect("changed", lambda _e: self._refilter())
        self.entry.connect("activate", lambda _e: self._activate())
        self.card.append(self.entry)

        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.rows.set_margin_start(8)
        self.rows.set_margin_end(8)
        self.rows.set_margin_bottom(10)

        # In a scroller, and not because the list is long: nine rows is about
        # 330px and a terminal is often a short window. Without this the card
        # simply grows past the bottom of the window and the last few
        # commands cannot be reached at all.
        self.scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            propagate_natural_height=True, child=self.rows)
        self.card.append(self.scroller)

        self.empty = Gtk.Label(label="Nothing matches")
        self.empty.add_css_class("palette-hint")
        self.empty.set_margin_top(10)
        self.empty.set_margin_bottom(14)
        self.empty.set_visible(False)
        self.card.append(self.empty)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.rows.add_controller(click)

    # ------------------------------------------------------------ theme --

    def restyle(self, scheme, light, config):
        self.card.set_material(glass.Material.card(
            scheme, config["opacity"], grain=config["grain"],
            frosted=bool(config["panel_blur"])))
        self._radius = int(config["corner_radius"]) + 4
        self.card.set_light(light)

    # ----------------------------------------------------------- showing --

    def open(self, commands):
        self._commands = list(commands)
        self.entry.set_text("")
        self._fit()
        self._refilter()
        self.set_visible(True)
        GLib.idle_add(self.entry.grab_focus)

    def _fit(self):
        """As tall as the window can take, and no taller."""
        root = self.get_root()
        available = root.get_height() if root is not None else 0
        # Everything above and below the list: the margin at the top of the
        # card, the entry, and a hand's width of terminal left showing.
        self.scroller.set_max_content_height(max(96, available - 190))

    def close(self):
        self.set_visible(False)

    # ---------------------------------------------------------- the list --

    def _refilter(self):
        query = self.entry.get_text().strip()
        self._shown = rank(query, self._commands)[:MAX_ROWS] if query else \
            self._commands[:MAX_ROWS]
        self._chosen = 0
        self._rebuild()

    def _rebuild(self):
        child = self.rows.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.rows.remove(child)
            child = following

        self.empty.set_visible(not self._shown)
        last_group = None
        for index, command in enumerate(self._shown):
            if command.group and command.group != last_group:
                heading = Gtk.Label(label=command.group, xalign=0.0)
                heading.add_css_class("palette-group")
                self.rows.append(heading)
                last_group = command.group

            row = Gtk.Box(spacing=8)
            row.add_css_class("palette-row")
            if index == self._chosen:
                row.add_css_class("chosen")
            row._command = command        # read back by the click handler
            row._index = index

            label = Gtk.Label(label=command.label, xalign=0.0)
            label.set_hexpand(True)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            row.append(label)

            if command.hint:
                hint = Gtk.Label(label=command.hint, xalign=1.0)
                hint.add_css_class("palette-hint")
                row.append(hint)
            if command.accel:
                accel = Gtk.Label(label=command.accel)
                accel.add_css_class("palette-key")
                row.append(accel)
            self.rows.append(row)

    def _move(self, step):
        if not self._shown:
            return
        self._chosen = (self._chosen + step) % len(self._shown)
        self._rebuild()
        self._reveal()

    def _reveal(self):
        """Keep the chosen row inside the scroller's view."""
        row = next((child for child in self._row_widgets()
                    if getattr(child, "_index", None) == self._chosen), None)
        if row is None:
            return
        adjustment = self.scroller.get_vadjustment()
        ok, point = row.compute_point(self.rows, Graphene.Point().init(0, 0))
        if not ok:
            return
        top = point.y
        bottom = top + row.get_height()
        if top < adjustment.get_value():
            adjustment.set_value(top)
        elif bottom > adjustment.get_value() + adjustment.get_page_size():
            adjustment.set_value(bottom - adjustment.get_page_size())

    def _row_widgets(self):
        child = self.rows.get_first_child()
        while child is not None:
            yield child
            child = child.get_next_sibling()

    def _activate(self):
        if 0 <= self._chosen < len(self._shown):
            command = self._shown[self._chosen]
            self.close()
            self._on_run(command)

    def _on_click(self, gesture, _n, x, y):
        widget = self.rows.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and not hasattr(widget, "_command"):
            widget = widget.get_parent()
            if widget is self.rows:
                return
        if widget is None:
            return
        self.close()
        self._on_run(widget._command)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_key(self, _controller, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            self._on_run(None)
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Tab):
            self._move(1)
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_ISO_Left_Tab):
            self._move(-1)
            return True
        if keyval in (Gdk.KEY_Page_Down,):
            self._move(4)
            return True
        if keyval in (Gdk.KEY_Page_Up,):
            self._move(-4)
            return True
        return False
