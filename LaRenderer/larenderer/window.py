"""The window: editor on the left, rendered pages on the right.

Owns the compile cycle. Every keystroke restarts a short timer; when it
expires the buffer goes to the engine on a worker thread, and whatever comes
back updates the preview, the problems list and the status strip. A compile
still running when the next one is due is killed rather than waited for.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import compiler, jobs, packages, templates, widgets  # noqa: E402
from .config import Config  # noqa: E402
from .document import UNTITLED, Document  # noqa: E402
from .preview import FIT_PAGE, FIT_WIDTH, THEME_LABEL, THEMES  # noqa: E402


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="LaRenderer")
        self.config = Config()
        self._package_status: dict[str, bool] = {}
        # Set once the tab view exists; until then there is no active document
        # and the properties below have to answer for one that isn't there.
        self.active: Document | None = None
        self._by_page: dict = {}

        self.set_default_size(self.config["window_width"], self.config["window_height"])
        if self.config["window_maximized"]:
            self.maximize()

        self._build_actions()
        self._build_ui()
        self._install_accelerators()
        self.problems_button.set_active(bool(self.config["show_problems"]))

        self.connect("close-request", self._on_close)

        GLib.idle_add(self._start_up)

    # ------------------------------------------------- the active document --
    #
    # Everything below the tab bar belongs to one document at a time, and the
    # rest of this class is written as though there were only ever one. These
    # forward to whichever is in front, so that stays true: `self.editor` is
    # the editor you can see, `self.path` is the file it came from.

    @property
    def editor(self):
        return self.active.editor

    @property
    def preview(self):
        return self.active.preview

    @property
    def problems(self):
        return self.active.problems

    @property
    def problems_revealer(self):
        return self.active.problems_revealer

    @property
    def paned(self):
        return self.active.paned

    @property
    def path(self) -> str | None:
        return self.active.path if self.active else None

    @path.setter
    def path(self, value):
        self.active.path = value

    @property
    def _result(self):
        return self.active.result

    @_result.setter
    def _result(self, value):
        self.active.result = value

    @property
    def _job(self):
        return self.active.job

    @_job.setter
    def _job(self, value):
        self.active.job = value

    @property
    def _compiling(self) -> bool:
        return self.active.compiling

    @_compiling.setter
    def _compiling(self, value):
        self.active.compiling = value

    @property
    def _pending(self) -> bool:
        return self.active.pending

    @_pending.setter
    def _pending(self, value):
        self.active.pending = value

    @property
    def _compile_timer(self) -> int:
        return self.active.compile_timer

    @_compile_timer.setter
    def _compile_timer(self, value):
        self.active.compile_timer = value

    @property
    def _edited_since_load(self) -> bool:
        return self.active.edited_since_load

    @_edited_since_load.setter
    def _edited_since_load(self, value):
        self.active.edited_since_load = value

    @property
    def documents(self) -> list:
        """Every open document, in tab order."""
        return [self._by_page[self.tabs.get_nth_page(i)]
                for i in range(self.tabs.get_n_pages())
                if self.tabs.get_nth_page(i) in self._by_page]

    # ----------------------------------------------------------------- UI

    def _build_ui(self):
        # One page per open document; the editor and preview live in there
        # rather than here. See document.py.
        self.tabs = Adw.TabView(vexpand=True)
        self.tabs.connect("notify::selected-page", self._on_tab_selected)
        # Returning True says the close is being handled: every close goes
        # through _confirm_close, which may have to ask about unsaved changes
        # before the page can actually go.
        self.tabs.connect("close-page", self._on_close_page)
        # Closing the last tab closes the window, the way a tabbed editor
        # does. Without this the window would stay up with no document in it
        # and nothing for the properties above to answer with.
        self.tabs.connect("notify::n-pages", self._on_pages_changed)

        self.tab_bar = Adw.TabBar(view=self.tabs, autohide=True)

        # One document before the chrome, because the header and the status
        # strip are built from it — the zoom label asks the preview what it
        # currently says, and there has to be a preview to ask. _start_up
        # either fills this tab or replaces it with the remembered ones.
        self.open_document()

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.tab_bar)
        content.append(self.tabs)
        content.append(self._build_status_strip())

        view = Adw.ToolbarView()
        view.add_top_bar(self._build_header())
        view.set_content(content)

        self.toasts = Adw.ToastOverlay(child=view)
        self.set_content(self.toasts)

    # --------------------------------------------------------------- tabs --

    def open_document(self, path: str | None = None, text: str | None = None,
                      focus: bool = True):
        """Put a document in a new tab and return it.

        Opening a file that is already open selects the tab it is in rather
        than making a second one — two tabs over the same file would be two
        buffers over one path, and whichever was saved last would win.
        """
        if path:
            for existing in self.documents:
                if existing.is_at(path):
                    if focus:
                        self.tabs.set_selected_page(self._page_for(existing))
                    return existing

        document = Document(self, path=path)
        page = self.tabs.append(document.root)
        self._by_page[page] = document
        page.set_title(document.name)
        if path:
            page.set_tooltip(path)
        if text is not None:
            document.editor.set_text(text)
            document.edited_since_load = False
        if focus:
            self.tabs.set_selected_page(page)
            # append() selects the first page itself, so for that one the
            # notify fired before this document was in _by_page and nothing
            # adopted it. Adopting again is free when it already happened.
            self._on_tab_selected()
        return document

    def _page_for(self, document):
        for page, candidate in self._by_page.items():
            if candidate is document:
                return page
        return None

    def _on_tab_selected(self, *_):
        page = self.tabs.get_selected_page()
        document = self._by_page.get(page) if page else None
        if document is None or document is self.active:
            return
        self.active = document
        # The first document is made while the chrome is still being built,
        # and there is nothing to point at it yet.
        if hasattr(self, "status_label"):
            self._adopt_active()

    def _adopt_active(self):
        """Point the shared chrome at whichever document is now in front.

        The header, the status strip and the problems toggle are one set of
        widgets shared by every tab, so switching tabs has to re-read all of
        them from the document rather than leaving the last one's numbers up.
        """
        document = self.active
        self._update_title()
        self.status_label.set_label(document.status_text)
        strip = self.status_label.get_parent()
        for name in ("ok", "bad", "compiling"):
            strip.remove_css_class(name)
        if document.status_css:
            strip.add_css_class(document.status_css)
        self.problems_button.set_label(document.problems_label)
        self.compile_button.set_sensitive(not document.compiling)
        self.problems_revealer.set_reveal_child(self.problems_button.get_active())
        self.page_label.set_label(document.page_text)
        self._on_cursor()

    def new_document(self):
        document = self.open_document()
        document.editor.set_text(templates.ARTICLE.replace(templates.CURSOR, ""))
        document.edited_since_load = False
        self._update_title()
        self.compile_now()

    def close_current(self):
        page = self.tabs.get_selected_page()
        if page is not None:
            self.tabs.close_page(page)

    def _on_close_page(self, view, page):
        """Adw asks before removing a page; unsaved work is why we might say no."""
        document = self._by_page.get(page)
        if document is None:
            view.close_page_finish(page, True)
            return True
        if not document.modified:
            self._forget(page, document)
            view.close_page_finish(page, True)
            return True

        dialog = Adw.AlertDialog(
            heading="Save before closing?",
            body=f"{document.name} has changes that have not been written to disk.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("discard", "Discard")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def answered(dlg, result):
            answer = dlg.choose_finish(result)
            if answer == "discard":
                self._forget(page, document)
                view.close_page_finish(page, True)
            elif answer == "save" and document.path:
                if self._write_document(document, document.path):
                    self._forget(page, document)
                    view.close_page_finish(page, True)
                else:
                    view.close_page_finish(page, False)
            else:
                # Cancel, or a save that still needs somewhere to go: keep the
                # tab, so Save As has a document to act on.
                view.close_page_finish(page, False)
                if answer == "save":
                    self.save_as()

        dialog.choose(self, None, answered)
        return True

    def _forget(self, page, document):
        """Let go of a document that is on its way out."""
        document.cancel_compile()
        self._by_page.pop(page, None)
        if self.active is document:
            self.active = None

    def _on_pages_changed(self, *_):
        """The last tab closing takes the window with it.

        Every tab has already been asked about its unsaved changes by the time
        it goes, so there is nothing left to confirm here.
        """
        if self.tabs.get_n_pages() == 0:
            self._remember_geometry()
            # And with nothing open, that is what gets restored: closing every
            # tab by hand should not be undone on the next start.
            self._remember_open_files()
            self.destroy()
            return
        # A tab going takes its neighbour's place at the front; adopt it.
        self._on_tab_selected()

    def next_tab(self):
        if not self.tabs.select_next_page():
            first = self.tabs.get_nth_page(0)
            if first is not None:
                self.tabs.set_selected_page(first)

    def previous_tab(self):
        if not self.tabs.select_previous_page():
            last = self.tabs.get_nth_page(self.tabs.get_n_pages() - 1)
            if last is not None:
                self.tabs.set_selected_page(last)

    def _build_header(self) -> Adw.HeaderBar:
        header = Adw.HeaderBar()

        open_button = Gtk.Button(label="Open")
        open_button.set_action_name("win.open")
        header.pack_start(open_button)

        self.save_button = Gtk.Button(label="Save")
        self.save_button.set_action_name("win.save")
        header.pack_start(self.save_button)

        new_button = Gtk.MenuButton(label="New")
        new_button.set_popover(self._templates_popover())
        header.pack_start(new_button)

        self.title_widget = Adw.WindowTitle(title=UNTITLED, subtitle="")
        header.set_title_widget(self.title_widget)

        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_menu_model(self._main_menu())
        menu_button.set_tooltip_text("Main menu")
        header.pack_end(menu_button)

        self.compile_button = Gtk.Button(label="Compile", css_classes=["suggested-action"])
        self.compile_button.set_action_name("win.compile")
        self.compile_button.set_tooltip_text("Compile now (Ctrl+R)")
        header.pack_end(self.compile_button)

        packages_button = Gtk.MenuButton(label="Packages")
        packages_button.set_popover(self._packages_popover())
        header.pack_end(packages_button)

        insert_button = Gtk.MenuButton(label="Insert")
        insert_button.set_popover(self._insert_popover())
        header.pack_end(insert_button)

        return header

    def _build_status_strip(self) -> Gtk.Widget:
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        strip.add_css_class("status-strip")

        self.status_label = Gtk.Label(label="Ready", xalign=0, hexpand=True)
        self.status_label.set_ellipsize(3)  # Pango.EllipsizeMode.END
        strip.append(self.status_label)

        self.problems_button = Gtk.ToggleButton(
            label="Problems", css_classes=["flat", "small-button"]
        )
        self.problems_button.set_tooltip_text("Errors and warnings (F8)")
        self.problems_button.connect("toggled", self._on_problems_toggled)
        strip.append(self.problems_button)

        strip.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.cursor_label = Gtk.Label(label="Ln 1, Col 1", css_classes=["dim-label"])
        strip.append(self.cursor_label)

        self.page_label = Gtk.Label(label="—", css_classes=["dim-label"])
        strip.append(self.page_label)

        strip.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        zoom_out = widgets.small_button("−", tooltip="Zoom out (Ctrl+−)")
        zoom_out.set_action_name("win.zoom-out")
        strip.append(zoom_out)

        self.zoom_button = Gtk.MenuButton(
            label=self.preview.zoom_label(), css_classes=["flat", "small-button"]
        )
        self.zoom_button.set_menu_model(self._zoom_menu())
        strip.append(self.zoom_button)

        zoom_in = widgets.small_button("+", tooltip="Zoom in (Ctrl++)")
        zoom_in.set_action_name("win.zoom-in")
        strip.append(zoom_in)

        return strip

    # -- popovers ---------------------------------------------------------

    def _templates_popover(self) -> Gtk.Popover:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6,
                      margin_bottom=6, margin_start=6, margin_end=6)
        popover = Gtk.Popover(child=box)
        for name, blurb, body in templates.TEMPLATES:
            button = Gtk.Button(css_classes=["flat"])
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            inner.append(Gtk.Label(label=name, xalign=0))
            inner.append(Gtk.Label(label=blurb, xalign=0, css_classes=["dim-label", "caption"]))
            button.set_child(inner)
            button.connect("clicked", self._on_template, body, popover)
            box.append(button)
        return popover

    def _insert_popover(self) -> Gtk.Popover:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6,
                        margin_bottom=6, margin_start=6, margin_end=6)
        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, propagate_natural_width=True)
        scroller.set_max_content_height(560)
        scroller.set_child(outer)
        popover = Gtk.Popover(child=scroller)

        for group, entries in templates.SNIPPETS:
            label = Gtk.Label(label=group, xalign=0, css_classes=["heading"],
                              margin_top=6, margin_start=4)
            outer.append(label)
            for name, snippet in entries:
                button = Gtk.Button(label=name, css_classes=["flat"])
                button.get_child().set_xalign(0)
                button.connect("clicked", self._on_snippet, snippet, popover)
                outer.append(button)
        return popover

    def _packages_popover(self) -> Gtk.Popover:
        self._packages_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2,
            margin_top=6, margin_bottom=6, margin_start=6, margin_end=6,
        )
        scroller = Gtk.ScrolledWindow(propagate_natural_width=True)
        scroller.set_max_content_height(600)
        scroller.set_min_content_width(400)
        scroller.set_child(self._packages_box)
        self._packages_popover_widget = Gtk.Popover(child=scroller)
        self._packages_popover_widget.connect("show", lambda *_: self._refresh_packages())
        return self._packages_popover_widget

    def _refresh_packages(self):
        box = self._packages_box
        while (child := box.get_first_child()) is not None:
            box.remove(child)

        status = self._package_status or {}
        loaded = packages.loaded_by(self.editor.get_text())
        missing_sty = []

        for group in packages.GROUPS:
            entries = [p for p in packages.CATALOGUE if p.group == group]
            if not entries:
                continue
            heading = Gtk.Label(label=group, xalign=0, css_classes=["heading"],
                                margin_top=8, margin_start=4)
            box.append(heading)
            for package in entries:
                installed = status.get(package.name, True)
                if not installed:
                    missing_sty.append(package.sty)
                box.append(self._package_row(package, installed, package.name in loaded))

        if missing_sty:
            hint = packages.install_hint(missing_sty)
            if hint:
                box.append(Gtk.Separator(margin_top=8, margin_bottom=6))
                note = Gtk.Label(
                    label="Not everything above is installed. To get the rest:",
                    xalign=0, css_classes=["dim-label", "caption"], wrap=True, margin_start=4,
                )
                box.append(note)
                command = Gtk.Label(label=hint, xalign=0, selectable=True, wrap=True,
                                    css_classes=["monospace", "caption"], margin_start=4)
                box.append(command)
                copy = Gtk.Button(label="Copy install command", css_classes=["flat"],
                                  margin_top=4)
                copy.connect("clicked",
                             lambda _b: widgets.copyable(self, hint, "Install command copied"))
                box.append(copy)

    def _package_row(self, package, installed: bool, already_loaded: bool) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        button = Gtk.Button(css_classes=["flat"], hexpand=True)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name = Gtk.Label(label=package.name, xalign=0, css_classes=["monospace"])
        title.append(name)
        if already_loaded:
            title.append(widgets.pill("loaded", "badge-ok"))
        if not installed:
            title.append(widgets.pill("not installed", "badge-missing"))
        inner.append(title)
        blurb = Gtk.Label(label=package.blurb, xalign=0, wrap=True,
                          css_classes=["dim-label", "caption"])
        inner.append(blurb)
        button.set_child(inner)
        if not installed:
            button.add_css_class("package-missing")
        button.set_tooltip_text(package.directive)
        button.connect("clicked", self._on_package_clicked, package, installed)
        row.append(button)
        return row

    def _zoom_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        fit = Gio.Menu()
        fit.append("Fit width", "win.zoom-mode('fit-width')")
        fit.append("Fit page", "win.zoom-mode('fit-page')")
        menu.append_section(None, fit)
        steps = Gio.Menu()
        for value in ("0.5", "0.75", "1.0", "1.5", "2.0"):
            steps.append(f"{round(float(value) * 100)}%", f"win.zoom-mode('{value}')")
        menu.append_section(None, steps)
        return menu

    def _main_menu(self) -> Gio.Menu:
        menu = Gio.Menu()

        tabs = Gio.Menu()
        tabs.append("New tab", "win.new-tab")
        tabs.append("Close tab", "win.close-tab")
        tabs.append("Next tab", "win.next-tab")
        tabs.append("Previous tab", "win.previous-tab")
        menu.append_section(None, tabs)

        files = Gio.Menu()
        files.append("Save as…", "win.save-as")
        files.append("Export PDF…", "win.export")
        files.append("Reload from disk", "win.reload")
        menu.append_section(None, files)

        build = Gio.Menu()
        build.append("Compile now", "win.compile")
        build.append("Compile automatically", "win.auto-compile")
        build.append("Follow the cursor in the preview", "win.sync-preview")
        engines = Gio.Menu()
        for engine in compiler.available_engines():
            engines.append(engine, f"win.engine('{engine}')")
        build.append_submenu("Engine", engines)
        menu.append_section("Building", build)

        view = Gio.Menu()
        pages = Gio.Menu()
        for name in THEMES:
            pages.append(THEME_LABEL[name], f"win.page-theme('{name}')")
        view.append_submenu("Page colours", pages)
        menu.append_section("Preview", view)

        tools = Gio.Menu()
        tools.append("Allow shell escape", "win.shell-escape")
        tools.append("Show the raw log", "win.raw-log")
        tools.append("Delete build files", "win.clean")
        menu.append_section(None, tools)

        about = Gio.Menu()
        about.append("Preferences", "win.preferences")
        about.append("Keyboard shortcuts", "win.shortcuts")
        about.append("About LaRenderer", "win.about")
        about.append("Quit", "app.quit")
        menu.append_section(None, about)
        return menu

    # ------------------------------------------------------------ actions

    def _build_actions(self):
        def simple(name, handler):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_: handler())
            self.add_action(action)
            return action

        simple("open", self.open_dialog)
        simple("new-tab", self.new_document)
        simple("close-tab", self.close_current)
        simple("next-tab", self.next_tab)
        simple("previous-tab", self.previous_tab)
        simple("save", self.save)
        simple("save-as", self.save_as)
        simple("export", self.export_pdf)
        simple("reload", self.reload_from_disk)
        simple("compile", self.compile_now)
        simple("raw-log", self.show_raw_log)
        simple("clean", self.clean_build)
        simple("preferences", self.show_preferences)
        simple("shortcuts", self.show_shortcuts)
        simple("about", self.show_about)
        simple("zoom-in", lambda: self._zoom_step(self.preview.zoom_in))
        simple("zoom-out", lambda: self._zoom_step(self.preview.zoom_out))
        simple("sync", self.sync_to_cursor)
        simple("problems", lambda: self.problems_button.set_active(
            not self.problems_button.get_active()))
        # Lazy: the preview does not exist until _build_ui runs.
        simple("next-page", lambda: self.preview.next_page())
        simple("previous-page", lambda: self.preview.previous_page())

        def toggle(name, key):
            action = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant.new_boolean(bool(self.config[key]))
            )

            def changed(act, _param):
                new = not act.get_state().get_boolean()
                act.set_state(GLib.Variant.new_boolean(new))
                self.config[key] = new
                self.config.save()
                self._on_toggle_changed(key, new)

            action.connect("activate", changed)
            self.add_action(action)

        toggle("auto-compile", "auto_compile")
        toggle("sync-preview", "sync_preview")
        toggle("shell-escape", "shell_escape")

        engine_action = Gio.SimpleAction.new_stateful(
            "engine", GLib.VariantType.new("s"),
            GLib.Variant.new_string(self.config["engine"]),
        )

        def set_engine(action, value):
            action.set_state(value)
            self.config["engine"] = value.get_string()
            self.config.save()
            self.compile_now()

        engine_action.connect("activate", set_engine)
        self.add_action(engine_action)

        zoom_action = Gio.SimpleAction.new_stateful(
            "zoom-mode", GLib.VariantType.new("s"),
            GLib.Variant.new_string(str(self.config["zoom"])),
        )

        def set_zoom(action, value):
            action.set_state(value)
            raw = value.get_string()
            self.preview.set_zoom(raw if raw in (FIT_WIDTH, FIT_PAGE) else float(raw))
            self.config["zoom"] = raw
            self.config.save()
            self.zoom_button.set_label(self.preview.zoom_label())

        zoom_action.connect("activate", set_zoom)
        self.add_action(zoom_action)

        theme_action = Gio.SimpleAction.new_stateful(
            "page-theme", GLib.VariantType.new("s"),
            GLib.Variant.new_string(str(self.config["pdf_theme"])),
        )

        def set_page_theme(action, value):
            action.set_state(value)
            self.config["pdf_theme"] = value.get_string()
            self.config.save()
            self.preview.set_page_theme(value.get_string())

        theme_action.connect("activate", set_page_theme)
        self.add_action(theme_action)

    def _install_accelerators(self):
        app = self.get_application()
        for action, keys in {
            "win.open": ["<Control>o"],
            # Ctrl+Page Up/Down are already the preview's page keys and stay
            # that way, so the tabs take Ctrl+Tab alone rather than fighting
            # them for a binding that would then do two things.
            "win.new-tab": ["<Control>t", "<Control>n"],
            "win.close-tab": ["<Control>w"],
            "win.next-tab": ["<Control>Tab"],
            "win.previous-tab": ["<Control><Shift>Tab"],
            "win.save": ["<Control>s"],
            "win.save-as": ["<Control><Shift>s"],
            "win.export": ["<Control>e"],
            "win.compile": ["<Control>r", "F5"],
            "win.problems": ["F8"],
            "win.sync": ["F7"],
            "win.zoom-in": ["<Control>plus", "<Control>equal", "<Control>KP_Add"],
            "win.zoom-out": ["<Control>minus", "<Control>KP_Subtract"],
            "win.next-page": ["<Control>Page_Down"],
            "win.previous-page": ["<Control>Page_Up"],
        }.items():
            app.set_accels_for_action(action, keys)

    def _on_toggle_changed(self, key, value):
        if key == "auto_compile" and value:
            self._schedule_compile()
        if key == "shell_escape" and value:
            widgets.toast(
                self,
                "Shell escape is on — a document can now run commands on your machine.",
                timeout=6,
            )

    def _zoom_step(self, function):
        function()
        self.config["zoom"] = (
            self.preview.zoom if isinstance(self.preview.zoom, str)
            else str(self.preview.zoom)
        )
        self.config.save()
        self.zoom_button.set_label(self.preview.zoom_label())
        self.lookup_action("zoom-mode").set_state(
            GLib.Variant.new_string(str(self.config["zoom"]))
        )

    def zoom_from_config(self):
        raw = str(self.config["zoom"])
        if raw in (FIT_WIDTH, FIT_PAGE):
            return raw
        try:
            return float(raw)
        except ValueError:
            return FIT_WIDTH

    # ------------------------------------------------------------- startup

    def _start_up(self):
        # The tabs that were open last time, in the order they were in. A file
        # deleted since is skipped rather than opening a tab named after a
        # document that is not there any more.
        remembered = [p for p in self.config["open_files"]
                      if isinstance(p, str) and p and os.path.exists(p)]
        if not remembered:
            last = self.config["last_file"]
            if last and os.path.exists(last):
                remembered = [last]

        for path in remembered:
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue  # unreadable now: not worth a dialog at start-up
            document = self.open_document(path=path, text=text, focus=False)
            self._update_title(document)

        if not self.documents:
            document = self.open_document()
            document.editor.set_text(templates.ARTICLE.replace(templates.CURSOR, ""))
            document.edited_since_load = False

        first = self.tabs.get_nth_page(0)
        self.tabs.set_selected_page(first)
        self._on_tab_selected()
        self._update_title()
        self.compile_now()

        jobs.run(packages.catalogue_status, self._on_package_status)
        return GLib.SOURCE_REMOVE

    def _on_package_status(self, status):
        self._package_status = status

    def select_path_arg(self, path: str):
        if os.path.isfile(path):
            self.load_file(os.path.abspath(path))

    # ---------------------------------------------------------- file state

    def _update_title(self, document=None):
        document = document or self.active
        if document is None:
            return
        page = self._page_for(document)
        if page is not None:
            # The bullet is the tab's unsaved marker as well as the title's.
            page.set_title(("• " if document.modified else "") + document.name)
            page.set_tooltip(document.path or document.name)
        if document is not self.active:
            return
        self.title_widget.set_title(("•  " if document.modified else "") + document.name)
        self.title_widget.set_subtitle(
            document.folder.replace(os.path.expanduser("~"), "~"))
        self.save_button.set_sensitive(document.modified or document.path is None)

    def load_file(self, path: str):
        """Open a file in a tab of its own, and bring it to the front."""
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            widgets.error_toast(self, exc)
            return
        already = next((d for d in self.documents if d.is_at(path)), None)
        if already is not None:
            self.tabs.set_selected_page(self._page_for(already))
            return

        # An untouched, never-saved first tab is scaffolding, not a document:
        # opening a file replaces it rather than leaving an empty tab behind.
        spare = self.active
        if (spare is not None and spare.path is None and not spare.modified
                and len(self.documents) == 1):
            document = spare
            document.path = path
            document.editor.set_text(text)
        else:
            document = self.open_document(path=path, text=text)
        document.edited_since_load = False
        self._remember_open_files()
        self._update_title(document)
        self.compile_now(document)

    def _remember_open_files(self):
        """The tabs to put back next time, in the order they are in now."""
        paths = [d.path for d in self.documents if d.path]
        self.config["open_files"] = paths
        self.config["last_file"] = self.active.path if self.active and self.active.path else ""
        self.config.save()

    def open_dialog(self):
        dialog = Gtk.FileDialog(title="Open a LaTeX document")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        tex = Gtk.FileFilter(name="LaTeX documents")
        tex.add_pattern("*.tex")
        tex.add_pattern("*.ltx")
        filters.append(tex)
        everything = Gtk.FileFilter(name="All files")
        everything.add_pattern("*")
        filters.append(everything)
        dialog.set_filters(filters)
        if self.path:
            dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.path)))

        def done(dlg, result):
            try:
                file = dlg.open_finish(result)
            except GLib.Error:
                return
            if file:
                self.load_file(file.get_path())

        dialog.open(self, None, done)

    def save(self):
        if not self.path:
            self.save_as()
            return
        self._write(self.path)

    def save_as(self):
        dialog = Gtk.FileDialog(title="Save the document")
        dialog.set_initial_name(os.path.basename(self.path) if self.path else UNTITLED)
        if self.path:
            dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.path)))

        def done(dlg, result):
            try:
                file = dlg.save_finish(result)
            except GLib.Error:
                return
            if file:
                path = file.get_path()
                if not os.path.splitext(path)[1]:
                    path += ".tex"
                self.path = path
                self._write(path)
                self.config["last_file"] = path
                self.config.save()
                self.compile_now()

        dialog.save(self, None, done)

    def _write(self, path: str) -> bool:
        return self._write_document(self.active, path)

    def _write_document(self, document, path: str) -> bool:
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(document.editor.get_text())
            os.replace(tmp, path)
        except OSError as exc:
            widgets.error_toast(self, exc)
            return False
        document.editor.mark_saved()
        self._update_title(document)
        widgets.toast(self, f"Saved {os.path.basename(path)}")
        return True

    def reload_from_disk(self):
        if not self.path:
            widgets.toast(self, "This document has never been saved.")
            return

        def go():
            self.load_file(self.path)

        if self.editor.modified:
            widgets.confirm(
                self, "Reload from disk?",
                "The unsaved changes in this window will be lost.",
                "Reload", go,
            )
        else:
            go()

    def export_pdf(self):
        if not (self._result and self._result.pdf):
            widgets.toast(self, "There is no PDF yet — compile first.")
            return
        dialog = Gtk.FileDialog(title="Export the PDF")
        stem = os.path.splitext(os.path.basename(self.path))[0] if self.path else "untitled"
        dialog.set_initial_name(stem + ".pdf")
        if self.path:
            dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.path)))

        def done(dlg, result):
            try:
                file = dlg.save_finish(result)
            except GLib.Error:
                return
            if not file:
                return
            try:
                compiler.export_pdf(self._result.pdf, file.get_path())
            except OSError as exc:
                widgets.error_toast(self, exc)
                return
            name = os.path.basename(file.get_path())
            if self._result.stale_pdf:
                widgets.toast(
                    self,
                    f"Exported {name} — but the document has errors, so this is "
                    "the last version that compiled.",
                    timeout=8,
                )
            else:
                widgets.toast(self, f"Exported {name}")

        dialog.save(self, None, done)

    def clean_build(self):
        compiler.clear_build(self.path)
        widgets.toast(self, "Build files deleted. The next compile starts fresh.")

    # ------------------------------------------------------------ compiling

    def document_edited(self, document):
        document.edited_since_load = True
        if document is not self.active:
            return
        self._update_title()
        if self.config["auto_compile"]:
            self._schedule_compile()

    def document_cursor_moved(self, document):
        if document is self.active:
            self._on_cursor()

    def document_page_changed(self, document, page, total):
        document.page_text = f"Page {page} of {total}"
        if document is self.active:
            self.page_label.set_label(document.page_text)

    def _on_cursor(self):
        line, column = self.editor.cursor_position()
        self.cursor_label.set_label(f"Ln {line}, Col {column}")

    def _schedule_compile(self, document=None):
        document = document or self.active
        if document.compile_timer:
            GLib.source_remove(document.compile_timer)
        delay = max(120, int(self.config["compile_delay_ms"]))
        document.compile_timer = GLib.timeout_add(
            delay, self._compile_timer_fired, document)

    def _compile_timer_fired(self, document):
        document.compile_timer = 0
        self.compile_now(document)
        return GLib.SOURCE_REMOVE

    # Every one of these takes the document it is working on rather than
    # reading self.active. A compile takes seconds and the user is free to
    # change tabs while it runs; read the active document when the result
    # lands and it is written into whichever tab happens to be in front.
    def compile_now(self, document=None):
        document = document or self.active
        if document is None:
            return
        if document.compile_timer:
            GLib.source_remove(document.compile_timer)
            document.compile_timer = 0

        if document.compiling:
            # Kill the one in flight; its results are already out of date.
            if document.job:
                document.job.cancel()
            document.pending = True
            return

        text = document.editor.get_text()
        if not text.strip():
            document.preview.clear()
            self._set_status("Nothing to compile", "", document)
            return

        document.compiling = True
        document.job = compiler.Job()
        job = document.job
        self._set_status("Compiling…", "compiling", document)
        if document is self.active:
            self.compile_button.set_sensitive(False)

        engine = self.config["engine"]
        if engine not in compiler.available_engines():
            engine = (compiler.available_engines() or ["pdflatex"])[0]

        path = document.path
        options = dict(
            engine=engine,
            shell_escape=bool(self.config["shell_escape"]),
            timeout=int(self.config["timeout_seconds"]),
            bibliography=bool(self.config["bibliography"]),
        )

        jobs.run(
            lambda: compiler.compile_document(text, path, job=job, **options),
            lambda result: self._on_compiled(result, document),
            lambda exc: self._on_compile_failed(exc, document),
        )

    def _on_compile_failed(self, exc, document):
        document.compiling = False
        if document is self.active:
            self.compile_button.set_sensitive(True)
        self._set_status(
            str(exc).splitlines()[0] if str(exc) else "Compile failed", "bad", document)
        if document.pending:
            document.pending = False
            self.compile_now(document)

    def _on_compiled(self, result: compiler.Result, document):
        document.compiling = False
        if document is self.active:
            self.compile_button.set_sensitive(True)

        # The tab may have been closed while this was running, in which case
        # there is nowhere for the result to go.
        if self._page_for(document) is None:
            return

        if document.pending:
            document.pending = False
            self.compile_now(document)
            return
        if result.cancelled:
            return

        document.result = result

        if result.failure:
            self._set_status(result.failure, "bad", document)
            document.problems.set_messages([], "")
            self._show_problems(True, document)
            return

        if result.pdf:
            document.preview.show_result(result)
        else:
            document.preview.clear()

        hint = ""
        if result.missing_packages:
            hint = packages.install_hint([n + ".sty" for n in result.missing_packages])
        document.problems.set_messages(result.messages, hint)
        document.editor.set_problem_lines(
            [m.line for m in result.errors if m.is_current_file],
            [m.line for m in result.warnings if m.is_current_file],
        )

        errors = len(result.errors)
        warnings = len(result.warnings)
        document.problems_label = self._problems_label(errors, warnings)
        if document is self.active:
            self.problems_button.set_label(document.problems_label)

        if errors:
            noun = "error" if errors == 1 else "errors"
            extra = f", {warnings} warning{'s' if warnings != 1 else ''}" if warnings else ""
            # Say so when the pane is showing an older render rather than this
            # compile's output, so a stale page is never mistaken for current.
            stale = " · preview is the last good render" if result.stale_pdf else ""
            self._set_status(f"{errors} {noun}{extra}{stale}", "bad", document)
            self._show_problems(True, document)
        else:
            pages = f"{result.pages} page{'s' if result.pages != 1 else ''}"
            passes = f", {result.passes} passes" if result.passes > 1 else ""
            warned = f" · {warnings} warning{'s' if warnings != 1 else ''}" if warnings else ""
            self._set_status(
                f"Compiled {pages} in {result.seconds:.2f}s{passes}{warned}", "ok", document
            )

        if self.config["sync_preview"] and document.edited_since_load:
            if document is self.active:
                self.sync_to_cursor(quiet=True)

    def _problems_label(self, errors, warnings) -> str:
        if errors and warnings:
            return f"Problems  {errors} ✕  {warnings} ⚠"
        if errors:
            return f"Problems  {errors} ✕"
        if warnings:
            return f"Problems  {warnings} ⚠"
        return "Problems"

    def _set_status(self, text, css, document=None):
        """Say something in the status strip, and remember it for that tab.

        The strip is one widget shared by every document, so what it says has
        to be kept on the document too — otherwise coming back to a tab shows
        whatever the last compile in another tab happened to leave there.
        """
        document = document or self.active
        if document is not None:
            document.status_text = text
            document.status_css = css
            if document is not self.active:
                return
        self.status_label.set_label(text)
        strip = self.status_label.get_parent()
        for name in ("ok", "bad", "compiling"):
            strip.remove_css_class(name)
        if css:
            strip.add_css_class(css)

    def _show_problems(self, visible: bool, document=None):
        # A background tab finding errors should not throw the panel open over
        # the document being read in front of it.
        if document is not None and document is not self.active:
            return
        if visible and not self.problems_button.get_active():
            self.problems_button.set_active(True)
        elif not visible and self.problems_button.get_active():
            self.problems_button.set_active(False)

    def _on_problems_toggled(self, button):
        self.problems_revealer.set_reveal_child(button.get_active())
        self.config["show_problems"] = button.get_active()

    # --------------------------------------------------------------- sync

    def sync_to_cursor(self, quiet: bool = False):
        result = self._result
        if not (result and result.pdf and result.synctex):
            if not quiet:
                widgets.toast(self, "Nothing to sync to yet — compile first.")
            return
        line, column = self.editor.cursor_position()
        name = compiler.job_name(self.path) + ".tex"

        def look():
            return compiler.forward_search(result.pdf, name, line, column)

        def landed(found):
            if not found:
                if not quiet:
                    widgets.toast(self, "SyncTeX could not place that line.")
                return
            page, x, y = found
            self.preview.flash_position(page, x, y)

        jobs.run(look, landed)

    # ------------------------------------------------------------- dialogs

    def show_raw_log(self):
        log = self._result.log if self._result else ""
        if not log.strip():
            widgets.toast(self, "There is no log yet.")
            return

        view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False,
                            wrap_mode=Gtk.WrapMode.NONE, left_margin=10, right_margin=10,
                            top_margin=8, bottom_margin=8)
        view.add_css_class("log-view")
        view.get_buffer().set_text(log)
        scroller = Gtk.ScrolledWindow(child=view, hexpand=True, vexpand=True)

        dialog = Adw.Dialog(title="Compile log", content_width=920, content_height=640)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        copy = Gtk.Button(label="Copy")
        copy.connect("clicked", lambda _b: widgets.copyable(self, log, "Log copied"))
        header.pack_end(copy)
        toolbar.add_top_bar(header)
        toolbar.set_content(scroller)
        dialog.set_child(toolbar)
        dialog.present(self)

    def show_preferences(self):
        dialog = Adw.PreferencesDialog(title="Preferences")
        page = Adw.PreferencesPage()

        building = Adw.PreferencesGroup(title="Building")

        engines = compiler.available_engines() or ["pdflatex"]
        engine_row = Adw.ComboRow(title="Engine",
                                  subtitle="XeLaTeX and LuaLaTeX can use system fonts")
        engine_row.set_model(Gtk.StringList.new(engines))
        if self.config["engine"] in engines:
            engine_row.set_selected(engines.index(self.config["engine"]))

        def engine_changed(row, _param):
            name = engines[row.get_selected()]
            self.config["engine"] = name
            self.config.save()
            self.lookup_action("engine").set_state(GLib.Variant.new_string(name))
            self.compile_now()

        engine_row.connect("notify::selected", engine_changed)
        building.add(engine_row)

        auto_row = Adw.SwitchRow(title="Compile as you type",
                                 active=bool(self.config["auto_compile"]))
        auto_row.connect("notify::active", lambda r, _p: self._set_option(
            "auto_compile", r.get_active(), action="auto-compile"))
        building.add(auto_row)

        delay_row = Adw.SpinRow.new_with_range(150, 3000, 50)
        delay_row.set_title("Pause before compiling")
        delay_row.set_subtitle("Milliseconds after the last keystroke")
        delay_row.set_value(self.config["compile_delay_ms"])
        delay_row.connect("notify::value", lambda r, _p: self._set_option(
            "compile_delay_ms", int(r.get_value())))
        building.add(delay_row)

        timeout_row = Adw.SpinRow.new_with_range(5, 300, 5)
        timeout_row.set_title("Give up after")
        timeout_row.set_subtitle("Seconds; a runaway macro would otherwise hang")
        timeout_row.set_value(self.config["timeout_seconds"])
        timeout_row.connect("notify::value", lambda r, _p: self._set_option(
            "timeout_seconds", int(r.get_value())))
        building.add(timeout_row)

        bib_row = Adw.SwitchRow(title="Run BibTeX when the document cites anything",
                                active=bool(self.config["bibliography"]))
        bib_row.connect("notify::active", lambda r, _p: self._set_option(
            "bibliography", r.get_active()))
        building.add(bib_row)

        shell_row = Adw.SwitchRow(
            title="Allow shell escape",
            subtitle="Lets a document run commands on this machine. Needed by minted.",
            active=bool(self.config["shell_escape"]),
        )
        shell_row.connect("notify::active", lambda r, _p: self._set_option(
            "shell_escape", r.get_active(), action="shell-escape"))
        building.add(shell_row)
        page.add(building)

        looks = Adw.PreferencesGroup(title="Editor and preview")

        font_row = Adw.SpinRow.new_with_range(7, 24, 1)
        font_row.set_title("Editor text size")
        font_row.set_value(self.config["editor_font_size"])

        def font_changed(row, _param):
            size = int(row.get_value())
            self.config["editor_font_size"] = size
            self.config.save()
            self.editor.set_font_size(size)

        font_row.connect("notify::value", font_changed)
        looks.add(font_row)

        theme_row = Adw.ComboRow(
            title="Page colours",
            subtitle="Dark inverts the rendered pages, keeping the colours' hues",
        )
        theme_row.set_model(Gtk.StringList.new([THEME_LABEL[t] for t in THEMES]))
        theme_row.set_selected(THEMES.index(self.preview.page_theme))

        def theme_changed(row, _param):
            name = THEMES[row.get_selected()]
            self.config["pdf_theme"] = name
            self.config.save()
            self.lookup_action("page-theme").set_state(GLib.Variant.new_string(name))
            self.preview.set_page_theme(name)

        theme_row.connect("notify::selected", theme_changed)
        looks.add(theme_row)

        sync_row = Adw.SwitchRow(
            title="Follow the cursor in the preview",
            subtitle="After each compile, jump to where the caret's line landed",
            active=bool(self.config["sync_preview"]),
        )
        sync_row.connect("notify::active", lambda r, _p: self._set_option(
            "sync_preview", r.get_active(), action="sync-preview"))
        looks.add(sync_row)
        page.add(looks)

        dialog.add(page)
        dialog.present(self)

    def _set_option(self, key, value, action=None):
        self.config[key] = value
        self.config.save()
        if action:
            self.lookup_action(action).set_state(GLib.Variant.new_boolean(bool(value)))

    def show_shortcuts(self):
        rows = [
            ("Ctrl+O", "Open a document"),
            ("Ctrl+T  /  Ctrl+N", "New tab"),
            ("Ctrl+W", "Close the tab"),
            ("Ctrl+Tab  /  Ctrl+Shift+Tab", "Next or previous tab"),
            ("Ctrl+S", "Save"),
            ("Ctrl+Shift+S", "Save as"),
            ("Ctrl+E", "Export the PDF"),
            ("Ctrl+R  /  F5", "Compile now"),
            ("F7", "Jump the preview to the cursor"),
            ("F8", "Show or hide the problems list"),
            ("Ctrl+ +  /  Ctrl+ −", "Zoom the preview"),
            ("Ctrl+Page Up / Down", "Previous or next page"),
            ("Ctrl+scroll over the preview", "Zoom the preview"),
            ("Ctrl+scroll over the text", "Resize the editor text"),
            ("Ctrl+/", "Comment or uncomment the selection"),
            ("Tab  /  Shift+Tab", "Indent or unindent the selection"),
            ("Enter after \\begin{…}", "Close the environment automatically"),
            ("Ctrl+Q", "Quit"),
        ]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                      margin_top=6, margin_bottom=12, margin_start=12, margin_end=12)
        group = Adw.PreferencesGroup()
        for keys, what in rows:
            row = Adw.ActionRow(title=what)
            row.add_suffix(Gtk.Label(label=keys, css_classes=["monospace", "dim-label"]))
            group.add(row)
        box.append(group)
        scroller = Gtk.ScrolledWindow(child=box, hexpand=True, vexpand=True)

        dialog = Adw.Dialog(title="Keyboard shortcuts", content_width=560, content_height=620)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(scroller)
        dialog.set_child(toolbar)
        dialog.present(self)

    def show_about(self):
        engines = ", ".join(compiler.available_engines()) or "none found"
        about = Adw.AboutDialog(
            application_name="LaRenderer",
            application_icon="text-x-generic",
            developer_name="Robin",
            version="1.0",
            comments=(
                "A LaTeX editor with a live preview.\n\n"
                f"Engines on this machine: {engines}.\n"
                "Pages are rendered by poppler; positions come from SyncTeX."
            ),
            copyright="© 2026 Robin",
        )
        about.present(self)

    # -------------------------------------------------------- menu actions

    def _on_template(self, _button, body, popover):
        popover.popdown()

        def apply():
            self.path = None
            self.config["last_file"] = ""
            self.config.save()
            self.editor.set_text(body.replace(templates.CURSOR, ""))
            self._edited_since_load = False
            cursor = body.find(templates.CURSOR)
            if cursor >= 0:
                self.editor.goto_line(body[:cursor].count("\n") + 1)
            self._update_title()
            self.compile_now()

        if self.editor.modified:
            widgets.confirm(
                self, "Start a new document?",
                "The unsaved changes in this window will be lost.",
                "Discard and start", apply,
            )
        else:
            apply()

    def _on_snippet(self, _button, snippet, popover):
        popover.popdown()
        self.editor.insert_snippet(snippet)

    def _on_package_clicked(self, button, package, installed):
        self._packages_popover_widget.popdown()
        self.editor.insert_package(package.directive)
        if not installed:
            _, how = packages.describe_missing(package.name)
            widgets.toast(self, f"{package.name} is not installed — {how}", timeout=8)

    # -------------------------------------------------------------- closing

    def _on_close(self, *_):
        self._remember_geometry()
        self._remember_open_files()

        # Any tab with unsaved work, not just the one in front — the others
        # are out of sight and are exactly the ones worth asking about.
        unsaved = [d for d in self.documents if d.modified]
        if not unsaved:
            return False
        if len(unsaved) > 1:
            names = ", ".join(d.name for d in unsaved)
            dialog = Adw.AlertDialog(
                heading="Save before closing?",
                body=f"{len(unsaved)} documents have unwritten changes: {names}.",
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("discard", "Discard all")
            dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")

            def answered_many(dlg, result):
                if dlg.choose_finish(result) == "discard":
                    for document in unsaved:
                        document.editor.mark_saved()
                    self.close()

            dialog.choose(self, None, answered_many)
            return True

        # Exactly one, and it may not be the tab in front.
        document = unsaved[0]
        if document is not self.active:
            self.tabs.set_selected_page(self._page_for(document))
        dialog = Adw.AlertDialog(
            heading="Save before closing?",
            body=f"{document.name} has changes that have not been written to disk.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("discard", "Discard")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def answered(dlg, result):
            answer = dlg.choose_finish(result)
            if answer == "discard":
                document.editor.mark_saved()
                self.close()
            elif answer == "save":
                if document.path:
                    if self._write_document(document, document.path):
                        self.close()
                else:
                    self.save_as()

        dialog.choose(self, None, answered)
        return True

    def _remember_geometry(self):
        self.config["window_maximized"] = self.is_maximized()
        if not self.is_maximized():
            self.config["window_width"] = self.get_width()
            self.config["window_height"] = self.get_height()
        # Every tab has a paned and an editor of its own; the one in front is
        # the one whose layout the user just chose.
        if self.active is not None:
            self.config["split_position"] = self.paned.get_position()
            self.config["editor_font_size"] = self.editor.get_font_size()
        self.config.save()
