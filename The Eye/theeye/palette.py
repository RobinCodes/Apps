"""Every command, by name: the palette.

Ctrl+K, a few letters, Return. Built from the same table the keys are bound
from (`shortcuts.py`), so everything the app can do is in it — including the
commands that have no key of their own, like *Use as a palace locus* — and it
cannot offer something the window has not installed.

Matching is a subsequence with a score, the way Hermione's palette does it:
a match at the start of a word counts for more than one in the middle, a run
of letters for more than scattered ones, and a short name beats a long one
on an equal score, so `imp` finds *Import notebook pages* and `pal` the
palace before the palette itself.

It floats on the window's overlay rather than in a popover, which is what
lets the glass behind it be real.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import shortcuts, widgets  # noqa: E402

MAX_ROWS = 40


def score(needle: str, haystack: str) -> int:
    """How well `needle` matches `haystack`; 0 is not at all."""
    if not needle:
        return 1
    text, want = haystack.lower(), needle.lower()
    if want in text:
        at = text.index(want)
        bonus = 400 if at == 0 else (200 if not text[at - 1].isalnum() else 0)
        return 1000 + bonus - at * 2 + int(60 * len(want) / max(1, len(text)))
    total, position, run = 0, 0, 0
    for character in want:
        found = text.find(character, position)
        if found < 0:
            return 0
        if found == position and position > 0:
            run += 1
            total += 18 + run * 6
        else:
            run = 0
            total += 10
        if found == 0 or not text[found - 1].isalnum():
            total += 25
        total -= min(found - position, 12)
        position = found + 1
    return max(1, total)


def commands() -> list[tuple[str, str, str]]:
    """(label, action, keys) for every command in the table, once each."""
    seen, out = set(), []
    for action, keys, label, _group, _scope in shortcuts.SHORTCUTS:
        if not action or action in seen:
            continue
        seen.add(action)
        out.append((label, action, shortcuts.pretty(keys[0]) if keys else ""))
    return out


def rank(items, needle: str, limit: int = MAX_ROWS):
    scored = [(score(needle, label), label, action, keys)
              for label, action, keys in items]
    scored = [row for row in scored if row[0] > 0]
    scored.sort(key=lambda row: (-row[0], len(row[1]), row[1].lower()))
    return [(label, action, keys) for _s, label, action, keys in scored[:limit]]


class Palette(Gtk.Box):
    """The contents of the floating box; `run(action)` is the window's to give."""

    __gtype_name__ = "TheEyePalette"

    def __init__(self, run, on_close):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         margin_start=10, margin_end=10, margin_top=10,
                         margin_bottom=10)
        self._run = run
        self._on_close = on_close
        self.shown = []
        self.active = 0
        self.set_size_request(460, -1)

        self.entry = Gtk.Entry(placeholder_text="Do…")
        self.entry.connect("changed", lambda _e: self.refresh())
        self.entry.connect("activate", lambda _e: self.run_active())
        keys = Gtk.EventControllerKey.new()
        keys.connect("key-pressed", self._on_key)
        self.entry.add_controller(keys)
        self.append(self.entry)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.connect("row-activated", self._on_row)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           max_content_height=360,
                                           propagate_natural_height=True)
        self.scroller.set_child(self.list)
        self.append(self.scroller)

    def open(self):
        self.entry.set_text("")
        self.refresh()
        GLib.idle_add(lambda: (self.entry.grab_focus(), GLib.SOURCE_REMOVE)[1])

    def refresh(self):
        self.shown = rank(commands(), self.entry.get_text().strip())
        while (child := self.list.get_first_child()) is not None:
            self.list.remove(child)
        for label, _action, keys in self.shown:
            row = widgets.row(widgets.label(label, xalign=0.0, hexpand=True, ellipsize=3),
                              widgets.label(keys, "mono", "dim", xalign=1.0),
                              spacing=12, margin_start=8, margin_end=8,
                              margin_top=5, margin_bottom=5)
            self.list.append(row)
        self.active = 0
        self._highlight()

    def _highlight(self):
        row = self.list.get_row_at_index(self.active)
        if row is None:
            return
        self.list.select_row(row)
        ok, bounds = row.compute_bounds(self.list)
        adjustment = self.scroller.get_vadjustment()
        if ok and adjustment is not None:
            top, height = bounds.get_y(), bounds.get_height()
            if top < adjustment.get_value():
                adjustment.set_value(top)
            elif top + height > adjustment.get_value() + adjustment.get_page_size():
                adjustment.set_value(top + height - adjustment.get_page_size())

    def _move(self, by: int):
        if self.shown:
            self.active = max(0, min(len(self.shown) - 1, self.active + by))
            self._highlight()

    def _on_key(self, _controller, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self._on_close()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Tab):
            self._move(1)
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_ISO_Left_Tab):
            self._move(-1)
            return True
        if keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            self._move(8 if keyval == Gdk.KEY_Page_Down else -8)
            return True
        return False

    def _on_row(self, _list, row):
        self.active = row.get_index()
        self.run_active()

    def run_active(self):
        if not self.shown:
            return
        _label, action, _keys = self.shown[min(self.active, len(self.shown) - 1)]
        self._on_close()
        # After the palette has gone, so a command that opens a panel of its
        # own is not opened behind it.
        GLib.idle_add(lambda: (self._run(action), GLib.SOURCE_REMOVE)[1])
