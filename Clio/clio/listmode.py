"""The board as a list, top to bottom: what the axis shows, when a list is what you want.

A long board read through, or a narrow window, where an axis has names to drop
and a list does not. Spans come where they start, marked with their `}`, so the
list still says what was going on when.
"""

from __future__ import annotations

from html import escape

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk  # noqa: E402

from . import layout, widgets  # noqa: E402
from .axis import age_at  # noqa: E402


def in_order(board, hidden=frozenset(), ghosts=()):
    """Every item shown, by when it starts; a span before what happens inside it."""
    items = [item for item in board.items if layout.shown(item, hidden)] + list(ghosts)
    return sorted(items, key=lambda i: (i.first_day(), not i.span, i.title.casefold()))


def details(board, item) -> str:
    """The line under a title: when, how old, what kind of thing."""
    if item.span:
        text = f"{item.start.label()} – {item.end.label() if item.end else 'now'}"
    else:
        text = item.start.label()
    if board.born is not None and item.first_day() >= board.born.first_day():
        text += f"  ·  age {age_at(board.born, item.first_day())}"
    if item.categories:
        text += f"  ·  {', '.join(item.categories)}"
    return text


class ListMode(Gtk.ScrolledWindow):
    """`on_open(item)` is a row being activated."""

    def __init__(self, on_open):
        super().__init__(hexpand=True, vexpand=True)
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.on_open = on_open
        self.rows = []
        self.box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                               css_classes=["navigation-sidebar"])
        self.box.connect("row-activated", lambda _b, row: self.on_open(row.item))
        self.empty = Gtk.Label(label="Nothing on this board yet.", margin_top=40,
                               css_classes=["clio-row-detail"])
        self.box.set_placeholder(self.empty)
        self.set_child(self.box)

    def show_board(self, board, hidden=frozenset(), ghosts=()):
        widgets.clear(self.box)
        self.rows = []
        if board is None:
            return
        ghost_ids = {id(g) for g in ghosts}
        for item in in_order(board, hidden, ghosts):
            row = self._row(board, item, id(item) in ghost_ids)
            self.box.append(row)
            self.rows.append(row)

    def _row(self, board, item, ghost):
        colour = escape(board.colour_of(item))
        mark = Gtk.Label(use_markup=True, valign=Gtk.Align.START, xalign=0.5,
                         width_chars=2, css_classes=["clio-list-mark"])
        mark.set_markup(f'<span foreground="{colour}">{"}" if item.span else "●"}</span>')
        classes = ["clio-list-title"]
        if item.milestone:
            classes.append("clio-list-milestone")
        title = Gtk.Label(label=item.title or "Untitled", xalign=0, wrap=True, hexpand=True,
                          css_classes=classes)
        detail = Gtk.Label(label=details(board, item), xalign=0, wrap=True,
                           css_classes=["clio-list-detail"])
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        words.append(title)
        words.append(detail)
        if item.notes:
            words.append(Gtk.Label(label=item.notes[0], xalign=0, wrap=True,
                                   css_classes=["clio-list-detail"]))
        inside = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        inside.append(mark)
        inside.append(words)
        row = Gtk.ListBoxRow(child=inside)
        if ghost:
            row.add_css_class("clio-list-ghost")
        row.item = item
        return row
