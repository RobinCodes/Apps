"""The window: the folder on the left, then the editor, then the rendered pages.

Owns the compile cycle. Every keystroke restarts a short timer; when it
expires the buffer goes to the engine on a worker thread, and whatever comes
back updates the preview, the problems list and the status strip. A compile
still running when the next one is due is killed rather than waited for.

Also where the three things around the editor meet it: the open folder
(filetree.py, and project.py for which file in it is the document), git
(gitlink.py, which asks Git Manager), and Overleaf, which is git with a
particular remote and a token.
"""

from __future__ import annotations

import os
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import (compiler, filetree, gitlink, jobs, overleafweb, packages,  # noqa: E402
               project, templates, widgets)
from . import __version__  # noqa: E402
from .config import Config  # noqa: E402
from .document import UNTITLED, Document  # noqa: E402
from .preview import (FIT_PAGE, FIT_WIDTH, THEME_LABEL, THEMES, ZOOM_MAX,  # noqa: E402
                      ZOOM_MIN)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="LaRenderer")
        self.config = Config()
        self._package_status: dict[str, bool] = {}
        # Set once the tab view exists; until then there is no active document
        # and the properties below have to answer for one that isn't there.
        self.active: Document | None = None
        self._by_page: dict = {}

        # The folder in the side bar, and the main document found in it.
        self.folder: str | None = None
        self._found_main: dict[str, str | None] = {}

        # Git goes through Git Manager, which is only started the first time
        # something here needs it. See gitlink.py.
        self.git = gitlink.Link()
        self.git.on_ready = self.refresh_git
        self._git_status: dict | None = None
        self._git_busy: str | None = None
        self._git_refresh_timer = 0
        self._overleaf_timer = 0
        self._closing = False
        self._warned_busy = False
        # Kept for this session only. When it works, git hands it to the
        # credential helper the user already has, which keeps it for good.
        self._overleaf_token = ""

        # Tabs showing Overleaf's own editor, by page (overleafweb.py). While
        # one is in front, the document chrome steps aside for it.
        self._web_tabs: dict = {}
        self._web_front = None
        self._document_chrome: list = []   # filled as the header and strip are built
        self._last_live_pull = 0.0

        self.set_default_size(self.config["window_width"], self.config["window_height"])
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
        self.problems_button.set_active(bool(self.config["show_problems"]))

        self.connect("close-request", self._on_close)
        # Files change behind our back — a terminal, another editor — and the
        # git letters should say so when the window comes back.
        self.connect("notify::is-active", self._on_active_changed)

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
        self.active.set_path(value)

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

        # Before the first document, which tells it which file is in front.
        self.tree = filetree.FileTree(on_open=self.open_path, on_request=self._on_tree_request)
        self.tree.set_show_hidden(bool(self.config["show_hidden_files"]))

        # One document before the chrome, because the header and the status
        # strip are built from it — the zoom label asks the preview what it
        # currently says, and there has to be a preview to ask. _start_up
        # either fills this tab or replaces it with the remembered ones.
        self.open_document()

        documents = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        documents.append(self.tab_bar)
        documents.append(self.tabs)

        self.side_paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL, vexpand=True,
            position=int(self.config["sidebar_width"]),
            resize_start_child=False, resize_end_child=True,
            shrink_start_child=False, shrink_end_child=False,
        )
        self.side_paned.set_start_child(self.tree)
        self.side_paned.set_end_child(documents)
        self.tree.set_visible(bool(self.config["sidebar_visible"]))

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.side_paned)
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
        web = self._web_tabs.get(page) if page else None
        if hasattr(self, "status_label"):
            left = self._web_front
            self._set_web_mode(web)
            if left is not None and web is None:
                self._after_live_editing(left, throttle=True)
        if web is not None:
            return
        document = self._by_page.get(page) if page else None
        if document is None or document is self.active:
            return
        self.active = document
        # The first document is made while the chrome is still being built,
        # and there is nothing to point at it yet.
        if hasattr(self, "status_label"):
            self._adopt_active()
            # A tab restored at start-up, or opened behind the one in front,
            # has not been compiled yet; showing it is when it is wanted.
            if document.result is None and not document.compiling:
                self.compile_now(document)

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
        self.zoom_button.set_label(document.preview.zoom_label())
        self.tree.set_current(document.path)
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
        web = self._web_tabs.pop(page, None)
        if web is not None:
            # Nothing to save: Overleaf saved it as it was typed.
            web.close()
            if self._web_front is web:
                self._web_front = None
            view.close_page_finish(page, True)
            self._after_live_editing(web)
            return True
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

        # A click opens a file, as it always did; the arrow has the rest.
        self._open_menu = Gio.Menu()
        opening = Gio.Menu()
        opening.append("Open file…", "win.open")
        opening.append("Open folder…", "win.open-folder")
        opening.append("Open an Overleaf project…", "win.overleaf-open")
        opening.append("Overleaf's editor, live, in a tab", "win.overleaf-web")
        self._open_menu.append_section(None, opening)
        self._recent_menu = Gio.Menu()
        self._open_menu.append_section("Recent folders", self._recent_menu)
        open_button = Adw.SplitButton(label="Open", menu_model=self._open_menu)
        open_button.set_action_name("win.open")
        open_button.set_tooltip_text("Open a document (Ctrl+O)")
        open_button.set_dropdown_tooltip("Open a folder, or a project on Overleaf")
        header.pack_start(open_button)
        self._refresh_recent_folders()

        self.save_button = Gtk.Button(label="Save")
        self.save_button.set_action_name("win.save")
        header.pack_start(self.save_button)

        new_button = Gtk.MenuButton(label="New")
        new_button.set_popover(self._templates_popover())
        header.pack_start(new_button)
        self._document_chrome += [self.save_button, new_button]

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

        self._document_chrome += [self.compile_button, packages_button, insert_button]
        return header

    def _build_status_strip(self) -> Gtk.Widget:
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        strip.add_css_class("status-strip")

        # The folder and git live down here rather than in the header, which
        # is what sets the window's narrowest width: on a 1366-pixel screen,
        # two more header buttons are the difference between fitting niri's
        # two-thirds column and not.
        files_button = Gtk.ToggleButton(label="Files", css_classes=["flat", "small-button"])
        files_button.set_action_name("win.sidebar")
        files_button.set_tooltip_text("Show or hide the folder (F9)")
        strip.append(files_button)

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

        # "Git" until git has been asked about; then the branch and what
        # changed, the way an IDE's status bar shows it.
        self.git_button = Gtk.MenuButton(label="Git", css_classes=["flat", "small-button"],
                                         direction=Gtk.ArrowType.UP)
        self.git_button.set_popover(self._git_popover())
        self.git_button.set_tooltip_text("Commit, pull, push and sync, through Git Manager")
        strip.append(self.git_button)

        self.cursor_label = Gtk.Label(label="Ln 1, Col 1", css_classes=["dim-label"])
        strip.append(self.cursor_label)

        self.page_label = Gtk.Label(label="—", css_classes=["dim-label"])
        strip.append(self.page_label)

        strip.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        zoom_out = widgets.small_button("−", tooltip="Zoom out (Ctrl+−)")
        zoom_out.set_action_name("win.zoom-out")
        strip.append(zoom_out)

        self.zoom_button = Gtk.MenuButton(
            label=self.preview.zoom_label(), css_classes=["flat", "small-button"],
            direction=Gtk.ArrowType.UP,
        )
        self.zoom_button.set_tooltip_text("Zoom: fit, a preset, or type any size")
        self.zoom_button.set_popover(self._zoom_popover())
        strip.append(self.zoom_button)

        zoom_in = widgets.small_button("+", tooltip="Zoom in (Ctrl++)")
        zoom_in.set_action_name("win.zoom-in")
        strip.append(zoom_in)

        self._document_chrome += [self.problems_button, self.cursor_label, self.page_label,
                                  zoom_out, self.zoom_button, zoom_in]
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

    def _zoom_popover(self) -> Gtk.Popover:
        """Any size at all in the box at the top; the usual ones below it."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8,
                      margin_bottom=8, margin_start=8, margin_end=8)
        popover = Gtk.Popover(child=box)

        exact = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        exact.append(Gtk.Label(label="Zoom", xalign=0, hexpand=True, css_classes=["heading"]))
        spin = Gtk.SpinButton.new_with_range(round(ZOOM_MIN * 100), round(ZOOM_MAX * 100), 5)
        spin.set_digits(0)
        spin.set_numeric(True)
        spin.set_width_chars(4)
        spin.set_tooltip_text(f"Any size from {round(ZOOM_MIN * 100)}% to "
                              f"{round(ZOOM_MAX * 100)}%")
        exact.append(spin)
        exact.append(Gtk.Label(label="%"))
        box.append(exact)
        box.append(Gtk.Label(label="Type a size and press Enter. Ctrl+0 is 100%.",
                             xalign=0, css_classes=["dim-label", "caption"]))
        box.append(Gtk.Separator(margin_top=2, margin_bottom=2))

        def choose(raw):
            popover.popdown()
            self._set_zoom_mode(raw)

        fits = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=True)
        for label, mode in (("Fit width", FIT_WIDTH), ("Fit page", FIT_PAGE)):
            button = Gtk.Button(label=label, css_classes=["flat"])
            button.connect("clicked", lambda _b, m=mode: choose(m))
            fits.append(button)
        box.append(fits)

        presets = Gtk.Grid(column_homogeneous=True, column_spacing=4, row_spacing=4)
        for index, value in enumerate((0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0)):
            button = Gtk.Button(label=f"{round(value * 100)}%", css_classes=["flat"])
            button.connect("clicked", lambda _b, v=value: choose(str(v)))
            presets.attach(button, index % 4, index // 4, 1, 1)
        box.append(presets)

        guard = {"on": False}

        def shown(_popover):
            # Show the size the pages are at, fit modes included, selected so
            # typing replaces it.
            guard["on"] = True
            spin.set_value(round(self.preview.effective_scale() * 100))
            guard["on"] = False
            spin.grab_focus()
            spin.select_region(0, -1)

        def changed(button):
            if not guard["on"]:
                self._set_zoom_mode(str(round(button.get_value() / 100, 4)))

        def entered(button):
            # Enter applies even an unchanged number: 118 typed while "Fit
            # width" happens to be at 118% means "stay at 118%", fixed.
            button.update()
            changed(button)
            popover.popdown()

        popover.connect("show", shown)
        spin.connect("value-changed", changed)
        spin.connect("activate", entered)
        self._zoom_spin = spin
        return popover

    def _set_zoom_mode(self, raw: str):
        self.activate_action("win.zoom-mode", GLib.Variant.new_string(raw))

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

        folder = Gio.Menu()
        folder.append("Open folder…", "win.open-folder")
        folder.append("Show the folder", "win.sidebar")
        folder.append("Close folder", "win.close-folder")
        menu.append_section("Folder", folder)

        overleaf = Gio.Menu()
        overleaf.append("Open an Overleaf project…", "win.overleaf-open")
        overleaf.append("Overleaf's editor, live, in a tab", "win.overleaf-web")
        overleaf.append("Sync with Overleaf", "win.git-sync")
        overleaf.append("Sync after every save", "win.overleaf-sync-on-save")
        menu.append_section("Overleaf", overleaf)

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
        about.append("New Window", "app.new-window")
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
        simple("open-folder", self.open_folder_dialog)
        simple("close-folder", self.close_folder)
        simple("overleaf-open", self.overleaf_open_dialog)
        simple("overleaf-web", lambda: self.open_overleaf_web())
        simple("git-show", self.git_show)
        simple("git-commit", self.git_commit)
        simple("git-pull", self.git_pull)
        simple("git-push", self.git_push)
        simple("git-sync", self.git_sync)
        simple("git-init", self.git_init)
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
        simple("zoom-reset", lambda: self._set_zoom_mode("1.0"))
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
        toggle("sidebar", "sidebar_visible")
        toggle("overleaf-sync-on-save", "overleaf_sync_on_save")

        recent = Gio.SimpleAction.new("open-recent-folder", GLib.VariantType.new("s"))
        recent.connect("activate", lambda _a, value: self.open_folder(value.get_string()))
        self.add_action(recent)

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
            raw = value.get_string()
            try:
                self.preview.set_zoom(raw if raw in (FIT_WIDTH, FIT_PAGE) else float(raw))
            except ValueError:
                return
            self._zoom_settled()

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
            "win.open-folder": ["<Control><Shift>o"],
            "win.sidebar": ["F9"],
            # Git, through Git Manager. Ctrl+Alt because every Ctrl+letter an
            # editor could want is taken, and niri keeps to Super.
            "win.git-show": ["<Control><Alt>g"],
            "win.git-commit": ["<Control><Alt>c"],
            "win.git-pull": ["<Control><Alt>u"],
            "win.git-push": ["<Control><Alt>p"],
            "win.git-sync": ["<Control><Alt>s"],
            "win.overleaf-web": ["<Control><Alt>o"],
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
            "win.zoom-reset": ["<Control>0", "<Control>KP_0"],
            "win.next-page": ["<Control>Page_Down"],
            "win.previous-page": ["<Control>Page_Up"],
        }.items():
            app.set_accels_for_action(action, keys)

    def _on_toggle_changed(self, key, value):
        if key == "sidebar_visible":
            self.tree.set_visible(value)
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
        self._zoom_settled()

    def _zoom_settled(self):
        """The front preview's zoom changed: its label, the action, the setting.

        One place for all of it, whichever way the zoom came — the menu, the
        box, a key, the wheel, or a pinch the preview handled by itself.
        """
        zoom = self.preview.zoom
        raw = zoom if isinstance(zoom, str) else str(zoom)
        self.config["zoom"] = raw
        self.config.save()
        self.zoom_button.set_label(self.preview.zoom_label())
        self.lookup_action("zoom-mode").set_state(GLib.Variant.new_string(raw))

    def document_zoom_changed(self, document):
        if document is self.active:
            self._zoom_settled()

    def zoom_from_config(self):
        raw = str(self.config["zoom"])
        if raw in (FIT_WIDTH, FIT_PAGE):
            return raw
        try:
            return max(ZOOM_MIN, min(ZOOM_MAX, float(raw)))
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

        # The blank tab _build_ui made so the chrome had a document to be
        # built from. It goes once a remembered one takes its place, rather
        # than sitting in front of them as an empty Untitled.tex.
        placeholder = self.active

        for path in remembered:
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue  # unreadable now: not worth a dialog at start-up
            document = self.open_document(path=path, text=text, focus=False)
            self._update_title(document)

        if (placeholder is not None and placeholder.path is None
                and not placeholder.modified and any(d.path for d in self.documents)):
            page = self._page_for(placeholder)
            if page is not None:
                self.tabs.close_page(page)

        # The folder before the blank template: a project whose tabs were all
        # closed should come back to its main document, not to an empty one.
        folder = self.config["open_folder"]
        if folder and os.path.isdir(folder):
            self.open_folder(folder, restoring=True)

        if not self.documents:
            document = self.open_document()
            document.editor.set_text(templates.ARTICLE.replace(templates.CURSOR, ""))
            document.edited_since_load = False

        first = self.tabs.get_nth_page(0)
        self.tabs.set_selected_page(first)
        self._on_tab_selected()
        self._update_title()
        if self.active.result is None and not self.active.compiling:
            self.compile_now()

        jobs.run(packages.catalogue_status, self._on_package_status)
        return GLib.SOURCE_REMOVE

    def _on_package_status(self, status):
        self._package_status = status

    def select_path_arg(self, path: str):
        if os.path.isdir(path):
            self.open_folder(os.path.abspath(path))
        elif os.path.isfile(path):
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
        if document is not self.active or self._web_front is not None:
            return
        self.title_widget.set_title(("•  " if document.modified else "") + document.name)
        self.title_widget.set_subtitle(
            document.folder.replace(os.path.expanduser("~"), "~"))
        self.save_button.set_sensitive(document.modified or document.path is None)

    def load_file(self, path: str, focus: bool = True):
        """Open a file in a tab of its own, and bring it to the front."""
        already = self._document_at(path)
        if already is not None:
            if focus:
                self.tabs.set_selected_page(self._page_for(already))
            return already
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            widgets.error_toast(self, exc)
            return None

        # An untouched, never-saved first tab is scaffolding, not a document:
        # opening a file replaces it rather than leaving an empty tab behind.
        spare = self.active
        if (spare is not None and spare.path is None and not spare.modified
                and len(self.documents) == 1):
            document = spare
            document.set_path(path)
            document.editor.set_text(text)
            self.tree.set_current(path)
        else:
            document = self.open_document(path=path, text=text, focus=focus)
        document.edited_since_load = False
        self._remember_open_files()
        self._update_title(document)
        # Bringing a new tab to the front has already started its compile.
        if not document.compiling:
            self.compile_now(document)
        return document

    def _document_at(self, path: str | None):
        """The open document for `path`, if it has a tab."""
        if not path:
            return None
        return next((d for d in self.documents if d.is_at(path)), None)

    def open_path(self, path: str):
        """A file chosen in the folder view: a tab if it is text, else its app.

        PDFs, images and the like go to whatever the desktop opens them
        with; anything that reads as text — .tex, .bib, .cls, a Makefile —
        comes here, where it can be edited.
        """
        if os.path.isdir(path):
            return
        if project.looks_like_text(path):
            self.load_file(path)
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))

        def done(source, result, *_):
            try:
                source.launch_finish(result)
            except GLib.Error as exc:
                widgets.error_toast(self, exc)

        launcher.launch(self, None, done)

    def _remember_open_files(self, closing=False):
        """The tabs to put back next time, in the order they are in now.

        The list is the application's to work out, not this window's: with
        more than one open, a window that wrote only its own tabs would leave
        whichever closed last having forgotten all the others. See
        LaRendererApp.remember_session.
        """
        app = self.get_application()
        if app is not None and hasattr(app, "remember_session"):
            paths = app.remember_session(closing=self if closing else None)
        else:
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
                self.tree.set_current(path)
                self.config["last_file"] = path
                self.config.save()
                self.compile_now()

        dialog.save(self, None, done)

    def _write(self, path: str) -> bool:
        return self._write_document(self.active, path)

    def _write_document(self, document, path: str, quiet: bool = False) -> bool:
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
        if not quiet:
            widgets.toast(self, f"Saved {os.path.basename(path)}")
        self._after_save(path)
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
        # A part of a larger document exports the whole, named after it.
        named = self._result.root or self.path
        stem = os.path.splitext(os.path.basename(named))[0] if named else "untitled"
        dialog.set_initial_name(stem + ".pdf")
        if named:
            dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(named)))

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
        compiler.clear_build(self.active.compiled_root or self.path)
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
        if document is None:
            return
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

        # A part of a larger document compiles the whole of it; see project.py.
        root = self._root_for(document)
        if root is None or document.is_at(root):
            if not document.is_tex:
                self._set_status(f"{document.name} is not a LaTeX document, so there "
                                 "is nothing to compile", "", document)
                return
            text = document.editor.get_text()
        else:
            owner = self._document_at(root)
            text = owner.editor.get_text() if owner is not None else None
        if text is not None and not text.strip():
            document.preview.clear()
            self._set_status("Nothing to compile", "", document)
            return
        overlays = self._overlays(root)
        document.compiled_root = root

        document.compiling = True
        document.job = compiler.Job()
        job = document.job
        through = "" if root is None or document.is_at(root) else f" {os.path.basename(root)}"
        self._set_status(f"Compiling{through}…", "compiling", document)
        if document is self.active:
            self.compile_button.set_sensitive(False)

        engine = self.config["engine"]
        if engine not in compiler.available_engines():
            engine = (compiler.available_engines() or ["pdflatex"])[0]

        options = dict(
            engine=engine,
            shell_escape=bool(self.config["shell_escape"]),
            timeout=int(self.config["timeout_seconds"]),
            bibliography=bool(self.config["bibliography"]),
            overlays=overlays,
        )

        def work():
            # The main file of a part that isn't open is read here, off the
            # main loop, rather than every keystroke paying for it there.
            source = text if text is not None else project.read(root)
            return compiler.compile_document(source, root, job=job, **options)

        jobs.run(
            work,
            lambda result: self._on_compiled(result, document),
            lambda exc: self._on_compile_failed(exc, document),
        )

    def _root_for(self, document) -> str | None:
        """The file to compile for `document`: itself, or what it is part of.

        The order is project.py's: a `% !TEX root` line, then a
        \\documentclass of its own, then the open folder's main document,
        then a document nearby that \\inputs it.
        """
        path = document.path
        if path is None:
            return None
        text = document.editor.get_text()
        directive = project.root_directive(text)
        if directive:
            named = os.path.normpath(os.path.join(os.path.dirname(path), directive))
            if not os.path.splitext(named)[1]:
                named += ".tex"
            if os.path.isfile(named) or self._document_at(named) is not None:
                return named
        if document.is_tex and project.is_standalone(text):
            return path
        if self.folder and project.inside(path, self.folder):
            main = self.project_main()
            if main and not project.same(main, path):
                return main
        if document.nearby_root is None:
            document.nearby_root = project.find_including_root(path) or ""
        return document.nearby_root or path

    def _overlays(self, root: str | None) -> dict[str, str] | None:
        """The unsaved buffers of the root's other files, by relative path.

        Only modified ones: a saved buffer is the same as its file, which the
        engine finds by itself, and leaving it out keeps the build directory
        from holding copies that could go stale.
        """
        if not root:
            return None
        here = os.path.dirname(os.path.abspath(root))
        overlays = {}
        for document in self.documents:
            if (document.path and document.modified and not document.is_at(root)
                    and project.inside(document.path, here)):
                overlays[os.path.relpath(document.path, here)] = document.editor.get_text()
        return overlays

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

        self._apply_result(document, result)
        # Every open part of the same document shows the same pages, so the
        # compile one tab asked for brings the others up to date as well —
        # its overlays had their buffers in it.
        if result.root:
            for other in self.documents:
                if (other is not document and not other.compiling and other.compiled_root
                        and project.same(other.compiled_root, result.root)):
                    self._apply_result(other, result)

        if self.config["sync_preview"] and document.edited_since_load:
            if document is self.active:
                self.sync_to_cursor(quiet=True)

    def _apply_result(self, document, result: compiler.Result):
        """Show a compile's pages, problems and summary in one document's tab."""
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
            self._lines_in(document, result, result.errors),
            self._lines_in(document, result, result.warnings),
        )

        errors = len(result.errors)
        warnings = len(result.warnings)
        document.problems_label = self._problems_label(errors, warnings)
        if document is self.active:
            self.problems_button.set_label(document.problems_label)

        # A part says which document it was compiled as, so "3 pages" is not
        # read as the length of the chapter on screen.
        through = ""
        if result.root and document.path and not project.same(result.root, document.path):
            through = f"{os.path.basename(result.root)}: "
        if errors:
            noun = "error" if errors == 1 else "errors"
            extra = f", {warnings} warning{'s' if warnings != 1 else ''}" if warnings else ""
            # Say so when the pane is showing an older render rather than this
            # compile's output, so a stale page is never mistaken for current.
            stale = " · preview is the last good render" if result.stale_pdf else ""
            self._set_status(f"{through}{errors} {noun}{extra}{stale}", "bad", document)
            self._show_problems(True, document)
        else:
            pages = f"{result.pages} page{'s' if result.pages != 1 else ''}"
            passes = f", {result.passes} passes" if result.passes > 1 else ""
            warned = f" · {warnings} warning{'s' if warnings != 1 else ''}" if warnings else ""
            self._set_status(
                f"{through}Compiled {pages} in {result.seconds:.2f}s{passes}{warned}", "ok",
                document)

    def _lines_in(self, document, result, messages) -> list[int]:
        """The lines of `document` that `messages` complain about."""
        lines = []
        for message in messages:
            if not message.line:
                continue
            if document.path is None or result.root is None:
                if message.is_current_file:
                    lines.append(message.line)
                continue
            source = compiler.message_source(result, message)
            if source and project.same(source, document.path):
                lines.append(message.line)
        return lines

    def open_message(self, document, message):
        """A row in the problems list was clicked: go to where it points.

        In a document made of several files that may well be another file,
        which is opened in its own tab — but only one inside the document's
        folder, not a class file somewhere in the TeX distribution.
        """
        if not message.line:
            return
        result = document.result
        if result is None or result.root is None or document.path is None:
            if message.is_current_file:
                document.editor.goto_line(message.line)
            return
        source = compiler.message_source(result, message)
        if source and project.same(source, document.path):
            document.editor.goto_line(message.line)
            return
        if not source or not os.path.isfile(source):
            widgets.toast(self, f"Could not find {message.file or 'that file'} on disk.")
            return
        home = os.path.dirname(result.root)
        if not (project.inside(source, home) or project.inside(source, self.folder)):
            widgets.toast(self, f"That is in {os.path.basename(source)}, outside this "
                                "document's folder.")
            return
        target = self.load_file(source)
        if target is not None:
            target.editor.goto_line(message.line)

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
            # Kept for when its tab comes back to the front, and not shown
            # over an Overleaf tab meanwhile.
            if document is not self.active or self._web_front is not None:
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
        path = self.path
        if result.root and path and not project.same(result.root, path):
            # A part: the engine knew it by its path relative to the main file
            # when it came from the build directory, and absolutely otherwise.
            relative = os.path.relpath(path, os.path.dirname(result.root))
            name = path if relative.startswith(os.pardir) else relative
        else:
            name = compiler.job_name(result.root or path) + ".tex"

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

    # ------------------------------------------------------------- folders

    def open_folder_dialog(self):
        dialog = Gtk.FileDialog(title="Open a folder")
        start = self.folder or (os.path.dirname(self.path) if self.path else None)
        if start:
            dialog.set_initial_folder(Gio.File.new_for_path(start))

        def done(dlg, result):
            try:
                file = dlg.select_folder_finish(result)
            except GLib.Error:
                return
            if file and file.get_path():
                self.open_folder(file.get_path())

        dialog.select_folder(self, None, done)

    def open_folder(self, folder: str, restoring: bool = False):
        """Show `folder` in the side bar and treat it as one project.

        Its main document is found (or remembered, if one was chosen), and
        opened when none of the folder's files is open yet — the way an IDE
        opens a project at the file you would start from. An Overleaf project
        also fetches what collaborators did since it was last open.
        """
        folder = os.path.abspath(os.path.expanduser(folder))
        if not os.path.isdir(folder):
            widgets.toast(self, f"{folder} is not a folder.")
            return
        self.folder = folder
        self.tree.set_folder(folder)
        if not self.tree.get_visible():
            self.activate_action("win.sidebar", None)

        self.config["open_folder"] = folder
        recent = [folder] + [f for f in self.config["recent_folders"]
                             if isinstance(f, str) and not project.same(f, folder)]
        self.config["recent_folders"] = recent[:8]
        self.config.save()
        self._refresh_recent_folders()
        self._update_title()

        def found(main):
            if self.folder != folder:
                return
            self._found_main[folder] = main
            self.tree.set_main(self.project_main())
            opened_here = any(d.path and project.inside(d.path, folder) for d in self.documents)
            chosen = self.project_main()
            # Coming back at start-up, the tabs that were open win; only a
            # session that ended with none of them brings up the main file.
            restored_tabs = restoring and any(d.path for d in self.documents)
            if chosen and not opened_here and not restored_tabs:
                self.load_file(chosen)
            elif self.active is not None and self.active.path and \
                    project.inside(self.active.path, folder):
                # A part that compiled as itself before the main was known
                # now compiles as what it belongs to.
                self.compile_now()

        if folder in self._found_main:
            found(self._found_main[folder])
        else:
            jobs.run(lambda: project.find_main(folder), found)

        if self.git.used:
            self._schedule_git_refresh(0)
        if self.config["overleaf_pull_on_open"] and project.overleaf_remote(folder):
            self._git_op("Pull from Overleaf", "pull", network=True, path=folder,
                         on_done=lambda _payload: self._reload_changed_files(folder),
                         quiet=True)

    def close_folder(self):
        """Take the folder out of the side bar. Its tabs stay open."""
        self.folder = None
        self.tree.set_folder(None)
        self.config["open_folder"] = ""
        self.config.save()
        self._update_title()
        if self.git.used:
            self._schedule_git_refresh(0)

    def project_main(self) -> str | None:
        """The open folder's main document: chosen by hand, else found."""
        if not self.folder:
            return None
        chosen = self.config["project_mains"].get(self.folder)
        if isinstance(chosen, str) and os.path.isfile(chosen):
            return chosen
        found = self._found_main.get(self.folder)
        return found if found and os.path.isfile(found) else None

    def set_project_main(self, path: str):
        if not self.folder:
            return
        mains = dict(self.config["project_mains"])
        mains[self.folder] = path
        self.config["project_mains"] = mains
        self.config.save()
        self.tree.set_main(path)
        widgets.toast(self, f"{os.path.basename(path)} is now the main document")
        if self.active is not None:
            self.compile_now()

    def _refresh_recent_folders(self):
        self._recent_menu.remove_all()
        home = os.path.expanduser("~")
        for folder in self.config["recent_folders"][:8]:
            if not isinstance(folder, str) or not os.path.isdir(folder):
                continue
            label = folder.replace(home, "~", 1) if folder.startswith(home) else folder
            item = Gio.MenuItem.new(label.replace("_", "__"), None)
            item.set_action_and_target_value("win.open-recent-folder",
                                             GLib.Variant.new_string(folder))
            self._recent_menu.append_item(item)

    def _on_tree_request(self, kind, payload):
        if kind == "open-folder":
            if payload:
                self.open_folder(payload)
            else:
                self.open_folder_dialog()
        elif kind == "close-folder":
            self.close_folder()
        elif kind == "main":
            self.set_project_main(payload)
        elif kind == "renamed":
            self._path_renamed(*payload)
        elif kind == "trashed":
            self._path_trashed(payload)
        elif kind == "hidden":
            self.config["show_hidden_files"] = bool(payload)
            self.config.save()

    def _path_renamed(self, old: str, new: str):
        """A file or folder was renamed in the tree: its tabs follow it."""
        def moved(path):
            if project.same(path, old):
                return new
            return os.path.join(new, os.path.relpath(path, old))

        for document in self.documents:
            if document.path and project.inside(document.path, old):
                document.set_path(moved(document.path))
                self._update_title(document)
        # So does the main document, whether it was chosen or found.
        mains = dict(self.config["project_mains"])
        for folder, main in list(mains.items()):
            if isinstance(main, str) and project.inside(main, old):
                mains[folder] = moved(main)
        self.config["project_mains"] = mains
        self.config.save()
        for folder, main in list(self._found_main.items()):
            if main and project.inside(main, old):
                self._found_main[folder] = moved(main)
        self.tree.set_main(self.project_main())
        self.tree.set_current(self.path)
        self._remember_open_files()

    def _path_trashed(self, path: str):
        """A file went to the trash. Its tab stays, with its text, unsaved.

        Closing it then asks first, as it would for any unsaved work — the
        buffer is the only copy outside the trash now.
        """
        for document in self.documents:
            if document.path and project.inside(document.path, path):
                document.editor.buffer.set_modified(True)
                self._update_title(document)

    # ----------------------------------------------------------------- git
    #
    # Nothing here runs git. Each action asks Git Manager through self.git,
    # which starts one in the background the first time; until then the
    # window shows no git state at all, because it has not asked.

    def _git_target(self) -> str | None:
        """What git acts on: the open folder, else the document's folder."""
        if self.folder:
            return self.folder
        if self.path:
            return os.path.dirname(self.path)
        return None

    def _on_active_changed(self, *_):
        if self.is_active() and self.git.used:
            self._schedule_git_refresh()

    def _schedule_git_refresh(self, delay_ms: int = 700):
        if self._git_refresh_timer:
            GLib.source_remove(self._git_refresh_timer)

        def fire():
            self._git_refresh_timer = 0
            self.refresh_git()
            return GLib.SOURCE_REMOVE

        self._git_refresh_timer = GLib.timeout_add(delay_ms, fire)

    def refresh_git(self):
        """Ask Git Manager where the target repository stands."""
        target = self._git_target()
        if not target:
            self._git_status = None
            self._show_git_status()
            return

        def done(status):
            self._git_status = status
            self._show_git_status()

        def failed(error):
            self._git_status = {"error": str(error), "kind": error.kind}
            self._show_git_status()

        self.git.call("status", done, failed, path=target)

    def _show_git_status(self):
        status = self._git_status or {}
        repo = status.get("repo")
        self.tree.set_git(repo, status.get("files") if repo else None)
        if self._git_busy:
            self.git_button.set_label(f"{self._git_busy}…")
        elif repo:
            self.git_button.set_label(_git_summary(status, short=True))
        else:
            self.git_button.set_label("Git")
        if self._git_popover_widget.get_visible():
            self._fill_git_popover()

    def _git_popover(self) -> Gtk.Popover:
        self._git_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                                margin_top=8, margin_bottom=6, margin_start=6, margin_end=6)
        self._git_box.set_size_request(300, -1)
        self._git_popover_widget = Gtk.Popover(child=self._git_box)
        self._git_popover_widget.connect("show", lambda *_: self._on_git_popover_shown())
        return self._git_popover_widget

    def _on_git_popover_shown(self):
        # Opening the menu counts as using git: this is where Git Manager
        # gets started, if nothing has started it yet.
        self._fill_git_popover()
        self.refresh_git()

    def _fill_git_popover(self):
        box = self._git_box
        while (child := box.get_first_child()) is not None:
            box.remove(child)

        status = self._git_status
        target = self._git_target()
        repo = status.get("repo") if status else None

        def note(text, css=("dim-label", "git-summary")):
            label = Gtk.Label(label=text, xalign=0, wrap=True, margin_start=6, margin_end=6,
                              css_classes=list(css))
            label.set_max_width_chars(44)
            box.append(label)

        if not target:
            note("Open a folder, or save the document, and git can act on its folder.")
            return
        if status is None:
            note("Asking Git Manager…" if self.git.state != "ready" else "Reading the repository…")
        elif status.get("error"):
            note(status["error"])
        elif not repo:
            note(f"{os.path.basename(target) or target} is not in a git repository.")
            box.append(self._git_action("Start a repository here", "win.git-init"))
            return
        else:
            name = os.path.basename(repo) or repo
            heading = Gtk.Label(label=name, xalign=0, margin_start=6,
                                css_classes=["git-heading"],
                                ellipsize=Pango.EllipsizeMode.END)
            box.append(heading)
            note(_git_summary(status))
            overleaf = project.overleaf_remote(repo)
            if overleaf:
                note("Kept in step with Overleaf: Sync sends your edits and brings in "
                     "your collaborators'.", css=("dim-label", "overleaf-note"))
            files = status.get("files") or []
            for entry in files[:8]:
                box.append(self._git_file_row(repo, entry))
            if len(files) > 8:
                note(f"and {len(files) - 8} more — Git Manager shows them all")

        box.append(Gtk.Separator(margin_top=6, margin_bottom=4))
        for label, action, keys in (
            ("Commit all…", "win.git-commit", "Ctrl+Alt+C"),
            ("Pull", "win.git-pull", "Ctrl+Alt+U"),
            ("Push", "win.git-push", "Ctrl+Alt+P"),
            ("Sync: commit, pull, push", "win.git-sync", "Ctrl+Alt+S"),
            ("Open in Git Manager", "win.git-show", "Ctrl+Alt+G"),
        ):
            box.append(self._git_action(label, action, keys))

    def _git_action(self, label, action, keys=""):
        button = Gtk.Button(css_classes=["flat"])
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        inner.append(Gtk.Label(label=label, xalign=0, hexpand=True))
        if keys:
            inner.append(Gtk.Label(label=keys, css_classes=["dim-label", "caption"]))
        button.set_child(inner)
        button.set_action_name(action)
        button.connect("clicked", lambda _b: self._git_popover_widget.popdown())
        return button

    def _git_file_row(self, repo, entry):
        path = os.path.join(repo, entry.get("path", ""))
        letter = entry.get("label") or "M"
        button = Gtk.Button(css_classes=["flat"])
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        badge = Gtk.Label(label=letter, css_classes=["git-badge",
                                                     filetree.GIT_CSS.get(letter, "git-modified")])
        inner.append(badge)
        inner.append(Gtk.Label(label=entry.get("path", ""), xalign=0, hexpand=True,
                               ellipsize=Pango.EllipsizeMode.START))
        button.set_child(inner)
        button.set_tooltip_text(path)

        def clicked(_button):
            self._git_popover_widget.popdown()
            if os.path.isfile(path):
                self.open_path(path)

        button.connect("clicked", clicked)
        return button

    def _git_op(self, label, operation, on_done=None, network=False, quiet=False, **arguments):
        """Run one Git Manager operation, with its progress in the status strip.

        A remote that wants a sign-in gets one asked for and the operation
        tried again with it; a pull that stops on a conflict says what that
        means rather than passing git's words along.
        """
        if self._git_busy:
            if not quiet:
                widgets.toast(self, f"{self._git_busy} is still running.")
            return
        path = arguments.get("path")
        overleaf = bool(project.overleaf_remote(path)) if path else False
        if network and overleaf and self._overleaf_token and "token" not in arguments:
            arguments["token"] = self._overleaf_token
        self._git_busy = label
        self._show_git_status()

        def done(payload):
            self._git_busy = None
            self._show_git_status()
            if on_done:
                on_done(payload)
            self.refresh_git()

        def failed(error):
            self._git_busy = None
            self._show_git_status()
            if error.kind == "auth" and network:
                def retry(token):
                    if overleaf:
                        self._overleaf_token = token
                    self._git_op(label, operation, on_done, network, quiet,
                                 **{**arguments, "token": token})

                self._ask_for_token(str(error), overleaf, retry)
            elif error.kind == "conflict":
                self._conflict(label, str(error), path)
            elif error.kind == "nothing" and quiet:
                pass
            else:
                widgets.toast(self, f"{label}: {error}", timeout=8)
            self.refresh_git()

        self.git.call(operation, done, failed, network=network, **arguments)

    def _ask_for_token(self, why, overleaf, retry):
        if overleaf:
            heading = "Sign in to Overleaf"
            body = (f"{why}\n\nOverleaf's git bridge takes a Git authentication token "
                    "instead of your password. Make one under Account settings → "
                    "Git integration. Git keeps it once it works, the way it keeps "
                    "your other sign-ins.")
        else:
            heading = "Sign in"
            body = (f"{why}\n\nEnter a token (or password) for this remote. Git keeps it "
                    "once it works.")
        widgets.ask_text(self, heading, body, "", "Sign in", retry, secret=True)

    def _conflict(self, label, message, path):
        dialog = Adw.AlertDialog(heading=f"{label} stopped on a conflict", body=message)
        dialog.add_response("close", "Fix it here")
        dialog.add_response("manager", "Open in Git Manager")
        dialog.set_default_response("close")
        dialog.set_close_response("close")

        def answered(dlg, result):
            if dlg.choose_finish(result) == "manager":
                self.git_show()

        dialog.choose(self, None, answered)
        # The files now hold both versions between conflict markers; show
        # them as they are on disk, so fixing them here is possible.
        top = project.git_top(path)
        if top:
            self._reload_changed_files(top)

    def _save_all_in(self, top: str) -> int:
        """Save every modified tab inside `top`. Returns how many were saved."""
        saved = 0
        for document in self.documents:
            if document.path and document.modified and project.inside(document.path, top):
                if self._write_document(document, document.path, quiet=True):
                    saved += 1
        return saved

    def _unsaved_in(self, top: str) -> list:
        return [d for d in self.documents
                if d.path and d.modified and project.inside(d.path, top)]

    def _reload_changed_files(self, top: str):
        """After a pull: bring every tab inside `top` in line with the disk.

        A tab with unsaved edits is left alone and named, since overwriting
        it would lose work and merging it is not something to do silently.
        """
        kept = []
        for document in self.documents:
            if not document.path or not project.inside(document.path, top):
                continue
            try:
                on_disk = project.read(document.path)
            except OSError:
                continue
            if on_disk == document.editor.get_text():
                continue
            if document.modified:
                kept.append(document.name)
                continue
            line, _column = document.editor.cursor_position()
            document.editor.set_text(on_disk)
            document.editor.goto_line(line, focus=False)
            document.edited_since_load = False
            self._update_title(document)
        if kept:
            widgets.toast(self, f"Changed on disk too, kept as you have it: {', '.join(kept)}",
                          timeout=8)
        if self.active is not None:
            self.compile_now()

    def _after_save(self, path: str):
        if self.git.used:
            self._schedule_git_refresh()
        if (self.config["overleaf_sync_on_save"] and not self._closing
                and project.overleaf_remote(path)):
            self._schedule_overleaf_sync(project.git_top(path))

    def _schedule_overleaf_sync(self, top):
        """Sync a moment after saving, so saving three tabs is one sync."""
        if self._overleaf_timer:
            GLib.source_remove(self._overleaf_timer)

        def fire():
            self._overleaf_timer = 0
            if self._git_busy:
                self._schedule_overleaf_sync(top)
                return GLib.SOURCE_REMOVE
            self._git_op("Sync with Overleaf", "sync", network=True, quiet=True, path=top,
                         on_done=lambda payload: self._after_sync(payload, top, quiet=True))
            return GLib.SOURCE_REMOVE

        self._overleaf_timer = GLib.timeout_add(1500, fire)

    def _need_repo(self, then):
        """Run then(top) for the target's repository, or say why not."""
        target = self._git_target()
        if not target:
            widgets.toast(self, "Open a folder, or save the document, first.")
            return
        top = project.git_top(target)
        if not top:
            widgets.toast(self, f"{os.path.basename(target) or target} is not in a git "
                                "repository. Start one from the Git menu.", timeout=6)
            return
        then(top)

    def git_show(self):
        target = self._git_target()
        if not target:
            widgets.toast(self, "Open a folder, or save the document, first.")
            return

        def failed(error):
            widgets.toast(self, f"Git Manager: {error}", timeout=8)

        self.git.call("show", None, failed, path=target)
        widgets.toast(self, "Opening Git Manager…")

    def git_commit(self):
        def with_repo(top):
            def after_status(status):
                repo = status.get("repo") or top
                unsaved = self._unsaved_in(repo)
                files = status.get("files") or []
                if not files and not unsaved:
                    widgets.toast(self, "Nothing to commit: everything is committed.")
                    return
                names = [f.get("path", "") for f in files][:4]
                body = ""
                if files:
                    more = f" and {len(files) - 4} more" if len(files) > 4 else ""
                    count = f"{len(files)} changed file{'s' if len(files) != 1 else ''}"
                    body = f"{count}: {', '.join(names)}{more}."
                if unsaved:
                    body += (f"\n{len(unsaved)} tab{'s' if len(unsaved) != 1 else ''} with "
                             "unsaved changes will be saved first.")

                def commit(message):
                    self._save_all_in(repo)
                    self._git_op("Commit", "commit", path=repo, message=message,
                                 on_done=lambda _p: widgets.toast(self, "Committed"))

                widgets.ask_text(self, "Commit all changes", body.strip(), "", "Commit",
                                 commit)

            self.git.call("status", after_status,
                          lambda error: widgets.toast(self, f"Commit: {error}", timeout=8),
                          path=top)

        self._need_repo(with_repo)

    def git_pull(self):
        def with_repo(top):
            self._git_op("Pull", "pull", network=True, path=top,
                         on_done=lambda payload: self._after_pull(payload, top))

        self._need_repo(with_repo)

    def _after_pull(self, payload, top):
        if payload.get("changed"):
            widgets.toast(self, "Pulled: new changes came in")
            self._reload_changed_files(top)
        else:
            widgets.toast(self, "Pulled: already up to date")

    def git_push(self):
        def with_repo(top):
            unsaved = self._unsaved_in(top)
            if unsaved:
                widgets.toast(self, f"{len(unsaved)} unsaved tab{'s' if len(unsaved) != 1 else ''}"
                                    " — push only sends what is committed.", timeout=6)
            self._git_op("Push", "push", network=True, path=top,
                         on_done=lambda _p: widgets.toast(self, "Pushed"))

        self._need_repo(with_repo)

    def git_sync(self):
        """Commit everything, pull, push — one step, for Overleaf especially.

        An Overleaf project commits without asking for a message: its history
        is Overleaf's own, where a commit message is never read. Any other
        repository gets asked, when there is something to commit.
        """
        def with_repo(top):
            overleaf = project.overleaf_remote(top)
            label = "Sync with Overleaf" if overleaf else "Sync"

            def go(message=""):
                self._save_all_in(top)
                self._git_op(label, "sync", network=True, path=top, message=message,
                             on_done=lambda payload: self._after_sync(payload, top))

            if overleaf:
                go()
                return

            def after_status(status):
                if status.get("files") or self._unsaved_in(top):
                    widgets.ask_text(self, "Sync", "Commit everything with this message, "
                                     "then pull and push.", "", "Sync", go)
                else:
                    go()

            self.git.call("status", after_status,
                          lambda error: widgets.toast(self, f"Sync: {error}", timeout=8),
                          path=top)

        self._need_repo(with_repo)

    def _after_sync(self, payload, top, quiet=False):
        if payload.get("pulled"):
            self._reload_changed_files(top)
        parts = []
        if payload.get("committed"):
            parts.append("committed")
        if payload.get("pulled"):
            parts.append("brought in new changes")
        if payload.get("pushed"):
            parts.append("sent")
        if payload.get("no_remote"):
            parts.append("nothing to send it to: there is no remote")
        if parts:
            widgets.toast(self, "Synced: " + ", ".join(parts))
        elif not quiet:
            widgets.toast(self, "Already in step: nothing to send or bring in")

    def git_init(self):
        target = self._git_target()
        if not target:
            widgets.toast(self, "Open a folder, or save the document, first.")
            return
        self._git_op("Start a repository", "init", path=target,
                     on_done=lambda _p: widgets.toast(self, "Started a git repository"))

    # ------------------------------------------------------------ overleaf
    #
    # Two ways to work on an Overleaf project. Live, in a tab showing
    # Overleaf's own editor — real-time with collaborators, any plan — or on
    # a copy here through the git bridge, with LaRenderer's editor and
    # preview. The two meet when a live tab closes: its project's copy, if
    # that is the open folder, pulls what was done in it.

    # Actions that belong to the document in front, and have no meaning while
    # an Overleaf tab is. Disabled rather than merely hidden, so their keys —
    # Ctrl+S above all — reach Overleaf's editor instead.
    DOCUMENT_ACTIONS = (
        "save", "save-as", "export", "reload", "compile", "raw-log", "clean",
        "zoom-in", "zoom-out", "zoom-reset", "zoom-mode", "sync", "problems",
        "next-page", "previous-page", "page-theme", "engine",
    )

    def open_overleaf_web(self, uri: str | None = None):
        """Overleaf's editor in a tab: the open project's, or the project list."""
        if not overleafweb.available():
            widgets.toast(self, "Overleaf's editor needs WebKitGTK 6.0 (the webkitgtk-6.0 "
                                "package), which is not installed.", timeout=8)
            return None
        uri = uri or self._overleaf_web_target()
        wanted = overleafweb.project_id_of(uri)
        for page, view in self._web_tabs.items():
            same = (wanted and view.project_id() == wanted) or \
                view.uri.rstrip("/") == uri.rstrip("/")
            if same:
                self.tabs.set_selected_page(page)
                return view
        try:
            view = overleafweb.OverleafView(uri, on_new_tab=self.open_overleaf_web,
                                            open_external=self._open_external_uri)
        except Exception as exc:  # noqa: BLE001 - a WebKit that will not start
            widgets.error_toast(self, exc)
            return None
        page = self.tabs.append(view)
        self._web_tabs[page] = view
        page.set_title("Overleaf")
        page.set_tooltip(uri)
        view.on_title = lambda title, page=page: self._web_title_changed(page, title)
        self.tabs.set_selected_page(page)
        return view

    def _overleaf_web_target(self) -> str:
        """The editor for the open folder's Overleaf project, if it is one."""
        for path in (self.folder, self.path):
            url = overleafweb.web_url_for_remote(project.overleaf_remote(path)) if path else None
            if url:
                return url
        return overleafweb.DASHBOARD

    def _open_external_uri(self, uri: str):
        overleafweb.launch_external(self, uri)

    def _web_title_changed(self, page, title):
        view = self._web_tabs.get(page)
        if view is None:
            return
        page.set_title(title)
        page.set_tooltip(view.uri)
        if self._web_front is view:
            self.title_widget.set_title(title)

    def _set_web_mode(self, web):
        """Step the document chrome aside for an Overleaf tab, or bring it back."""
        was = self._web_front
        self._web_front = web
        on = web is not None
        for name in self.DOCUMENT_ACTIONS:
            action = self.lookup_action(name)
            if action is not None:
                action.set_enabled(not on)
        for widget in self._document_chrome:
            widget.set_visible(not on)
        strip = self.status_label.get_parent()
        if on:
            self.title_widget.set_title(web.title())
            self.title_widget.set_subtitle("Overleaf, live")
            self.status_label.set_label(
                "Overleaf's own editor: every change is saved on Overleaf as it is "
                "typed, and collaborators see it live.")
            for name in ("ok", "bad", "compiling"):
                strip.remove_css_class(name)
        elif was is not None and self.active is not None:
            self._adopt_active()

    def _after_live_editing(self, view, throttle: bool = False):
        """Bring what was done in a live tab into the local copy, if one is open.

        Only for the project that tab was showing, only when the open folder
        is that project's git clone, and at most every twenty seconds when it
        is only a switch of tabs rather than a close.
        """
        project_id = view.project_id() if view is not None else None
        folder = self.folder
        if not project_id or not folder:
            return
        remote = project.overleaf_remote(folder)
        if not remote or project_id not in remote:
            return
        now = time.monotonic()
        if throttle and now - self._last_live_pull < 20:
            return
        self._last_live_pull = now
        self._git_op("Pull from Overleaf", "pull", network=True, quiet=True, path=folder,
                     on_done=lambda _payload: self._reload_changed_files(folder))

    def overleaf_open_dialog(self):
        """Clone an Overleaf project through its git bridge, then open it.

        From then on the project is an ordinary folder here, and Sync
        (Ctrl+Alt+S) is how edits cross in either direction.
        """
        dialog = Adw.Dialog(title="Open an Overleaf project", content_width=560)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        clone = Gtk.Button(label="Open", css_classes=["suggested-action"])
        header.pack_end(clone)
        toolbar.add_top_bar(header)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            description="Open works on a copy of the project, kept in step with Overleaf "
                        "through its git bridge (a paid-plan feature): LaRenderer's own "
                        "editor and preview, and Sync (Ctrl+Alt+S) to send and receive. "
                        "Edit live instead opens Overleaf's own editor in a tab — "
                        "real-time with collaborators, on any plan.")
        link = Adw.EntryRow(title="Project link, as in overleaf.com/project/…")
        group.add(link)
        name = Adw.EntryRow(title="Folder name (the project id if left empty)")
        group.add(name)

        base = {"dir": str(self.config["overleaf_dir"])}
        where = Adw.ActionRow(title="Saved in")
        where.set_subtitle(base["dir"].replace(os.path.expanduser("~"), "~", 1))
        change = Gtk.Button(label="Change…", valign=Gtk.Align.CENTER)
        where.add_suffix(change)
        group.add(where)

        token = Adw.PasswordEntryRow(title="Git authentication token (first time only)")
        group.add(token)
        hint = Adw.ActionRow(
            subtitle="Overleaf → Account settings → Git integration → Generate token. "
                     "Git keeps it after the first clone, the same way it keeps your other "
                     "sign-ins, so this stays empty from then on.")
        group.add(hint)
        page.add(group)

        status = Gtk.Label(xalign=0, wrap=True, margin_start=12, margin_end=12,
                           margin_bottom=12, css_classes=["dim-label"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(page)
        box.append(status)
        toolbar.set_content(box)
        dialog.set_child(toolbar)

        def choose_base(_button):
            chooser = Gtk.FileDialog(title="Where Overleaf projects go")
            if os.path.isdir(base["dir"]):
                chooser.set_initial_folder(Gio.File.new_for_path(base["dir"]))

            def picked(dlg, result):
                try:
                    file = dlg.select_folder_finish(result)
                except GLib.Error:
                    return
                if file and file.get_path():
                    base["dir"] = file.get_path()
                    where.set_subtitle(base["dir"].replace(os.path.expanduser("~"), "~", 1))
                    self.config["overleaf_dir"] = base["dir"]
                    self.config.save()

            chooser.select_folder(self, None, picked)

        change.connect("clicked", choose_base)

        def say(text, bad=False):
            status.set_label(text)
            if bad:
                status.remove_css_class("dim-label")
                status.add_css_class("error")
            else:
                status.remove_css_class("error")
                status.add_css_class("dim-label")

        def start(*_):
            parsed = project.overleaf_git_url(link.get_text())
            if not parsed:
                say("That doesn't look like an Overleaf project: the link should have the "
                    "project's 24-character id in it, as in "
                    "overleaf.com/project/64b1…", bad=True)
                link.grab_focus()
                return
            url, project_id = parsed
            folder_name = name.get_text().strip() or project_id
            if "/" in folder_name or os.sep in folder_name or folder_name in (".", ".."):
                say("The folder name can't contain a slash.", bad=True)
                return
            dest = os.path.join(base["dir"], folder_name)

            # Already here: open it rather than refuse to clone over it.
            if os.path.isdir(dest) and any(project_id in u for u in project.remote_urls(dest)):
                dialog.close()
                self.open_folder(dest)
                return

            secret = token.get_text().strip()
            if secret:
                self._overleaf_token = secret
            clone.set_sensitive(False)
            say("Cloning from Overleaf…")

            def done(_payload):
                dialog.close()
                widgets.toast(self, f"Cloned {folder_name} from Overleaf")
                self.open_folder(dest)

            def failed(error):
                clone.set_sensitive(True)
                if error.kind == "auth":
                    say("Overleaf asked for a Git authentication token"
                        + (" and did not accept that one." if secret else ".")
                        + " Paste one from Account settings → Git integration.", bad=True)
                    token.grab_focus()
                else:
                    say(str(error), bad=True)

            self.git.call("clone", done, failed, network=True, url=url, dest=dest,
                          token=secret or self._overleaf_token)

        def live(*_):
            text = link.get_text().strip()
            parsed = project.overleaf_git_url(text) if text else None
            if text and not parsed:
                say("That doesn't look like an Overleaf project link.", bad=True)
                return
            dialog.close()
            if parsed and parsed[0].startswith("https://git.overleaf.com/"):
                self.open_overleaf_web(f"{overleafweb.OVERLEAF}/project/{parsed[1]}")
            elif parsed:
                self.open_overleaf_web(overleafweb.web_url_for_remote(parsed[0]))
            else:
                self.open_overleaf_web(overleafweb.DASHBOARD)

        live_button = Gtk.Button(label="Edit live instead")
        live_button.set_tooltip_text("Overleaf's own editor in a tab: real-time with "
                                     "collaborators, on any Overleaf plan (Ctrl+Alt+O)")
        live_button.connect("clicked", live)
        header.pack_start(live_button)

        clone.connect("clicked", start)
        link.connect("entry-activated", start)
        token.connect("entry-activated", start)
        dialog.present(self)
        link.grab_focus()

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
            if self.active is not None:
                self.editor.set_font_size(size)

        font_row.connect("notify::value", font_changed)
        looks.add(font_row)

        theme_row = Adw.ComboRow(
            title="Page colours",
            subtitle="Dark inverts the rendered pages, keeping the colours' hues",
        )
        theme_row.set_model(Gtk.StringList.new([THEME_LABEL[t] for t in THEMES]))
        current = str(self.config["pdf_theme"])
        theme_row.set_selected(THEMES.index(current) if current in THEMES else 0)

        def theme_changed(row, _param):
            name = THEMES[row.get_selected()]
            self.config["pdf_theme"] = name
            self.config.save()
            self.lookup_action("page-theme").set_state(GLib.Variant.new_string(name))
            if self.active is not None:
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

        folders = Adw.PreferencesGroup(title="Folders and Overleaf")
        hidden_row = Adw.SwitchRow(
            title="Show hidden files",
            subtitle="Dotfiles and .git in the folder view",
            active=bool(self.config["show_hidden_files"]),
        )

        def hidden_changed(row, _param):
            self.config["show_hidden_files"] = row.get_active()
            self.config.save()
            self.tree.set_show_hidden(row.get_active())

        hidden_row.connect("notify::active", hidden_changed)
        folders.add(hidden_row)

        pull_row = Adw.SwitchRow(
            title="Pull when an Overleaf project opens",
            subtitle="Brings in what collaborators did since you last had it open",
            active=bool(self.config["overleaf_pull_on_open"]),
        )
        pull_row.connect("notify::active", lambda r, _p: self._set_option(
            "overleaf_pull_on_open", r.get_active()))
        folders.add(pull_row)

        push_row = Adw.SwitchRow(
            title="Sync with Overleaf after every save",
            subtitle="Commit, pull and push a moment after each Ctrl+S",
            active=bool(self.config["overleaf_sync_on_save"]),
        )
        push_row.connect("notify::active", lambda r, _p: self._set_option(
            "overleaf_sync_on_save", r.get_active(), action="overleaf-sync-on-save"))
        folders.add(push_row)
        page.add(folders)

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
            ("Ctrl+Shift+O", "Open a folder"),
            ("F9", "Show or hide the folder"),
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
            ("Ctrl+0", "Zoom the preview to 100%"),
            ("Click the zoom size", "Type an exact zoom"),
            ("Ctrl+Page Up / Down", "Previous or next page"),
            ("Pinch, or Ctrl+scroll, over the preview", "Zoom the preview"),
            ("Pinch, or Ctrl+scroll, over the text", "Resize the editor text"),
            ("Ctrl+/", "Comment or uncomment the selection"),
            ("Tab  /  Shift+Tab", "Indent or unindent the selection"),
            ("Enter after \\begin{…}", "Close the environment automatically"),
            ("F2  /  Delete in the folder", "Rename, or move to the trash"),
            ("Ctrl+Alt+C", "Git: commit all changes"),
            ("Ctrl+Alt+U", "Git: pull"),
            ("Ctrl+Alt+P", "Git: push"),
            ("Ctrl+Alt+S", "Git: sync — commit, pull, push (and Overleaf)"),
            ("Ctrl+Alt+G", "Open the repository in Git Manager"),
            ("Ctrl+Alt+O", "Overleaf's own editor, live, in a tab"),
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
            version=__version__,
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
        self._remember_open_files(closing=True)
        if self._git_busy and not self._warned_busy:
            # Git Manager finishes what it started even once we are gone, but
            # nothing would then say whether it worked. Ask once; a second
            # close goes through.
            self._warned_busy = True
            widgets.toast(self, f"{self._git_busy} is still running. Close again to quit "
                                "anyway.", timeout=6)
            return True
        self._closing = True

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

    # ------------------------------------------------------------ geometry

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

    def _remember_geometry(self):
        # Under a tiling compositor neither number is the user's to remember,
        # so the ones from the last session that did get to choose them stay.
        if not self._compositor_tiles_windows():
            self.config["window_maximized"] = self.is_maximized()
            if not self.is_maximized():
                self.config["window_width"] = self.get_width()
                self.config["window_height"] = self.get_height()
        # Every tab has a paned and an editor of its own; the one in front is
        # the one whose layout the user just chose.
        if self.active is not None:
            self.config["split_position"] = self.paned.get_position()
            fraction = self.active.split_fraction()
            if fraction is not None:
                self.config["split_fraction"] = round(fraction, 4)
            self.config["editor_font_size"] = self.editor.get_font_size()
        if self.tree.get_visible():
            self.config["sidebar_width"] = self.side_paned.get_position()
        self.config.save()


def _git_summary(status: dict, short: bool = False) -> str:
    """Branch, distance from upstream and changes, for the strip or the menu."""
    branch = status.get("branch") or ("detached HEAD" if status.get("detached") else "no branch")
    parts = [branch]
    ahead, behind = status.get("ahead") or 0, status.get("behind") or 0
    if ahead:
        parts.append(f"↑{ahead}")
    if behind:
        parts.append(f"↓{behind}")
    changed = len(status.get("files") or [])
    if status.get("state"):
        parts.append(f"{status['state']} in progress")
    if short:
        if changed:
            parts.append(f"· {changed} changed")
        return " ".join(parts)
    where = f" of {status['upstream']}" if status.get("upstream") and (ahead or behind) else ""
    tail = f"{changed} changed file{'s' if changed != 1 else ''}" if changed else "nothing to commit"
    if not status.get("upstream"):
        tail += " · not tracking a remote branch"
    return " ".join(parts) + where + " · " + tail
