"""Go to a note by its name: Ctrl+P, a few letters, Enter.

A second brain of a few thousand notes is not browsed; it is asked. The file
dialog is the wrong tool for that twice over — it wants a folder before a
name, and it knows file names rather than titles — so this asks the Order's
index, which knows every note's title, aliases and file name, and matches on
all of them, accents and case aside.

With nothing typed it lists the notes opened most recently, because the note
wanted next is usually one of the last few. Nothing in it needs the mouse.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk, Pango  # noqa: E402


class Switcher(Adw.Dialog):
    """`find(query)` and `recent()` give (title, detail, path); `on_open(path)`."""

    def __init__(self, find, recent, on_open):
        super().__init__(title="Go to Note", content_width=460, content_height=440)
        self.find = find
        self.recent = recent
        self.on_open = on_open
        self.rows: list[tuple[str, str, str]] = []

        self.entry = Gtk.SearchEntry(placeholder_text="A note's title, or part of it",
                                     hexpand=True)
        self.entry.connect("search-changed", lambda *_: self.fill())
        self.entry.connect("activate", lambda *_: self.open_selected())
        self.entry.connect("stop-search", lambda *_: self.close())
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.entry.add_controller(keys)

        header = Adw.HeaderBar(title_widget=self.entry)
        self.caption = Gtk.Label(label="Recent", xalign=0,
                                 css_classes=["switcher-caption"])
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", lambda _list, row: self._open(row))
        self.empty = Gtk.Label(label="", wrap=True, justify=Gtk.Justification.CENTER,
                               css_classes=["switcher-empty"])
        self.list.set_placeholder(self.empty)

        self.scroller = Gtk.ScrolledWindow(vexpand=True)
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_child(self.list)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.caption)
        body.append(self.scroller)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(body)
        self.set_child(view)
        self.set_focus(self.entry)
        self.fill()

    def fill(self):
        """The rows for what is typed now — or the recent notes, for nothing."""
        query = self.entry.get_text().strip()
        try:
            self.rows = list(self.find(query) if query else self.recent())
        except Exception:  # noqa: BLE001 - an index that will not answer is no rows
            self.rows = []
        self.caption.set_visible(not query and bool(self.rows))
        self.empty.set_text(f"No note is called anything like “{query}”."
                            if query else "No notes opened yet. Type to find one.")
        while (row := self.list.get_first_child()) is not None:
            self.list.remove(row)
        for title, detail, path in self.rows:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            box.append(Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                 css_classes=["switcher-title"]))
            if detail:
                box.append(Gtk.Label(label=detail, xalign=0,
                                     ellipsize=Pango.EllipsizeMode.START,
                                     css_classes=["switcher-detail"]))
            row = Gtk.ListBoxRow(child=box)
            row._path = path
            self.list.append(row)
        first = self.list.get_row_at_index(0)
        if first is not None:
            self.list.select_row(first)

    def _key(self, _controller, keyval, _keycode, _state):
        step = {Gdk.KEY_Down: 1, Gdk.KEY_KP_Down: 1, Gdk.KEY_Up: -1, Gdk.KEY_KP_Up: -1,
                Gdk.KEY_Page_Down: 8, Gdk.KEY_Page_Up: -8}.get(keyval)
        if step is None or not self.rows:
            return False
        row = self.list.get_selected_row()
        index = (row.get_index() if row is not None else -1) + step
        target = self.list.get_row_at_index(max(0, min(len(self.rows) - 1, index)))
        self.list.select_row(target)
        self._reveal(target)
        return True

    def _reveal(self, row):
        """Scroll the selection into sight, leaving the focus in the entry."""
        found, bounds = row.compute_bounds(self.list)
        if not found:
            return
        adjustment = self.scroller.get_vadjustment()
        top = bounds.get_y()
        bottom = top + bounds.get_height()
        if top < adjustment.get_value():
            adjustment.set_value(top)
        elif bottom > adjustment.get_value() + adjustment.get_page_size():
            adjustment.set_value(bottom - adjustment.get_page_size())

    def open_selected(self):
        row = self.list.get_selected_row() or self.list.get_row_at_index(0)
        if row is not None:
            self._open(row)

    def _open(self, row):
        path = getattr(row, "_path", None)
        if path:
            self.close()
            self.on_open(path)
