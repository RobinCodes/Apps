"""The window: one slab of glass, with three cards laid on it.

The layout, outside in:

    AdwApplicationWindow           (paints nothing; transparent)
      glass.Slab                   (the rounded, translucent pane, padded
                                    by style.GAP — that padding is the gap)
        AdwToastOverlay
          GtkOverlay               (the palette and the job strips float here)
            GtkBox                 (the modules, spaced by style.GAP)
              GtkWindowHandle      -> the toolbar      [card 1]
              AdwOverlaySplitView
                sidebar: places, bookmarks, devices, trash   [card 2]
                content: AdwToolbarView                       [card 3]
                           tab bar
                           AdwTabView -> one Pane, or two in a GtkPaned

Each card is a `.module`: one radius, one alpha, one rim, and `Overflow.HIDDEN`
so what it holds is clipped to its corners. The toolbar spans the whole width
rather than beginning where the sidebar ends — wedged into the content pane it
shared an edge with each of the other two and could not read as a card of its
own, whatever it was painted.

Three things here are about this machine rather than about file managers.

*A tiling compositor has no minimise and no business being maximised.* niri
gives a window the column it has decided on; calling `maximize` on startup
because the settings remember it from an X11 session takes the whole output
and throws away the user's column widths — and `is_maximized` then saves
`true` for ever. So tiling is detected from the toplevel's capabilities
(MINIMIZE missing is the tell), the remembered maximise is handed back once,
and geometry is not saved while tiled.

*It has to fit in a tiling column.* The screen this was written on is
1366×768, so niri's narrowest preset column is about 455 pixels. An
`Adw.Breakpoint` at 720 collapses the sidebar into an overlay and hides the
header buttons that are also in the menu; the suite measures the result.

*Opening a file must go through the desktop.* That is `Gtk.FileLauncher`,
which is in `pane.py`, and which the tests must never reach.
"""

from __future__ import annotations

import os
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import (dialogs, glass, housestyle, inbox as inboxmod, ops, order,
               palette, places, prefs, style, tools, trash as trashmod, units,
               winenv)
from .askpanel import AskPanel  # noqa: E402
from .pane import Breadcrumbs, Pane, icon_button  # noqa: E402

NARROW = 720            # below this, the sidebar slides over instead of sitting beside


class MainWindow(Adw.ApplicationWindow):
    """One window. Several of them share nothing but the settings file."""

    # A window that is picking a file rather than browsing to one takes the
    # file itself instead of handing it to the desktop. `chooser.py` turns
    # this on; `Pane.launch` is what asks.
    picks_files = False

    def __init__(self, app, path: str = "", restore: bool = False):
        super().__init__(application=app)
        self.config = app.config
        self.undo_stack = ops.UndoStack()
        self.jobs: list = []
        self._trash_count = -1
        # The Order's inbox: how much is in it, who is showing it, and the
        # monitor that keeps both current when something else adds to it.
        self._inbox_count = -1
        self._inbox_views: list = []
        self._inbox_monitor = None
        self._inbox_pending = False

        self.add_css_class("hermione")
        self.set_title("Hermione")
        # The remembered size and maximise, applied before the first map —
        # see housestyle.Geometry for why the two are treated differently
        # under a tiling compositor.
        self.geometry = housestyle.Geometry(self, self.config,
                                            minimum=(360, 320))
        self.geometry.restore()

        self._build()
        self._install_actions()
        self._watch_toplevel()

        if restore and self.config["restore_session"] and self.config["tabs"]:
            self._restore_session()
        else:
            self.new_tab(path or self.config["last_folder"]
                         or os.path.expanduser("~"), focus=True)
        self.refresh_sidebar()
        self.sync_header()
        self.connect("close-request", self._on_close)
        self.connect("destroy", lambda _w: self._stop_watching_inbox())
        self.inbox_changed()
        order.warm_up()

    # ------------------------------------------------------------ building ---

    def _build(self) -> None:
        self.slab = glass.Slab(orientation=Gtk.Orientation.VERTICAL)
        self._relight()

        self.toasts = Adw.ToastOverlay()
        self.overlay = Gtk.Overlay()
        self.toasts.set_child(self.overlay)

        self.split = Adw.OverlaySplitView()
        self.split.set_sidebar_width_fraction(0.22)
        self.split.set_min_sidebar_width(190)
        self.split.set_max_sidebar_width(340)
        self.split.set_show_sidebar(bool(self.config["show_sidebar"]))
        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())
        self.split.set_vexpand(True)

        # The three modules. The toolbar is built out here rather than as a
        # top bar of the content pane, which is what it used to be: a bar
        # that stops where the sidebar begins is a bar merged into one of
        # the other two cards, and it read as one. Across the top it is its
        # own card, the width of the window, with the places and the listing
        # under it and the slab showing between all three.
        self.modules = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                               spacing=style.GAP)
        self.modules.append(self._build_header())
        self.modules.append(self.split)
        self.overlay.set_child(self.modules)

        # The highlight that follows the pointer, under the floating layers.
        self.sheen = glass.Sheen(0.05 if self.config["glass"] else 0.0)
        self.overlay.add_overlay(self.sheen)
        self.sheen.track(self)

        self.job_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.job_box.set_halign(Gtk.Align.CENTER)
        self.job_box.set_valign(Gtk.Align.END)
        self.job_box.set_margin_bottom(18)
        self.job_box.set_size_request(420, -1)
        self.overlay.add_overlay(self.job_box)

        self.ask_panel = AskPanel(self)
        self.overlay.add_overlay(self.ask_panel)

        self.palette = palette.Palette(
            self._palette_items, navigate=self._palette_navigate,
            asker=self._palette_ask if order.here() is not None else None)
        self.overlay.add_overlay(self.palette)

        self.slab.append(self.toasts)
        self.set_content(self.slab)
        self._add_breakpoint()

    def _build_content(self) -> Gtk.Widget:
        view = Adw.ToolbarView()
        view.add_css_class("plain-bars")
        view.add_css_class("module")
        view.add_css_class("files-card")
        # GTK has no `overflow` in CSS, but Gtk.Overflow.HIDDEN honours the
        # corner radius — which is what stops the status line and a selected
        # row at the bottom of the list from squaring off the card, the same
        # trick glass.Slab plays for the window.
        view.set_overflow(Gtk.Overflow.HIDDEN)
        self.content_card = view

        self.tabs = Adw.TabView()
        self.tabs.set_vexpand(True)
        self.tabs.connect("notify::selected-page", lambda *_a: self._tab_changed())
        # In a split view the toolbar follows the focus from one half to the
        # other, so "back" means back in the pane you are looking at.
        self.connect("notify::focus-widget", lambda *_a: self.sync_header())
        self.tabs.connect("close-page", self._on_close_page)
        self.tabs.connect("create-window", self._on_tab_torn_off)

        self.tab_bar = Adw.TabBar()
        self.tab_bar.set_view(self.tabs)
        self.tab_bar.set_autohide(True)
        self.tab_bar.set_expand_tabs(False)
        view.add_top_bar(self.tab_bar)

        view.set_content(self.tabs)
        return view

    def _build_header(self) -> Gtk.Widget:
        """The window's one toolbar, as the module across the top.

        One, not two. Everything here used to be split between a nearly
        empty window header and a second bar inside the pane, which meant
        two search buttons, two menus and two rows of chrome above every
        listing. With two panes open it still drives whichever one has the
        focus, and each pane says which folder it is showing on a slim line
        of its own.

        It spans the whole window rather than starting where the sidebar
        ends. Tucked into the content pane it shared an edge with the places
        and an edge with the listing, and a bar wedged between two other
        surfaces cannot be a card of its own however it is painted.
        """
        # A box rather than an Adw.HeaderBar, for one reason: a header bar
        # centres its title widget between the two groups of buttons, and
        # the title here is the path — which wants to start where the
        # arrows end and take whatever room is left, not sit squeezed in
        # the middle being ellipsised from both sides. Gtk.WindowControls
        # still gives the right buttons for the desktop's own layout, and
        # the WindowHandle around it still drags the window.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header.add_css_class("chrome")
        header.add_css_class("module")

        start_controls = Gtk.WindowControls(side=Gtk.PackType.START)
        header.append(start_controls)

        self.sidebar_button = Gtk.ToggleButton()
        self.sidebar_button.set_icon_name("sidebar-show-symbolic")
        self.sidebar_button.set_tooltip_text("Places  (F9)")
        self.sidebar_button.add_css_class("pill-flat")
        self.sidebar_button.set_active(self.split.get_show_sidebar())
        self._sidebar_handler = self.sidebar_button.connect(
            "toggled",
            lambda b: self.split.set_show_sidebar(b.get_active()))
        self.split.connect("notify::show-sidebar", self._sidebar_shown)
        header.append(self.sidebar_button)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        nav.add_css_class("button-cluster")
        self.back_button = icon_button("go-previous-symbolic", "Back  (Alt+Left)")
        self.back_button.connect("clicked", lambda _b: self._on_pane("go_back"))
        self.forward_button = icon_button("go-next-symbolic",
                                           "Forward  (Alt+Right)")
        self.forward_button.connect("clicked",
                                    lambda _b: self._on_pane("go_forward"))
        self.up_button = icon_button("go-up-symbolic", "Up  (Alt+Up)")
        self.up_button.connect("clicked", lambda _b: self._on_pane("go_up"))
        for button in (self.back_button, self.forward_button, self.up_button):
            nav.append(button)
        header.append(nav)

        # The title area is the path: breadcrumbs, or an entry when typing one.
        self.crumbs = Breadcrumbs(self._go_to, self._drop_on_crumb)
        self.crumbs.set_hexpand(True)
        self.path_entry = Gtk.Entry()
        self.path_entry.add_css_class("path-entry")
        self.path_entry.set_hexpand(True)
        self.path_entry.connect("activate", self._on_path_typed)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_path_key)
        self.path_entry.add_controller(keys)

        self.path_area = Gtk.Stack()
        self.path_area.set_hhomogeneous(False)
        self.path_area.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.path_area.set_transition_duration(110)
        self.path_area.add_named(self.crumbs, "crumbs")
        self.path_area.add_named(self.path_entry, "entry")
        self.path_area.set_hexpand(True)
        header.append(self.path_area)

        self.find_button = icon_button("system-search-symbolic",
                                       "Search in this folder  (Ctrl+F)")
        self.find_button.connect("clicked",
                                 lambda _b: self._on_pane("toggle_filter"))
        header.append(self.find_button)

        view_toggle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        view_toggle.add_css_class("button-cluster")
        self.list_button = Gtk.ToggleButton()
        self.list_button.set_icon_name("view-list-symbolic")
        self.list_button.set_tooltip_text("List  (Ctrl+1)")
        self.grid_button = Gtk.ToggleButton()
        self.grid_button.set_icon_name("view-grid-symbolic")
        self.grid_button.set_tooltip_text("Icons  (Ctrl+2)")
        self.grid_button.set_group(self.list_button)
        for button in (self.list_button, self.grid_button):
            button.add_css_class("pill-flat")
            view_toggle.append(button)
        (self.grid_button if self.config["view_mode"] == "grid"
         else self.list_button).set_active(True)
        self._list_handler = self.list_button.connect("toggled",
                                                      self._view_toggled)
        header.append(view_toggle)

        self.split_button = Gtk.ToggleButton()
        self.split_button.set_icon_name("view-dual-symbolic")
        self.split_button.set_tooltip_text("Two panes  (F3)")
        self.split_button.add_css_class("pill-flat")
        self._split_handler = self.split_button.connect(
            "toggled", self._on_split_toggled)
        header.append(self.split_button)

        new_tab = Gtk.Button.new_from_icon_name("tab-new-symbolic")
        new_tab.add_css_class("pill-flat")
        new_tab.set_tooltip_text("New tab  (Ctrl+T)")
        new_tab.connect("clicked", lambda _b: self.new_tab(focus=True))
        header.append(new_tab)
        self.new_tab_button = new_tab

        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.add_css_class("pill-flat")
        menu_button.set_menu_model(self._main_menu())
        menu_button.set_tooltip_text("Menu  (F10)")
        header.append(menu_button)

        header.append(Gtk.WindowControls(side=Gtk.PackType.END))

        handle = Gtk.WindowHandle()
        handle.set_child(header)
        self.header = header
        return handle

    # -- what the one toolbar acts on ------------------------------------

    def _on_pane(self, method: str) -> None:
        pane = self.current_pane()
        if pane is not None:
            getattr(pane, method)()

    def _go_to(self, path: str) -> None:
        pane = self.current_pane()
        if pane is not None:
            pane.navigate(path)

    def _drop_on_crumb(self, paths, destination: str) -> None:
        pane = self.current_pane()
        if pane is not None:
            pane._drop_paths(paths, destination)

    def sync_header(self) -> None:
        """Make the toolbar describe the pane it is pointing at.

        Called whenever the active pane changes or moves: a tab switch, a
        click into the other half of a split, a navigation, a search.
        """
        pane = self.current_pane()
        if pane is None:
            return
        # The menus name `pane.` actions; pointing the group at the active
        # pane is what makes one menu serve both halves of a split view.
        self.insert_action_group("pane", pane.actions)

        if pane.searching and pane.search_label:
            self.crumbs.set_search(pane.search_label, pane.path)
        else:
            self.crumbs.set_path(pane.path)
        if self.path_area.get_visible_child_name() == "entry":
            self.path_entry.set_text(pane.path)

        # With two panes open, say which one the toolbar is driving.
        holder = self.current_tab_holder()
        panes = getattr(holder, "_panes", []) if holder is not None else []
        if len(panes) > 1:
            for candidate in panes:
                mark = candidate is pane
                if mark != candidate.path_strip.has_css_class("active"):
                    if mark:
                        candidate.path_strip.add_css_class("active")
                    else:
                        candidate.path_strip.remove_css_class("active")

        self.back_button.set_sensitive(bool(pane.back_stack))
        self.forward_button.set_sensitive(bool(pane.forward_stack))
        self.up_button.set_sensitive(pane.can_go_up)

        wanted = self.grid_button if pane.view.mode == "grid" else self.list_button
        if not wanted.get_active():
            self.list_button.handler_block(self._list_handler)
            wanted.set_active(True)
            self.list_button.handler_unblock(self._list_handler)

    def _view_toggled(self, _button) -> None:
        pane = self.current_pane()
        if pane is not None:
            pane.set_view_mode("list" if self.list_button.get_active()
                               else "grid")

    # -- typing a path ----------------------------------------------------

    def edit_path(self) -> None:
        pane = self.current_pane()
        if pane is None:
            return
        self.path_entry.set_text(pane.path)
        self.path_area.set_visible_child_name("entry")
        self.path_entry.grab_focus()
        self.path_entry.select_region(0, -1)

    def finish_path_edit(self) -> None:
        if self.path_area.get_visible_child_name() != "entry":
            return
        self.path_area.set_visible_child_name("crumbs")
        pane = self.current_pane()
        if pane is not None:
            pane.view.grab_focus()

    def _on_path_typed(self, entry) -> None:
        text = entry.get_text().strip()
        self.finish_path_edit()
        if text:
            self._go_to(text)

    def _on_path_key(self, _controller, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.finish_path_edit()
            return True
        return False

    def _main_menu(self) -> Gio.Menu:
        """One menu. What used to be a second "this folder" menu on the
        pane's own toolbar is folded in here, because there is one toolbar
        now and two menus on it would be the same mistake in miniature."""
        menu = Gio.Menu()

        making = Gio.Menu()
        making.append("New folder", "pane.new-folder")
        making.append("New file…", "pane.new-file")
        making.append("New tab", "win.new-tab")
        making.append("New window", "app.new-window")
        menu.append_section(None, making)

        looking = Gio.Menu()
        sort = Gio.Menu()
        for key, label in (("name", "Name"), ("size", "Size"),
                           ("modified", "Date"), ("type", "Type"),
                           ("extension", "Extension")):
            sort.append(label, f"pane.sort::{key}")
        order = Gio.Menu()
        order.append("Reversed", "pane.sort-reverse")
        order.append("Folders first", "pane.folders-first")
        sort.append_section(None, order)
        looking.append_submenu("Sort by", sort)
        looking.append("Hidden files", "pane.show-hidden")
        looking.append("Preview pane", "pane.show-preview")
        looking.append("Folder sizes", "pane.folder-sizes")
        looking.append("Two panes", "win.split")
        menu.append_section(None, looking)

        going = Gio.Menu()
        going.append("Home", "win.home")
        going.append("Type a path…", "win.edit-path")
        going.append("Trash", "win.trash-folder")
        going.append("Bookmark this folder", "pane.bookmark")
        going.append("Copy location", "pane.copy-location")
        going.append("Open in terminal", "pane.terminal")
        menu.append_section(None, going)

        doing = Gio.Menu()
        doing.append("Undo", "win.undo")
        doing.append("Redo", "win.redo")
        menu.append_section(None, doing)

        tools_menu = Gio.Menu()
        tools_menu.append("Find duplicates…", "pane.duplicates")
        tools_menu.append("Biggest files…", "pane.largest")
        tools_menu.append("Empty folders…", "pane.empty-folders")
        tools_menu.append("Empty the trash…", "win.empty-trash")
        menu.append_section(None, tools_menu)

        last = Gio.Menu()
        last.append("Settings", "win.settings")
        last.append("Keyboard shortcuts", "win.shortcuts")
        last.append("About Hermione", "win.about")
        menu.append_section(None, last)
        return menu

    def _add_breakpoint(self) -> None:
        """Narrow enough for a tiling column: fold the chrome away.

        Only what is also reachable from a menu is hidden, so nothing becomes
        unavailable — it becomes one click further away.
        """
        condition = Adw.BreakpointCondition.new_length(
            Adw.BreakpointConditionLengthType.MAX_WIDTH, NARROW,
            Adw.LengthUnit.PX)
        point = Adw.Breakpoint.new(condition)
        point.add_setter(self.split, "collapsed", True)
        point.add_setter(self.split_button, "visible", False)
        point.add_setter(self.new_tab_button, "visible", False)
        point.connect("apply", lambda _p: self._set_narrow(True))
        point.connect("unapply", lambda _p: self._set_narrow(False))
        self.add_breakpoint(point)

    def _set_narrow(self, narrow: bool) -> None:
        """Tell every pane to tuck itself in, and the sidebar how it floats."""
        sidebar = self.split.get_sidebar()
        if sidebar is not None:
            if narrow:
                sidebar.add_css_class("overlay")
            else:
                sidebar.remove_css_class("overlay")
        self.find_button.set_visible(not narrow)
        for pane in self.all_panes():
            pane.preview.set_visible(bool(self.config["show_preview"])
                                     and not narrow)
        if narrow and self.split_view_active():
            self.toggle_split()

    # ------------------------------------------------------------- sidebar ---

    def _build_sidebar(self) -> Gtk.Widget:
        self.sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=0)
        self.sidebar_box.add_css_class("sidebar-glass")
        self.sidebar_box.add_css_class("module")
        self.sidebar_box.set_overflow(Gtk.Overflow.HIDDEN)
        # The split view packs its two halves edge to edge, so the gap
        # between this card and the listing is a margin on this one.
        self.sidebar_box.set_margin_end(style.GAP)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        self.places_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=0)
        self.places_box.set_margin_top(6)
        self.places_box.set_margin_bottom(8)
        scroller.set_child(self.places_box)
        self.sidebar_box.append(scroller)
        return self.sidebar_box

    def refresh_sidebar(self) -> None:
        """Rebuild the places list. Cheap, and called whenever it could change."""
        while (child := self.places_box.get_first_child()) is not None:
            self.places_box.remove(child)

        monitor = Gio.VolumeMonitor.get()
        if not hasattr(self, "_volume_handlers"):
            self._volume_handlers = [
                monitor.connect(name, lambda *_a: self.refresh_sidebar())
                for name in ("mount-added", "mount-removed", "volume-added",
                             "volume-removed", "drive-connected",
                             "drive-disconnected")]
            self._monitor = monitor

        current = self.current_pane().path if self.current_pane() else ""
        for heading, group in places.sidebar(self.config, monitor,
                                             self._inbox_count):
            if heading:
                label = Gtk.Label(label=heading, xalign=0.0)
                label.add_css_class("group-heading")
                self.places_box.append(label)
            for place in group:
                self.places_box.append(self._place_row(place, current))

        label = Gtk.Label(label="Trash", xalign=0.0)
        label.add_css_class("group-heading")
        self.places_box.append(label)
        self.places_box.append(
            self._place_row(places.trash_place(self._trash_count), current))
        self._count_trash()

    def _place_row(self, place: places.Place, current: str) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        row.add_css_class("place-row")
        if place.path and place.path == current:
            row.add_css_class("current")

        icon = Gtk.Image.new_from_icon_name(place.icon)
        icon.set_pixel_size(16)
        row.append(icon)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        name = Gtk.Label(label=place.name, xalign=0.0)
        name.set_ellipsize(3)
        text.append(name)
        if place.detail:
            detail = Gtk.Label(label=place.detail, xalign=0.0)
            detail.add_css_class("detail")
            detail.set_ellipsize(3)
            text.append(detail)
        text.set_hexpand(True)
        row.append(text)

        if place.badge:
            badge = Gtk.Label(label=place.badge)
            badge.add_css_class("pill-badge")
            badge.add_css_class("accent")
            badge.set_valign(Gtk.Align.CENTER)
            badge.set_tooltip_text(f"{place.badge} waiting to be filed")
            row.append(badge)

        if place.can_eject:
            eject = Gtk.Button.new_from_icon_name("media-eject-symbolic")
            eject.add_css_class("pill-flat")
            eject.set_tooltip_text("Eject")
            eject.connect("clicked", lambda _b, p=place: self._eject(p))
            row.append(eject)

        click = Gtk.GestureClick()
        click.connect("released", lambda *_a, p=place: self._open_place(p))
        row.add_controller(click)

        right = Gtk.GestureClick()
        right.set_button(Gdk.BUTTON_SECONDARY)
        right.connect("pressed",
                      lambda _g, _n, x, y, p=place, r=row:
                      self._place_menu(p, r, x, y))
        row.add_controller(right)

        middle = Gtk.GestureClick()
        middle.set_button(Gdk.BUTTON_MIDDLE)
        middle.connect("released",
                       lambda *_a, p=place: self.new_tab(p.path, focus=True)
                       if p.path else None)
        row.add_controller(middle)

        if place.path:
            self._place_drop_target(row, place.path)
        return row

    def _place_drop_target(self, row: Gtk.Widget, path: str) -> None:
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY
                                    | Gdk.DragAction.MOVE)
        target.connect("enter", lambda *_a: (row.add_css_class("drop-into"),
                                             Gdk.DragAction.COPY)[1])
        target.connect("leave", lambda *_a: row.remove_css_class("drop-into"))

        def dropped(_target, value, _x, _y):
            row.remove_css_class("drop-into")
            if not isinstance(value, Gdk.FileList):
                return False
            paths = [f.get_path() for f in value.get_files() if f.get_path()]
            pane = self.current_pane()
            if paths and pane is not None:
                pane._drop_paths(paths, path)
            return True

        target.connect("drop", dropped)
        row.add_controller(target)

    def _open_place(self, place: places.Place) -> None:
        pane = self.current_pane()
        if pane is None:
            return
        if place.kind == "trash":
            self.show_trash()
            return
        if place.kind == "inbox" and not os.path.isdir(place.path):
            # The one place the Order says exists; made the first time it is
            # asked for rather than reported missing.
            os.makedirs(place.path, exist_ok=True)
            self.inbox_changed()
        if place.path:
            pane.navigate(place.path)
            self.refresh_sidebar()
            return
        if place.volume is not None:
            self.toast(f"Mounting {place.name}…")
            places.mount(place, lambda path, error:
                         self.toast(error) if error
                         else (pane.navigate(path), self.refresh_sidebar()))

    def _eject(self, place: places.Place) -> None:
        name = place.name
        places.unmount(place, lambda error: self.toast(
            error or f"{name} can be unplugged"))

    def _place_menu(self, place: places.Place, row: Gtk.Widget,
                    x: float, y: float) -> None:
        menu = Gio.Menu()
        if place.path:
            menu.append("Open", "win.place-open")
            menu.append("Open in new tab", "win.place-tab")
        if place.kind == "bookmark":
            menu.append("Remove from bookmarks", "win.place-unbookmark")
        elif place.path and not self.config.is_bookmarked(place.path):
            menu.append("Add to bookmarks", "win.place-bookmark")
        if place.can_eject:
            menu.append("Eject", "win.place-eject")
        if place.kind == "trash":
            menu.append("Empty the trash…", "win.empty-trash")
        if place.kind == "inbox":
            menu.append("Process the inbox…", "win.process-inbox")
        self._menu_place = place
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(row)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.popup()

    def _count_trash(self) -> None:
        """How full the trash is, without walking it on the GTK thread."""
        import threading

        def worker():
            try:
                count = len(trashmod.list_items())
            except OSError:
                count = 0
            GLib.idle_add(done, count)

        def done(count: int) -> bool:
            if count != self._trash_count:
                self._trash_count = count
                # Only the one row needs rebuilding, but it is the last one
                # and rebuilding the list is cheaper than finding it again.
                GLib.idle_add(self.refresh_sidebar)
            return False

        if self._trash_count < 0:
            threading.Thread(target=worker, daemon=True,
                             name="hermione-trash-count").start()

    # ---------------------------------------------------------------- tabs ---

    def new_tab(self, path: str = "", focus: bool = False) -> Pane:
        pane = Pane(self, path or os.path.expanduser("~"))
        holder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        holder.append(pane)
        holder._panes = [pane]
        page = self.tabs.append(holder)
        pane.connect("path-changed", lambda _p, where: self._retitle(page, where))
        pane.connect("path-changed", lambda *_a: self.sync_header())
        pane.connect("status-changed", lambda _p: self._sync_buttons())
        self._retitle(page, pane.path)
        if focus or self.tabs.get_n_pages() == 1:
            self.tabs.set_selected_page(page)
            GLib.idle_add(lambda: (pane.view.grab_focus(), False)[1])
        return pane

    def _retitle(self, page, path: str) -> None:
        name = os.path.basename(path.rstrip(os.sep)) or path
        if path == os.path.expanduser("~"):
            name = "Home"
        page.set_title(name)
        page.set_tooltip(path)
        if page is self.tabs.get_selected_page():
            self.set_title(f"{name} — Hermione")
            self.refresh_sidebar()

    def _tab_changed(self) -> None:
        pane = self.current_pane()
        if pane is not None:
            self._retitle(self.tabs.get_selected_page(), pane.path)
            self._sync_buttons()
            self.sync_header()

    def _on_close_page(self, view, page) -> bool:
        holder = page.get_child()
        for pane in getattr(holder, "_panes", []):
            pane.model.shutdown()
        view.close_page_finish(page, True)
        if view.get_n_pages() == 0:
            self.close()
        return True

    def _on_tab_torn_off(self, _view):
        """A tab dragged out of the window becomes a window of its own."""
        window = MainWindow(self.get_application())
        # The new window starts with a tab of its own, which the dragged page
        # is about to replace; closing it first keeps the count honest.
        for page in list(window.tabs.get_pages()):
            window.tabs.close_page(page)
        window.present()
        return window.tabs

    def current_tab_holder(self) -> Gtk.Widget | None:
        page = self.tabs.get_selected_page()
        return page.get_child() if page is not None else None

    def current_pane(self) -> Pane | None:
        holder = self.current_tab_holder()
        if holder is None:
            return None
        panes = getattr(holder, "_panes", [])
        if not panes:
            return None
        focused = self.get_focus()
        while focused is not None:
            if focused in panes:
                return focused
            focused = focused.get_parent()
        return panes[0]

    def all_panes(self) -> list[Pane]:
        found = []
        for page in self.tabs.get_pages():
            holder = page.get_child()
            found.extend(getattr(holder, "_panes", []))
        return found

    # --------------------------------------------------------- two panes ---

    def split_view_active(self) -> bool:
        holder = self.current_tab_holder()
        return bool(holder is not None and len(getattr(holder, "_panes", [])) > 1)

    def toggle_split(self) -> None:
        holder = self.current_tab_holder()
        if holder is None:
            return
        panes = getattr(holder, "_panes", [])
        if len(panes) > 1:
            second = panes[1]
            second.model.shutdown()
            paned = holder.get_first_child()
            if isinstance(paned, Gtk.Paned):
                first = panes[0]
                paned.set_start_child(None)
                paned.set_end_child(None)
                holder.remove(paned)
                holder.append(first)
            holder._panes = panes[:1]
            panes[0].set_split(False)
        else:
            first = panes[0]
            holder.remove(first)
            second = Pane(self, first.path)
            second.connect("status-changed", lambda _p: self._sync_buttons())
            second.connect("path-changed", lambda *_a: self.sync_header())
            paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            paned.set_start_child(first)
            paned.set_end_child(second)
            paned.set_resize_start_child(True)
            paned.set_resize_end_child(True)
            paned.set_position(int(self.config["split_position"]))
            holder.append(paned)
            holder._panes = [first, second]
            for pane in holder._panes:
                pane.set_split(True)
        self._sync_buttons()
        self.sync_header()

    def other_pane(self) -> Pane | None:
        holder = self.current_tab_holder()
        panes = getattr(holder, "_panes", []) if holder else []
        if len(panes) < 2:
            return None
        current = self.current_pane()
        return panes[1] if current is panes[0] else panes[0]

    def open_in_other_pane(self, path: str) -> None:
        if not self.split_view_active():
            self.toggle_split()
        other = self.other_pane()
        if other is not None and path:
            other.navigate(path)

    def _sync_buttons(self) -> None:
        """Make the chrome agree with the state, without starting an argument.

        Setting a toggle's state emits `toggled`, whose handler changes the
        state this reads — so the handler is blocked while it happens. Without
        the block, `toggle_split` and this method call each other until the
        interpreter runs out of stack, and the traceback lands wherever the
        next callback happens to be.
        """
        active = self.split_view_active()
        if self.split_button.get_active() != active:
            self.split_button.handler_block(self._split_handler)
            self.split_button.set_active(active)
            self.split_button.handler_unblock(self._split_handler)
        self.lookup_action("undo").set_enabled(self.undo_stack.can_undo)
        self.lookup_action("redo").set_enabled(self.undo_stack.can_redo)

    def _on_split_toggled(self, _button) -> None:
        self.toggle_split()

    def _sidebar_shown(self, split, _param) -> None:
        shown = split.get_show_sidebar()
        if self.sidebar_button.get_active() != shown:
            self.sidebar_button.handler_block(self._sidebar_handler)
            self.sidebar_button.set_active(shown)
            self.sidebar_button.handler_unblock(self._sidebar_handler)

    # ------------------------------------------------------------- the jobs ---

    def run_job(self, job, description: str, on_finished=None) -> None:
        """Start a long operation and show it. Called by the panes."""
        strip = _JobStrip(description, job)
        self.job_box.append(strip)
        self.jobs.append(job)

        def progressed(progress):
            GLib.idle_add(strip.update, progress)

        def done(result):
            GLib.idle_add(finish, result)

        def finish(result) -> bool:
            strip.finish()
            GLib.timeout_add(900, lambda: (self._drop_strip(strip), False)[1])
            if job in self.jobs:
                self.jobs.remove(job)
            if result.cancelled:
                self.toast("Stopped")
            elif result.errors:
                self.toast(result.errors[0] if len(result.errors) == 1
                           else f"{len(result.errors)} problems",
                           "Details",
                           lambda: dialogs.tell(self, description,
                                                "\n".join(result.errors[:20])))
            if on_finished is not None:
                on_finished(result)
            for pane in self.all_panes():
                if pane.config["folder_sizes"]:
                    pane.measure_folders()
            self._trash_count = -1
            self._count_trash()
            self.inbox_changed()
            return False

        job.on_progress = progressed
        job.on_done = done
        job.start()

    def _drop_strip(self, strip) -> None:
        if strip.get_parent() is self.job_box:
            self.job_box.remove(strip)

    def record(self, change) -> None:
        """Remember something that can be undone."""
        if self.undo_stack.push(change) is not None:
            self._sync_buttons()

    def undo(self) -> None:
        change = self.undo_stack.pop_undo()
        if change is None:
            return
        outcome = change.undo()
        if isinstance(outcome, tuple):
            ok, why = outcome
            self.toast(f"Undone: {change.label}" if ok
                       else f"Could not undo it: {why}")
            self._refresh_panes()
        else:
            self.run_job(outcome, change.undo_label,
                         on_finished=lambda _r: self._refresh_panes())
        self._sync_buttons()

    def redo(self) -> None:
        change = self.undo_stack.pop_redo()
        if change is None:
            return
        outcome = change.redo()
        if isinstance(outcome, tuple):
            ok, why = outcome
            self.toast(f"Redone: {change.label}" if ok else why)
            self._refresh_panes()
        else:
            self.run_job(outcome, change.label,
                         on_finished=lambda _r: self._refresh_panes())
        self._sync_buttons()

    def _refresh_panes(self) -> None:
        for pane in self.all_panes():
            pane.refresh()

    # ------------------------------------------------------------- the trash ---

    def show_trash(self) -> None:
        """The trash, as a list you can put things back from.

        On Windows it is the Recycle Bin, read directly — see trash.py.
        """
        items = trashmod.list_items()
        dialog = dialogs._dialog("Trash", 600)
        dialog.set_content_height(520)
        body = dialogs._page(
            dialog, "Recycle Bin" if winenv.WINDOWS else "Trash",
            subtitle=("Nothing in it" if not items else
                      f"{len(items)} items, "
                      f"{units.human_size(sum(i.size for i in items))}"))

        listbox = Gtk.ListBox()
        listbox.add_css_class("panel")
        listbox.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(listbox)
        scroller.set_vexpand(True)
        body.append(scroller)

        for item in items:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            for side in ("start", "end"):
                getattr(row, f"set_margin_{side}")(10)
            row.set_margin_top(4)
            row.set_margin_bottom(4)
            from . import filetypes

            icon = Gtk.Image.new_from_gicon(
                filetypes.icon_for(item.original, item.is_dir))
            icon.set_pixel_size(16)
            icon.add_css_class("entry-icon")
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            name = Gtk.Label(label=item.display_name, xalign=0.0)
            name.set_ellipsize(3)
            where = Gtk.Label(label=dialogs._short(item.original_folder),
                              xalign=0.0)
            where.add_css_class("where")
            where.set_ellipsize(1)
            text.append(name)
            text.append(where)
            text.set_hexpand(True)
            when = Gtk.Label(label=units.human_time(item.deleted_at))
            when.add_css_class("numeric")
            row.append(icon)
            row.append(text)
            row.append(when)
            wrapper = Gtk.ListBoxRow()
            wrapper.set_child(row)
            wrapper._item = item
            listbox.append(wrapper)

        def selected_items():
            return [row._item for row in listbox.get_selected_rows()
                    if hasattr(row, "_item")]

        restore = dialogs._button("Put back", "suggested-action")
        restore.set_sensitive(False)

        def put_back(*_args):
            failures = []
            done = 0
            for item in selected_items():
                ok, why = trashmod.restore(item)
                if ok:
                    done += 1
                else:
                    failures.append(f"{item.display_name}: {why}")
            dialog.close()
            self.toast(f"{done} put back" if done else "Nothing was put back")
            if failures:
                dialogs.tell(self, "Some could not be put back",
                             "\n".join(failures[:10]))
            self._trash_count = -1
            self._count_trash()
            self._refresh_panes()

        restore.connect("clicked", put_back)
        listbox.connect("selected-rows-changed",
                        lambda _l: restore.set_sensitive(bool(selected_items())))
        listbox.connect("row-activated", lambda *_a: put_back())

        empty = dialogs._button("Empty it…", "destructive-action")
        empty.set_sensitive(bool(items))
        empty.connect("clicked", lambda _b: (dialog.close(), self.empty_trash()))
        close = dialogs._button("Close")
        close.connect("clicked", lambda _b: dialog.close())
        body.append(dialogs._buttons(close, empty, restore))
        dialog.present(self)

    def empty_trash(self) -> None:
        count, total = trashmod.size_of_trash()
        if not count:
            self.toast("The trash is already empty")
            return

        def go():
            gone, errors = trashmod.empty()
            self.toast(f"{gone} deleted for good")
            if errors:
                dialogs.tell(self, "Some could not be deleted",
                             "\n".join(errors[:10]))
            self._trash_count = -1
            self._count_trash()

        dialogs.confirm(
            self, "Empty the trash?",
            f"{count} items, {units.human_size(total)}. This cannot be undone.",
            "Empty it", go)

    # ------------------------------------------------------------ the Order ---

    def _dialog_parent(self) -> Gtk.Widget:
        """What a dialog is presented on: a widget inside the content.

        Not the window itself. A host that embeds this window's content
        builds the window without ever presenting it, and a dialog presented
        on a window that is not on screen is a dialog nobody sees.
        """
        return self.current_pane() or self.get_content()

    def inbox_changed(self) -> None:
        """Count the inbox again, and tell whatever is showing it."""
        count = order.inbox_count()
        self._watch_inbox()
        if count != self._inbox_count:
            self._inbox_count = count
            self.refresh_sidebar()
        for view in list(self._inbox_views):
            view.reload()

    def _watch_inbox(self) -> None:
        """A monitor on the inbox, so a capture from another app shows here."""
        if self._inbox_monitor is not None:
            return
        where = order.here()
        if where is None or not where.inbox.is_dir():
            return
        try:
            monitor = Gio.File.new_for_path(str(where.inbox)).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error:
            return
        monitor.connect("changed", lambda *_a: self._inbox_soon())
        self._inbox_monitor = monitor

    def _inbox_soon(self) -> None:
        # A move into the inbox is several events; one recount is enough.
        if self._inbox_pending:
            return
        self._inbox_pending = True

        def later() -> bool:
            self._inbox_pending = False
            self.inbox_changed()
            return False

        GLib.timeout_add(250, later)

    def _stop_watching_inbox(self) -> None:
        if self._inbox_monitor is not None:
            self._inbox_monitor.cancel()
            self._inbox_monitor = None

    def show_inbox(self) -> None:
        """The inbox as a queue to file, oldest first (inbox.py)."""
        if order.here() is None:
            self.toast("There is no second brain on this machine")
            return
        view = inboxmod.InboxView(self, self._dialog_parent())
        self._inbox_views.append(view)
        view.present()

    def forget_inbox_view(self, view) -> None:
        if view in self._inbox_views:
            self._inbox_views.remove(view)

    def ask_claude(self, paths: list[str], folder: str) -> None:
        """Open the Ask Claude panel on a selection, or on a folder."""
        if not order.claude_available():
            self.toast("Claude Code is not installed on this machine")
            return
        self.ask_panel.about(paths, folder)

    def search_brain(self) -> None:
        self.palette.open("?")

    def _palette_ask(self, text: str) -> list:
        """A `?` query in the palette, answered by the brain. On a worker."""
        items = []
        for hit in order.search(text):
            path = str(hit.path)
            items.append(palette.Item(
                hit.title, hit.kind, _kind_icon(hit.kind),
                action=lambda p=path: self._open_found(p),
                snippet=hit.snippet or dialogs._short(os.path.dirname(path)),
                alternate=lambda p=path: self._reveal(p)))
        return items

    def _open_found(self, path: str) -> None:
        pane = self.current_pane()
        if pane is None:
            return
        if os.path.exists(path):
            pane.open_path(path)
        else:
            self.toast(f"{os.path.basename(path)} is not there any more")

    # -------------------------------------------------------------- toasts ---

    def toast(self, message: str, action: str = "", callback=None) -> None:
        if not message:
            return
        toast = Adw.Toast.new(message)
        toast.set_timeout(5 if action else 3)
        if action and callback is not None:
            toast.set_button_label(action)
            toast.connect("button-clicked", lambda _t: callback())
        self.toasts.add_toast(toast)

    # ------------------------------------------------------------- palette ---

    def _palette_items(self) -> list:
        pane = self.current_pane()
        items: list[palette.Item] = []

        def add(label, detail="", icon="", shortcut="", action=None, hint=0):
            items.append(palette.Item(label, detail, icon, shortcut, action, hint))

        add("New tab", "", "tab-new-symbolic", "Ctrl+T",
            lambda: self.new_tab(focus=True))
        add("New window", "", "window-new-symbolic", "Ctrl+N",
            lambda: self.get_application().new_window().present())
        add("New folder", "", "folder-new-symbolic", "Ctrl+Shift+N",
            lambda: pane and pane.new_folder())
        add("Two panes", "", "view-dual-symbolic", "F3", self.toggle_split)
        add("Icons", "view the folder as a grid", "view-grid-symbolic", "Ctrl+2",
            lambda: pane and pane.set_view_mode("grid"))
        add("List", "view the folder as a list", "view-list-symbolic", "Ctrl+1",
            lambda: pane and pane.set_view_mode("list"))
        add("Hidden files", "show or stop showing them",
            "view-reveal-symbolic", "Ctrl+H",
            lambda: pane and pane.toggle_hidden())
        add("Preview pane", "", "sidebar-show-right-symbolic", "F7",
            lambda: pane and pane.toggle_preview())
        add("Open in terminal", "", "utilities-terminal-symbolic", "Ctrl+Alt+T",
            lambda: pane and pane.open_terminal())
        add("Search in this folder", "", "system-search-symbolic", "Ctrl+F",
            lambda: pane and pane.toggle_filter())
        add("Find duplicates", "identical files, grouped",
            "edit-copy-symbolic", "",
            lambda: pane and self.show_tool("duplicates", pane.path))
        add("Biggest files", "what is taking up the room",
            "drive-harddisk-symbolic", "",
            lambda: pane and self.show_tool("largest", pane.path))
        add("Empty folders", "", "folder-symbolic", "",
            lambda: pane and self.show_tool("empty", pane.path))
        add("Trash", "what is in it, and putting it back",
            "user-trash-symbolic", "", self.show_trash)
        add("Empty the trash", "", "user-trash-full-symbolic", "",
            self.empty_trash)
        add("Settings", "", "preferences-system-symbolic", "Ctrl+,",
            self.show_settings)
        add("Keyboard shortcuts", "", "preferences-desktop-keyboard-symbolic",
            "Ctrl+?", self.show_shortcuts)
        add("Properties", "", "document-properties-symbolic", "Alt+Return",
            lambda: pane and pane.show_properties())
        add("Compress", "", "package-x-generic-symbolic", "",
            lambda: pane and pane.compress())
        add("Rename", "", "document-edit-symbolic", "F2",
            lambda: pane and pane.rename())
        if order.here() is not None:
            add("Send to Inbox", "move the selection to the brain's inbox",
                "document-send-symbolic", "Ctrl+Shift+I",
                lambda: pane and pane.send_to_inbox())
            add("Process the inbox", "file what is waiting, oldest first",
                "mail-unread-symbolic", "", self.show_inbox)
            add("Search the brain", "or type ? and what to look for",
                "system-search-symbolic", "", self.search_brain)
        if order.claude_available():
            add("Ask Claude about these", "the selection, or this folder",
                "chat-message-new-symbolic", "",
                lambda: pane and pane.ask_claude())

        for place in _flatten(places.sidebar(self.config)):
            if place.path:
                add(place.name, dialogs._short(place.path), place.icon, "",
                    lambda where=place.path: pane and pane.navigate(where), 1)

        for path in self.config["recent_folders"][:12]:
            if os.path.isdir(path):
                add(os.path.basename(path.rstrip(os.sep)) or path,
                    dialogs._short(os.path.dirname(path)), "document-open-recent-symbolic",
                    "", lambda where=path: pane and pane.navigate(where), 1)

        if pane is not None:
            model = pane.model.selection
            for position in range(min(model.get_n_items(), 400)):
                entry = model.get_item(position)
                if entry is None:
                    continue
                add(entry.name, "in this folder",
                    "folder-symbolic" if entry.is_dir else "text-x-generic-symbolic",
                    "", lambda e=entry: pane._on_activated(None, e), 2)
        return items

    def _palette_navigate(self, path: str) -> None:
        pane = self.current_pane()
        if pane is not None:
            pane.navigate(path)

    # ------------------------------------------------------------ the rest ---

    def show_tool(self, mode: str, folder: str) -> None:
        tools.show(self, mode, folder, self.config,
                   on_reveal=lambda path: self._reveal(path),
                   on_changed=self._refresh_panes)

    def _reveal(self, path: str) -> None:
        pane = self.current_pane()
        if pane is not None:
            pane.reveal(path)

    def bookmark(self, path: str) -> None:
        if not path:
            return
        if self.config.is_bookmarked(path):
            self.config.remove_bookmark(path)
            self.toast("Bookmark removed")
        else:
            self.config.add_bookmark(path)
            self.toast(f"{os.path.basename(path.rstrip(os.sep))} bookmarked")
        self.config.save()
        self.refresh_sidebar()

    def show_settings(self) -> None:
        prefs.show(self, self.config, self._setting_changed)

    def _relight(self) -> None:
        """Hand the slab the current theme's light.

        Called when the window is built and whenever the theme or the glass
        settings change — the texture is cached per theme, so this is a
        dictionary lookup in the common case.
        """
        self.slab.set_light(style.scheme_for(self.config, _is_dark()),
                            float(self.config["translucency"]),
                            bool(self.config["glass"]))

    def _setting_changed(self, key: str) -> None:
        if key in ("theme", "follow_system", "dark_theme", "light_theme",
                   "glass", "glass_blur", "translucency", "rounding",
                   "colour_icons", "row_height"):
            style.update(self.config, _is_dark())
            self._relight()
            self.sheen.set_strength(0.05 if self.config["glass"] else 0.0)
        if key in ("columns", "icon_size", "show_preview", "show_status",
                   "thumbnail_limit_mb", "colour_icons", "row_height"):
            for pane in self.all_panes():
                pane.restyle()
        if key in ("show_hidden", "folders_first", "git_status",
                   "folder_sizes", "show_thumbnails"):
            self._refresh_panes()
        if key == "single_click":
            for pane in self.all_panes():
                pane.view.column_view.set_single_click_activate(
                    bool(self.config["single_click"]))
                pane.view.grid_view.set_single_click_activate(
                    bool(self.config["single_click"]))

    def show_shortcuts(self) -> None:
        dialog = dialogs._dialog("Keyboard shortcuts", 560)
        dialog.set_content_height(560)
        body = dialogs._page(dialog, "Keyboard shortcuts")
        grid = Gtk.Grid(column_spacing=18, row_spacing=5)
        grid.add_css_class("panel")
        for side in ("start", "end", "top", "bottom"):
            getattr(grid, f"set_margin_{side}")(14)
        row = 0
        for heading, pairs in SHORTCUTS:
            label = Gtk.Label(label=heading, xalign=0.0)
            label.add_css_class("group-heading")
            grid.attach(label, 0, row, 2, 1)
            row += 1
            for keys, what in pairs:
                key = Gtk.Label(label=keys, xalign=1.0)
                key.add_css_class("shortcut")
                key.set_valign(Gtk.Align.START)
                text = Gtk.Label(label=what, xalign=0.0)
                text.add_css_class("field-value")
                text.set_wrap(True)
                grid.attach(key, 0, row, 1, 1)
                grid.attach(text, 1, row, 1, 1)
                row += 1
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(grid)
        scroller.set_vexpand(True)
        body.append(scroller)
        close = dialogs._button("Close", "suggested-action")
        close.connect("clicked", lambda _b: dialog.close())
        body.append(dialogs._buttons(close))
        dialog.present(self)

    def show_about(self) -> None:
        from . import __version__

        about = Adw.AboutDialog()
        about.set_application_name("Hermione")
        about.set_version(__version__)
        about.set_developer_name("Robin")
        about.set_comments(
            "A file manager: two panes, tabs, a command palette, and a "
            "window made of glass.")
        about.set_license_type(Gtk.License.MIT_X11)
        about.present(self)

    # --------------------------------------------------------- the actions ---

    def _install_actions(self) -> None:
        pane_calls = {
            "back": lambda p: p.go_back(),
            "forward": lambda p: p.go_forward(),
            "up": lambda p: p.go_up(),
            "home": lambda p: p.go_home(),
            "refresh": lambda p: p.refresh(),
            "find": lambda p: p.toggle_filter(),
            "copy": lambda p: p.copy(),
            "cut": lambda p: p.cut(),
            "paste": lambda p: p.paste(),
            "rename": lambda p: p.rename(),
            "trash": lambda p: p.trash(),
            "delete": lambda p: p.delete_for_good(),
            "new-folder": lambda p: p.new_folder(),
            "new-file": lambda p: p.new_file(),
            "select-all": lambda p: p.select_all(),
            "select-none": lambda p: p.select_none(),
            "invert-selection": lambda p: p.invert_selection(),
            "properties": lambda p: p.show_properties(),
            "toggle-hidden": lambda p: p.toggle_hidden(),
            "toggle-preview": lambda p: p.toggle_preview(),
            "list-view": lambda p: p.set_view_mode("list"),
            "grid-view": lambda p: p.set_view_mode("grid"),
            "zoom-in": lambda p: p.zoom(16),
            "zoom-out": lambda p: p.zoom(-16),
            "terminal": lambda p: p.open_terminal(),
            "compress": lambda p: p.compress(),
            "extract": lambda p: p.extract(),
            "duplicate": lambda p: p.duplicate(),
            "open-with": lambda p: p.open_with(),
            "send-to-inbox": lambda p: p.send_to_inbox(),
            "ask-claude": lambda p: p.ask_claude(),
        }
        for name, call in pane_calls.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect(
                "activate",
                lambda _a, _p, fn=call: (lambda pane: fn(pane) if pane else None)(
                    self.current_pane()))
            self.add_action(action)

        window_calls = {
            "new-tab": lambda: self.new_tab(focus=True),
            "close-tab": self.close_current_tab,
            "next-tab": lambda: self._cycle_tab(1),
            "previous-tab": lambda: self._cycle_tab(-1),
            "split": self.toggle_split,
            "other-pane": self.focus_other_pane,
            "edit-path": self.edit_path,
            "sidebar": lambda: self.sidebar_button.set_active(
                not self.sidebar_button.get_active()),
            "palette": lambda: self.palette.open(),
            "undo": self.undo,
            "redo": self.redo,
            "settings": self.show_settings,
            "shortcuts": self.show_shortcuts,
            "about": self.show_about,
            "trash-folder": self.show_trash,
            "empty-trash": self.empty_trash,
            "process-inbox": self.show_inbox,
            "search-brain": self.search_brain,
            "menu": lambda: None,
            "place-open": lambda: self._open_place(self._menu_place),
            "place-tab": lambda: self.new_tab(self._menu_place.path, focus=True),
            "place-bookmark": lambda: self.bookmark(self._menu_place.path),
            "place-unbookmark": lambda: self.bookmark(self._menu_place.path),
            "place-eject": lambda: self._eject(self._menu_place),
        }
        for name, call in window_calls.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=call: fn())
            self.add_action(action)

        self.lookup_action("undo").set_enabled(False)
        self.lookup_action("redo").set_enabled(False)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    def _on_key(self, _controller, keyval, _code, state) -> bool:
        """The keys that cannot be accelerators.

        Escape has to mean different things depending on what is open, and a
        plain Delete or Backspace cannot be an app accelerator without
        stealing them from every entry in the window.
        """
        pane = self.current_pane()
        focus = self.get_focus()
        typing = isinstance(focus, (Gtk.Entry, Gtk.SearchEntry, Gtk.TextView))

        if keyval == Gdk.KEY_Escape:
            if self.palette.get_visible():
                self.palette.close()
                return True
            if self.ask_panel.get_visible():
                self.ask_panel.close()
                return True
            if pane is not None and pane.filter_bar.get_visible():
                pane.close_filter()
                return True
            if self.path_area.get_visible_child_name() == "entry":
                self.finish_path_edit()
                return True
            if pane is not None:
                pane.select_none()
                return True
        if typing:
            return False
        if keyval == Gdk.KEY_Delete and pane is not None:
            if state & Gdk.ModifierType.SHIFT_MASK:
                pane.delete_for_good()
            else:
                pane.trash()
            return True
        if keyval == Gdk.KEY_BackSpace and pane is not None:
            pane.go_up()
            return True
        if keyval == Gdk.KEY_F5 and pane is not None:
            pane.refresh()
            return True
        if keyval == Gdk.KEY_space and pane is not None:
            if pane.preview.get_visible():
                return False
            pane.toggle_preview()
            return True
        return False

    def close_current_tab(self) -> None:
        page = self.tabs.get_selected_page()
        if page is not None:
            self.tabs.close_page(page)

    def _cycle_tab(self, delta: int) -> None:
        count = self.tabs.get_n_pages()
        if count < 2:
            return
        page = self.tabs.get_selected_page()
        index = self.tabs.get_page_position(page) if page is not None else 0
        self.tabs.set_selected_page(
            self.tabs.get_nth_page((index + delta) % count))

    def focus_other_pane(self) -> None:
        other = self.other_pane()
        if other is None:
            self.toggle_split()
            other = self.other_pane()
        if other is not None:
            other.view.grab_focus()

    # -------------------------------------------- the window's own state ---

    def _watch_toplevel(self) -> None:
        """Watch the toplevel's state, so the slab can square its corners off.

        The geometry half of this — the remembered size, and the maximise
        that has to be handed back under a tiling compositor — is
        `housestyle.Geometry`, which the constructor set up before the map.
        """
        def realized(*_args):
            surface = self.get_surface()
            if surface is None:
                return
            surface.connect("notify::state", self._state_changed)

        self.connect("realize", realized)

    def _state_changed(self, surface, _param) -> None:
        state = surface.get_state()
        covered = bool(state & (Gdk.ToplevelState.MAXIMIZED
                                | Gdk.ToplevelState.FULLSCREEN))
        self.slab.set_square(covered)

    def _on_close(self, _window) -> bool:
        for job in list(self.jobs):
            job.cancel()
        self.ask_panel.stop()
        self._stop_watching_inbox()
        self._save_session()
        self.geometry.save()
        self.config["show_sidebar"] = self.split.get_show_sidebar()
        pane = self.current_pane()
        if pane is not None:
            self.config["last_folder"] = pane.path
        self.config.save()
        for pane in self.all_panes():
            pane.model.shutdown()
        return False

    def _save_session(self) -> None:
        if not self.config["restore_session"]:
            return
        tabs = []
        for page in self.tabs.get_pages():
            holder = page.get_child()
            panes = getattr(holder, "_panes", [])
            if not panes:
                continue
            tabs.append({"name": os.path.basename(panes[0].path) or "/",
                         "path": panes[0].path})
        self.config["tabs"] = tabs[:24]

    def _restore_session(self) -> None:
        opened = 0
        for tab in self.config["tabs"]:
            path = tab.get("path", "")
            if path and os.path.isdir(path):
                self.new_tab(path, focus=opened == 0)
                opened += 1
        if not opened:
            self.new_tab(os.path.expanduser("~"), focus=True)

    def select_path_arg(self, argument: str) -> None:
        """A path — or a `file:` URI — handed in from outside."""
        path = places.path_from_argument(argument)
        pane = self.current_pane()
        if pane is None:
            return
        if not path:
            self.toast(f"{argument} is not somewhere this can open")
            return
        if os.path.isdir(path):
            pane.navigate(path)
        elif os.path.exists(path):
            pane.reveal(path)
        else:
            self.toast(f"{argument} is not there")


class _JobStrip(Gtk.Box):
    """One running operation, as a floating line of glass."""

    def __init__(self, description: str, job):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("job-strip")
        self.job = job
        self.started = time.monotonic()

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.title = Gtk.Label(label=description, xalign=0.0)
        self.title.set_ellipsize(3)
        self.title.set_hexpand(True)
        self.title.add_css_class("strong")
        self.cancel = Gtk.Button.new_from_icon_name("window-close-symbolic")
        self.cancel.add_css_class("pill-flat")
        self.cancel.set_tooltip_text("Stop")
        self.cancel.connect("clicked", lambda _b: job.cancel())
        top.append(self.title)
        top.append(self.cancel)
        self.append(top)

        self.bar = Gtk.ProgressBar()
        self.bar.set_show_text(False)
        self.append(self.bar)

        self.detail = Gtk.Label(xalign=0.0)
        self.detail.add_css_class("where")
        self.detail.set_ellipsize(3)
        self.append(self.detail)

    def update(self, progress) -> bool:
        if progress.phase == "counting":
            self.bar.pulse()
            self.detail.set_label("Working out what is there…")
            return False
        fraction = progress.fraction
        self.bar.set_fraction(fraction)
        parts = []
        if progress.files_total:
            parts.append(f"{progress.files_done} of {progress.files_total}")
        if progress.bytes_total:
            parts.append(f"{units.human_size(progress.bytes_done)} of "
                         f"{units.human_size(progress.bytes_total)}")
        rate = progress.rate
        if rate > 0:
            parts.append(units.human_rate(progress.bytes_done, progress.elapsed))
        left = progress.remaining
        if left > 1:
            parts.append(units.human_duration(left) + " left")
        if progress.current:
            parts.append(progress.current)
        self.detail.set_label("   ".join(parts))
        return False

    def finish(self) -> None:
        self.bar.set_fraction(1.0)
        self.cancel.set_visible(False)
        self.detail.set_label("Done")


def _kind_icon(kind: str) -> str:
    """An icon for a brain hit, by what kind of node it is."""
    return {
        "idea": "dialog-information-symbolic",
        "question": "dialog-question-symbolic",
        "quote": "insert-text-symbolic",
        "person": "avatar-default-symbolic",
        "palace": "view-paged-symbolic",
    }.get(kind, "text-x-generic-symbolic")


def _flatten(groups) -> list:
    out = []
    for _heading, group in groups:
        out.extend(group)
    return out


def _is_dark() -> bool:
    """What the desktop is asking for, light or dark.

    Asked of the portal rather than of `AdwStyleManager`, whose `dark`
    property stops being the desktop's answer the moment the sheet forces a
    colour scheme — which it does, so that libadwaita's own light/dark
    decisions follow the theme rather than the session.
    """
    return housestyle.system_is_dark()


SHORTCUTS = (
    ("Getting around", (
        ("Alt+←", "Back"),
        ("Alt+→", "Forward"),
        ("Alt+↑ / Backspace", "Up one folder"),
        ("Alt+Home", "Home folder"),
        ("Ctrl+L", "Type a path"),
        ("Ctrl+Shift+P", "Go to, or do — the command palette"),
        ("F5", "Read the folder again"),
    )),
    ("Tabs and panes", (
        ("Ctrl+T", "New tab"),
        ("Ctrl+W", "Close this tab"),
        ("Ctrl+Tab", "Next tab"),
        ("Ctrl+N", "New window"),
        ("F3", "One pane or two"),
        ("F6", "The other pane"),
        ("F9", "Places sidebar"),
        ("F7 / Space", "Preview pane"),
    )),
    ("Looking at things", (
        ("Ctrl+1 / Ctrl+2", "List or icons"),
        ("Ctrl+H", "Hidden files"),
        ("Ctrl+F", "Search in this folder"),
        ("Ctrl++ / Ctrl+-", "Bigger or smaller icons"),
        ("Type a few letters", "Jump to that name"),
    )),
    ("Doing things", (
        ("Return", "Open"),
        ("Ctrl+C / Ctrl+X / Ctrl+V", "Copy, cut, paste"),
        ("F2", "Rename — several at once if several are selected"),
        ("Delete", "Move to the trash"),
        ("Shift+Delete", "Delete for good"),
        ("Ctrl+Shift+N", "New folder"),
        ("Ctrl+D", "Duplicate"),
        ("Ctrl+Z / Ctrl+Shift+Z", "Undo, redo"),
        ("Ctrl+A", "Select everything"),
        ("Alt+Return", "Properties"),
        ("Ctrl+Alt+T", "Open a terminal here"),
    )),
    ("The second brain", (
        ("Ctrl+Shift+I", "Send to the inbox"),
        ("Ctrl+Shift+P, then ?", "Search the brain"),
        ("1–9 / Return", "In the inbox: file it there / in the first place"),
    )),
)
