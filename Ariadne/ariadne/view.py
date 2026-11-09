"""Ariadne, as one widget: the list, the question pages, the thread canvas.

It is built without a header bar and without assuming whose window it is in,
because it lives in two: its own (window.py, which puts a header bar above
it) and Mind Palace's (room.py, which puts it in a room). Everything it can
do is an action in its own group, `room`, with the keys for them in
`ACCELERATORS`; toasts and dialogs stay inside it.

The files are watched. A question or thread changed by something else — the
Anamnesis viewer, a text editor, a sync bringing another machine's edit — is
read again, and what is on screen follows.
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import brain, claude, jobs, threads, widgets  # noqa: E402
from .config import Config  # noqa: E402
from .dialogs import QuickQuestion  # noqa: E402
from .library import Library  # noqa: E402
from .questionpage import QuestionPage  # noqa: E402
from .sidebar import Sidebar  # noqa: E402
from .threadpage import ThreadPage  # noqa: E402
from .threads import Question, Thread  # noqa: E402

ACCELERATORS = (
    ("room.keep-question", ["<Control>n"]),
    ("room.new-thread", ["<Control><Shift>n"]),
    ("room.add-thought", ["<Control>Return"]),
    ("room.arrange", ["<Control>l"]),
    ("room.zoom-in", ["<Control>plus", "<Control>equal"]),
    ("room.zoom-out", ["<Control>minus"]),
    ("room.zoom-fit", ["<Control>0"]),
    ("room.find", ["<Control>f"]),
    ("room.add-revisit", ["<Control>r"]),
    ("room.think", ["<Control><Shift>a"]),
    ("room.toggle-list", ["F9"]),
    ("room.shortcuts", ["<Control>question"]),
)

SHORTCUTS = [
    ("Questions", [
        ("Ctrl+N", "Keep a question — now, before it goes"),
        ("Ctrl+R", "Add a revisit"),
        ("Ctrl+F", "Find a question or thread"),
    ]),
    ("Threads", [
        ("Ctrl+Shift+N", "New thread"),
        ("Ctrl+Enter", "Add a thought"),
        ("Double-click", "Add a thought there, or edit one"),
        ("Drag the handle", "Draw an arrow to another thought"),
        ("Click an arrow", "Label it"),
        ("Delete", "Take away what is selected (with Undo)"),
        ("Ctrl+L", "Arrange"),
        ("Ctrl++ / Ctrl+− / Ctrl+0", "Zoom in, out, everything"),
    ]),
    ("Everywhere", [
        ("Ctrl+Shift+A", "Think with Claude"),
        ("F9", "The list"),
        ("Ctrl+?", "These keys"),
    ]),
]

DISK_SETTLE_MS = 400

# Below this the list folds away. Measured, not guessed: the list and a question page
# need about 770 pixels side by side, and a window narrower than what its content needs
# just cuts the content off.
NARROW = 780


class AriadneView(Adw.BreakpointBin):
    """The whole of Ariadne. `host` is Mind Palace's, or None in the app's own window."""

    def __init__(self, config: Config | None = None, host=None,
                 where: brain.Places | None = None):
        super().__init__(width_request=360, height_request=320)
        self.config = config or Config()
        self.host = host
        self.library = Library(where)
        self.current = None
        self.on_title = None             # the window's title follows what is shown
        self._monitors = []
        self._disk_timer = 0

        self.actions = Gio.SimpleActionGroup()
        self.insert_action_group("room", self.actions)

        self.sidebar = Sidebar(self.open_item, self.keep_question, lambda: self.new_thread())
        self.question_page = QuestionPage(self)
        self.thread_page = ThreadPage(self)
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.pages.add_named(self._empty(), "empty")
        self.pages.add_named(self.question_page, "question")
        self.pages.add_named(self.thread_page, "thread")

        self.split = Adw.OverlaySplitView(sidebar=self.sidebar, content=self.pages,
                                          min_sidebar_width=240, max_sidebar_width=340,
                                          sidebar_width_fraction=0.3)
        self.split.set_show_sidebar(bool(self.config["sidebar_visible"]))
        self.split.connect("notify::show-sidebar", lambda *_: self._list_buttons())
        self.split.connect("notify::collapsed", lambda *_: self._list_buttons())
        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_child(self.toasts)

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"max-width: {NARROW}px"))
        narrow.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(narrow)

        self._build_actions()
        self.reload()
        self._watch()
        self.sidebar.set_page(self.config["sidebar_page"])
        last = self.config["last_open"]
        if last and self.library.find(last) is not None:
            self.open_item(self.library.find(last))
        self.connect("map", lambda *_: self.check_disk())
        self._list_buttons()

    def _empty(self):
        page = Adw.StatusPage(icon_name="dialog-question-symbolic", title="What are you wondering?",
                              description="Keep a question the moment you have it. Answer it "
                                          "slowly, with a thread of thoughts — or never.")
        keep = Gtk.Button(label="Keep a Question", halign=Gtk.Align.CENTER,
                          css_classes=["pill", "suggested-action"])
        keep.connect("clicked", lambda _b: self.keep_question())
        page.set_child(keep)
        return page

    # -- actions -------------------------------------------------------------

    def _build_actions(self):
        page = self.thread_page
        for name, handler in (
                ("keep-question", self.keep_question),
                ("new-thread", lambda: self.new_thread(
                    self.current if isinstance(self.current, Question) else None)),
                ("add-thought", lambda: self._on_thread(page.add_thought)),
                ("arrange", lambda: self._on_thread(page.arrange)),
                ("zoom-in", lambda: self._on_thread(lambda: page.canvas.zoom(1.2))),
                ("zoom-out", lambda: self._on_thread(lambda: page.canvas.zoom(1 / 1.2))),
                ("zoom-fit", lambda: self._on_thread(page.canvas.fit)),
                ("find", self.find),
                ("add-revisit", self.add_revisit),
                ("think", self.think),
                ("toggle-list", lambda: self.show_list(not self.split.get_show_sidebar())),
                ("shortcuts", self.show_shortcuts)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=handler: self._guarded(fn))
            self.actions.add_action(action)
        if not claude.available():
            self.actions.remove_action("think")

    def _guarded(self, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            import traceback

            traceback.print_exc()
            widgets.error_toast(self, exc)

    def _on_thread(self, fn):
        if isinstance(self.current, Thread):
            fn()

    # -- what is shown -------------------------------------------------------

    def reload(self):
        try:
            self.library.refresh()
        except OSError as exc:
            widgets.error_toast(self, exc)
        self.refresh_list()

    def refresh_list(self):
        self.library.questions = threads.order_questions(self.library.questions)
        self.library.threads.sort(key=lambda t: t.title.casefold())
        self.sidebar.show(self.library.questions, self.library.threads, self.current)

    def open_item(self, item):
        """Show a question or a thread."""
        self.question_page.flush()
        self.thread_page.flush()
        self.current = item
        if isinstance(item, Question):
            self.question_page.show(item)
            self.pages.set_visible_child_name("question")
            self.sidebar.set_page("questions")
        elif isinstance(item, Thread):
            self.thread_page.show(item)
            self.pages.set_visible_child_name("thread")
            self.sidebar.set_page("threads")
        else:
            self.question_page.question = None
            self.thread_page.thread = None
            self.pages.set_visible_child_name("empty")
        self.sidebar.show(self.library.questions, self.library.threads, item)
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        self.config["last_open"] = str(item.path) if item is not None and item.path else ""
        title = item.title if item is not None else "Ariadne"
        if callable(self.on_title):
            self.on_title(title)
        if self.host is not None and hasattr(self.host, "set_subtitle"):
            self.host.set_subtitle(item.title if item is not None else "")

    def show_list(self, show: bool):
        self.split.set_show_sidebar(show)
        self.config["sidebar_visible"] = show

    def _list_buttons(self):
        hidden = not self.split.get_show_sidebar()
        for page in (self.question_page, self.thread_page):
            page.list_button.set_visible(hidden)

    def toast(self, text, undo=None):
        return widgets.toast(self, text, undo=undo)

    # -- writing -------------------------------------------------------------

    def save(self, item):
        try:
            self.library.save(item)
        except OSError as exc:
            widgets.error_toast(self, exc)

    def keep_question(self):
        if not self.library.exists():
            self.toast("There is no second brain on this machine to keep it in")
            return
        QuickQuestion(self._kept).present(self)

    def _kept(self, title, text, asked):
        try:
            question = threads.new_question(title, text, when=asked, where=self.library.where)
        except OSError as exc:
            widgets.error_toast(self, exc)
            return None
        self.library.questions.append(question)
        self.library.remember(question)
        self.refresh_list()
        written = question.dump()

        def undo():
            # Undo is the human act SYSTEM.md asks a deletion to be — and only
            # of a file nobody has touched since it was kept.
            try:
                with open(question.path, encoding="utf-8", newline="") as fh:
                    untouched = fh.read() == written
                # Closed first: Windows will not remove a file that is open.
                if untouched:
                    question.path.unlink()
            except OSError:
                return
            if question in self.library.questions:
                self.library.questions.remove(question)
            if self.current is question:
                self.open_item(None)
            self.refresh_list()

        self.toast("Kept", undo=undo)
        return question

    def new_thread(self, question=None, then=None):
        if not self.library.exists():
            self.toast("There is no second brain on this machine to keep it in")
            return

        def made(title):
            if not title:
                return
            try:
                thread = threads.new_thread(title, [question.title] if question else [],
                                            where=self.library.where)
            except OSError as exc:
                widgets.error_toast(self, exc)
                return
            self.library.threads.append(thread)
            self.library.remember(thread)
            self.open_item(thread)
            if then is not None:
                then(thread)

        body = (f"It will answer “{question.title}”. " if question else "") + \
            "What does it argue, in a few words? It can change later."
        widgets.ask_text(self, "A New Thread", body, "", "Start", made,
                         placeholder="Compatibilism holds")

    def link(self, thread, question):
        if question.title not in thread.answers:
            thread.answers = [*thread.answers, question.title]
            self.save(thread)
        if self.current is question:
            self.question_page.fill_threads()
        self.refresh_list()

    def retitled(self, question, old_title):
        """A question renamed: the threads that answered the old title follow it."""
        old = brain.key(old_title)
        for thread in self.library.threads:
            if any(brain.key(title) == old for title in thread.answers):
                thread.answers = [question.title if brain.key(title) == old else title
                                  for title in thread.answers]
                self.save(thread)

    # -- links and Claude ----------------------------------------------------

    def follow_link(self, target, wiki=True):
        if not wiki:
            Gtk.UriLauncher.new(target).launch(None, None, None)
            return
        near = self.current.path if self.current is not None else None
        path = self.library.resolve(target, near)
        if path is None:
            self.toast(f"Nothing is called “{target}” yet")
            return
        item = self.library.find(path)
        if item is not None:
            self.open_item(item)
        else:
            self.open_elsewhere(path)

    def open_elsewhere(self, path):
        """A note that is not Ariadne's: the palace's room for it, or the desktop's app."""
        if self.host is not None and hasattr(self.host, "open") and self.host.open(str(path)):
            return
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(path))).launch(None, None, None)

    def think(self):
        """Think with Claude about what is in front: a thought, a thread, a question."""
        if isinstance(self.current, Thread):
            self.thread_page.think()
        elif isinstance(self.current, Question):
            question = self.current
            found = self.library.threads_for(question)
            if found:
                self.open_item(found[0])
                self.thread_page.think(question)
            else:
                self.new_thread(question, then=lambda _t: self.thread_page.think(question))

    def find(self):
        self.show_list(True)
        self.sidebar.search.grab_focus()

    def add_revisit(self):
        if isinstance(self.current, Question):
            self.question_page.focus_revisit()

    def show_shortcuts(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for name, rows in SHORTCUTS:
            box.append(widgets.heading(name))
            grid = Gtk.Grid(column_spacing=14, row_spacing=3)
            for index, (keys, what) in enumerate(rows):
                grid.attach(Gtk.Label(label=keys, xalign=1, css_classes=["ariadne-key"]),
                            0, index, 1, 1)
                grid.attach(Gtk.Label(label=what, xalign=0, wrap=True), 1, index, 1, 1)
            box.append(grid)
        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=520)
        scroller.set_child(box)
        dialog = Adw.AlertDialog(heading="Keyboard shortcuts", body="")
        dialog.set_extra_child(scroller)
        dialog.add_response("close", "Close")
        dialog.present(self)

    # -- the disk ------------------------------------------------------------

    def _watch(self):
        if not self.library.exists():
            return
        for folder in (self.library.questions_folder, self.library.threads_folder):
            try:
                folder.mkdir(parents=True, exist_ok=True)
                monitor = Gio.File.new_for_path(str(folder)).monitor_directory(
                    Gio.FileMonitorFlags.WATCH_MOVES, None)
            except (OSError, GLib.Error):
                continue
            monitor.connect("changed", lambda *_: self._disk_changed())
            self._monitors.append(monitor)

    def _disk_changed(self):
        if self._disk_timer:
            GLib.source_remove(self._disk_timer)
        self._disk_timer = GLib.timeout_add(DISK_SETTLE_MS, self._disk_tick)

    def _disk_tick(self):
        self._disk_timer = 0
        self.check_disk()
        return GLib.SOURCE_REMOVE

    def check_disk(self):
        """Read again whatever changed on disk, keeping what is on screen in step."""
        try:
            if self.library.on_disk() == self.library._stamps:
                return
        except OSError:
            # A folder can refuse to be listed for a moment on Windows, while a
            # virus scanner or a sync client has it. The next look will see it.
            return
        self.question_page.flush()
        self.thread_page.flush()
        current = self.current
        elsewhere = current is not None and self.library.changed_elsewhere(current)
        self.library.refresh()
        if current is not None:
            fresh = self.library.find(current.path) if current.path else None
            if fresh is None:
                self.open_item(None)
                self.toast(f"“{current.title}” is gone from the brain")
            elif elsewhere:
                self.current = fresh
                if isinstance(fresh, Thread):
                    self.thread_page.thread = fresh
                    self.thread_page.show(fresh, keep_view=True)
                else:
                    self.question_page.question = None
                    self.question_page.show(fresh)
                self.toast(f"“{fresh.title}” changed elsewhere, and was read again")
            else:
                # Unchanged: the object on screen stays the one in the list.
                bag = self.library.questions if isinstance(current, Question) \
                    else self.library.threads
                bag[bag.index(fresh)] = current
        self.refresh_list()

    # -- for Mind Palace and the window ----------------------------------------

    def open(self, path) -> bool:
        """Open a question's or a thread's file; False for anything else."""
        path = Path(str(path))
        item = self.library.find(path)
        if item is None and path.is_file() and path.suffix.lower() == ".md":
            try:
                loaded = threads.load(path)
            except (OSError, UnicodeDecodeError):
                return False
            if str(loaded.front.get("kind", "")) not in ("question", "thread"):
                return False
            item = loaded
        if item is None:
            return False
        self.open_item(item)
        return True

    def save_state(self):
        self.question_page.flush()
        self.thread_page.flush()
        self.config["sidebar_visible"] = bool(self.split.get_show_sidebar())
        self.config["sidebar_page"] = self.sidebar.page()
        self.config.save()


def refresh_notes_once():
    """The Order's note index, brought up to date once per process, off the main loop."""
    if getattr(refresh_notes_once, "started", False):
        return
    refresh_notes_once.started = True
    jobs.run(lambda: brain.Notes.shared().refresh(), None, lambda _exc: None)
