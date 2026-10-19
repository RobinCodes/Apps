"""The three jobs people open a file manager to do and then do by hand.

What is duplicated, what is biggest, and what is empty. Each is a walk from
`search.py` shown as a list you can act on, which is the part that makes them
tools rather than reports: finding six copies of a photo is only useful if
five of them can go from the same window.

The safety rule here is worth stating. A duplicate finder that pre-selects
rows is a duplicate finder that eventually deletes somebody's only copy, so
nothing is ever selected to begin with, and the one action offered by default
is the trash rather than deletion.
"""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from . import dialogs, filetypes, ops, search, units  # noqa: E402

DUPLICATES = "duplicates"
LARGEST = "largest"
EMPTY = "empty"

TITLES = {
    DUPLICATES: ("Duplicates", "Files whose contents are identical. "
                               "Grouped — one of each group is worth keeping."),
    LARGEST: ("Biggest files", "What is actually taking up the room."),
    EMPTY: ("Empty folders", "Folders with nothing in them but other empty "
                             "folders. Deepest first, so removing them all "
                             "works in one go."),
}


class Finding(GObject.Object):
    """One row of a result list."""

    __gtype_name__ = "HermioneFinding"

    def __init__(self, path: str, size: int = 0, group: int = -1,
                 heading: str = ""):
        super().__init__()
        self.path = path
        self.size = size
        self.group = group
        self.heading = heading          # set for a group's first row
        self.ticked = False


class Tools:
    """One dialog, in one of three modes."""

    def __init__(self, parent: Gtk.Widget, mode: str, folder: str, config,
                 on_reveal=None, on_changed=None):
        self.parent = parent
        self.mode = mode
        self.folder = folder
        self.config = config
        self.on_reveal = on_reveal
        self.on_changed = on_changed
        self.stop = threading.Event()
        self.store = Gio.ListStore.new(Finding)

        title, blurb = TITLES[mode]
        self.dialog = dialogs._dialog(title, 620)
        self.dialog.set_content_height(560)
        self.dialog.connect("closed", lambda _d: self.stop.set())
        body = dialogs._page(self.dialog, title, subtitle=blurb)

        self.status = Gtk.Label(label=f"Looking through {dialogs._short(folder)}…",
                                xalign=0.0)
        self.status.add_css_class("dialog-body")
        body.append(self.status)

        self.spinner = Adw.Spinner()
        self.spinner.set_size_request(18, 18)
        self.spinner.set_halign(Gtk.Align.START)
        body.append(self.spinner)

        self.list_view = self._build_list()
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(self.list_view)
        scroller.set_vexpand(True)
        scroller.add_css_class("panel")
        body.append(scroller)

        body.append(self._actions())
        self._start()

    def present(self) -> None:
        self.dialog.present(self.parent)

    # -- the list ---------------------------------------------------------

    def _build_list(self) -> Gtk.ListView:
        factory = Gtk.SignalListItemFactory()

        def setup(_factory, item):
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            heading = Gtk.Label(xalign=0.0)
            heading.add_css_class("group-heading")
            heading.set_visible(False)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.set_margin_start(10)
            row.set_margin_end(10)
            row.set_margin_top(3)
            row.set_margin_bottom(3)
            tick = Gtk.CheckButton()
            icon = Gtk.Image()
            icon.set_pixel_size(16)
            icon.add_css_class("entry-icon")
            name = Gtk.Label(xalign=0.0)
            name.set_ellipsize(1)      # start: the end of a path matters more
            name.set_hexpand(True)
            size = Gtk.Label(xalign=1.0)
            size.add_css_class("numeric")
            row.append(tick)
            row.append(icon)
            row.append(name)
            row.append(size)
            outer.append(heading)
            outer.append(row)

            def update(finding):
                heading.set_label(finding.heading)
                heading.set_visible(bool(finding.heading))
                name.set_label(dialogs._short(finding.path))
                name.set_tooltip_text(finding.path)
                size.set_label(units.human_size(finding.size)
                               if finding.size else "")
                for css in list(icon.get_css_classes()):
                    if css.startswith("cat-"):
                        icon.remove_css_class(css)
                is_dir = finding.size == 0 and self.mode == EMPTY
                icon.set_from_gicon(filetypes.icon_for(finding.path, is_dir))
                icon.add_css_class(
                    f"cat-{filetypes.category(finding.path, is_dir)}")
                tick.set_active(finding.ticked)

            item.set_child(outer)
            item._update = update
            item._tick = tick
            item._handler = 0

        def bind(_factory, item):
            finding = item.get_item()
            item._update(finding)
            item._handler = item._tick.connect(
                "toggled", lambda button, f=finding: setattr(f, "ticked",
                                                             button.get_active())
                or self._count_ticked())

        def unbind(_factory, item):
            if item._handler:
                item._tick.disconnect(item._handler)
                item._handler = 0

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        factory.connect("unbind", unbind)

        selection = Gtk.NoSelection.new(self.store)
        view = Gtk.ListView.new(selection, factory)
        view.connect("activate", self._on_activate)
        return view

    def _on_activate(self, _view, position: int) -> None:
        finding = self.store.get_item(position)
        if finding is not None and self.on_reveal is not None:
            self.on_reveal(finding.path)

    # -- the buttons ------------------------------------------------------

    def _actions(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(8)

        self.tick_all = dialogs._button("Tick all but the first of each")
        if self.mode != DUPLICATES:
            self.tick_all.set_label("Tick everything")
        self.tick_all.connect("clicked", lambda _b: self._tick_sensible())
        row.append(self.tick_all)

        self.ticked_label = Gtk.Label(label="")
        self.ticked_label.add_css_class("dialog-body")
        self.ticked_label.set_hexpand(True)
        self.ticked_label.set_xalign(0.0)
        row.append(self.ticked_label)

        self.trash_button = dialogs._button("Move to trash", "destructive-action")
        self.trash_button.set_sensitive(False)
        self.trash_button.connect("clicked", lambda _b: self._act(ops.TRASH))
        row.append(self.trash_button)

        close = dialogs._button("Close")
        close.connect("clicked", lambda _b: self.dialog.close())
        row.append(close)
        return row

    def _count_ticked(self) -> None:
        count = 0
        total = 0
        for index in range(self.store.get_n_items()):
            finding = self.store.get_item(index)
            if finding.ticked:
                count += 1
                total += finding.size
        self.trash_button.set_sensitive(count > 0)
        if count:
            self.ticked_label.set_label(
                f"{count} ticked" + (f", {units.human_size(total)}" if total else ""))
        else:
            self.ticked_label.set_label("")

    def _tick_sensible(self) -> None:
        """Tick what is safe to tick.

        For duplicates that is everything *but* the first of each group, so
        one copy of each always survives. For the other two it is everything,
        because an empty folder and a big file have no "other copy".
        """
        seen_groups = set()
        for index in range(self.store.get_n_items()):
            finding = self.store.get_item(index)
            if self.mode == DUPLICATES:
                first = finding.group not in seen_groups
                seen_groups.add(finding.group)
                finding.ticked = not first
            else:
                finding.ticked = True
        self._refresh_rows()
        self._count_ticked()

    def _refresh_rows(self) -> None:
        # Nothing about the items changed as far as the model knows, so the
        # rows are told to rebind by replacing the model's contents with
        # themselves — cheaper than a signal per row for a list this size.
        count = self.store.get_n_items()
        if count:
            self.store.items_changed(0, count, count)

    def _act(self, kind: str) -> None:
        paths = [self.store.get_item(index).path
                 for index in range(self.store.get_n_items())
                 if self.store.get_item(index).ticked]
        if not paths:
            return

        def go():
            job = ops.Job(kind, paths)
            job.on_done = lambda result: GLib.idle_add(self._done, result, paths)
            job.start()

        dialogs.confirm(
            self.dialog,
            f"Move {len(paths)} items to the trash?",
            "They can be put back from the trash afterwards.",
            "Move to trash", go)

    def _done(self, result, paths: list[str]) -> bool:
        gone = set(result.trashed) | set(result.deleted)
        for index in reversed(range(self.store.get_n_items())):
            if self.store.get_item(index).path in gone:
                self.store.remove(index)
        self._count_ticked()
        if result.errors:
            dialogs.tell(self.dialog, "Some could not be moved",
                         "\n".join(result.errors[:8]))
        if self.on_changed is not None:
            self.on_changed()
        _ = paths
        return False

    # -- the walk ---------------------------------------------------------

    def _start(self) -> None:
        folder = self.folder
        mode = self.mode
        hidden = bool(self.config["show_hidden"])

        def worker():
            if mode == DUPLICATES:
                groups = search.duplicates(folder, self.stop, hidden)
                rows = []
                for number, group in enumerate(groups):
                    try:
                        size = os.path.getsize(group[0])
                    except OSError:
                        size = 0
                    for position, path in enumerate(group):
                        heading = ""
                        if position == 0:
                            heading = (f"{len(group)} copies, "
                                       f"{units.human_size(size)} each")
                        rows.append(Finding(path, size, number, heading))
                summary = (f"{len(groups)} groups, "
                           f"{sum(len(g) - 1 for g in groups)} redundant copies"
                           if groups else "No duplicates")
            elif mode == LARGEST:
                found = search.largest_files(folder, 200, self.stop, hidden)
                rows = [Finding(path, size) for path, size in found]
                summary = (f"{len(rows)} files, "
                           f"{units.human_size(sum(s for _p, s in found))} between them"
                           if rows else "Nothing here")
            else:
                found = search.empty_folders(folder, self.stop, hidden)
                rows = [Finding(path) for path in found]
                summary = (f"{len(rows)} empty folders" if rows
                           else "No empty folders")
            if not self.stop.is_set():
                GLib.idle_add(self._finished, rows, summary)

        threading.Thread(target=worker, daemon=True, name="hermione-tools").start()

    def _finished(self, rows: list, summary: str) -> bool:
        if self.stop.is_set():
            return False
        self.spinner.set_visible(False)
        self.status.set_label(summary)
        self.store.splice(0, 0, rows)
        self.tick_all.set_sensitive(bool(rows))
        return False


def show(parent: Gtk.Widget, mode: str, folder: str, config,
         on_reveal=None, on_changed=None) -> None:
    Tools(parent, mode, folder, config, on_reveal, on_changed).present()
