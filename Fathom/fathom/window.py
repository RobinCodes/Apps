"""The window: the list on one side, the map on the other, one scan behind both.

Selecting in either half selects in the other, which is the whole point of
having both — a rectangle you cannot name is not an answer, and a row you
cannot see the size of is not either.

The map can be zoomed into a folder while the tree stays showing the whole
scan, so the breadcrumb above the map is about the map alone.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import jobs, listview, scan, treemap, volumes, widgets  # noqa: E402
from .config import Config  # noqa: E402
from .units import (human_count, human_duration, human_percent,  # noqa: E402
                    human_size)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Fathom")
        self.config = Config()
        self.result: scan.Result | None = None
        self.scan: scan.Scan | None = None
        self.map_root = None
        self._selected = None
        self._menu_node = None
        self._loaded_extras = False
        self._top_files: list = []
        self._search_generation = 0

        self.set_default_size(self.config["window_width"],
                              self.config["window_height"])
        # Applied before the window is mapped, because that is the only way to
        # come up maximised without a frame at the wrong size. Whether it was
        # the right thing to do is only knowable later — see _on_realize.
        self._maximize_pending = False
        if self.config["window_maximized"]:
            self.maximize()
            self._maximize_pending = True
        self.connect("realize", self._on_realize)

        self._build_actions()
        self._build_ui()
        self._install_accelerators()
        self.connect("close-request", self._on_close)

        GLib.idle_add(self._start_up)

    # ----------------------------------------------------------------- ui --

    def _build_ui(self):
        self.toasts = Adw.ToastOverlay()
        outer = Adw.ToolbarView()

        header = Adw.HeaderBar()
        self.back_button = Gtk.Button(icon_name="go-previous-symbolic",
                                      tooltip_text="Back to the disk list")
        self.back_button.connect("clicked", lambda _b: self.show_start())
        self.back_button.set_visible(False)
        header.pack_start(self.back_button)

        self.rescan_button = Gtk.Button(icon_name="view-refresh-symbolic",
                                        tooltip_text="Scan again  (Ctrl+R)")
        self.rescan_button.connect("clicked", lambda _b: self.rescan())
        self.rescan_button.set_visible(False)
        header.pack_start(self.rescan_button)

        self.switcher = Adw.ViewSwitcher()
        self.switcher.add_css_class("tab-switcher")
        self.switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        # Nothing to switch between until something has been scanned.
        self.switcher.set_visible(False)
        header.set_title_widget(self.switcher)

        menu = Gio.Menu()
        section = Gio.Menu()
        section.append("Scan a folder…", "win.open")
        section.append("Scan again", "win.rescan")
        section.append("New Window", "app.new-window")
        menu.append_section(None, section)
        view = Gio.Menu()
        view.append("Size on disk", "win.basis::alloc")
        view.append("Apparent size", "win.basis::size")
        menu.append_section("Measure by", view)
        colours = Gio.Menu()
        colours.append("Colour by file type", "win.colour::type")
        colours.append("Colour by top folder", "win.colour::folder")
        colours.append("Colour by depth", "win.colour::depth")
        menu.append_section("The map", colours)
        rest = Gio.Menu()
        rest.append("Export this view as CSV…", "win.export")
        rest.append("Preferences", "win.preferences")
        rest.append("Keyboard shortcuts", "win.shortcuts")
        rest.append("About Fathom", "win.about")
        menu.append_section(None, rest)

        self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic",
                                          menu_model=menu,
                                          tooltip_text="Main menu")
        header.pack_end(self.menu_button)

        self.search_button = Gtk.ToggleButton(icon_name="system-search-symbolic",
                                              tooltip_text="Search  (Ctrl+F)")
        self.search_button.set_visible(False)
        header.pack_end(self.search_button)
        outer.add_top_bar(header)

        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Filter by name — *.iso, or part of a name",
            hexpand=True)
        self.search_entry.connect("search-changed", self._on_search)
        clamp = Adw.Clamp(maximum_size=560, child=self.search_entry)
        self.search_bar.set_child(clamp)
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_key_capture_widget(self)
        self.search_button.bind_property(
            "active", self.search_bar, "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL)
        outer.add_top_bar(self.search_bar)

        self.banner = Gtk.Revealer()
        self.banner.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.banner.set_child(self._build_progress())
        outer.add_top_bar(self.banner)

        self.stack_pages = Gtk.Stack()
        self.stack_pages.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        self.start_page = widgets.StartPage(self.start_scan, self.choose_folder)
        self.stack_pages.add_named(self.start_page, "start")
        self.stack_pages.add_named(self._build_results(), "results")
        outer.set_content(self.stack_pages)

        self.status = Gtk.Label(label="", xalign=0, ellipsize=3,
                                css_classes=["status-strip", "dim-label"])
        outer.add_bottom_bar(self.status)

        self.toasts.set_child(outer)
        self.set_content(self.toasts)
        self._add_breakpoint()

    def _build_progress(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                      css_classes=["scan-banner"])
        self.progress = Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER)
        self.progress_label = Gtk.Label(label="", xalign=0, width_chars=26,
                                        css_classes=["caption"])
        stop = Gtk.Button(label="Stop", css_classes=["flat"],
                          valign=Gtk.Align.CENTER)
        stop.connect("clicked", lambda _b: self.cancel_scan())
        box.append(self.progress_label)
        box.append(self.progress)
        box.append(stop)
        return box

    def _build_results(self):
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL,
                               shrink_start_child=False, shrink_end_child=False,
                               resize_start_child=True, resize_end_child=True)
        self.paned.set_position(self.config["split_position"])

        self.views = Adw.ViewStack()
        self.switcher.set_stack(self.views)

        self.tree = listview.TreeView(self.config)
        self.tree.connect("selected", lambda _v, node: self.select(node, "tree"))
        self.tree.connect("activated", self._on_row_activated)
        self.tree.connect("menu", self._on_menu)
        page = self.views.add_titled(self.tree, "tree", "Tree")
        page.set_icon_name("view-list-symbolic")

        self.files = listview.FilesView(self.config)
        self.files.connect("selected", lambda _v, node: self.select(node, "files"))
        self.files.connect("activated", self._on_row_activated)
        self.files.connect("menu", self._on_menu)
        page = self.views.add_titled(self.files, "files", "Biggest files")
        page.set_icon_name("document-open-symbolic")

        self.types = listview.TypesView(self.config)
        page = self.views.add_titled(self.types, "types", "File types")
        page.set_icon_name("application-x-addon-symbolic")

        self.views.connect("notify::visible-child", self._on_page_changed)
        self.paned.set_start_child(self.views)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.crumbs = widgets.Breadcrumbs()
        self.crumbs.connect("go", lambda _c, node: self.zoom_map(node))
        right.append(self.crumbs)

        self.map = treemap.TreemapView(self.config)
        self.map.connect("selected", lambda _m, node: self.select(node, "map"))
        self.map.connect("hovered", self._on_hover)
        self.map.connect("activated", self._on_map_activated)
        self.map.connect("menu", self._on_menu)
        frame = Gtk.Frame(css_classes=["treemap-frame"], margin_start=6,
                          margin_end=6, margin_bottom=2)
        frame.set_child(self.map)
        frame.set_vexpand(True)
        right.append(frame)

        self.legend = widgets.Legend()
        right.append(self.legend)
        self.paned.set_end_child(right)
        return self.paned

    def _add_breakpoint(self):
        """Below 800px the side-by-side layout stops being two halves.

        Robin's compositor tiles windows, so a window that cannot be made
        narrow is a window that cannot share a screen. Flipping the pane to
        vertical and dropping the view switcher's labels takes the floor from
        around 780px to around 360.
        """
        condition = Adw.BreakpointCondition.new_length(
            Adw.BreakpointConditionLengthType.MAX_WIDTH, 800,
            Adw.LengthUnit.PX)
        breakpoint_ = Adw.Breakpoint.new(condition)
        breakpoint_.add_setter(self.paned, "orientation",
                               Gtk.Orientation.VERTICAL)
        breakpoint_.add_setter(self.switcher, "policy",
                               Adw.ViewSwitcherPolicy.NARROW)
        breakpoint_.add_setter(self.legend, "visible", False)
        # The name is the column the tree exists for, and it is the one that
        # gives way when the fixed-width numeric columns will not: at 420px it
        # collapsed to a single ellipsis. Narrow, only the name and the size
        # earn their space. Adw puts the rest back on the way out.
        for key in ("size", "percent", "items", "files", "folders", "modified"):
            breakpoint_.add_setter(self.tree.columns[key], "visible", False)
        breakpoint_.add_setter(self.paned, "position", 420)
        self.add_breakpoint(breakpoint_)

    # ------------------------------------------------------------ actions --

    def _build_actions(self):
        for name, handler in (
            ("open", lambda *_: self.choose_folder()),
            ("rescan", lambda *_: self.rescan()),
            ("export", lambda *_: self.export_csv()),
            ("preferences", lambda *_: self.show_preferences()),
            ("shortcuts", lambda *_: self.show_shortcuts()),
            ("about", lambda *_: self.show_about()),
            ("zoom-out", lambda *_: self.zoom_out()),
            ("open-item", lambda *_: self.open_selected()),
            ("reveal", lambda *_: self.reveal_selected()),
            ("copy-path", lambda *_: self.copy_path()),
            ("trash", lambda *_: self.delete_selected(permanent=False)),
            ("delete", lambda *_: self.delete_selected(permanent=True)),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

        basis = Gio.SimpleAction.new_stateful(
            "basis", GLib.VariantType.new("s"),
            GLib.Variant.new_string(self.config["size_basis"]))
        basis.connect("activate", self._on_basis)
        self.add_action(basis)

        colour = Gio.SimpleAction.new_stateful(
            "colour", GLib.VariantType.new("s"),
            GLib.Variant.new_string(self.config["treemap_colour"]))
        colour.connect("activate", self._on_colour)
        self.add_action(colour)

    def _install_accelerators(self):
        app = self.get_application()
        if app is None:
            return
        for action, keys in (
            ("win.open", ["<Control>o"]),
            ("win.rescan", ["<Control>r", "F5"]),
            ("win.export", ["<Control>e"]),
            ("win.zoom-out", ["<Alt>Up", "BackSpace"]),
            ("win.open-item", ["<Control>Return"]),
            ("win.copy-path", ["<Control>c"]),
            ("win.trash", ["Delete"]),
            ("win.delete", ["<Shift>Delete"]),
            ("win.shortcuts", ["<Control>question"]),
        ):
            app.set_accels_for_action(action, keys)

    # ------------------------------------------------------------ startup --

    def _start_up(self):
        """Open on the list of disks, having scanned nothing.

        The other apps in this repository reopen what you had last, and this
        one deliberately does not: reopening a document is instant, and
        rescanning a filesystem is ten seconds of disk before the user has
        asked for anything. What was scanned last is remembered, and is the
        first thing under "Recent" — one click, when it is wanted.
        """
        self.refresh_start_page()
        return GLib.SOURCE_REMOVE

    def refresh_start_page(self):
        def work():
            return volumes.volumes()

        def done(found):
            self.start_page.set_volumes(found)
            self.start_page.set_recent(
                [p for p in self.config["recent_paths"] if os.path.isdir(p)])

        jobs.run(work, done)

    def show_start(self):
        self.cancel_scan()
        self.stack_pages.set_visible_child_name("start")
        self.back_button.set_visible(False)
        self.rescan_button.set_visible(False)
        self.search_button.set_visible(False)
        self.search_bar.set_search_mode(False)
        self.switcher.set_visible(False)
        self.set_title("Fathom")
        self.status.set_text("")
        self.refresh_start_page()

    def select_path_arg(self, path: str):
        """A path from the command line."""
        if os.path.isdir(path):
            self.start_scan(path)
        elif os.path.exists(path):
            self.start_scan(os.path.dirname(os.path.abspath(path)))
        else:
            widgets.toast(self, f"{path} is not there")

    def choose_folder(self):
        dialog = Gtk.FileDialog(title="Choose a folder to scan")
        start = self.config["last_path"] or os.path.expanduser("~")
        if os.path.isdir(start):
            dialog.set_initial_folder(Gio.File.new_for_path(start))

        def chosen(_dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            if folder is not None and folder.get_path():
                self.start_scan(folder.get_path())

        dialog.select_folder(self, None, chosen)

    # ------------------------------------------------------------- the scan --

    def start_scan(self, path: str):
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            widgets.toast(self, f"{path} is not a folder")
            return
        self.cancel_scan()

        self.set_title(f"{_short(path)} — Fathom")
        self.banner.set_reveal_child(True)
        self.progress.set_fraction(0.0)
        self.progress_label.set_text("Starting…")
        self.status.set_text(f"Scanning {path}")
        self.status.remove_css_class("warning")

        self.scan = scan.Scan(
            path,
            one_filesystem=self.config["one_filesystem"],
            dedup_hardlinks=self.config["dedup_hardlinks"])
        running = self.scan
        report = jobs.Throttle(self._on_progress)

        def work():
            return running.run(on_progress=report)

        def done(result):
            if running is not self.scan:
                return          # a newer scan started while this one finished
            self._scan_finished(result)

        def failed(exc):
            if running is not self.scan:
                return
            self.scan = None
            self.banner.set_reveal_child(False)
            # Left alone, the strip would go on saying "Scanning C:\" under a
            # window that has stopped, which reads as a scan that never ends.
            self.status.set_text(f"Could not scan {path}")
            self.status.add_css_class("warning")
            widgets.error_toast(self, exc)

        jobs.run(work, done, failed)

    def rescan(self):
        if self.result is not None:
            self.start_scan(self.result.path)

    def cancel_scan(self):
        if self.scan is not None:
            self.scan.cancel()
            self.scan = None
            self.banner.set_reveal_child(False)

    def _on_progress(self, progress):
        if progress.done:
            return
        self.progress.pulse()
        self.progress_label.set_text(
            f"{human_count(progress.files)} files · {human_size(progress.bytes)}")
        self.status.set_text(_short(progress.where))

    def _scan_finished(self, result):
        self.scan = None
        self.banner.set_reveal_child(False)
        self.result = result
        self.map_root = result.root
        self._selected = None
        self._loaded_extras = False

        self.config.remember_path(result.path)
        self.config.save()

        free = result.free_bytes if self._is_whole_volume(result.path) else 0
        self._free_shown = free

        self.tree.set_root(result.root, free)
        self.map.set_root(result.root, free)
        self.crumbs.set_chain(result.root, result.root)
        self.files.set_files([], 1)
        self.types.set_rows([], 1)

        self.stack_pages.set_visible_child_name("results")
        self.back_button.set_visible(True)
        self.rescan_button.set_visible(True)
        self.search_button.set_visible(True)
        self.switcher.set_visible(True)
        self._update_status()
        self._load_extras()

        # An empty tree and an empty map are what "you are not allowed to look
        # in here" looks like otherwise, and that reads as a broken app rather
        # than as an answer. /boot is root-only on this machine, and so is
        # most of what a scan of / cannot reach.
        if result.root.unreadable and not result.root.subdirs \
                and not result.root.fnames:
            widgets.toast(self, f"Nothing in {_short(result.path)} could be "
                                f"read — it belongs to another user", timeout=7)

    def _is_mount_point(self, path: str) -> bool:
        try:
            return os.path.ismount(path)
        except OSError:
            return False

    def _is_whole_volume(self, path: str) -> bool:
        """Free space belongs on the map only when the map is of a whole disk.

        Drawn beside a folder it is nonsense — 25 GB free next to a 2 GB
        folder makes the folder a sliver and says nothing true.
        """
        return self.config["show_free_space"] and self._is_mount_point(path)

    def _load_extras(self):
        """The biggest-files and file-type tables, built off the main loop."""
        if self._loaded_extras or self.result is None:
            return
        self._loaded_extras = True
        root = self.result.root
        basis = self.config["size_basis"]
        limit = self.config["max_file_rows"]

        def work():
            return (scan.largest_files(root, limit, basis),
                    scan.by_extension(root, basis))

        def done(both):
            if self.result is None or self.result.root is not root:
                return
            files, extensions = both
            total = scan.value_of(root, basis) or 1
            self._top_files = files
            self.types.set_rows(extensions, total)
            text = self.search_entry.get_text()
            if text:
                self._run_search(text)
            else:
                self.files.set_files(files, total)

        jobs.run(work, done)

    def _update_status(self, note: str = ""):
        result = self.result
        if result is None:
            self.status.set_text("")
            return
        basis = self.config["size_basis"]
        total = scan.value_of(result.root, basis)
        parts = [
            f"{human_size(total, self.config['si_units'])} in "
            f"{human_count(result.root.nfiles)} files and "
            f"{human_count(result.root.ndirs)} folders",
            f"scanned in {human_duration(result.elapsed)}",
        ]
        if result.total_bytes:
            parts.append(f"{human_size(result.free_bytes)} free of "
                         f"{human_size(result.total_bytes)}")
        # Otherwise the three numbers on screen do not add up to the size of
        # the disk and there is nothing to say why.
        if result.reserved_bytes and self._is_mount_point(result.path):
            parts.append(f"{human_size(result.reserved_bytes)} reserved for root")
        if result.hardlinks and self.config["dedup_hardlinks"]:
            parts.append(f"{human_count(result.hardlinks)} hardlinks counted once")
        if result.cancelled:
            parts.append("stopped early — the totals are partial")
        if result.unreadable:
            parts.append(f"{human_count(result.unreadable)} not readable")

        self.status.set_text(note or "  ·  ".join(parts))
        if result.unreadable or result.cancelled:
            self.status.add_css_class("warning")
        else:
            self.status.remove_css_class("warning")
        if result.examples:
            self.status.set_tooltip_text(
                "Could not be read:\n" + "\n".join(result.examples[:12]))

    # -------------------------------------------------------- interaction ---

    def select(self, node, source: str):
        if node is self._selected:
            return
        self._selected = node
        if source != "map":
            self.map.set_selected(node)
        if source != "tree" and node is not None:
            self.tree.select_node(node)
        self._show_detail(node)

    def _show_detail(self, node):
        if node is None:
            self._update_status()
            return
        basis = self.config["size_basis"]
        value = scan.value_of(node, basis)
        total = scan.value_of(self.result.root, basis) if self.result else 0
        if isinstance(node, scan.Dir):
            detail = (f"{human_size(value, self.config['si_units'])} · "
                      f"{human_count(node.nfiles)} files in "
                      f"{human_count(node.ndirs)} folders")
        else:
            detail = human_size(value, self.config["si_units"])
        share = human_percent(value, total) if total else ""
        self.status.set_text(f"{_short(node.path)}  —  {detail}"
                             + (f"  ·  {share} of the scan" if share else ""))
        self.status.remove_css_class("warning")

    def _on_hover(self, _map, node):
        if node is None:
            self._show_detail(self._selected)
        elif node is not treemap.FREE:
            self._show_detail(node)

    def _on_row_activated(self, _view, node):
        if isinstance(node, scan.Dir):
            self.zoom_map(node)
        else:
            self.open_selected()

    def _on_map_activated(self, _map, node):
        if isinstance(node, scan.Dir):
            self.zoom_map(node)
        elif node is not None:
            self.select(node, "map")
            self.open_selected()

    def zoom_map(self, node):
        if not isinstance(node, scan.Dir):
            return
        self.map_root = node
        free = self._free_shown if node is (self.result and self.result.root) else 0
        self.map.set_root(node, free)
        self.crumbs.set_chain(node, self.result.root if self.result else node)

    def zoom_out(self):
        if self.map_root is not None and self.map_root.parent is not None:
            self.zoom_map(self.map_root.parent)

    def _on_page_changed(self, *_args):
        name = self.views.get_visible_child_name()
        self.search_entry.set_placeholder_text(
            "Filter extensions" if name == "types"
            else "Filter by name — *.iso, or part of a name")

    def _on_search(self, entry):
        text = entry.get_text()
        name = self.views.get_visible_child_name()
        if text and name == "tree":
            # The tree cannot usefully be filtered in place — hiding a folder
            # whose children match would hide the match — so searching moves
            # to the flat list, which is what the question wants anyway.
            self.views.set_visible_child_name("files")
        self.types.apply_filter(text)
        self._run_search(text)

    def _run_search(self, text: str):
        """Search every file in the scan, not just the ones already listed."""
        if self.result is None:
            return
        total = scan.value_of(self.result.root, self.config["size_basis"]) or 1
        if not text.strip():
            self._search_generation += 1
            self.files.set_files(self._top_files, total)
            self._update_status()
            return

        self._search_generation += 1
        generation = self._search_generation
        root = self.result.root
        basis = self.config["size_basis"]
        limit = self.config["max_file_rows"]

        def work():
            return scan.search(root, text, limit, basis)

        def done(hits):
            # Typing is faster than searching a million names; whichever
            # search was started last is the one the user is waiting for.
            if generation != self._search_generation or self.result is None:
                return
            self.files.set_files(hits, total)
            found = human_count(len(hits))
            capped = " (the biggest shown)" if len(hits) >= limit else ""
            self.status.set_text(f"{found} files match “{text}”{capped}")
            self.status.remove_css_class("warning")

        jobs.run(work, done)

    # ------------------------------------------------------ the item menu ---

    def _on_menu(self, source, node, px, py):
        if node is None:
            return
        self.select(node, "map" if source is self.map else "tree")
        self._menu_node = node

        menu = Gio.Menu()
        first = Gio.Menu()
        if isinstance(node, scan.Dir):
            first.append("Zoom the map here", "win.open-item")
        else:
            first.append("Open", "win.open-item")
        first.append("Show in file manager", "win.reveal")
        first.append("Copy path", "win.copy-path")
        menu.append_section(None, first)
        gone = Gio.Menu()
        gone.append("Move to Trash", "win.trash")
        gone.append("Delete permanently…", "win.delete")
        menu.append_section(None, gone)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(source)
        popover.set_has_arrow(False)
        popover.set_pointing_to(Gdk.Rectangle(x=int(px), y=int(py), width=1, height=1))
        popover.connect("closed", lambda pop: pop.unparent())
        popover.popup()

    def _target(self):
        return self._menu_node or self._selected

    def open_selected(self):
        node = self._target()
        if node is None:
            return
        if isinstance(node, scan.Dir):
            self.zoom_map(node)
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(node.path))
        launcher.launch(self, None, None)

    def reveal_selected(self):
        node = self._target()
        if node is None:
            return
        opener = Gtk.FileLauncher.new(Gio.File.new_for_path(node.path))
        opener.open_containing_folder(self, None, None)

    def copy_path(self):
        node = self._target()
        if node is not None:
            widgets.copy_text(self, node.path, "Path copied")

    # ----------------------------------------------------------- deleting ---

    def delete_selected(self, permanent: bool):
        node = self._target()
        if node is None or self.result is None:
            return
        path = node.path
        if not path or path == self.result.path:
            widgets.toast(self, "That is the folder being scanned")
            return

        size = human_size(scan.value_of(node, self.config["size_basis"]))
        what = "folder" if isinstance(node, scan.Dir) else "file"
        if permanent:
            body = (f"{path}\n\n{size}. Deleting it permanently cannot be undone, "
                    f"and Fathom will not ask twice.")
            widgets.confirm(self, f"Delete this {what} permanently?", body,
                            "Delete", lambda: self._do_delete(node, True))
        else:
            self._do_delete(node, False)

    def _do_delete(self, node, permanent: bool):
        path = node.path
        is_dir = isinstance(node, scan.Dir)

        def work():
            target = Gio.File.new_for_path(path)
            if not permanent:
                target.trash(None)
                return
            if is_dir:
                import shutil

                shutil.rmtree(path)
            else:
                os.unlink(path)

        def done(_result):
            self._forget(node)
            widgets.toast(self, ("Moved to Trash: " if not permanent
                                 else "Deleted: ") + os.path.basename(path))

        def failed(exc):
            widgets.error_toast(self, exc)

        jobs.run(work, done, failed)

    def _forget(self, node):
        """Take a deleted thing out of the tree, the map and the totals."""
        parent = node.parent
        if isinstance(node, scan.Dir):
            store = self.tree.store_for(parent)
            if store is not None:
                for position in range(store.get_n_items()):
                    row = store.get_item(position)
                    if row.node is node:
                        store.remove(position)
                        break
            self.tree.forget(node)
            scan.remove_dir(node)
        else:
            store = self.tree.store_for(parent)
            index = node.index
            scan.remove_file(node)
            if store is not None:
                position = len(parent.subdirs) + index
                if position < store.get_n_items():
                    store.remove(position)
                # Every file after the one that went has moved down a slot in
                # the parent's arrays, and the rows still point at the old
                # positions.
                for slot in range(position, store.get_n_items()):
                    row = store.get_item(slot)
                    if isinstance(row.node, scan.FileRef):
                        row.node.index -= 1
            self.files.remove(node)

        if node is self._selected:
            self._selected = None
        self._menu_node = None
        self.tree.refresh_totals(parent)
        if self.map_root is node or _is_under(self.map_root, node):
            self.map_root = parent
            self.crumbs.set_chain(parent, self.result.root)
            self.map.set_root(parent, 0)
        else:
            self.map.refresh()
        self._update_status()

    # -------------------------------------------------------- preferences ---

    def _on_basis(self, action, value):
        action.set_state(value)
        self.config["size_basis"] = value.get_string()
        self.config.save()
        self._reapply()

    def _on_colour(self, action, value):
        action.set_state(value)
        self.config["treemap_colour"] = value.get_string()
        self.config.save()
        self.legend.set_visible(self.config["treemap_colour"] == "type"
                                and self.paned.get_orientation()
                                == Gtk.Orientation.HORIZONTAL)
        self.map.refresh()

    def _reapply(self):
        """Redo everything that depends on a setting the user just changed."""
        if self.result is None:
            return
        self._loaded_extras = False
        self.tree.set_root(self.result.root, self._free_shown)
        self.map.refresh()
        self._load_extras()
        self._update_status()

    def show_preferences(self):
        dialog = Adw.PreferencesDialog(title="Preferences")
        page = Adw.PreferencesPage()

        measuring = Adw.PreferencesGroup(
            title="Measuring",
            description="What a scan counts, and what it counts it as.")
        measuring.add(self._combo(
            "Size shown", "size_basis", (("alloc", "On disk"),
                                         ("size", "Apparent")),
            "Blocks actually used, or the length of the file. They differ for "
            "sparse files, compressed filesystems, and anything smaller than "
            "a block."))
        measuring.add(self._switch(
            "Stay on one filesystem", "one_filesystem",
            "Do not follow mount points out of the disk being scanned."))
        measuring.add(self._switch(
            "Count hardlinked files once", "dedup_hardlinks",
            "One inode is one file however many names point at it. This is "
            "what du does, and what you get back by deleting it."))
        measuring.add(self._switch(
            "Use 1000-byte kilobytes", "si_units",
            "Off means 1024, the way every disk tool spells it."))
        page.add(measuring)

        showing = Adw.PreferencesGroup(title="Showing")
        showing.add(self._switch(
            "Free space on the map", "show_free_space",
            "Only when a whole filesystem is scanned."))
        showing.add(self._switch(
            "Folder names on the map", "treemap_labels",
            "Where a rectangle is big enough to hold one."))
        showing.add(self._switch(
            "Open the biggest branch after a scan", "auto_expand", ""))
        page.add(showing)

        columns = Adw.PreferencesGroup(
            title="Columns", description="Which of them the tree shows.")
        for key, label in (("alloc", "Size"), ("size", "Apparent size"),
                           ("percent", "Share of parent"), ("items", "Items"),
                           ("files", "Files"), ("folders", "Folders"),
                           ("modified", "Modified")):
            columns.add(self._column_switch(key, label))
        page.add(columns)

        dialog.add(page)
        dialog.present(self)

    def _switch(self, title, key, subtitle):
        row = Adw.SwitchRow(title=title, subtitle=subtitle,
                            active=bool(self.config[key]))

        def changed(switch, _param):
            self.config[key] = switch.get_active()
            self.config.save()
            if key in ("one_filesystem", "dedup_hardlinks"):
                widgets.toast(self, "Takes effect on the next scan")
            else:
                self._reapply()

        row.connect("notify::active", changed)
        return row

    def _column_switch(self, key, label):
        row = Adw.SwitchRow(title=label,
                            active=bool(self.config["columns"].get(key, True)))

        def changed(switch, _param):
            self.config["columns"][key] = switch.get_active()
            self.config.save()
            self.tree.apply_columns()

        row.connect("notify::active", changed)
        return row

    def _combo(self, title, key, options, subtitle):
        labels = Gtk.StringList()
        for _value, label in options:
            labels.append(label)
        current = [value for value, _label in options].index(self.config[key]) \
            if self.config[key] in [value for value, _label in options] else 0
        row = Adw.ComboRow(title=title, subtitle=subtitle, model=labels,
                           selected=current)

        def changed(combo, _param):
            self.config[key] = options[combo.get_selected()][0]
            self.config.save()
            if key == "size_basis":
                self.lookup_action("basis").set_state(
                    GLib.Variant.new_string(self.config[key]))
            self._reapply()

        row.connect("notify::selected", changed)
        return row

    # ------------------------------------------------------------- export ---

    def export_csv(self):
        if self.result is None:
            widgets.toast(self, "Nothing has been scanned yet")
            return
        name = self.views.get_visible_child_name()
        dialog = Gtk.FileDialog(title="Export as CSV")
        dialog.set_initial_name(
            f"fathom-{os.path.basename(self.result.path) or 'root'}-{name}.csv")

        def chosen(_dialog, result):
            try:
                target = dialog.save_finish(result)
            except GLib.Error:
                return
            path = target.get_path() if target else None
            if path:
                self._write_csv(path, name)

        dialog.save(self, None, chosen)

    def _write_csv(self, path: str, which: str):
        root = self.result.root
        basis = self.config["size_basis"]

        def work():
            import csv

            with open(path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                if which == "types":
                    writer.writerow(["extension", "files", "apparent bytes",
                                     "bytes on disk"])
                    for row in scan.by_extension(root, basis):
                        writer.writerow(row)
                elif which == "files":
                    writer.writerow(["path", "apparent bytes", "bytes on disk",
                                     "modified"])
                    for ref in scan.largest_files(
                            root, self.config["max_file_rows"], basis):
                        writer.writerow([ref.path, ref.size, ref.alloc,
                                         int(ref.mtime)])
                else:
                    writer.writerow(["path", "apparent bytes", "bytes on disk",
                                     "files", "folders", "modified"])
                    for node in root.walk():
                        writer.writerow([node.path, node.size, node.alloc,
                                         node.nfiles, node.ndirs,
                                         int(node.mtime)])
            return path

        jobs.run(work,
                 lambda written: widgets.toast(self, f"Written to {written}"),
                 lambda exc: widgets.error_toast(self, exc))

    # -------------------------------------------------------------- about ---

    def show_shortcuts(self):
        rows = (
            ("Ctrl+O", "Scan a folder"),
            ("Ctrl+R  /  F5", "Scan again"),
            ("Ctrl+F", "Search the biggest files"),
            ("Backspace  /  Alt+↑", "Zoom the map out one level"),
            ("Double click", "Zoom the map into a folder, or open a file"),
            ("Ctrl+Return", "Open the selected file"),
            ("Ctrl+C", "Copy the selected path"),
            ("Delete", "Move the selection to Trash"),
            ("Shift+Delete", "Delete the selection permanently"),
            ("Ctrl+E", "Export the current view as CSV"),
        )
        dialog = Adw.Dialog(title="Keyboard shortcuts",
                            content_width=460, content_height=470)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        group = Adw.PreferencesGroup(margin_top=10, margin_bottom=16,
                                     margin_start=16, margin_end=16)
        for keys, what in rows:
            row = Adw.ActionRow(title=what)
            row.add_suffix(Gtk.Label(label=keys, css_classes=["dim-label",
                                                              "monospace"]))
            group.add(row)
        scroller = Gtk.ScrolledWindow(child=group, vexpand=True)
        view.set_content(scroller)
        dialog.set_child(view)
        dialog.present(self)

    def show_about(self):
        from . import __version__

        about = Adw.AboutDialog(
            application_name="Fathom",
            application_icon="fathom",
            version=__version__,
            developer_name="Robin",
            comments=("A disk scanner. It walks a filesystem once and shows "
                      "what is on it two ways — as a tree you can read and a "
                      "map you can see."),
            license_type=Gtk.License.MIT_X11,
        )
        about.present(self)

    # ----------------------------------------------------------- geometry ---

    def _compositor_tiles_windows(self) -> bool:
        """Whether the compositor lays windows out itself, as niri and Sway do.

        Under one, the window's size is the layout's arithmetic rather than
        anything the user chose, and asking to be maximised takes the whole
        output away from the arrangement they made. Two things give such a
        compositor away: it offers no minimise, and when it does tell clients
        they are tiled it says so in the toplevel state.
        """
        surface = self.get_surface()
        if not isinstance(surface, Gdk.Toplevel):
            return False
        if surface.get_state() & Gdk.ToplevelState.TILED:
            return True
        if not hasattr(surface, "get_capabilities"):   # GTK before 4.20
            return False
        return not surface.get_capabilities() & Gdk.ToplevelCapabilities.MINIMIZE

    def _on_realize(self, _widget):
        surface = self.get_surface()
        if not isinstance(surface, Gdk.Toplevel):
            return
        surface.connect("notify::capabilities",
                        lambda *_: self._settle_restored_maximize())
        # X11 knows what the window manager can do as soon as the window
        # exists; Wayland reports a placeholder until the compositor's first
        # configure, which only arrives after the map. Settle straight away
        # when the answer is already in, and wait for the notify otherwise.
        if (not hasattr(surface, "get_capabilities")
                or surface.get_capabilities() & Gdk.ToplevelCapabilities.MINIMIZE):
            self._settle_restored_maximize()

    def _settle_restored_maximize(self):
        """Take back the maximise from the config if it was the wrong call.

        Runs once, before the first frame: a maximise the user asks for later
        is theirs to keep. The flag is forgotten as well, so the next start
        under this compositor does not ask again.
        """
        if not self._maximize_pending:
            return
        self._maximize_pending = False
        if not self._compositor_tiles_windows():
            return
        self.unmaximize()
        self.config["window_maximized"] = False
        self.config.save()

    def _on_close(self, _window):
        self.cancel_scan()
        # Under a tiling compositor neither number is the user's to remember,
        # so the ones from the last session that did get to choose them stay.
        if not self._compositor_tiles_windows():
            self.config["window_maximized"] = self.is_maximized()
            if not self.is_maximized():
                self.config["window_width"] = self.get_width()
                self.config["window_height"] = self.get_height()
        # In the narrow layout the pane is vertical, and its position is a
        # height. Saving it would come back as a wrong width next time.
        if (self.stack_pages.get_visible_child_name() == "results"
                and self.paned.get_orientation() == Gtk.Orientation.HORIZONTAL):
            self.config["split_position"] = self.paned.get_position()
        self.config.save()
        return False


def _is_under(node, ancestor) -> bool:
    walker = node
    while walker is not None:
        if walker is ancestor:
            return True
        walker = walker.parent
    return False


def _short(path: str) -> str:
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path
