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
from .editor import Editor  # noqa: E402
from .preview import FIT_PAGE, FIT_WIDTH, THEME_LABEL, THEMES, Preview  # noqa: E402

UNTITLED = "Untitled.tex"


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="LaRenderer")
        self.config = Config()
        self.path: str | None = None
        self._job: compiler.Job | None = None
        self._compile_timer = 0
        self._result: compiler.Result | None = None
        self._compiling = False
        self._pending = False
        self._package_status: dict[str, bool] = {}
        # Following the cursor is right while you type and wrong on open —
        # nobody wants a freshly opened document scrolled to its last line.
        self._edited_since_load = False

        self.set_default_size(self.config["window_width"], self.config["window_height"])
        if self.config["window_maximized"]:
            self.maximize()

        self._build_actions()
        self._build_ui()
        self._install_accelerators()
        self.problems_button.set_active(bool(self.config["show_problems"]))

        self.connect("close-request", self._on_close)

        GLib.idle_add(self._start_up)

    # ----------------------------------------------------------------- UI

    def _build_ui(self):
        self.editor = Editor(on_changed=self._on_edited, on_cursor=self._on_cursor)
        self.editor.set_font_size(self.config["editor_font_size"])
        self.preview = Preview(on_page_changed=self._on_page_changed)
        self.preview.zoom = self._zoom_from_config()
        self.preview.set_page_theme(str(self.config["pdf_theme"]))

        # -- left: editor over the problems panel --------------------------
        self.problems = widgets.ProblemsList(on_activate=self.editor.goto_line)
        self.problems_revealer = Gtk.Revealer(
            child=self.problems,
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            reveal_child=False,
        )
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.append(self.editor)
        left.append(Gtk.Separator())
        left.append(self.problems_revealer)

        self.paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL,
            position=self.config["split_position"],
            resize_start_child=True, resize_end_child=True,
            shrink_start_child=False, shrink_end_child=False,
        )
        self.paned.set_start_child(left)
        self.paned.set_end_child(self.preview)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.paned)
        content.append(self._build_status_strip())

        view = Adw.ToolbarView()
        view.add_top_bar(self._build_header())
        view.set_content(content)

        self.toasts = Adw.ToastOverlay(child=view)
        self.set_content(self.toasts)

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

    def _zoom_from_config(self):
        raw = str(self.config["zoom"])
        if raw in (FIT_WIDTH, FIT_PAGE):
            return raw
        try:
            return float(raw)
        except ValueError:
            return FIT_WIDTH

    # ------------------------------------------------------------- startup

    def _start_up(self):
        last = self.config["last_file"]
        if last and os.path.exists(last):
            self.load_file(last)
        else:
            self.editor.set_text(templates.ARTICLE.replace(templates.CURSOR, ""))
            self._edited_since_load = False
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

    def _update_title(self):
        name = os.path.basename(self.path) if self.path else UNTITLED
        modified = "•  " if self.editor.modified else ""
        self.title_widget.set_title(modified + name)
        folder = os.path.dirname(self.path) if self.path else ""
        self.title_widget.set_subtitle(folder.replace(os.path.expanduser("~"), "~"))
        self.save_button.set_sensitive(self.editor.modified or self.path is None)

    def load_file(self, path: str):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            widgets.error_toast(self, exc)
            return
        self.path = path
        self.editor.set_text(text)
        self._edited_since_load = False
        self.config["last_file"] = path
        self.config.save()
        self._update_title()
        self.compile_now()

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
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(self.editor.get_text())
            os.replace(tmp, path)
        except OSError as exc:
            widgets.error_toast(self, exc)
            return False
        self.editor.mark_saved()
        self._update_title()
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

    def _on_edited(self):
        self._edited_since_load = True
        self._update_title()
        if self.config["auto_compile"]:
            self._schedule_compile()

    def _on_cursor(self):
        line, column = self.editor.cursor_position()
        self.cursor_label.set_label(f"Ln {line}, Col {column}")

    def _on_page_changed(self, page, total):
        self.page_label.set_label(f"Page {page} of {total}")

    def _schedule_compile(self):
        if self._compile_timer:
            GLib.source_remove(self._compile_timer)
        delay = max(120, int(self.config["compile_delay_ms"]))
        self._compile_timer = GLib.timeout_add(delay, self._compile_timer_fired)

    def _compile_timer_fired(self):
        self._compile_timer = 0
        self.compile_now()
        return GLib.SOURCE_REMOVE

    def compile_now(self):
        if self._compile_timer:
            GLib.source_remove(self._compile_timer)
            self._compile_timer = 0

        if self._compiling:
            # Kill the one in flight; its results are already out of date.
            if self._job:
                self._job.cancel()
            self._pending = True
            return

        text = self.editor.get_text()
        if not text.strip():
            self.preview.clear()
            self._set_status("Nothing to compile", "")
            return

        self._compiling = True
        self._job = compiler.Job()
        job = self._job
        self._set_status("Compiling…", "compiling")
        self.compile_button.set_sensitive(False)

        engine = self.config["engine"]
        if engine not in compiler.available_engines():
            engine = (compiler.available_engines() or ["pdflatex"])[0]

        path = self.path
        options = dict(
            engine=engine,
            shell_escape=bool(self.config["shell_escape"]),
            timeout=int(self.config["timeout_seconds"]),
            bibliography=bool(self.config["bibliography"]),
        )

        jobs.run(
            lambda: compiler.compile_document(text, path, job=job, **options),
            self._on_compiled,
            self._on_compile_failed,
        )

    def _on_compile_failed(self, exc):
        self._compiling = False
        self.compile_button.set_sensitive(True)
        self._set_status(str(exc).splitlines()[0] if str(exc) else "Compile failed", "bad")
        if self._pending:
            self._pending = False
            self.compile_now()

    def _on_compiled(self, result: compiler.Result):
        self._compiling = False
        self.compile_button.set_sensitive(True)

        if self._pending:
            self._pending = False
            self.compile_now()
            return
        if result.cancelled:
            return

        self._result = result

        if result.failure:
            self._set_status(result.failure, "bad")
            self.problems.set_messages([], "")
            self._show_problems(True)
            return

        if result.pdf:
            self.preview.show_result(result)
        else:
            self.preview.clear()

        hint = ""
        if result.missing_packages:
            hint = packages.install_hint([n + ".sty" for n in result.missing_packages])
        self.problems.set_messages(result.messages, hint)
        self.editor.set_problem_lines(
            [m.line for m in result.errors if m.is_current_file],
            [m.line for m in result.warnings if m.is_current_file],
        )

        errors = len(result.errors)
        warnings = len(result.warnings)
        self.problems_button.set_label(self._problems_label(errors, warnings))

        if errors:
            noun = "error" if errors == 1 else "errors"
            extra = f", {warnings} warning{'s' if warnings != 1 else ''}" if warnings else ""
            # Say so when the pane is showing an older render rather than this
            # compile's output, so a stale page is never mistaken for current.
            stale = " · preview is the last good render" if result.stale_pdf else ""
            self._set_status(f"{errors} {noun}{extra}{stale}", "bad")
            self._show_problems(True)
        else:
            pages = f"{result.pages} page{'s' if result.pages != 1 else ''}"
            passes = f", {result.passes} passes" if result.passes > 1 else ""
            warned = f" · {warnings} warning{'s' if warnings != 1 else ''}" if warnings else ""
            self._set_status(
                f"Compiled {pages} in {result.seconds:.2f}s{passes}{warned}", "ok"
            )

        if self.config["sync_preview"] and self._edited_since_load:
            self.sync_to_cursor(quiet=True)

    def _problems_label(self, errors, warnings) -> str:
        if errors and warnings:
            return f"Problems  {errors} ✕  {warnings} ⚠"
        if errors:
            return f"Problems  {errors} ✕"
        if warnings:
            return f"Problems  {warnings} ⚠"
        return "Problems"

    def _set_status(self, text, css):
        self.status_label.set_label(text)
        strip = self.status_label.get_parent()
        for name in ("ok", "bad", "compiling"):
            strip.remove_css_class(name)
        if css:
            strip.add_css_class(css)

    def _show_problems(self, visible: bool):
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
        if not self.editor.modified:
            return False

        name = os.path.basename(self.path) if self.path else UNTITLED
        dialog = Adw.AlertDialog(
            heading="Save before closing?",
            body=f"{name} has changes that have not been written to disk.",
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
                self.editor.mark_saved()
                self.close()
            elif answer == "save":
                if self.path:
                    if self._write(self.path):
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
        self.config["split_position"] = self.paned.get_position()
        self.config["editor_font_size"] = self.editor.get_font_size()
        self.config.save()
