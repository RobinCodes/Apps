"""The window: the chrome, the tabs, and everything that has a shortcut.

Laid out as one Gtk.Overlay with the ambient light at the back and
everything else floating over it. That is unusual — most windows put the
background on the window and the widgets in a box — and it is what makes the
glass work: the panes have margins, the light is drawn edge to edge behind
them, and the gaps between the panes are where you see it. A box with a
background would have the same widgets in the same places and nothing
visible between them.

The actions are all on the window rather than on the application, because
every one of them means "in this window" and an accelerator installed on the
application fires in whichever window has the focus — which is the same
thing right up until the second window opens.
"""

from __future__ import annotations

import json
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Vte  # noqa: E402

from . import command, find, glass, housestyle, paths, prefs, shell, widgets  # noqa: E402
from . import theme as theming  # noqa: E402
from .panes import PaneGroup  # noqa: E402

SESSION_PATH = os.path.join(paths.data_home(), "session.json")

# Below this the header has to give things up; see `_on_narrow`.
NARROW = 620


def card_radius(config) -> int:
    """A floating panel is rounded a little more than a pane.

    One number, read by both the stylesheet and the cards' own drawing, so
    the blurred region and the painted body can never disagree about where
    the corner is.
    """
    return int(config["corner_radius"]) + 4


class MainWindow(Adw.ApplicationWindow):
    __gtype_name__ = "PrismWindow"

    def __init__(self, app, cwd=None, restore=False, with_tab=True,
                 command=None):
        super().__init__(application=app)
        self.config = app.config
        self.add_css_class("prism")
        self.set_title("Prism")

        self.scheme = theming.get(self.config.theme_key(self._system_is_dark()))
        self.light = glass.Light(self.scheme, motion=self.config["motion"])
        self._narrow = False
        self._closing = False
        # The remembered size and maximise, applied before the first map —
        # see housestyle.Geometry for why the two are treated differently
        # under a tiling compositor.
        self.geometry = housestyle.Geometry(self, self.config)
        self.geometry.restore()

        widgets.install_css(self.scheme, self.get_display(),
                            self.config["panel_blur"], card_radius(self.config))
        self._build()
        self._install_actions()
        self._install_breakpoint()

        self.connect("close-request", self._on_close_request)
        self.connect("notify::is-active", lambda *_: self.light.wake())

        # Adw.StyleManager is a process-wide singleton, so a handler
        # connected to it outlives the window that connected it: the closure
        # keeps a destroyed window alive, and the next time the desktop
        # changes between light and dark it is called on every window this
        # process has ever opened. Hold the id and let it go on the way out.
        self._style_manager = Adw.StyleManager.get_default()
        self._style_handler = self._style_manager.connect(
            "notify::dark", lambda *_: self._follow_system_theme())
        self.connect("destroy", self._disconnect_style)

        opened = self._restore_session() if restore else 0
        if not opened and with_tab:
            self.new_tab(cwd=cwd, command=command)

    # ------------------------------------------------------------ layout --

    def _build(self):
        self.toasts = Adw.ToastOverlay()
        self.overlay = Gtk.Overlay()
        self.toasts.set_child(self.overlay)
        self.set_content(self.toasts)

        self.ambient = glass.Ambient(self.light, opacity=self.config["opacity"],
                                     grain=self.config["grain"])
        self.ambient.set_hexpand(True)
        self.ambient.set_vexpand(True)
        self.overlay.set_child(self.ambient)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_start(8)
        root.set_margin_end(8)
        root.set_margin_top(8)
        root.set_margin_bottom(8)
        self.overlay.add_overlay(root)

        # --- the chrome: one slab holding the header and the tab strip ---
        self.chrome = glass.Slab(
            glass.Material.chrome(self.scheme, self.config["opacity"],
                                  radius=self.config["corner_radius"],
                                  grain=self.config["grain"]),
            radius=self.config["corner_radius"],
            specular=self.config["specular"], light=self.light)
        self.chrome.add_css_class("chrome")
        root.append(self.chrome)

        self.header = Adw.HeaderBar()
        self.header.add_css_class("flat")
        self.header.set_show_title(True)
        self.title = Adw.WindowTitle(title="Prism", subtitle="")
        self.header.set_title_widget(self.title)
        self.chrome.append(self.header)

        self.new_tab_button = widgets.icon_button(
            "tab-new-symbolic", "New tab  (Ctrl+Shift+T)", "win.new-tab")
        self.header.pack_start(self.new_tab_button)

        self.split_button = widgets.icon_button(
            "view-dual-symbolic", "Split to the right  (Ctrl+Shift+\\)",
            "win.split-right")
        self.header.pack_start(self.split_button)

        self.header.pack_end(self._menu_button())
        self.find_button = widgets.icon_button(
            "edit-find-symbolic", "Find in output  (Ctrl+Shift+F)", "win.find")
        self.header.pack_end(self.find_button)
        self.palette_button = widgets.icon_button(
            "view-more-symbolic", "Commands  (Ctrl+Shift+P)", "win.palette")
        self.header.pack_end(self.palette_button)

        self.tabs = Adw.TabView()
        self.tabs.connect("notify::selected-page", lambda *_: self._on_tab_changed())
        self.tabs.connect("close-page", self._on_close_page)
        self.tabs.connect("create-window", self._on_tab_torn_off)
        self.tabs.connect("page-attached", self._on_page_attached)
        self.tabs.connect("notify::n-pages", lambda *_: self._check_empty())

        self.tab_bar = Adw.TabBar(view=self.tabs, autohide=False,
                                  expand_tabs=False)
        self.chrome.append(self.tab_bar)

        self.overview = Adw.TabOverview(view=self.tabs, enable_new_tab=True)
        self.overview.connect("create-tab", lambda _o: self.new_tab())
        self.overview.set_child(self.tabs)
        self.overview.set_vexpand(True)
        root.append(self.overview)

        # --- the floating things ---
        self.scrim = glass.Scrim(self.scheme)
        self.scrim.set_visible(False)
        self.overlay.add_overlay(self.scrim)

        self.palette = command.Palette(self.scheme, self.light, self.config,
                                       self._run_command)
        self.palette.set_visible(False)
        self.overlay.add_overlay(self.palette)

        self.find_bar = find.FindBar(self.scheme, self.light, self.config,
                                     lambda: self.active_pane, self._find_closed)
        self.find_bar.set_visible(False)
        self.overlay.add_overlay(self.find_bar)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda *_: (self.light.wake(), False)[1])
        self.add_controller(keys)

    def _menu_button(self):
        menu = Gio.Menu()

        session = Gio.Menu()
        session.append("New tab", "win.new-tab")
        session.append("New window", "win.new-window")
        session.append("Close tab", "win.close-tab")
        menu.append_section(None, session)

        layout = Gio.Menu()
        layout.append("Split to the right", "win.split-right")
        layout.append("Split downwards", "win.split-down")
        layout.append("Close this pane", "win.close-pane")
        menu.append_section(None, layout)

        view = Gio.Menu()
        view.append("Tab overview", "win.overview")
        view.append("Zoom in", "win.zoom-in")
        view.append("Zoom out", "win.zoom-out")
        view.append("Reset zoom", "win.zoom-reset")
        view.append("Full screen", "win.fullscreen")
        menu.append_section(None, view)

        themes = Gio.Menu()
        for scheme in theming.ordered():
            item = Gio.MenuItem.new(scheme.name, None)
            item.set_action_and_target_value(
                "win.set-theme", GLib.Variant.new_string(scheme.key))
            themes.append_item(item)
        appearance = Gio.Menu()
        appearance.append_submenu("Theme", themes)
        menu.append_section(None, appearance)

        rest = Gio.Menu()
        rest.append("Commands…", "win.palette")
        rest.append("Preferences", "win.preferences")
        rest.append("Keyboard shortcuts", "win.shortcuts")
        rest.append("About Prism", "app.about")
        menu.append_section(None, rest)

        button = Gtk.MenuButton(icon_name="open-menu-symbolic",
                                tooltip_text="Menu", css_classes=["flat"],
                                menu_model=menu, primary=True)
        return button

    def _install_breakpoint(self):
        """At a tiling column's width the header gives up its extras.

        The screen this runs on is 1366 wide and niri's narrowest preset
        column is a third of that, so a terminal that cannot be put in one is
        a terminal that cannot be used the way the desktop is used. The two
        buttons hidden here are both in the menu, and the subtitle is the
        directory, which the tab already says.
        """
        condition = Adw.BreakpointCondition.new_length(
            Adw.BreakpointConditionLengthType.MAX_WIDTH, NARROW, Adw.LengthUnit.PX)
        point = Adw.Breakpoint.new(condition)
        point.connect("apply", lambda *_: self._on_narrow(True))
        point.connect("unapply", lambda *_: self._on_narrow(False))
        self.add_breakpoint(point)

    def _on_narrow(self, narrow: bool):
        self._narrow = narrow
        self.split_button.set_visible(not narrow)
        self.palette_button.set_visible(not narrow)
        self.title.set_visible(not narrow)
        self._retitle()

    # -------------------------------------------------------------- tabs --

    @property
    def group(self) -> PaneGroup | None:
        page = self.tabs.get_selected_page()
        return page.get_child() if page is not None else None

    @property
    def active_pane(self):
        group = self.group
        return group.active if group is not None else None

    def new_tab(self, cwd=None, select=True, command=None):
        if cwd is None:
            pane = self.active_pane
            cwd = pane.cwd() if pane is not None else None
        group = PaneGroup(self.config, self.scheme, self.light, cwd=cwd,
                          command=command)
        group.connect("changed", lambda _g: self._on_group_changed(_g))
        group.connect("activity", self._on_activity)
        group.connect("emptied", self._on_group_emptied)
        group.connect("open-uri", lambda _g, uri: self._open_uri(uri))

        page = self.tabs.append(group)
        page.set_title(group.title())
        page.set_live_thumbnail(True)
        if select:
            self.tabs.set_selected_page(page)
            GLib.idle_add(group.focus_active)
        self._sync_tab_bar()
        return group

    def _page_for(self, group):
        return self.tabs.get_page(group) if group is not None else None

    def _on_group_changed(self, group):
        page = self._page_for(group)
        if page is not None:
            page.set_title(group.title())
            if page is self.tabs.get_selected_page():
                self._retitle()

    def _on_activity(self, group):
        self.light.wake()
        page = self._page_for(group)
        if page is not None and page is not self.tabs.get_selected_page():
            page.set_needs_attention(True)

    def _on_group_emptied(self, group):
        page = self._page_for(group)
        if page is not None:
            self.tabs.close_page(page)

    def _on_tab_changed(self):
        page = self.tabs.get_selected_page()
        if page is not None:
            page.set_needs_attention(False)
            group = page.get_child()
            GLib.idle_add(group.focus_active)
        self._retitle()
        self._sync_tab_bar()
        if self.find_bar.get_visible():
            self.find_bar.close()

    def _on_close_page(self, view, page):
        """Adw.TabView asks whether a tab may close.

        Returning False lets Adwaita close it in the ordinary way. Returning
        True means "I am handling this", and then the answer has to come back
        through close_page_finish — which is what makes it possible to ask a
        question first.
        """
        if self._closing or not self.config["confirm_close"]:
            return False
        running = page.get_child().busy_command()
        if not running:
            return False

        dialog = Adw.AlertDialog(
            heading=f"“{running}” is still running",
            body="Closing this tab ends it.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("close", "Close tab")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response",
                       lambda _d, answer: view.close_page_finish(page, answer == "close"))
        dialog.present(self)
        return True

    def _on_tab_torn_off(self, _view):
        """A tab dragged out of the window becomes a window of its own.

        Made with no tab of its own: one that opened a terminal and then had
        it closed would spawn a shell for a tenth of a second for nothing,
        and the empty-window check below would race the dragged tab's
        arrival.
        """
        window = MainWindow(self.get_application(), with_tab=False)
        window.present()
        return window.tabs

    def _on_page_attached(self, _view, page, _position):
        group = page.get_child()
        if hasattr(group, "set_light"):
            group.set_light(self.light)
            group.apply_config(self.config, self.scheme)
        self._sync_tab_bar()

    def _check_empty(self):
        """The last tab closed, so there is nothing left to be a window of.

        Deferred, because a tab being dragged from one window to another is
        detached before it is attached, and for that moment the window it
        left has no pages and is not finished with.
        """
        self._sync_tab_bar()
        if self.tabs.get_n_pages() > 0 or self._closing:
            return
        def later():
            if self.tabs.get_n_pages() == 0 and not self._closing:
                self.close()
            return False
        GLib.timeout_add(120, later)

    def _sync_tab_bar(self):
        mode = self.config["tab_bar"]
        count = self.tabs.get_n_pages()
        self.tab_bar.set_visible(mode == "always"
                                 or (mode == "auto" and count > 1))

    def _retitle(self):
        group = self.group
        if group is None:
            return
        name = group.title()
        self.title.set_title(name)
        pane = group.active
        cwd = pane.cwd() if pane is not None else None
        subtitle = ""
        if self.config["cwd_in_title"] and cwd and cwd != name:
            home = os.path.expanduser("~")
            subtitle = "~" + cwd[len(home):] if cwd.startswith(home) else cwd
        self.title.set_subtitle(subtitle)
        self.set_title(f"{name} — Prism")

    # ----------------------------------------------------------- actions --

    def _install_actions(self):
        spec = [
            ("new-tab", lambda: self.new_tab(), ["<Control><Shift>t"]),
            ("new-window", self._new_window, ["<Control><Shift>n"]),
            ("close-tab", self._close_tab, ["<Control><Shift>w"]),
            ("next-tab", lambda: self.tabs.select_next_page(),
             ["<Control>Page_Down", "<Control><Shift>bracketright"]),
            ("prev-tab", lambda: self.tabs.select_previous_page(),
             ["<Control>Page_Up", "<Control><Shift>bracketleft"]),
            ("overview", self._toggle_overview, ["<Control><Shift>space"]),

            ("split-right", lambda: self._split(True), ["<Control><Shift>backslash"]),
            ("split-down", lambda: self._split(False), ["<Control><Shift>minus"]),
            ("close-pane", self._close_pane, ["<Control><Shift>x"]),
            ("focus-left", lambda: self._focus(-1, 0), ["<Alt>Left"]),
            ("focus-right", lambda: self._focus(1, 0), ["<Alt>Right"]),
            ("focus-up", lambda: self._focus(0, -1), ["<Alt>Up"]),
            ("focus-down", lambda: self._focus(0, 1), ["<Alt>Down"]),
            ("focus-next", lambda: self._cycle(1), ["<Control><Shift>o"]),

            ("copy", self._copy, ["<Control><Shift>c"]),
            ("paste", self._paste, ["<Control><Shift>v"]),
            ("select-all", self._select_all, ["<Control><Shift>a"]),
            ("find", self._open_find, ["<Control><Shift>f"]),
            ("find-next", lambda: self.find_bar.step(1), ["<Control><Shift>g"]),
            ("palette", self._open_palette, ["<Control><Shift>p"]),
            ("preferences", self._open_prefs, ["<Control>comma"]),
            ("shortcuts", self._open_shortcuts, ["<Control><Shift>question"]),

            ("zoom-in", lambda: self._zoom(0.1),
             ["<Control>plus", "<Control>equal", "<Control>KP_Add"]),
            ("zoom-out", lambda: self._zoom(-0.1),
             ["<Control>minus", "<Control>KP_Subtract"]),
            ("zoom-reset", self._zoom_reset, ["<Control>0", "<Control>KP_0"]),
            ("fullscreen", self._toggle_fullscreen, ["F11"]),
            ("reset-terminal", self._reset_terminal, []),
            ("copy-all", self._copy_all, []),
        ]
        for name, run, accels in spec:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=run: fn())
            self.add_action(action)
            if accels:
                self.get_application().set_accels_for_action(f"win.{name}", accels)

        # Alt+1..9 for the tabs, and Alt+0 for the last one.
        for index in range(1, 10):
            action = Gio.SimpleAction.new(f"tab-{index}", None)
            action.connect("activate", lambda _a, _p, n=index: self._select_tab(n - 1))
            self.add_action(action)
            self.get_application().set_accels_for_action(
                f"win.tab-{index}", [f"<Alt>{index}"])
        last = Gio.SimpleAction.new("tab-last", None)
        last.connect("activate", lambda *_: self._select_tab(self.tabs.get_n_pages() - 1))
        self.add_action(last)
        self.get_application().set_accels_for_action("win.tab-last", ["<Alt>0"])

        theme_action = Gio.SimpleAction.new("set-theme", GLib.VariantType.new("s"))
        theme_action.connect("activate", self._on_set_theme)
        self.add_action(theme_action)

    def _select_tab(self, index):
        if 0 <= index < self.tabs.get_n_pages():
            self.tabs.set_selected_page(self.tabs.get_nth_page(index))

    def _new_window(self):
        pane = self.active_pane
        MainWindow(self.get_application(),
                   cwd=pane.cwd() if pane else None).present()

    def _close_tab(self):
        page = self.tabs.get_selected_page()
        if page is not None:
            self.tabs.close_page(page)

    def _split(self, horizontal):
        group = self.group
        if group is not None:
            group.split(horizontal)

    def _close_pane(self):
        group = self.group
        if group is not None:
            group.close_active()

    def _focus(self, dx, dy):
        group = self.group
        if group is not None:
            group.focus_direction(dx, dy)

    def _cycle(self, step):
        group = self.group
        if group is not None:
            group.focus_cycle(step)

    def _toggle_overview(self):
        self.overview.set_open(not self.overview.get_open())

    def _toggle_fullscreen(self):
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def _zoom(self, step):
        pane = self.active_pane
        if pane is not None:
            pane.zoom(step)

    def _zoom_reset(self):
        pane = self.active_pane
        if pane is not None:
            pane.zoom_reset()

    def _reset_terminal(self):
        pane = self.active_pane
        if pane is not None:
            pane.terminal.reset(True, True)

    # ------------------------------------------------------- copy & paste --

    def _copy(self):
        pane = self.active_pane
        if pane is None:
            return
        if pane.terminal.get_has_selection():
            pane.terminal.copy_clipboard_format(Vte.Format.TEXT)
            widgets.toast(self, "Copied")

    def _copy_all(self):
        pane = self.active_pane
        if pane is None:
            return
        pane.terminal.select_all()
        self._copy()
        pane.terminal.unselect_all()

    def _select_all(self):
        pane = self.active_pane
        if pane is not None:
            pane.terminal.select_all()

    def _paste(self):
        """Paste, with a look at what is being pasted first.

        A single line goes straight through. Anything with a newline in it is
        held up and shown, because a newline in a terminal is not punctuation
        — it is the Enter key, and text that arrives with one already in it
        runs without ever having been read. This is the oldest trick on the
        web: a copy button that quietly puts `rm -rf` and a newline on the
        clipboard. The guard can be turned off and the toast says so.
        """
        pane = self.active_pane
        if pane is None:
            return
        clipboard = self.get_display().get_clipboard()

        def arrived(_clipboard, result):
            try:
                text = clipboard.read_text_finish(result)
            except GLib.Error:
                return
            if not text:
                return
            if not self.config["paste_guard"] or not shell.paste_runs_itself(text):
                pane.send(text)
                return
            lines = text.strip().count("\n") + 1
            first = (text.strip().splitlines() or [""])[0]
            preview = "".join(c if c >= " " else "·" for c in first)[:70]
            widgets.confirm(
                self, f"Paste {lines} lines?",
                f"The clipboard holds more than one line, so it will run as "
                f"soon as it is pasted.\n\nIt starts with:\n{preview}",
                "Paste", lambda: pane.send(text), destructive=True)

        clipboard.read_text_async(None, arrived)

    def _open_uri(self, uri: str) -> bool:
        launcher = Gtk.UriLauncher.new(uri)
        launcher.launch(self, None, None)
        return True

    # ----------------------------------------------------------- overlays --

    def _open_find(self):
        self.scrim.set_visible(False)
        pane = self.active_pane
        seed = ""
        if pane is not None and pane.terminal.get_has_selection():
            seed = (pane.terminal.get_text_selected(Vte.Format.TEXT) or "").strip()
            seed = seed.splitlines()[0] if seed else ""
        self.find_bar.open(seed)

    def _find_closed(self):
        group = self.group
        if group is not None:
            group.focus_active()

    def _open_palette(self):
        self.scrim.set_visible(True)
        self.palette.open(self._commands())

    def _run_command(self, chosen):
        self.scrim.set_visible(False)
        if chosen is None:
            group = self.group
            if group is not None:
                group.focus_active()
            return
        chosen.run()

    def _commands(self):
        def act(name):
            return lambda: self.activate_action(name, None)

        items = [
            command.Command("new-tab", "New tab", "Session", act("win.new-tab"),
                            "Ctrl+Shift+T"),
            command.Command("new-window", "New window", "Session",
                            act("win.new-window"), "Ctrl+Shift+N"),
            command.Command("close-tab", "Close tab", "Session",
                            act("win.close-tab"), "Ctrl+Shift+W"),
            command.Command("overview", "Tab overview", "Session",
                            act("win.overview"), "Ctrl+Shift+Space"),

            command.Command("split-right", "Split to the right", "Panes",
                            act("win.split-right"), "Ctrl+Shift+\\"),
            command.Command("split-down", "Split downwards", "Panes",
                            act("win.split-down"), "Ctrl+Shift+-"),
            command.Command("close-pane", "Close this pane", "Panes",
                            act("win.close-pane"), "Ctrl+Shift+X"),
            command.Command("focus-next", "Focus the next pane", "Panes",
                            act("win.focus-next"), "Ctrl+Shift+O"),

            command.Command("find", "Find in output", "Edit", act("win.find"),
                            "Ctrl+Shift+F"),
            command.Command("copy", "Copy selection", "Edit", act("win.copy"),
                            "Ctrl+Shift+C"),
            command.Command("paste", "Paste", "Edit", act("win.paste"),
                            "Ctrl+Shift+V"),
            command.Command("copy-all", "Copy the whole buffer", "Edit",
                            act("win.copy-all")),
            command.Command("select-all", "Select all", "Edit",
                            act("win.select-all"), "Ctrl+Shift+A"),

            command.Command("zoom-in", "Zoom in", "View", act("win.zoom-in"),
                            "Ctrl++"),
            command.Command("zoom-out", "Zoom out", "View", act("win.zoom-out"),
                            "Ctrl+-"),
            command.Command("zoom-reset", "Reset zoom", "View",
                            act("win.zoom-reset"), "Ctrl+0"),
            command.Command("fullscreen", "Full screen", "View",
                            act("win.fullscreen"), "F11"),
            command.Command("reset", "Reset the terminal", "View",
                            act("win.reset-terminal")),
        ]

        for scheme in theming.ordered():
            items.append(command.Command(
                f"theme-{scheme.key}", f"Theme: {scheme.name}", "Appearance",
                lambda key=scheme.key: self._use_theme(key),
                hint=scheme.blurb))

        items.append(command.Command(
            "motion", "Living light: " + ("on" if self.config["motion"] else "off"),
            "Appearance", lambda: self._toggle("motion")))
        items.append(command.Command(
            "specular",
            "Specular highlight: " + ("on" if self.config["specular"] else "off"),
            "Appearance", lambda: self._toggle("specular")))
        items.append(command.Command(
            "prefs", "Preferences", "Appearance", act("win.preferences"),
            "Ctrl+,"))
        return items

    def _toggle(self, key):
        self.config.set(key, not self.config[key])
        self.apply_settings()

    def _on_set_theme(self, _action, value):
        self._use_theme(value.get_string())

    def _use_theme(self, key):
        scheme = theming.get(key)
        self.config.set("follow_system", False)
        self.config.set("theme", key)
        self._switch_scheme(scheme)

    def _follow_system_theme(self):
        if self.config["follow_system"]:
            self._switch_scheme(
                theming.get(self.config.theme_key(self._system_is_dark())))

    @staticmethod
    def _system_is_dark() -> bool:
        """What the desktop wants, asked of the portal rather than of Adwaita.

        `AdwStyleManager:dark` is the obvious place and it stops being honest
        the moment anything forces a colour scheme, which the house sheet
        does so that libadwaita's own light/dark decisions match the theme.
        `housestyle` keeps asking the portal, which keeps answering.
        """
        return housestyle.system_is_dark()

    def _switch_scheme(self, scheme):
        self.scheme = scheme
        self.light.set_scheme(scheme)
        self.scrim.set_scheme(scheme)
        widgets.install_css(scheme, self.get_display(), self.config["panel_blur"],
                            card_radius(self.config))
        self.apply_settings()

    def apply_settings(self):
        """Push every setting into every widget that cares about one."""
        config = self.config
        self.ambient.set_opacity_level(config["opacity"])
        self.ambient.set_motion(config["motion"])
        self.ambient.set_grain(config["grain"])
        self.chrome.set_material(glass.Material.chrome(
            self.scheme, config["opacity"], radius=config["corner_radius"],
            grain=config["grain"]))
        self.chrome.set_specular(config["specular"])
        widgets.install_css(self.scheme, self.get_display(), config["panel_blur"],
                            card_radius(config))
        self.palette.restyle(self.scheme, self.light, config)
        self.find_bar.restyle(self.scheme, self.light, config)
        # A blurred panel needs less dimming behind it; the blur is already
        # doing what the dimming was there for.
        self.scrim.set_strength(0.22 if config["panel_blur"] else 0.42)
        for index in range(self.tabs.get_n_pages()):
            self.tabs.get_nth_page(index).get_child().apply_config(
                config, self.scheme)
        self._sync_tab_bar()
        self._retitle()

    def _open_prefs(self):
        prefs.PrefsDialog(self.config, self.apply_settings).present(self)

    def _open_shortcuts(self):
        lines = []
        for item in self._commands():
            if item.accel:
                lines.append(f"{item.accel}\t{item.label}")
        body = "\n".join(lines)
        dialog = Adw.AlertDialog(heading="Keyboard shortcuts", body=body)
        dialog.add_response("ok", "Close")
        dialog.present(self)

    # ----------------------------------------------------------- session --

    def session_state(self):
        """What this window would need to come back as it is."""
        tabs = []
        for index in range(self.tabs.get_n_pages()):
            group = self.tabs.get_nth_page(index).get_child()
            panes = [pane.cwd() or "" for pane in group.panes]
            tabs.append({"panes": panes})
        return {"tabs": tabs}

    def _save_session(self):
        if not self.config["restore_session"]:
            return
        try:
            os.makedirs(paths.data_home(), exist_ok=True)
            tmp = SESSION_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.session_state(), fh, indent=2)
            os.replace(tmp, SESSION_PATH)
        except OSError:
            pass

    def _restore_session(self) -> int:
        if not self.config["restore_session"]:
            return 0
        try:
            with open(SESSION_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return 0
        if not isinstance(data, dict):
            return 0
        opened = 0
        for tab in data.get("tabs", [])[:20]:
            if not isinstance(tab, dict):
                continue
            places = [p for p in tab.get("panes", []) if isinstance(p, str)]
            group = self.new_tab(cwd=places[0] if places else None,
                                 select=opened == 0)
            opened += 1
            # Splits are restored as splits but not as the exact tree: the
            # sizes and the nesting are not worth the file format, and a row
            # of panes in the right directories is what anyone actually
            # wanted back.
            for place in places[1:4]:
                pane = group.split(True)
                if pane is not None and place:
                    pane.send(f" cd {GLib.shell_quote(place)}\n")
        return opened

    # ------------------------------------------------------------ window --

    def _remember_geometry(self):
        self.geometry.save()
        self.config.save()

    def _busy_tabs(self):
        names = []
        for index in range(self.tabs.get_n_pages()):
            running = self.tabs.get_nth_page(index).get_child().busy_command()
            if running:
                names.append(running)
        return names

    def _disconnect_style(self, *_args):
        if self._style_handler:
            self._style_manager.disconnect(self._style_handler)
            self._style_handler = 0

    def save_state(self):
        """The session and the geometry, written now."""
        self._save_session()
        self._remember_geometry()

    def _on_close_request(self, _window):
        if not self._closing and self.config["confirm_close"]:
            busy = self._busy_tabs()
            if busy:
                listed = ", ".join(sorted(set(busy))[:4])
                dialog = Adw.AlertDialog(
                    heading="Something is still running",
                    body=f"Closing this window ends it: {listed}.")
                dialog.add_response("cancel", "Cancel")
                dialog.add_response("close", "Close anyway")
                dialog.set_response_appearance(
                    "close", Adw.ResponseAppearance.DESTRUCTIVE)
                dialog.set_default_response("cancel")
                dialog.set_close_response("cancel")

                def answered(_dialog, answer):
                    if answer == "close":
                        self._closing = True
                        self.close()

                dialog.connect("response", answered)
                dialog.present(self)
                return True
        self._closing = True
        self.save_state()
        # Here rather than only on `destroy`: close() does not destroy the
        # window there and then — the application holds it until it has
        # finished with it — and the cancel path above returns before this,
        # so a window that is *not* closing keeps its handler.
        self._disconnect_style()
        return False
