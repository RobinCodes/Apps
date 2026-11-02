"""Ctrl+K: search everything from anywhere.

Rooms first, because going somewhere is the commonest thing to want and typing
two letters of a name is the quickest way to do it. Then `brain.search`: the
brain's own index when it exists, note titles when it does not. Whatever is
picked goes through the palace's routing, so a hit opens in whichever room opens
that kind of thing, and the desktop is the last resort, not the first.

Searching is on a worker, and only the newest search is ever shown: typing is
faster than a query, and the answer wanted is the one for what is typed now.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import brain, jobs, widgets  # noqa: E402


def snippet_markup(text: str) -> str:
    """The index's «match» markers as bold, and everything else escaped."""
    escaped = GLib.markup_escape_text(text)
    return escaped.replace("«", "<b>").replace("»", "</b>")


class SearchDialog(Adw.Dialog):
    def __init__(self, palace, search=brain.search):
        super().__init__(title="Search the palace", content_width=560, content_height=480)
        self.palace = palace
        self.search = search
        self.generation = 0
        self.actions: dict = {}           # row -> what picking it does

        self.entry = Gtk.SearchEntry(placeholder_text="A room, a note, a thought…",
                                     hexpand=True, margin_start=10, margin_end=10,
                                     margin_bottom=8)
        self.entry.connect("search-changed", lambda _e: self._changed())
        self.entry.connect("activate", lambda _e: self._pick_first())
        self.entry.connect("stop-search", lambda _e: self.close())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_entry_key)
        self.entry.add_controller(keys)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE,
                                css_classes=["navigation-sidebar"])
        self.list.connect("row-activated", lambda _l, row: self._pick(row))
        self.status = widgets.label("", "dim-label", wrap=True)
        self.status.set_margin_start(16)
        self.status.set_margin_top(10)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.list)
        content.append(self.status)
        scroller = Gtk.ScrolledWindow(child=content, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.add_top_bar(self.entry)
        view.set_content(scroller)
        self.set_child(view)
        self.set_focus(self.entry)
        self._changed()

    # -- searching -------------------------------------------------------

    def _changed(self) -> None:
        text = self.entry.get_text().strip()
        self.generation += 1
        generation = self.generation
        self._show_rooms(text)
        if not text:
            self.status.set_text("")
            return
        self.status.set_text("Searching…")

        def work():
            return self.search(text)

        def done(hits):
            if generation != self.generation:
                return
            self.status.set_text("" if hits or self.actions else f"Nothing for “{text}”")
            self._add_hits(hits)

        def failed(exc):
            if generation == self.generation:
                self.status.set_text(f"The brain could not be searched: {exc}")

        jobs.run(work, done, failed)

    def _show_rooms(self, text: str) -> None:
        widgets.clear(self.list)
        self.actions.clear()
        needle = text.casefold()
        for spec in self.palace.specs:
            if needle in spec.title.casefold():
                self._add_row(spec.title, spec.comment, None,
                              lambda title=spec.title: self.palace.show_room(title),
                              widgets.room_icon(spec, 20))

    def _add_hits(self, hits) -> None:
        for hit in hits:
            where = brain.places().relative(hit.path)
            self._add_row(hit.title, f"{hit.kind} · {where}", hit.snippet,
                          lambda path=str(hit.path): self.palace.open_target(path), None)

    def _add_row(self, title, subtitle, snippet, action, icon) -> None:
        row = Gtk.ListBoxRow()
        outer = Gtk.Box(spacing=10)
        if icon is not None:
            outer.append(icon)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        box.append(widgets.label(title, ellipsize=True))
        if subtitle:
            box.append(widgets.label(subtitle, "dim-label", "caption", ellipsize=True))
        if snippet:
            text = widgets.label("", "search-hit-snippet", ellipsize=True)
            text.set_markup(snippet_markup(snippet))
            box.append(text)
        outer.append(box)
        row.set_child(outer)
        self.list.append(row)
        self.actions[row] = action

    # -- picking ---------------------------------------------------------

    def _on_entry_key(self, _controller, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Down:
            first = self.list.get_row_at_index(0)
            if first is not None:
                self.list.select_row(first)
                first.grab_focus()
            return True
        return False

    def _pick_first(self) -> None:
        first = self.list.get_row_at_index(0)
        if first is not None:
            self._pick(first)

    def _pick(self, row) -> None:
        action = self.actions.get(row)
        self.close()
        if action is not None:
            action()
