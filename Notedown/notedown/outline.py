"""The heading sidebar: where you are in the document, and how to get elsewhere.

A Markdown file long enough to need an editor is long enough to get lost in,
and its headings already describe it — so the sidebar is built from them and
from nothing else. No manual bookmarks, no folding state to keep in sync: the
outline is a view of the text, rebuilt whenever the text changes.

It is a `Gtk.ListBox` rather than a tree, with the nesting drawn as indentation.
A tree widget would bring expanders, and an expander on a heading is a promise
that collapsing it does something to the document, which here it does not.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Pango  # noqa: E402


class Outline(Gtk.Box):
    """Headings, in order, with the one the cursor is in marked.

    `on_activate(line)` is called with the source line of a clicked heading.
    """

    def __init__(self, on_activate):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_activate = on_activate
        self.add_css_class("outline-pane")
        self._entries: list[tuple[int, str, int, str]] = []
        self._rows: list[Gtk.ListBoxRow] = []
        self._current = -1
        self._selecting = False

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("outline-header")
        self.count = Gtk.Label(label="Outline", xalign=0, hexpand=True,
                               css_classes=["heading"])
        header.append(self.count)
        self.append(header)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", self._activated)

        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.list)
        self.append(scroller)

        self._empty = Gtk.Label(
            label="No headings yet.\nLines starting with # appear here.",
            justify=Gtk.Justification.CENTER, wrap=True,
            css_classes=["outline-empty"])
        self.list.set_placeholder(self._empty)

    def set_entries(self, entries):
        """entries is (level, title, line, anchor), as markdown.outline gives."""
        if entries == self._entries:
            return
        self._entries = list(entries)
        self._rows = []
        while (row := self.list.get_first_child()) is not None:
            self.list.remove(row)

        # The shallowest heading in the document is the left margin, so a file
        # written entirely in `##` is not indented for no reason.
        top = min((level for level, _, _, _ in self._entries), default=1)

        for index, (level, title, line, _anchor) in enumerate(self._entries):
            label = Gtk.Label(label=title or "Untitled", xalign=0,
                              ellipsize=Pango.EllipsizeMode.END,
                              max_width_chars=28, hexpand=True)
            label.add_css_class(f"outline-h{min(level, 6)}")
            label.set_tooltip_text(title)
            label.set_margin_start(min(level - top, 4) * 12)

            row = Gtk.ListBoxRow(child=label, css_classes=["outline-row"])
            row._line = line
            row._index = index
            self.list.append(row)
            self._rows.append(row)

        self.count.set_text(f"Outline · {len(self._entries)}"
                            if self._entries else "Outline")
        self._current = -1

    def mark_line(self, line: int):
        """Highlight the heading whose section contains `line`."""
        found = -1
        for index, (_level, _title, start, _anchor) in enumerate(self._entries):
            if start <= line:
                found = index
            else:
                break
        if found == self._current:
            return
        if 0 <= self._current < len(self._rows):
            self._rows[self._current].remove_css_class("outline-current")
        self._current = found
        if 0 <= found < len(self._rows):
            self._rows[found].add_css_class("outline-current")

    def _activated(self, _list, row):
        line = getattr(row, "_line", None)
        if line is not None and self.on_activate:
            self.on_activate(line)
