"""The window: six pages, and whatever is being done at the moment.

Two layers. The **pages** are where things are chosen — Today, Decks, Paper,
Palaces, Trainers, Week — switched between with the view switcher (Alt+1…6).
An **activity** is a thing being done: a review, a walk through a palace, a
drill, a sheet being typed back. It takes the whole content while it runs,
the header turns into a back button and its title, and Esc or the back button
ends it. One activity at a time is the design, not a limitation: a review is a
sitting, and a second one half-finished in a tab is a review nobody finishes.

*It has to fit in a tiling column.* niri on a 1366-wide screen gives about
455, 683 and 900 pixels. Below 720px the switcher moves from the header to a
bar along the bottom, where six pages fit at 455, and the Claude panel becomes
an overlay instead of a side column. The test suite checks the window can be
made 360px wide.

*It may not be the window.* In Mind Palace, Mnemosyne is a room (room.py):
this class is built with a `host` and never presented, and what the palace
shows is `room_widget` — the same pages and activities, under a bar of the
room's own buttons instead of a header bar, with no glass of its own (the
palace has the glass), its breakpoints on an `Adw.BreakpointBin`, and this
window inserted as the widget's "win" action group so every `win.*` name
still resolves. The window is not given the application in that case: an
application keeps running while it has a window, hidden or not, and a room
must not keep the palace alive after its own window has closed. Every dialog
is presented on a widget inside the content, and nothing here asks a widget
for its root expecting to find a `MainWindow`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__, housestyle, jobs, widgets, winenv  # noqa: E402
from .assistant import Assistant  # noqa: E402
from .config import Config  # noqa: E402
from .decks import DecksPage  # noqa: E402
from .drills import TrainersPage  # noqa: E402
from .library import Library  # noqa: E402
from .palaces import PalacesPage  # noqa: E402
from .paperpage import PaperPage  # noqa: E402
from .session import Session  # noqa: E402
from .study import StudyView  # noqa: E402
from .today import TodayPage  # noqa: E402
from .week import WeekPage  # noqa: E402

NARROW = 720
MEDIUM = 1100

# Action, keys. Installed both as application accelerators (so the menus show
# them) and on a controller inside the content (so they work when the content
# is hosted somewhere else).
SHORTCUTS = (
    ("win.study-today", ["<Control>Return"]),
    ("win.print-sheet", ["<Control>p"]),
    ("win.enter-results", ["<Control>e"]),
    ("win.study-note", ["<Control>o"]),
    ("win.refresh", ["F5", "<Control>r"]),
    ("win.ask-claude", ["<Control>k"]),
    ("win.preferences", ["<Control>comma"]),
    ("win.shortcuts", ["<Control>question"]),
    ("win.page-today", ["<Alt>1"]),
    ("win.page-decks", ["<Alt>2"]),
    ("win.page-paper", ["<Alt>3"]),
    ("win.page-palaces", ["<Alt>4"]),
    ("win.page-trainers", ["<Alt>5"]),
    ("win.page-week", ["<Alt>6"]),
)


class RoomBar(Gtk.CenterBox):
    """A header bar without a window to head: the room's own buttons, and no more.

    Answers the three calls the window makes of an `Adw.HeaderBar`, so the
    rest of this class builds one bar or the other the same way.
    """

    def __init__(self):
        super().__init__(css_classes=["toolbar", "room-bar"])
        self._start = Gtk.Box(spacing=4)
        self._end = Gtk.Box(spacing=4)
        self.set_start_widget(self._start)
        self.set_end_widget(self._end)

    def pack_start(self, widget):
        self._start.append(widget)

    def pack_end(self, widget):
        # As a header bar does it: the first packed is the outermost.
        self._end.prepend(widget)

    def set_title_widget(self, widget):
        self.set_center_widget(widget)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, library: Library | None = None, host=None):
        self.host = host
        if host is None:
            super().__init__(application=app, title="Mnemosyne")
        else:
            super().__init__(title="Mnemosyne")
        self.config = Config()
        self.library = library or Library()
        self.activity = None
        self._refreshing = False
        self._after_refresh: list = []
        self._fit_checked = False
        self._refreshed_at = 0.0

        # The remembered size, applied before the first map — see
        # housestyle.Geometry for why the maximise is treated differently
        # under a tiling compositor.
        self.geometry = housestyle.Geometry(self, self.config, minimum=(360, 360))
        if host is None:
            self.geometry.restore()

        self.assistant = Assistant(self)
        self._build_actions()
        self._build_ui()
        self._install_shortcuts()

        if host is None:
            self.connect("close-request", self._on_close)
            self.connect("notify::is-active", self._on_active_changed)
        else:
            # A room is never the active window; coming into view is the
            # moment to read the notes again.
            self.room_widget.connect("map", self._on_active_changed)
        GLib.idle_add(self.refresh)

    # ------------------------------------------------------------ building --

    def _build_ui(self):
        self.toasts = Adw.ToastOverlay()
        if self.host is None:
            # The house style: the window paints nothing and everything sits
            # on one rounded, translucent slab of glass inside it.
            self.glazing = housestyle.Glazing(
                self, self.config, extra=widgets.css(self.config["card_font_size"]),
                app_class="mnemosyne")
            self.glazing.wrap(self.toasts)
            self.room_widget = None
        else:
            # A room: the palace has the glass and the house sheet. This app's
            # own rules go on a provider of their own, so that installing them
            # does not replace whatever the palace installed.
            self.glazing = None
            self.room_widget = Adw.BreakpointBin(width_request=360, height_request=360,
                                                 css_classes=["mnemosyne"])
            self.room_widget.set_child(self.toasts)
            self.toasts.insert_action_group("win", self)
            self._room_css = Gtk.CssProvider()
            self._room_css.load_from_string(widgets.css(self.config["card_font_size"]))
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), self._room_css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

        # The Claude panel shares the width with everything else, and folds
        # over it in a narrow column.
        self.split = Adw.OverlaySplitView(sidebar_position=Gtk.PackType.END,
                                          show_sidebar=False, max_sidebar_width=380,
                                          min_sidebar_width=260)
        self.split.set_sidebar(self.assistant.panel)
        self.toasts.set_child(self.split)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.split.set_content(outer)
        self.content_box = outer

        self.header = Adw.HeaderBar() if self.host is None else RoomBar()
        outer.append(self.header)

        self.back_button = widgets.flat("go-previous-symbolic", "Back (Esc)")
        self.back_button.connect("clicked", lambda *_: self.close_activity())
        self.back_button.set_visible(False)
        self.header.pack_start(self.back_button)

        self.refresh_button = widgets.flat("view-refresh-symbolic",
                                           "Read the notes again (F5)", "win.refresh")
        self.header.pack_start(self.refresh_button)

        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic",
                                     tooltip_text="Main menu",
                                     menu_model=self._main_menu())
        self.header.pack_end(menu_button)

        self.claude_button = Gtk.ToggleButton(icon_name="chat-bubbles-text-symbolic",
                                              tooltip_text="Claude (Ctrl+K)",
                                              visible=self.assistant.available)
        self.claude_button.connect("toggled", lambda b: self.split.set_show_sidebar(b.get_active()))
        self.split.connect("notify::show-sidebar", self._sidebar_shown)
        self.assistant.panel.set_visible(False)
        self.header.pack_end(self.claude_button)

        # -- the pages ------------------------------------------------------
        self.views = Adw.ViewStack(vexpand=True)
        self.pages = {}
        for page in (TodayPage(self), DecksPage(self), PaperPage(self),
                     PalacesPage(self), TrainersPage(self), WeekPage(self)):
            stack_page = self.views.add_titled_with_icon(page, page.name, page.title,
                                                         page.icon)
            page.stack_page = stack_page
            self.pages[page.name] = page
        if self.config["page"] in self.pages:
            self.views.set_visible_child_name(self.config["page"])
        self.views.connect("notify::visible-child-name", self._page_changed)

        self.switcher = Adw.ViewSwitcher(stack=self.views,
                                         policy=Adw.ViewSwitcherPolicy.WIDE)
        self.title = Adw.WindowTitle(title="Mnemosyne")
        self.header.set_title_widget(self.switcher)

        # Pages and the activity, one over the other.
        self.stage = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
                               transition_duration=160, vexpand=True)
        self.stage.add_named(self.views, "pages")
        self.activity_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.stage.add_named(self.activity_holder, "activity")
        outer.append(self.stage)

        self.switcher_bar = Adw.ViewSwitcherBar(stack=self.views)
        outer.append(self.switcher_bar)

        self._install_breakpoint()

        # Keys an activity wants before any button does — Space, the digits.
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.toasts.add_controller(keys)

    def _sidebar_shown(self, split, _param):
        # Hidden outright while it is closed: a closed sidebar is still given a
        # width of nothing, and its padding makes that less than nothing.
        shown = split.get_show_sidebar()
        self.assistant.panel.set_visible(shown)
        self.claude_button.set_active(shown)

    def _install_breakpoint(self):
        # Only one breakpoint is in force at a time — the last added of those
        # that match — so the wider one goes in first. Between the column and
        # a wide screen the switcher stacks its labels under its icons rather
        # than cutting them short; in the column it leaves the header.
        medium = Adw.Breakpoint.new(Adw.BreakpointCondition.new_length(
            Adw.BreakpointConditionLengthType.MAX_WIDTH, MEDIUM, Adw.LengthUnit.PX))
        medium.add_setter(self.switcher, "policy", Adw.ViewSwitcherPolicy.NARROW)
        holder = self.room_widget or self
        holder.add_breakpoint(medium)
        condition = Adw.BreakpointCondition.new_length(
            Adw.BreakpointConditionLengthType.MAX_WIDTH, NARROW, Adw.LengthUnit.PX)
        self.narrow = Adw.Breakpoint.new(condition)
        self.narrow.connect("apply", lambda *_: self._set_narrow(True))
        self.narrow.connect("unapply", lambda *_: self._set_narrow(False))
        holder.add_breakpoint(self.narrow)
        self._narrow = False
        self._set_narrow(False)

    def _set_narrow(self, narrow):
        self._narrow = narrow
        self.split.set_collapsed(narrow)
        self._update_header()

    def _update_header(self):
        busy = self.activity is not None
        self.back_button.set_visible(busy)
        self.refresh_button.set_visible(not busy)
        if busy:
            self.title.set_title(getattr(self.activity, "title", "Mnemosyne"))
            self.title.set_subtitle("")
            self.header.set_title_widget(self.title)
        elif self._narrow:
            self.title.set_title("Mnemosyne")
            self.title.set_subtitle(self.pages[self.views.get_visible_child_name()].title
                                    if self.views.get_visible_child_name() in self.pages else "")
            self.header.set_title_widget(self.title)
        else:
            self.header.set_title_widget(self.switcher)
        self.switcher_bar.set_reveal(self._narrow and not busy)

    # -------------------------------------------------------------- menus --

    def _main_menu(self):
        menu = Gio.Menu()
        study = Gio.Menu()
        study.append("Study Today", "win.study-today")
        study.append("Study a Note…", "win.study-note")
        menu.append_section(None, study)

        paper = Gio.Menu()
        paper.append("Print Today's Sheet", "win.print-sheet")
        paper.append("Enter Sheet Results", "win.enter-results")
        menu.append_section(None, paper)

        self.claude_menu = Gio.Menu()
        if self.assistant.available:
            self.claude_menu.append("Ask Claude…", "win.ask-claude")
            self.claude_menu.append("Make Cards from a Note…", "win.make-cards")
            menu.append_section(None, self.claude_menu)

        tools = Gio.Menu()
        tools.append("Read the Notes Again", "win.refresh")
        menu.append_section(None, tools)

        about = Gio.Menu()
        about.append("Preferences", "win.preferences")
        about.append("Keyboard Shortcuts", "win.shortcuts")
        about.append("About Mnemosyne", "win.about")
        about.append("Quit", "app.quit")
        menu.append_section(None, about)
        return menu

    # ------------------------------------------------------------ actions --

    def _build_actions(self):
        simple = {
            "study-today": self.study_today,
            "study-note": self.choose_note_to_study,
            "print-sheet": lambda: self.pages["paper"].print_today(),
            "enter-results": self.enter_results,
            "refresh": self.refresh,
            "ask-claude": lambda: self.assistant.ask_dialog(),
            "make-cards": lambda: self.assistant.choose_note_for_cards(),
            "preferences": self.show_preferences,
            "shortcuts": self.show_shortcuts,
            "about": self.show_about,
        }
        for name in ("today", "decks", "paper", "palaces", "trainers", "week"):
            simple[f"page-{name}"] = lambda n=name: self.show_page(n)
        for name, handler in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=handler: self._guarded(fn))
            self.add_action(action)

    def _guarded(self, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            import traceback

            traceback.print_exc()
            widgets.error_toast(self.toasts, exc)

    def _install_shortcuts(self):
        app = self.get_application()
        if app is not None:
            for action, keys in SHORTCUTS:
                app.set_accels_for_action(action, keys)
        # In a room, only while the focus is inside it: the palace applies the
        # room's accelerators itself while the room is the one on show.
        controller = Gtk.ShortcutController(
            scope=Gtk.ShortcutScope.MANAGED if self.host is None
            else Gtk.ShortcutScope.LOCAL)
        for action, keys in SHORTCUTS:
            for key in keys:
                trigger = Gtk.ShortcutTrigger.parse_string(key)
                if trigger is not None:
                    controller.add_shortcut(Gtk.Shortcut(
                        trigger=trigger, action=Gtk.NamedAction.new(action)))
        self.toasts.add_controller(controller)

    def _on_key(self, _controller, keyval, _keycode, state):
        if self.activity is not None:
            if self.activity.handle_key(keyval, state):
                return True
            if keyval == Gdk.KEY_Escape and not (state & Gdk.ModifierType.CONTROL_MASK):
                self.close_activity()
                return True
            return False
        page = self.pages.get(self.views.get_visible_child_name())
        handler = getattr(page, "handle_key", None)
        return bool(handler and handler(keyval, state))

    # -------------------------------------------------------------- pages --

    def show_page(self, name):
        if self.activity is not None:
            self.close_activity()
        if name in self.pages:
            self.views.set_visible_child_name(name)

    def _page_changed(self, *_):
        name = self.views.get_visible_child_name()
        if name in self.pages:
            self.config["page"] = name
            self._update_header()

    def toast(self, message, timeout=3):
        widgets.toast(self.toasts, message, timeout)

    # ----------------------------------------------------------- the brain --

    def refresh(self, then=None):
        """Read the notes and the log again, on a worker; then redraw the pages."""
        if then is not None:
            self._after_refresh.append(then)
        if self._refreshing:
            return
        self._refreshing = True
        self.refresh_button.set_sensitive(False)

        def done(_result):
            self._refreshing = False
            self._refreshed_at = time.monotonic()
            self.refresh_button.set_sensitive(True)
            self.reload_pages()
            waiting, self._after_refresh = self._after_refresh, []
            for fn in waiting:
                fn()
            self._fit_when_due()

        def failed(exc):
            self._refreshing = False
            self.refresh_button.set_sensitive(True)
            widgets.error_toast(self.toasts, exc)

        jobs.run(self.library.refresh, done, failed)

    def _fit_when_due(self):
        """Once a session: fit FSRS to the log if it has grown enough (fit.py)."""
        if self._fit_checked or not self.library.exists():
            return
        self._fit_checked = True

        def fitted(result):
            if result is not None:
                self.toast(f"The schedule is now fitted to your {result.reviews} reviews")
                self.refresh()

        jobs.run(self.library.fit_if_due, fitted, lambda exc: None)

    def reload_pages(self):
        for page in self.pages.values():
            try:
                page.reload()
            except Exception as exc:  # noqa: BLE001
                import traceback

                traceback.print_exc()
                widgets.error_toast(self.toasts, exc)

    def _on_active_changed(self, *_):
        # Coming back to the window after writing notes elsewhere: read them
        # again, unless something is being studied right now.
        # Not more than once a minute: a tiling desktop moves the focus about
        # all day, and a rescan is the most expensive thing this app does.
        shown = self.host is not None or self.props.is_active
        if (shown and self.activity is None and self.library.loaded
                and time.monotonic() - self._refreshed_at > 60):
            self.refresh()

    # ---------------------------------------------------------- activities --

    def open_activity(self, widget):
        if self.activity is not None:
            self._leave_activity()
        self.activity = widget
        self.activity_holder.append(widget)
        self.stage.set_visible_child_name("activity")
        if self._narrow:
            self.split.set_show_sidebar(False)
        self._update_header()
        widget.grab_focus()

    def _leave_activity(self):
        old = self.activity
        self.activity = None
        if old is not None:
            leave = getattr(old, "leave", None)
            if callable(leave):
                leave()
            if old.get_parent() is self.activity_holder:
                self.activity_holder.remove(old)

    def close_activity(self):
        self._leave_activity()
        self.stage.set_visible_child_name("pages")
        self._update_header()
        self.reload_pages()

    def study(self, items, title="Today", on_finished=None):
        """Start a review of `items`, as an activity."""
        if not self.library.exists():
            self.toast("There is no brain on this machine to write answers to")
            return
        if not items:
            self.toast("Nothing to study there right now")
            return
        session = Session(self.library, items, title=title)
        self.open_activity(StudyView(self, session, on_finished=on_finished))

    def study_today(self):
        if not self.library.loaded:
            self.refresh(then=self.study_today)
            return
        self.study(self.library.today(), "Today")

    def study_note(self, path, everything=False):
        """`mnemosyne --study FILE`: one note's cards, once the notes are read."""
        if not self.library.loaded or self._refreshing:
            self.refresh(then=lambda: self.study_note(path, everything))
            return
        items = self.library.for_note(path, everything=everything)
        name = os.path.splitext(os.path.basename(str(path)))[0]
        if items:
            self.study(items, name)
            return
        size = self.library.note_size(path)
        if size:
            widgets.confirm(
                self.toasts, "Nothing due in this note",
                f"All {size} of its cards are learned for today. Go over them anyway? "
                "It counts as a review; the schedule takes it into account.",
                "Go over them", lambda: self.study(
                    self.library.for_note(path, everything=True), name))
        else:
            self.toast(f"{name} has no cards yet — write one as Front :: Back")

    def choose_note_to_study(self):
        dialog = Gtk.FileDialog(title="Study a note")
        notes = Gtk.FileFilter()
        notes.set_name("Notes")
        for pattern in ("*.md", "*.markdown", "*.tex"):
            notes.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(notes)
        dialog.set_filters(filters)
        if self.library.places.studies.is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(str(self.library.places.studies)))

        def chosen(source, result):
            try:
                gfile = source.open_finish(result)
            except GLib.Error:
                return
            if gfile and gfile.get_path():
                self.study_note(gfile.get_path())

        dialog.open(self._parent_window(), None, chosen)

    def enter_results(self):
        sheets = self.library.sheets.open()
        if not sheets:
            self.toast("No printed sheet is waiting to be typed back")
            return
        self.pages["paper"].enter(sheets[0])

    # ------------------------------------------------------------- outside --

    def _parent_window(self):
        """A window to parent a file dialog on: ours, or whichever hosts the content."""
        root = self.toasts.get_root()
        return root if isinstance(root, Gtk.Window) else self

    def launch_file(self, path):
        """Open a file with whatever the desktop opens it with (a PDF viewer, say)."""
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(path)))

        def launched(source, result):
            try:
                source.launch_finish(result)
            except GLib.Error:
                if not winenv.open_file(str(path)):
                    self.toast(f"Saved as {path}", timeout=8)

        launcher.launch(self._parent_window(), None, launched)

    def open_note(self, path, line=None):
        """Open a note in Notedown when it is beside this app, else as the desktop would."""
        path = str(path)
        # In the palace, Notedown is a room too: the note opens there.
        opener = getattr(self.host, "open", None)
        if callable(opener):
            try:
                if opener(path):
                    return
            except Exception:  # noqa: BLE001 - the window's own way still works
                pass
        notedown = _sibling("Notedown", "notedown.py")
        if notedown:
            exe = sys.executable
            if winenv.WINDOWS and exe.lower().endswith("python.exe"):
                exe = exe[:-len("python.exe")] + "pythonw.exe"
            try:
                subprocess.Popen([exe, notedown, path], creationflags=winenv.NO_WINDOW,
                                 start_new_session=not winenv.WINDOWS)
                return
            except OSError:
                pass
        self.launch_file(path)

    # --------------------------------------------------------------- dialogs --

    def show_preferences(self):
        from .prefs import preferences                     # noqa: PLC0415

        preferences(self).present(self.toasts)

    def apply_card_size(self):
        if self.glazing is None:
            self._room_css.load_from_string(widgets.css(self.config["card_font_size"]))
            return
        self.glazing.extra = widgets.css(self.config["card_font_size"])
        self.glazing.apply()

    def show_shortcuts(self):
        groups = [
            ("Reviewing", [
                ("Space", "Show the answer, then Good"),
                ("1 2 3 4", "Again, Hard, Good, Easy"),
                ("S", "Skip — ask it again at the end"),
                ("E", "Open the note the card is in"),
                ("X", "Ask Claude to explain the card"),
                ("Esc", "Stop here"),
            ]),
            ("Paper", [
                ("Ctrl+P", "Print today's sheet"),
                ("Ctrl+E", "Type a sheet's ticks back"),
                ("1–4, 0 or −", "One digit per row while typing back"),
            ]),
            ("Trainers", [
                ("Enter", "Answer, or go on"),
                ("0–6", "Doomsday: Sunday … Saturday"),
                ("Space", "Next round"),
            ]),
            ("The window", [
                ("Ctrl+Enter", "Study today"),
                ("Ctrl+O", "Study a note"),
                ("Alt+1…6", "Today, Decks, Paper, Palaces, Trainers, Week"),
                ("F5", "Read the notes again"),
                ("Ctrl+K", "Claude"),
                ("Ctrl+,", "Preferences"),
            ]),
        ]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for name, rows in groups:
            box.append(Gtk.Label(label=name, xalign=0, css_classes=["shortcut-group"]))
            grid = Gtk.Grid(column_spacing=14, row_spacing=3)
            for index, (keys, what) in enumerate(rows):
                grid.attach(Gtk.Label(label=keys, xalign=1, css_classes=["shortcut-key"]),
                            0, index, 1, 1)
                grid.attach(Gtk.Label(label=what, xalign=0, wrap=True), 1, index, 1, 1)
            box.append(grid)
        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, hexpand=True)
        scroller.set_max_content_height(520)
        scroller.set_child(box)
        dialog = Adw.AlertDialog(heading="Keyboard shortcuts", body="")
        dialog.set_extra_child(scroller)
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.present(self.toasts)

    def show_about(self):
        about = Adw.AboutDialog(
            application_name="Mnemosyne",
            application_icon="mnemosyne",
            version=__version__,
            developer_name="Robin",
            comments=("The cards written in your notes, scheduled by FSRS and "
                      "reviewed on screen or on paper — with memory palaces and "
                      "the Mentalist's trainers beside them.\n\n"
                      "Nothing is stored that the notes and the review log do not "
                      "already say: the schedule is replayed from the log every time."),
            license_type=Gtk.License.MIT_X11,
            website="https://github.com/RobinCodes",
        )
        about.present(self.toasts)

    # ------------------------------------------------------------- closing --

    def _on_close(self, *_):
        self._leave_activity()
        self.assistant.cancel()
        self.geometry.save()
        self.config.save()
        return False


def _sibling(folder: str, entry: str) -> str | None:
    """Another app in the same checkout as this one, if it is there."""
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(here, folder, entry)
    return path if os.path.isfile(path) else None
