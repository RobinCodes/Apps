"""The list of boards, and the button that makes a new one."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk  # noqa: E402

from . import widgets  # noqa: E402


def summary(board) -> str:
    events, spans = len(board.events), len(board.spans)
    return (f"{events} {'event' if events == 1 else 'events'}, "
            f"{spans} {'span' if spans == 1 else 'spans'}")


class Sidebar(Gtk.Box):
    def __init__(self, on_open, on_new):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["clio-sidebar"])
        self.on_open = on_open
        self._filling = False
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, css_classes=["clio-sidebar-top"])
        top.append(Gtk.Label(label="Boards", xalign=0, hexpand=True,
                             css_classes=["clio-row-title"]))
        new = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"],
                         tooltip_text="New board (Ctrl+Shift+N)")
        new.connect("clicked", lambda _b: on_new())
        top.append(new)
        self.append(top)
        self.box = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.box.set_placeholder(Gtk.Label(label="No boards yet.", margin_top=24,
                                           css_classes=["clio-row-detail"]))
        self.box.connect("row-selected", self._selected)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.box)
        self.append(scroller)

    def _selected(self, _box, row):
        if not self._filling and row is not None:
            self.on_open(row.board)

    def show_boards(self, boards, current):
        self._filling = True
        widgets.clear(self.box)
        for board in boards:
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            words.append(Gtk.Label(label=board.title, xalign=0, wrap=True,
                                   css_classes=["clio-row-title"]))
            words.append(Gtk.Label(label=summary(board), xalign=0,
                                   css_classes=["clio-row-detail"]))
            row = Gtk.ListBoxRow(child=words)
            row.board = board
            self.box.append(row)
            if board is current:
                self.box.select_row(row)
        self._filling = False
