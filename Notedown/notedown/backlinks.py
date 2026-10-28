"""What links here: the other half of every `[[link]]`.

A link written in one note is a fact about two notes, and the one it points
at is where it is useful to know — that is where you are when you wonder what
else touched this idea. So the notes that link to the one in front are listed
under it, each with the line it does it on, and a click opens that note at
that line.

The drawer is at the bottom rather than at the side, for the same reason the
find bar is: a side panel is the first thing to go in a 455px column, and
this is most worth having exactly when a note is open on its own in one.

Below the backlinks are this note's own links that lead nowhere yet. A link
to a note not written is a promise, and a list of them is a list of what is
still to write; clicking one offers to write it.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, Pango  # noqa: E402

from . import markdown  # noqa: E402


class Backlinks(Gtk.Revealer):
    """The notes that link to this one, and this one's links to nothing yet.

    `on_open(path, line)` is called for a backlink, with its 1-based line;
    `on_unwritten(target)` for a link that leads nowhere.
    """

    def __init__(self, on_open, on_unwritten):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        self.on_open = on_open
        self.on_unwritten = on_unwritten
        self.links = []
        self.unwritten: list[str] = []

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class("backlinks-pane")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("backlinks-header")
        self.heading = Gtk.Label(label="Backlinks", xalign=0, hexpand=True,
                                 ellipsize=Pango.EllipsizeMode.END,
                                 css_classes=["heading"])
        header.append(self.heading)
        self.count = Gtk.Label(label="", xalign=1, css_classes=["backlinks-count"])
        header.append(self.count)
        box.append(header)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", self._activated)
        self.empty = Gtk.Label(label="", justify=Gtk.Justification.CENTER, wrap=True,
                               css_classes=["backlinks-empty"])
        self.list.set_placeholder(self.empty)

        scroller = Gtk.ScrolledWindow(hexpand=True, propagate_natural_height=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(190)
        scroller.set_child(self.list)
        box.append(scroller)
        self.set_child(box)

    def show(self, links, unwritten, saved=True):
        """`links` are brain.Backlink; `unwritten` the targets with no note."""
        self.links = list(links)
        self.unwritten = list(unwritten)
        while (row := self.list.get_first_child()) is not None:
            self.list.remove(row)

        sources = {link.path for link in self.links}
        if not saved:
            self.heading.set_text("Backlinks")
            self.empty.set_text("Save this note, and what links to it is listed here.")
        elif sources:
            self.heading.set_text(f"Linked from {len(sources)} "
                                  f"{'note' if len(sources) == 1 else 'notes'}")
        else:
            self.heading.set_text("Backlinks")
            self.empty.set_text("Nothing links here yet.")
        self.count.set_text(
            f"{len(self.unwritten)} {'link leads' if len(self.unwritten) == 1 else 'links lead'}"
            f" nowhere yet" if self.unwritten else "")

        for link in self.links:
            row = Gtk.ListBoxRow(css_classes=["backlink-row"])
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            top.append(Gtk.Label(label=link.title, xalign=0, hexpand=True,
                                 ellipsize=Pango.EllipsizeMode.END,
                                 css_classes=["backlink-title"]))
            top.append(Gtk.Label(label=f"line {link.line}", xalign=1,
                                 css_classes=["backlink-line"]))
            box.append(top)
            # The line as it reads, not as it is typed: a context full of
            # brackets and asterisks is harder to recognise than the sentence.
            context = markdown.spans_text(markdown.inline(link.context)).strip()
            box.append(Gtk.Label(label=context, xalign=0,
                                 ellipsize=Pango.EllipsizeMode.END,
                                 css_classes=["backlink-context"]))
            row.set_child(box)
            row._open = (str(link.path), link.line)
            self.list.append(row)

        for target in self.unwritten:
            row = Gtk.ListBoxRow(css_classes=["backlink-row", "backlink-unwritten"])
            label = Gtk.Label(label=f"{target}  — not written yet", xalign=0,
                              ellipsize=Pango.EllipsizeMode.END,
                              tooltip_text=f"Make a note called {target}")
            row.set_child(label)
            row._unwritten = target
            self.list.append(row)

    def _activated(self, _list, row):
        if getattr(row, "_open", None):
            self.on_open(*row._open)
        elif getattr(row, "_unwritten", None):
            self.on_unwritten(row._unwritten)
