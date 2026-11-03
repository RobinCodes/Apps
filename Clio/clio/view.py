"""Clio, as one widget: the list of boards, a board on its axis or as a list.

It is built without a header bar and without assuming whose window it is in,
because it lives in two: its own (window.py, which puts a header bar above it)
and Mind Palace's (room.py, which puts it in a room). Everything it can do is an
action in its own group, `room`, with the keys for them in `ACCELERATORS`; toasts
and dialogs stay inside it.

The files are the truth and are watched. A board changed by something else — the
Anamnesis viewer, a text editor, a sync bringing another machine's edit — is read
again, and what is on screen follows, staying where it was on the axis.
"""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import asking, brain, claude, editing, jobs, widgets  # noqa: E402
from .boardcanvas import BoardCanvas  # noqa: E402
from .config import Config  # noqa: E402
from .dialogs import BoardDialog, ItemDialog, NewBoardDialog  # noqa: E402
from .library import Library  # noqa: E402
from .listmode import ListMode  # noqa: E402
from .sidebar import Sidebar  # noqa: E402
from .timelines import Board, Item, When, today_days  # noqa: E402

ACCELERATORS = (
    ("room.new-event", ["<Control>n"]),
    ("room.new-span", ["<Control>b"]),
    ("room.new-board", ["<Control><Shift>n"]),
    ("room.group", ["<Control>g"]),
    ("room.jump", ["<Control>j"]),
    ("room.toggle-mode", ["<Control>l"]),
    ("room.zoom-in", ["<Control>plus", "<Control>equal"]),
    ("room.zoom-out", ["<Control>minus"]),
    ("room.zoom-fit", ["<Control>0"]),
    ("room.board-settings", ["<Control>e"]),
    ("room.ask", ["<Control><Shift>a"]),
    ("room.toggle-list", ["F9"]),
    ("room.shortcuts", ["<Control>question"]),
)

SHORTCUTS = [
    ("On a board", [
        ("Ctrl+N", "New event"),
        ("Ctrl+B", "New span, the } brace"),
        ("Double-click", "Add an event there, or edit what is there"),
        ("Shift+click", "Add to the selection"),
        ("Shift+drag", "Select everything across a stretch"),
        ("Ctrl+G", "Group what is selected into a span"),
        ("Delete", "Take away what is selected (with Undo)"),
        ("Ctrl+E", "This board: its name, born, categories"),
    ]),
    ("Moving", [
        ("Drag / scroll", "Along the axis"),
        ("Ctrl+scroll / pinch", "Zoom, at the pointer"),
        ("Ctrl++ / Ctrl+− / Ctrl+0", "Zoom in, out, everything"),
        ("Ctrl+J", "Jump to a date"),
        ("Ctrl+L", "The axis or the list"),
    ]),
    ("Everywhere", [
        ("Ctrl+Shift+N", "New board"),
        ("Ctrl+Shift+A", "Ask Claude"),
        ("F9", "The list of boards"),
        ("Ctrl+?", "These keys"),
    ]),
]

DISK_SETTLE_MS = 400


class ClioView(Adw.BreakpointBin):
    """The whole of Clio. `host` is Mind Palace's, or None in the app's own window."""

    def __init__(self, config: Config | None = None, host=None,
                 where: brain.Places | None = None):
        super().__init__(width_request=360, height_request=320)
        self.config = config or Config()
        self.host = host
        self.library = Library(where)
        self.current: Board | None = None
        self.on_title = None             # the window's title follows what is shown
        self.hidden: dict[str, set[str]] = {}   # hidden categories, by board, this session
        self._views: dict[str, tuple] = {}      # where each board was on the axis
        self._monitors = []
        self._disk_timer = 0
        self.command = None              # the suite's stand-in for Claude
        self._ask = None
        self.proposal: list[Item] = []
        self.last_undo = None

        self.actions = Gio.SimpleActionGroup()
        self.insert_action_group("room", self.actions)

        self.sidebar = Sidebar(self.open_board, self.new_board)
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.pages.add_named(self._empty(), "empty")
        self.pages.add_named(self._board_page(), "board")

        self.split = Adw.OverlaySplitView(sidebar=self.sidebar, content=self.pages,
                                          min_sidebar_width=220, max_sidebar_width=320,
                                          sidebar_width_fraction=0.26)
        self.split.set_show_sidebar(bool(self.config["sidebar_visible"]))
        self.split.connect("notify::show-sidebar", lambda *_: self._list_button_state())
        self.split.connect("notify::collapsed", lambda *_: self._list_button_state())
        self.toasts = Adw.ToastOverlay(child=self.split)
        self.set_child(self.toasts)

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640px"))
        narrow.add_setter(self.split, "collapsed", True)
        narrow.add_setter(self.ask_button, "visible", False)
        self.add_breakpoint(narrow)

        self._build_actions()
        self.reload()
        self._watch()
        last = self.config["last_open"]
        if last and self.library.find(last) is not None:
            self.open_board(self.library.find(last))
        self.connect("map", lambda *_: self.check_disk())
        self._list_button_state()

    def _empty(self):
        self.status = Adw.StatusPage(
            icon_name="x-office-calendar-symbolic", title="What happened, and when?",
            description="A board is a timeline to write against: a life, a history course, "
                        "a project.")
        make = Gtk.Button(label="New Board", halign=Gtk.Align.CENTER,
                          css_classes=["pill", "suggested-action"])
        make.connect("clicked", lambda _b: self.new_board())
        self.status.set_child(make)
        return self.status

    def _board_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2,
                      css_classes=["clio-board-bar"])
        self.list_button = Gtk.Button(icon_name="sidebar-show-symbolic", css_classes=["flat"],
                                      tooltip_text="The boards (F9)")
        self.list_button.connect("clicked", lambda _b: self.show_list(True))
        bar.append(self.list_button)
        self.title_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, margin_start=6,
                                     width_chars=4, css_classes=["clio-board-title"])
        bar.append(self.title_label)

        self.filter_popover = Gtk.Popover()
        self.filter_button = Gtk.MenuButton(label="Filter", css_classes=["flat"],
                                            popover=self.filter_popover,
                                            tooltip_text="Which categories are shown")
        self.filter_popover.connect("show", lambda _p: self.fill_filter())
        bar.append(self.filter_button)

        for icon, tip, action in (
                ("zoom-out-symbolic", "Zoom out (Ctrl+−)", "room.zoom-out"),
                ("zoom-in-symbolic", "Zoom in (Ctrl++)", "room.zoom-in"),
                ("zoom-fit-best-symbolic", "Everything in view (Ctrl+0)", "room.zoom-fit"),
                ("go-jump-symbolic", "Jump to a date (Ctrl+J)", "room.jump")):
            bar.append(Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=["flat"],
                                  action_name=action))
        self.mode_button = Gtk.ToggleButton(icon_name="view-list-symbolic", css_classes=["flat"],
                                            tooltip_text="As a list (Ctrl+L)")
        self.mode_button.connect("toggled", lambda _b: self._mode_changed())
        bar.append(self.mode_button)
        self.ask_button = Gtk.Button(label="Ask Claude…", css_classes=["flat"],
                                     tooltip_text="Ask Claude for events to add (Ctrl+Shift+A)",
                                     visible=claude.available())
        self.ask_button.connect("clicked", lambda _b: self.ask_claude())
        bar.append(self.ask_button)
        bar.append(Gtk.MenuButton(icon_name="view-more-symbolic", css_classes=["flat"],
                                  menu_model=self._menu(), tooltip_text="More"))
        page.append(bar)

        self.canvas = BoardCanvas(on_add=self._add_at, on_edit=self.edit_item,
                                  on_delete=self.delete_items, on_select=self._selected)
        self.list_mode = ListMode(self.edit_item)
        self.modes = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self.modes.add_named(self.canvas, "axis")
        self.modes.add_named(self.list_mode, "list")
        page.append(self.modes)

        # What can be done with what is selected.
        self.action_bar = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                          css_classes=["clio-action-bar"])
        self.selected_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        actions.append(self.selected_label)
        self.group_button = Gtk.Button(label="Group into a Span", action_name="room.group",
                                       css_classes=["flat"], tooltip_text="Ctrl+G")
        actions.append(self.group_button)
        self.edit_button = Gtk.Button(label="Edit", css_classes=["flat"])
        self.edit_button.connect("clicked", lambda _b: self._edit_selected())
        actions.append(self.edit_button)
        self.action_bar.set_child(actions)
        page.append(self.action_bar)

        # What Claude has proposed, until it is kept or not.
        self.ghost_bar = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        ghost = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                        css_classes=["clio-ghost-bar"])
        self.ghost_label = Gtk.Label(xalign=0, hexpand=True, wrap=True,
                                     css_classes=["clio-row-detail"])
        ghost.append(self.ghost_label)
        self.stop_button = Gtk.Button(label="Stop", css_classes=["flat"])
        self.stop_button.connect("clicked", lambda _b: self.stop_asking())
        ghost.append(self.stop_button)
        self.discard_button = Gtk.Button(label="Discard", css_classes=["flat"])
        self.discard_button.connect("clicked", lambda _b: self.discard())
        ghost.append(self.discard_button)
        self.accept_button = Gtk.Button(label="Keep Them", css_classes=["suggested-action"])
        self.accept_button.connect("clicked", lambda _b: self.accept())
        ghost.append(self.accept_button)
        self.ghost_bar.set_child(ghost)
        page.append(self.ghost_bar)
        return page

    def _menu(self):
        menu = Gio.Menu()
        making = Gio.Menu()
        making.append("New Event", "room.new-event")
        making.append("New Span", "room.new-span")
        making.append("New Board", "room.new-board")
        menu.append_section(None, making)
        board = Gio.Menu()
        board.append("This Board and Its Categories", "room.board-settings")
        item = Gio.MenuItem.new("Ask Claude…", "room.ask")
        item.set_attribute_value("hidden-when", GLib.Variant.new_string("action-missing"))
        board.append_item(item)
        menu.append_section(None, board)
        about = Gio.Menu()
        about.append("Keyboard Shortcuts", "room.shortcuts")
        menu.append_section(None, about)
        return menu

    # -- actions -------------------------------------------------------------

    def _build_actions(self):
        canvas = self.canvas
        for name, handler in (
                ("new-event", lambda: self._on_board(self.new_event)),
                ("new-span", lambda: self._on_board(self.new_span)),
                ("new-board", self.new_board),
                ("group", lambda: self._on_board(self.group_selected)),
                ("jump", lambda: self._on_board(self.ask_jump)),
                ("toggle-mode", lambda: self._on_board(
                    lambda: self.mode_button.set_active(not self.mode_button.get_active()))),
                ("zoom-in", lambda: self._on_board(lambda: canvas.zoom(1.5))),
                ("zoom-out", lambda: self._on_board(lambda: canvas.zoom(1 / 1.5))),
                ("zoom-fit", lambda: self._on_board(canvas.fit)),
                ("board-settings", lambda: self._on_board(self.board_settings)),
                ("ask", lambda: self._on_board(self.ask_claude)),
                ("toggle-list", lambda: self.show_list(not self.split.get_show_sidebar())),
                ("shortcuts", self.show_shortcuts)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=handler: self._guarded(fn))
            self.actions.add_action(action)
        if not claude.available():
            self.actions.remove_action("ask")

    def _guarded(self, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            import traceback

            traceback.print_exc()
            widgets.error_toast(self, exc)

    def _on_board(self, fn):
        if self.current is not None:
            fn()

    # -- what is shown -------------------------------------------------------

    def reload(self):
        try:
            self.library.refresh()
        except OSError as exc:
            widgets.error_toast(self, exc)
        self.refresh_list()

    def refresh_list(self):
        self.library.boards.sort(key=lambda b: b.title.casefold())
        self.sidebar.show_boards(self.library.boards, self.current)

    def open_board(self, board):
        """Show a board, where it was last looked at; or none."""
        if board is self.current and board is not None:
            if self.split.get_collapsed():
                self.split.set_show_sidebar(False)
            return
        self._remember_view()
        self.stop_asking()
        self._drop_proposal()
        self.current = board
        self._show_current()
        self.refresh_list()
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        self.config["last_open"] = str(board.path) if board is not None and board.path else ""
        title = board.title if board is not None else "Clio"
        if callable(self.on_title):
            self.on_title(title)
        if self.host is not None and hasattr(self.host, "set_subtitle"):
            self.host.set_subtitle(board.title if board is not None else "")

    def _show_current(self, keep_view=None):
        board = self.current
        if board is None:
            self.pages.set_visible_child_name("empty")
            return
        self.title_label.set_label(board.title)
        self.canvas.set_board(board, keep_view or self._views.get(str(board.path)))
        self.canvas.set_hidden(self.hidden.get(str(board.path), set()))
        self.pages.set_visible_child_name("board")
        self.mode_button.set_active(bool(self.config["list_mode"]))
        self._mode_changed()
        self._selected([])

    def _remember_view(self):
        if self.current is not None and self.current.path:
            self._views[str(self.current.path)] = self.canvas.view_state()

    def _mode_changed(self):
        listing = self.mode_button.get_active()
        self.config["list_mode"] = listing
        self.modes.set_visible_child_name("list" if listing else "axis")
        if listing:
            self.canvas.select([])
            self._fill_list()
        self.mode_button.set_tooltip_text("On the axis (Ctrl+L)" if listing
                                          else "As a list (Ctrl+L)")

    def _fill_list(self):
        self.list_mode.show_board(self.current, self._hidden_here(), self.proposal)

    def _hidden_here(self) -> set[str]:
        return self.hidden.get(str(self.current.path), set()) if self.current else set()

    def changed(self):
        """The board's contents changed: everything that shows them follows."""
        self.canvas.refresh()
        if self.mode_button.get_active():
            self._fill_list()
        self.refresh_list()

    def show_list(self, show: bool):
        self.split.set_show_sidebar(show)
        self.config["sidebar_visible"] = show

    def _list_button_state(self):
        # Its own window has this in the header bar; the palace's has no such thing.
        self.list_button.set_visible(self.host is not None and not self.split.get_show_sidebar())

    def toast(self, text, undo=None):
        return widgets.toast(self, text, undo=undo)

    def fill_filter(self):
        """The categories, each with a check, for the filter button's popover."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6,
                      margin_bottom=6, margin_start=6, margin_end=6)
        board = self.current
        names = board.categories() if board is not None else []
        if not names:
            box.append(Gtk.Label(label="No categories yet", css_classes=["clio-row-detail"]))
        hidden = self._hidden_here()
        for name in names:
            check = Gtk.CheckButton(active=name not in hidden)
            label = Gtk.Label(use_markup=True, xalign=0)
            colour = GLib.markup_escape_text(board.category_colour(name))
            label.set_markup(f'<span foreground="{colour}">●</span> '
                             f"{GLib.markup_escape_text(name)}")
            check.set_child(label)
            check.connect("toggled", lambda c, n=name: self.set_category_shown(n, c.get_active()))
            box.append(check)
        self.filter_popover.set_child(box)

    def set_category_shown(self, name, show):
        if self.current is None:
            return
        hidden = set(self._hidden_here())
        (hidden.discard if show else hidden.add)(name)
        self.hidden[str(self.current.path)] = hidden
        self.canvas.set_hidden(hidden)
        self.filter_button.set_label("Filter" if not hidden else f"Filter ({len(hidden)} off)")
        if self.mode_button.get_active():
            self._fill_list()

    def _selected(self, items):
        real = [i for i in items if not self.canvas.is_ghost(i)]
        count = len(items)
        if count == 0:
            self.action_bar.set_reveal_child(False)
            return
        if count == 1:
            self.selected_label.set_label(items[0].title or "Untitled")
        else:
            self.selected_label.set_label(f"{count} selected")
        self.group_button.set_visible(len(real) >= 2)
        self.edit_button.set_visible(count == 1)
        self.action_bar.set_reveal_child(True)

    def _edit_selected(self):
        if len(self.canvas.selection) == 1:
            self.edit_item(self.canvas.selection[0])

    # -- writing -------------------------------------------------------------

    def save(self, board=None):
        board = board or self.current
        if board is None:
            return False
        try:
            self.library.save(board)
        except OSError as exc:
            widgets.error_toast(self, exc)
            return False
        return True

    def new_board(self):
        if not self.library.exists():
            self.toast("There is no second brain on this machine to keep it in")
            return
        NewBoardDialog(self._make_board).present(self)

    def _make_board(self, title, born):
        try:
            board = self.library.new_board(title, born)
        except OSError as exc:
            widgets.error_toast(self, exc)
            return None
        self.open_board(board)
        return board

    def _default_date(self) -> When:
        """Today, if today is what is being looked at; else the middle of the view."""
        first, last = self.canvas.axis.span()
        today = today_days()
        day = today if first <= today <= last else int(self.canvas.axis.centre)
        return When.from_days(day, self.canvas.axis.precision())

    def new_event(self, when=None):
        self._new(Item(when or self._default_date()))

    def new_span(self):
        self._new(Item(self._default_date(), span=True))

    def _add_at(self, when):
        self.new_event(when)

    def _new(self, draft: Item):
        board = self.current
        ItemDialog(board, draft, lambda fields: self._save_new(board, fields),
                   on_link=self.follow_link).present(self)

    def _save_new(self, board, fields):
        board = self._still(board)
        item = Item(fields["start"], fields["title"], end=fields["end"], span=fields["span"],
                    categories=fields["categories"], milestone=fields["milestone"],
                    notes=fields["notes"])
        board.add(item)
        self.save(board)
        if board is self.current:
            self.changed()
        return item

    def _still(self, board):
        """The board as it is now: it may have been read again while a dialog was open."""
        if board in self.library.boards or board.path is None:
            return board
        return self.library.find(board.path) or board

    def edit_item(self, item):
        board = self.current
        if self.canvas.is_ghost(item):
            ItemDialog(board, item, lambda fields: self._save_ghost(item, fields),
                       on_delete=lambda: self.delete_items([item])).present(self)
            return
        ItemDialog(board, item, lambda fields: self._save_edit(board, item, fields),
                   on_delete=lambda: self.delete_items([item]),
                   on_link=self.follow_link).present(self)

    def _save_edit(self, board, item, fields):
        fresh = self._still(board)
        if fresh is not board:
            item = editing.find_again(fresh, item)
            if item is None:
                return self._save_new(fresh, fields)
        editing.write_item(fresh, item, **fields)
        self.save(fresh)
        if fresh is self.current:
            self.changed()
        return item

    def _save_ghost(self, item, fields):
        editing.write_item(self.current, item, **fields)
        self.changed()

    def delete_items(self, items):
        ghosts = [i for i in items if self.canvas.is_ghost(i)]
        if ghosts:
            self.proposal = [p for p in self.proposal if not any(p is g for g in ghosts)]
            self.canvas.ghosts = list(self.proposal)
            if self.proposal:
                self._say_proposal()
            else:
                self.discard()
        real = [i for i in items if not any(i is g for g in ghosts)]
        if not real:
            self.changed()
            return
        board = self.current
        taken = [(item, board.remove(item)) for item in real]
        taken = [(item, index) for item, index in taken if index >= 0]
        self.save(board)
        self.changed()
        text = f"“{real[0].title}” taken away" if len(real) == 1 else f"{len(real)} taken away"

        def undo():
            fresh = self._still(board)
            for item, index in reversed(taken):
                if fresh is board:
                    fresh.restore(item, index)
                else:
                    fresh.add(item)
            self.save(fresh)
            if fresh is self.current:
                self.changed()

        self.last_undo = undo
        self.toast(text, undo=undo)

    def group_selected(self):
        """A span around what is selected: filled in but for its name, and not yet kept."""
        chosen = [i for i in self.canvas.selection if not self.canvas.is_ghost(i)]
        if len(chosen) < 2:
            self.toast("Select two or more things first (Shift+click, or Shift+drag)")
            return
        self._new(editing.span_around(chosen))

    def ask_jump(self):
        widgets.ask_text(self, "Jump to a Date", "A year, a month or a day: 1848, 2017-09, "
                         "-0044-03-15, or today.", "", "Go", self.jump, placeholder="1241")

    def jump(self, text):
        when = editing.read_jump(text)
        if when is None:
            self.toast(editing.DATE_HELP)
            return False
        if self.mode_button.get_active():
            self.mode_button.set_active(False)
        self.canvas.jump(when)
        return True

    def board_settings(self):
        board = self.current
        BoardDialog(board, lambda *values: self._save_settings(board, *values)).present(self)

    def _save_settings(self, board, title, born, renames, rows):
        board = self._still(board)
        if title != board.title:
            board.title = title
        if str(born or "") != str(board.born or ""):
            board.born = born
        for old, new in renames:
            editing.rename_category(board, old, new)
        editing.write_categories(board, rows)
        self.save(board)
        if board is self.current:
            self.title_label.set_label(board.title)
            self.changed()
            if callable(self.on_title):
                self.on_title(board.title)
            if self.host is not None and hasattr(self.host, "set_subtitle"):
                self.host.set_subtitle(board.title)

    # -- links and Claude ----------------------------------------------------

    def follow_link(self, target):
        near = self.current.path if self.current is not None else None
        path = self.library.resolve(target, near)
        if path is None:
            self.toast(f"Nothing is called “{target}” yet")
            return
        board = self.library.find(path)
        if board is not None:
            self.open_board(board)
        else:
            self.open_elsewhere(path)

    def open_elsewhere(self, path):
        """A note is not Clio's: the palace's room for it, or the desktop's app."""
        if self.host is not None and hasattr(self.host, "open") and self.host.open(str(path)):
            return
        Gtk.FileLauncher.new(Gio.File.new_for_path(str(path))).launch(None, None, None)

    def ask_claude(self):
        """"Add the Árpád kings": what comes back is ghosts on the axis, to keep or not."""
        widgets.ask_text(self, "Ask Claude",
                         "What should be on this board? What comes back is shown dashed on the "
                         "axis, and nothing is added until you keep it.",
                         "", "Ask", self.ask, placeholder="Add the Árpád kings")

    def ask(self, request):
        if not request or self.current is None:
            return
        self.stop_asking()
        self._drop_proposal()
        board = self.current
        files = [board.path] if board.path else []
        ask = claude.Ask(asking.prompt(board, request), files=files, command=self.command)
        ask.on_done = lambda ok, result, session: GLib.idle_add(
            self._answered, ask, board, ok, result)
        self._ask = ask
        self._say("Claude is reading the board…", running=True)
        ask.start()

    def _answered(self, ask, board, ok, result):
        if ask is not self._ask:
            return GLib.SOURCE_REMOVE
        self._ask = None
        if board is not self.current:
            self.ghost_bar.set_reveal_child(False)
            return GLib.SOURCE_REMOVE
        if not ok:
            self._say("Stopped." if result == "cancelled" else f"Claude could not answer: {result}",
                      done=True)
            return GLib.SOURCE_REMOVE
        self.proposal = asking.read(result, board)
        if not self.proposal:
            self._say("Claude proposed nothing the board does not have.", done=True)
            return GLib.SOURCE_REMOVE
        self.canvas.set_ghosts(self.proposal)
        if self.mode_button.get_active():
            self._fill_list()
        self._say_proposal()
        return GLib.SOURCE_REMOVE

    def _say_proposal(self):
        count = len(self.proposal)
        self._say(f"Claude proposes {count} {'thing' if count == 1 else 'things'}, shown dashed. "
                  f"Select one and press Delete to leave it out.", proposal=True)

    def _say(self, text, running=False, proposal=False, done=False):
        self.ghost_label.set_text(text)
        self.stop_button.set_visible(running)
        self.accept_button.set_visible(proposal)
        self.discard_button.set_visible(proposal or done)
        self.discard_button.set_label("Discard" if proposal else "Close")
        self.ghost_bar.set_reveal_child(True)

    def stop_asking(self):
        if self._ask is not None:
            ask, self._ask = self._ask, None
            ask.cancel()
        self.ghost_bar.set_reveal_child(False)

    def _drop_proposal(self):
        self.proposal = []
        self.canvas.clear_ghosts()

    def discard(self):
        self._drop_proposal()
        self.ghost_bar.set_reveal_child(False)
        if self.mode_button.get_active():
            self._fill_list()

    def accept(self):
        board = self._still(self.current)
        kept = list(self.proposal)
        self._drop_proposal()
        self.ghost_bar.set_reveal_child(False)
        for item in kept:
            board.add(item)
        self.save(board)
        self.changed()

        def undo():
            for item in kept:
                board.remove(item)
            self.save(board)
            self.changed()

        self.toast(f"{len(kept)} added to the board", undo=undo)

    def show_shortcuts(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for name, rows in SHORTCUTS:
            box.append(widgets.heading(name))
            grid = Gtk.Grid(column_spacing=14, row_spacing=3)
            for index, (keys, what) in enumerate(rows):
                grid.attach(Gtk.Label(label=keys, xalign=1, css_classes=["clio-key"]),
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
        folder = self.library.folder
        try:
            folder.mkdir(parents=True, exist_ok=True)
            monitor = Gio.File.new_for_path(str(folder)).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
        except (OSError, GLib.Error):
            return
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
        if self.library.on_disk() == self.library._stamps:
            return
        current = self.current
        elsewhere = current is not None and self.library.changed_elsewhere(current)
        self.library.refresh()
        if current is not None:
            fresh = self.library.find(current.path) if current.path else None
            outside = current.path is not None and not self.library.holds(current.path)
            if fresh is None and outside:
                pass                        # opened from elsewhere: not this folder's to judge
            elif fresh is None:
                self.open_board(None)
                self.toast(f"“{current.title}” is gone from the brain")
            elif elsewhere:
                view = self.canvas.view_state()
                ghosts = list(self.proposal)
                self.current = fresh
                self._show_current(keep_view=view)
                if ghosts:
                    self.canvas.ghosts = ghosts
                self.toast(f"“{fresh.title}” changed elsewhere, and was read again")
            else:
                # Unchanged: the object on screen stays the one in the list.
                boards = self.library.boards
                boards[boards.index(fresh)] = current
        self.refresh_list()

    # -- for Mind Palace and the window ----------------------------------------

    def open(self, path) -> bool:
        """Open a board's file; False for anything else."""
        path = Path(str(path))
        board = self.library.find(path)
        if board is None and path.is_file() and path.suffix.lower() == ".md":
            try:
                loaded = Board.load(path)
            except (OSError, UnicodeDecodeError):
                return False
            if str(loaded.front.get("kind", "")) != "timeline":
                return False
            board = loaded
        if board is None:
            return False
        self.open_board(board)
        return True

    def save_state(self):
        self.config["sidebar_visible"] = bool(self.split.get_show_sidebar())
        self.config["list_mode"] = bool(self.mode_button.get_active())
        self.config.save()

    def cancel_work(self):
        self.stop_asking()


def refresh_notes_once():
    """The Order's note index, brought up to date once per process, off the main loop."""
    if getattr(refresh_notes_once, "started", False):
        return
    refresh_notes_once.started = True
    jobs.run(lambda: brain.Notes.shared().refresh(), None, lambda _exc: None)

