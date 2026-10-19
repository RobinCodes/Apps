"""The main window: a list of sessions on the left, one conversation on the right.

Sessions are the organising idea. Each row is a separate Claude Code
conversation with its own folder, model and permission mode, and the coloured
dot tells you what it is doing — working, waiting on you, idle, or asleep with
its memory handed back. Switching between them is instant even when the child
process behind one has been reclaimed, because the transcript lives here and
the process is only ever borrowed.

Only a few conversation views are kept built at once. Rebuilding one from its
transcript is fast; keeping a dozen fully realised widget trees on a machine
with 4 GB is not.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import config, housestyle, widgets  # noqa: E402
from .backend import MODELS, PERMISSION_MODES, model_label  # noqa: E402
from .chat import ChatView  # noqa: E402
from .manager import Manager, short_path  # noqa: E402

VIEW_CACHE = 3

STATE_SUBTITLES = {
    "busy": "Working…",
    "starting": "Starting…",
    "waiting": "Waiting for a free slot",
    "ready": "Ready",
    "asleep": "Asleep",
}

STATE_DOTS = {
    "busy": ("dot-busy", "Working"),
    "starting": ("dot-busy", "Starting"),
    "waiting": ("dot-waiting", "Queued for a free slot"),
    "ready": ("dot-ready", "Live and idle"),
    "asleep": ("dot-asleep", "Asleep — resumes on your next message"),
}


class SessionRow(Gtk.ListBoxRow):
    """One conversation, what it is tagged with, and two ways to file it.

    One button and a menu, where there were three buttons. Three of them
    beside a name left the names less room than the furniture in a sidebar
    that can be dragged down to 190 px, and of the three only the archive
    earns a press of its own: it is the one that makes a chat leave the list,
    which is the thing you do often and want done in one go. The star moved
    into the menu beside it, where it sits with the section it belongs to and
    with every tag — one place, opened once, rather than two buttons and a
    list that knew about neither.

    The button sits in the row rather than appearing on hover: a button you
    cannot see is not a button you can click, and reserving the space keeps
    the names from reflowing as the pointer crosses the list. Both are drawn
    faint until the pointer is on them, so the sidebar still reads as names.
    """

    def __init__(self, session, on_menu):
        super().__init__()
        self.session = session
        self.on_menu = on_menu

        box = Gtk.Box(spacing=8, margin_top=8, margin_bottom=8, margin_start=10, margin_end=4)
        self.dot = Gtk.Box(css_classes=["dot", "dot-asleep"], valign=Gtk.Align.CENTER)
        box.append(self.dot)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                              css_classes=["session-name"])
        self.meta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE,
                              css_classes=["session-meta"])
        # A line of its own, and only for a chat that has tags: putting them
        # on the end of the line above would have them competing with the
        # folder and the model for the width that ellipsizes first.
        self.tags = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                              visible=False, css_classes=["session-tags"])
        text.append(self.name)
        text.append(self.meta)
        text.append(self.tags)
        box.append(text)

        self.spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        box.append(self.spinner)

        actions = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["row-actions"])
        self.archive = widgets.icon_button("folder-download-symbolic", "", self.toggle_archive,
                                           css=("flat", "row-action"))
        # A MenuButton rather than a popover of our own: it owns the thing it
        # opens, and the menu is built on each click, so a tag made in one
        # row's menu is in the next row's without anything having to say so.
        self.menu = Gtk.MenuButton(icon_name="view-more-symbolic",
                                   tooltip_text="Favourite, file and tag…",
                                   css_classes=["flat", "row-action"])
        self.menu.set_create_popup_func(lambda _b: self.on_menu(self))
        for button in (self.archive, self.menu):
            actions.append(button)
        box.append(actions)

        # The same menu on a right-click, and on a long press where there is
        # no right button to press: in a list of names that is the first
        # place to look for one. It opens where the button is rather than
        # where the pointer is, because the button owns the popover and one
        # menu in one place beats two ways of putting the same one up.
        secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        secondary.connect("pressed", self._open_menu)
        self.add_controller(secondary)
        held = Gtk.GestureLongPress(touch_only=True)
        held.connect("pressed", self._open_menu)
        self.add_controller(held)

        self.set_child(box)
        self.refresh()

    def _open_menu(self, *_args):
        self.menu.popup()

    # The archive is a toggle: pressing the button of a chat that is already
    # archived takes it out again, which is the only thing the same button
    # can sensibly mean the second time.
    def toggle_archive(self):
        self.session.set_group("" if self.session.meta.group == config.ARCHIVE_GROUP
                               else config.ARCHIVE_GROUP)

    def refresh(self):
        meta = self.session.meta
        self.name.set_label(meta.name or "New session")
        model = dict(MODELS).get(meta.model, meta.model)
        line = f"{os.path.basename(meta.cwd) or meta.cwd} · {model}"
        queued = len(self.session.outbox)
        if queued:
            line += f" · {queued} queued"
        self.meta.set_label(line)
        css, tooltip = STATE_DOTS.get(self.session.state, ("dot-asleep", ""))
        self.dot.set_css_classes(["dot", css])
        busy = self.session.state in ("busy", "starting")
        self.spinner.set_visible(busy)
        self.spinner.set_spinning(busy)
        pending = self.session.pending_permission
        if pending:
            tooltip = ("Waiting for your answer" if pending.get("questions")
                       else "Waiting for your approval")
            self.dot.set_css_classes(["dot", "dot-waiting"])

        tags = " ".join(config.tag_label(tag) for tag in meta.tags)
        self.tags.set_label(tags)
        self.tags.set_visible(bool(tags))

        archived = meta.group == config.ARCHIVE_GROUP
        self.archive.set_icon_name("go-up-symbolic" if archived
                                   else "folder-download-symbolic")
        self.archive.set_tooltip_text("Take out of the archive" if archived else "Archive")
        self.archive.set_css_classes(["flat", "row-action", "row-action-on"] if archived
                                     else ["flat", "row-action"])

        where = config.group_label(meta.group) if meta.group else ""
        # Tags in here as well as on the row: the row's line ellipsizes, and
        # a chat with five tags should still be able to say what they are.
        self.set_tooltip_text("\n".join(part for part in
                                        (short_path(meta.cwd), where, tags, tooltip)
                                        if part))


class ArchiveRow(Gtk.ListBoxRow):
    """The Archived section's own header, and the click that opens it.

    A section header made by `set_header_func` cannot be clicked and cannot
    exist without a row under it, and a collapsed archive needs both: one
    line that says how much is in there and opens it. So this is a row —
    unselectable, so it never becomes the conversation on screen.
    """

    def __init__(self, count, opened, on_toggle):
        super().__init__(selectable=False, activatable=True,
                         css_classes=["archive-row"])
        self.on_toggle = on_toggle
        box = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6,
                      margin_start=12, margin_end=12)
        box.append(Gtk.Image(icon_name="pan-down-symbolic" if opened
                             else "pan-end-symbolic", valign=Gtk.Align.CENTER))
        box.append(Gtk.Label(label=f"Archived · {count}", xalign=0, hexpand=True,
                             css_classes=["group-header"]))
        self.set_child(box)
        self.set_tooltip_text("Hide the archive" if opened else "Show the archive")


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, cfg, manager=None):
        super().__init__(application=app, title="Claude Desk",
                         default_width=config.DEFAULT_WINDOW_WIDTH,
                         default_height=config.DEFAULT_WINDOW_HEIGHT)
        self.cfg = cfg
        # The remembered size and maximise, applied before the first map —
        # see housestyle.Geometry for why the two are treated differently
        # under a tiling compositor.
        self.geometry = housestyle.Geometry(
            self, cfg, minimum=(config.MIN_WINDOW_WIDTH, config.MIN_WINDOW_HEIGHT))
        self.geometry.restore()
        # One Manager for the application, not one per window. It owns the
        # live Claude processes and rations them against a budget, so a
        # second one would run a second set of children over the same
        # sessions, double the budget, and save the same file from both.
        self.manager = manager if manager is not None else Manager(cfg)
        self._watch = self.manager.watch(self._on_manager_changed,
                                         self._on_manager_notice)
        self.rows = {}
        self.views = {}
        self.view_order = []
        self.current = None
        self._syncing = False
        # The sessions in the order the sidebar shows them, which is also what
        # Ctrl+1…9 counts: the nth row, not the nth session ever started.
        self.ordered = []
        # One subscription per session per window, kept so it can be taken
        # back. Subscribing inside the rebuild — which now runs whenever a
        # chat changes group — would add a listener every time and deliver
        # each change as many times as the sidebar had been rebuilt.
        self._subs = {}
        self._layout = None
        self._relayout_queued = False
        self._sections = set()
        # Which tag the list is narrowed to, "" for all of them. Per window
        # and not saved: a filter is where you are in a session's work, not a
        # setting, and a sidebar that opened showing a tenth of the chats
        # would look like the rest had been lost.
        self.tag_filter = ""
        self._tag_bar_state = None

        self.toasts = Adw.ToastOverlay()
        # A Paned rather than an Adw.OverlaySplitView, for the one thing the
        # split view has no handle for: dragging the boundary. What the split
        # view was doing here it does with the panes' own style classes, and
        # the Paned adds a pointer that changes shape over the seam, keyboard
        # resizing with F8, and a handle wide enough to aim at.
        sidebar, content = self._build_sidebar(), self._build_content()
        sidebar.add_css_class("sidebar-pane")
        sidebar.set_size_request(config.MIN_SIDEBAR_WIDTH, -1)
        content.add_css_class("content-pane")
        content.set_size_request(config.MIN_CHAT_WIDTH, -1)

        # resize_start_child=False keeps the sidebar the width you left it at
        # when the window grows; the conversation takes the new space, which
        # is where it is wanted. shrink=False on both is what makes the two
        # minimums above the real ends of the drag rather than advisory.
        self.paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True,
            start_child=sidebar, end_child=content,
            resize_start_child=False, shrink_start_child=False,
            resize_end_child=True, shrink_end_child=False,
        )
        self.paned.set_position(cfg.sidebar_width)
        self.paned.connect("notify::position", self._on_sidebar_dragged)
        self._width_timer = 0

        # Below this the conversation's header gives up its dropdowns, which
        # is what lets the window be narrower than they are wide. It has to be
        # above the header's own un-broken minimum, or there would be a band
        # of widths the window can be asked for and cannot honour; it is well
        # above it, because a window with no room for the dropdowns also has
        # no room to drag the sidebar, and the extra row costs only its own
        # height. AdwBreakpointBin measures with its breakpoints in hand, so
        # adding this is what drops the window's own reported minimum.
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 1000px"))
        narrow.connect("apply", lambda _b: self._set_compact(True))
        narrow.connect("unapply", lambda _b: self._set_compact(False))
        self.add_breakpoint(narrow)

        self.toasts.set_child(self.paned)
        # The house style: the window paints nothing and everything sits on
        # one rounded, translucent slab of glass inside it. `self.glazing` is
        # what the Look page calls back into when a setting changes.
        self.glazing = housestyle.Glazing(self, self.cfg.house,
                                          extra=widgets.CSS,
                                          app_class="claude-desk")
        self.glazing.wrap(self.toasts)

        self._install_shortcuts()
        self.connect("close-request", self._on_close)

        self._rebuild_sidebar()
        # A chat you archived is not the one to open the window on, unless it
        # is the only kind you have left.
        ranked = self.manager.ordered_sessions()
        shown = [s for s in ranked
                 if s.meta.group != config.ARCHIVE_GROUP or cfg.show_archived] or ranked
        first = next((s for s in shown if s.meta.uid == cfg.active_uid),
                     shown[0] if shown else None)
        if first is None:
            first = self.manager.new_session()
            self._rebuild_sidebar()
        self.select(first)

    # ---------------------------------------------------------------- build --

    def _build_sidebar(self):
        # Adw.HeaderBar splits the window buttons between two panes by itself,
        # but only when it can find an Adw split view above it — in a plain
        # Paned each header believes it is the only one and reserves the whole
        # decoration layout, which both duplicates the close button and makes
        # the pane as wide as two sets of controls before anything is in it.
        # So the split is made here instead: the near side on the left header,
        # the far side on the right one, which is what the split view did.
        header = Adw.HeaderBar(css_classes=["flat"], show_end_title_buttons=False)
        header.set_title_widget(Adw.WindowTitle(title="Sessions"))
        header.pack_start(widgets.icon_button("list-add-symbolic", "New session (Ctrl+N)",
                                              self.new_session))

        menu = Gio.Menu()
        menu.append("New Window", "app.new-window")
        menu.append("Preferences", "win.preferences")
        menu.append("About Claude Desk", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Menu"))

        self.listbox = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._on_row_selected)
        # The archive line is a row, not a header, so it has to be activated
        # rather than selected -- see ArchiveRow.
        self.listbox.connect("row-activated", self._on_row_activated)
        self.listbox.set_header_func(self._group_header)
        # Only ever seen with a tag filter on and nothing wearing it: without
        # a word in it an empty list looks like something broke rather than
        # like an answer.
        self.listbox.set_placeholder(Gtk.Label(label="Nothing is tagged that",
                                               css_classes=["dim"], margin_top=18,
                                               margin_start=12, margin_end=12,
                                               wrap=True, justify=Gtk.Justification.CENTER))

        scroller = Gtk.ScrolledWindow(vexpand=True, child=self.listbox)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # The filter: one chip per tag in use, and the only thing in the
        # sidebar that is not a chat. Hidden outright until something is
        # tagged, so a sidebar with no tags in it looks exactly as it did.
        # It scrolls sideways rather than wrapping — a filter that grew a
        # second and third line would take the room from the list it filters
        # — and with no scrollbar of its own, which would cost more height
        # than the chips do.
        self.tag_bar = Gtk.Box(spacing=4, css_classes=["tag-bar"])
        self.tag_scroller = Gtk.ScrolledWindow(
            child=self.tag_bar, visible=False, propagate_natural_height=True,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            hscrollbar_policy=Gtk.PolicyType.EXTERNAL)

        # Ellipsized, and not for looks: a label that cannot shorten sets the
        # minimum width of everything above it, so "1 of 2 live · ~450 MB · 3
        # waiting for a slot" would quietly become the narrowest the sidebar
        # could ever be dragged.
        self.memory_label = Gtk.Label(xalign=0, css_classes=["session-meta"],
                                      ellipsize=Pango.EllipsizeMode.END,
                                      margin_start=12, margin_end=12,
                                      margin_top=6, margin_bottom=8)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.tag_scroller)
        box.append(scroller)
        box.append(Gtk.Separator())
        box.append(self.memory_label)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(box)
        return view

    def _build_content(self):
        self.title_widget = Adw.WindowTitle(title="Claude Desk")
        header = Adw.HeaderBar(show_start_title_buttons=False)
        header.set_title_widget(self.title_widget)
        header.pack_start(widgets.icon_button("sidebar-show-symbolic", "Toggle sidebar",
                                              self._toggle_sidebar))

        # The working folder is the single most important thing about a
        # session, so it is a control rather than a caption: click it to move
        # the session somewhere else.
        self.folder_label = Gtk.Label(ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=30)
        chip = Gtk.Box(spacing=6)
        chip.append(Gtk.Image(icon_name="folder-symbolic"))
        chip.append(self.folder_label)
        self.folder_button = Gtk.Button(child=chip, css_classes=["flat", "folder-chip"],
                                        tooltip_text="Change working folder")
        self.folder_button.connect("clicked", lambda _b: self.change_folder())
        header.pack_start(self.folder_button)

        # "Default" on its own is the one entry in this list that does not say
        # which model it means. It is also the entry most sessions are on, so
        # the name goes in the entry itself — "Default (Opus)" — rather than
        # being something you have to open Preferences to find out.
        self.model_strings = Gtk.StringList.new([label for _, label in MODELS])
        self.model_drop = Gtk.DropDown(model=self.model_strings)
        self.model_drop.connect("notify::selected", self._on_model_changed)
        self._default_shown = None

        self.mode_drop = Gtk.DropDown.new_from_strings([label for _, label in PERMISSION_MODES])
        self.mode_drop.set_tooltip_text("Permission mode")
        self.mode_drop.connect("notify::selected", self._on_mode_changed)

        # The two dropdowns travel together, in one box, because between them
        # they are 296 px of the header — nearly half of what it needs — and
        # in a tiling column that is the difference between a sidebar you can
        # drag and a sidebar pinned to its floor. In a narrow window the box
        # moves out of the header onto a row of its own; see _set_compact.
        self.controls = Gtk.Box(spacing=6)
        self.controls.append(self.mode_drop)
        self.controls.append(self.model_drop)

        menu = Gio.Menu()
        menu.append("Rename…", "win.rename")
        menu.append("Change folder…", "win.folder")
        section = Gio.Menu()
        section.append("Sleep now (free memory)", "win.sleep")
        section.append("Clear conversation", "win.clear")
        menu.append_section(None, section)
        close = Gio.Menu()
        close.append("Delete session", "win.delete")
        menu.append_section(None, close)

        # One box for the whole end of the header, rather than three things
        # packed individually: pack_end's order depends on what is already
        # packed, so taking the dropdowns out for a narrow window and packing
        # them back put the ⋮ button on the other side of them. Inside a box,
        # append is append and the menu never moves.
        self.header_end = Gtk.Box(spacing=6)
        self.header_end.append(Gtk.MenuButton(icon_name="view-more-symbolic",
                                              menu_model=menu, tooltip_text="Session"))
        self.header_end.append(self.controls)
        header.pack_end(self.header_end)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=90)
        self.content_header = header
        self.header_menu = self.header_end.get_first_child()
        # Where the dropdowns go when the header has no room for them. Empty
        # and hidden while it has.
        self.control_bar = Adw.Bin(css_classes=["toolbar"], visible=False)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(self.control_bar)
        view.set_content(self.stack)
        return view

    def _install_shortcuts(self):
        for name, handler in (
            ("preferences", self.show_preferences),
            ("about", self.show_about),
            ("rename", self.rename_session),
            ("folder", self.change_folder),
            ("sleep", self.sleep_session),
            ("clear", self.clear_session),
            ("delete", self.delete_session),
            ("new", self.new_session),
            ("interrupt", self.interrupt_session),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=handler: fn())
            self.add_action(action)

        app = self.get_application()
        app.set_accels_for_action("win.new", ["<Control>n"])
        app.set_accels_for_action("win.interrupt", ["Escape"])

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_window_key)
        self.add_controller(keys)

    def _on_window_key(self, _controller, keyval, _code, state):
        if state & Gdk.ModifierType.CONTROL_MASK:
            if Gdk.KEY_1 <= keyval <= Gdk.KEY_9:
                index = keyval - Gdk.KEY_1
                if index < len(self.ordered):
                    self.select(self.ordered[index])
                return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    # ------------------------------------------------------------- sessions --

    def _layout_of(self):
        """What the sidebar would look like, as something comparable.

        Cheaper than rebuilding to find out nothing moved — and the sidebar
        now depends on more than the list of sessions, so "did a session
        change" is no longer the same question as "did the list change".

        Tagging is in here only through the filter: with none on, `"" in
        tags` is False for every chat however it is tagged, so ticking a tag
        refreshes the one row and leaves the menu it was ticked from open.
        """
        return [(session.meta.uid, session.meta.group,
                 self.tag_filter in session.meta.tags)
                for session in self.manager.ordered_sessions()] + [
                    self.cfg.show_archived, self.tag_filter]

    def _rebuild_sidebar(self):
        self._relayout_queued = False
        self._layout = self._layout_of()
        # A row about to be destroyed may hold the keyboard focus -- one of
        # its own buttons usually does, since clicking a star is what brought
        # us here. Note it now and put the focus back on the selected row at
        # the end, rather than letting GTK look for somewhere to put it.
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        had_focus = focus is not None and focus.is_ancestor(self.listbox)

        self._syncing = True
        # Rows only: the placeholder is a child of the list box like any row
        # is, and sits in the same sibling chain, so walking the chain reaches
        # it too. Handing it to remove() unparents it for good -- the empty
        # state would be gone from the first rebuild onwards, and GTK would
        # warn about a non-child on every one after that.
        child = self.listbox.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            if isinstance(child, Gtk.ListBoxRow):
                self.listbox.remove(child)
            child = following
        self.rows.clear()

        sessions = self.manager.ordered_sessions()
        if self.tag_filter and self.tag_filter not in self.cfg.all_tags():
            # The last chat wearing it has just been untagged or deleted, so
            # the tag is gone and there is nothing left to be filtered by.
            self.tag_filter = ""
        if self.tag_filter:
            # Archived chats included: the archive is where a chat is, and
            # asking for a tag is asking across all three sections. It is
            # still behind its own line, with a count of what matched.
            sessions = [s for s in sessions if self.tag_filter in s.meta.tags]
        archived = [s for s in sessions if s.meta.group == config.ARCHIVE_GROUP]
        shown = [s for s in sessions
                 if s.meta.group != config.ARCHIVE_GROUP or self.cfg.show_archived]
        # Headers earn their place only once there is more than one section to
        # tell apart: a single "Chats" above every chat you have says nothing.
        self._sections = {s.meta.group for s in shown}
        self.ordered = shown

        for session in shown:
            if session.meta.group == config.ARCHIVE_GROUP and archived:
                self._append_archive_row(len(archived))
                archived = []
            row = SessionRow(session, self._row_menu)
            self.rows[session.meta.uid] = row
            self.listbox.append(row)
        if archived:
            # Nothing was appended above, because the archive is closed.
            self._append_archive_row(len(archived))

        # Subscriptions follow the sessions, not the rows: a chat inside a
        # closed archive still has a footer to keep honest and may still be
        # the one on screen. A session that has gone took its listeners with
        # it, so there is nothing to unsubscribe from — only to forget.
        live = {session.meta.uid: session for session in self.manager.sessions}
        for uid in [uid for uid in self._subs if uid not in live]:
            del self._subs[uid]
        for uid, session in live.items():
            if uid not in self._subs:
                self._subs[uid] = session.subscribe(
                    lambda _w, _d, s=session: self._refresh_row(s)
                )

        row = self.rows.get(self.current.meta.uid) if self.current is not None else None
        if row is not None:
            self.listbox.select_row(row)
            if had_focus:
                row.grab_focus()
        self._syncing = False
        self._refresh_tag_bar()
        self._refresh_footer()

    def _append_archive_row(self, count):
        self.listbox.append(ArchiveRow(count, self.cfg.show_archived,
                                       self._toggle_archive_section))

    def _toggle_archive_section(self):
        self.cfg.show_archived = not self.cfg.show_archived
        self.cfg.save()
        self._queue_relayout()

    def _queue_relayout(self):
        """Rebuild the sidebar once the handler that asked for it has returned.

        What asks is nearly always a widget the rebuild will destroy — the
        star on a row, the archive line itself — and destroying a widget from
        inside its own signal is a thing to avoid rather than to get right.
        """
        if not self._relayout_queued:
            self._relayout_queued = True
            GLib.idle_add(self._rebuild_sidebar)

    def _group_header(self, row, before):
        """The line above the first row of each section."""
        if not isinstance(row, SessionRow) or len(self._sections) < 2:
            row.set_header(None)
            return
        group = row.session.meta.group
        previous = before.session.meta.group if isinstance(before, SessionRow) else None
        if (before is not None and previous == group) or group == config.ARCHIVE_GROUP:
            # The archive has a row of its own saying so, above these.
            row.set_header(None)
            return
        row.set_header(Gtk.Label(label=config.group_label(group), xalign=0,
                                 css_classes=["group-header"],
                                 margin_top=10, margin_bottom=2,
                                 margin_start=12, margin_end=12))

    def _row_menu(self, row):
        """What the ⋮ button and a right-click on the row both open."""
        session = row.session
        row.menu.set_popover(widgets.row_menu(
            self.cfg.all_tags(), session.meta.group, session.meta.tags,
            session.set_group, session.toggle_tag,
            lambda: widgets.prompt(self, "New tag",
                                   f"“{session.meta.name}” wears it first.",
                                   "Create", lambda name: self._new_tag(session, name)),
        ))

    def _new_tag(self, session, name):
        """A name typed into New tag, if it is a name a tag can have."""
        if not config.clean_tag(name):
            # A built-in group's name is not a tag -- see config.clean_tag.
            # Refusing it silently would look like the dialog had not worked.
            widgets.toast(self, f"“{name}” is a group, not a tag")
            return
        session.add_tag(name)

    def _refresh_tag_bar(self):
        """The chips above the list: every tag in use, and which one is on.

        Rebuilt only when it would come out different, because what calls it
        is every refresh of every row: a chip rebuilt on each token of a
        reply would be a lot of widgets for a list that changes twice a week.
        """
        tags = self.cfg.all_tags()
        state = (tags, self.tag_filter)
        if state == self._tag_bar_state:
            return
        self._tag_bar_state = state
        widgets.clear_box(self.tag_bar)
        for tag in tags:
            label = config.tag_label(tag)
            on = tag == self.tag_filter
            self.tag_bar.append(widgets.tag_chip(
                label, on, lambda t=tag: self._filter_by_tag(t),
                "Show every chat again" if on else f"Only chats tagged {label}"))
        self.tag_scroller.set_visible(bool(tags))

    def _filter_by_tag(self, tag):
        """Narrow the list to one tag, or widen it to all of them again."""
        self.tag_filter = "" if tag == self.tag_filter else tag
        # On an idle, like every other relayout: the chip that asked is one
        # of the widgets the rebuild destroys, and its own handler is still
        # on the stack. See _queue_relayout.
        self._queue_relayout()

    def _refresh_row(self, session):
        row = self.rows.get(session.meta.uid)
        if row is not None:
            row.refresh()
        if session is self.current:
            self._sync_header()
        # A tag may have just been made or taken off the last chat wearing
        # it, which adds or removes a chip without moving a single row.
        self._refresh_tag_bar()
        self._refresh_footer()

    def _refresh_footer(self):
        live = len(self.manager.live)
        waiting = len(self.manager.queue)
        text = f"{live} of {self.cfg.max_live} live · ~{live * 450} MB"
        if waiting:
            # Distinct from a session's message queue: these want a process.
            text += f" · {waiting} waiting for a slot"
        self.memory_label.set_label(text)

    def _on_manager_changed(self, session=None):
        if self._layout != self._layout_of():
            # On an idle, not here: what usually changed the layout is a
            # button on one of the rows about to be destroyed, and that
            # button's own handler is still on the stack.
            self._queue_relayout()
        if session is not None:
            self._refresh_row(session)
        else:
            self._refresh_footer()

    def _on_manager_notice(self, message):
        widgets.toast(self, message, timeout=6)

    def _on_row_selected(self, _listbox, row):
        if self._syncing or not isinstance(row, SessionRow):
            return
        self.select(row.session)

    def _on_row_activated(self, _listbox, row):
        if isinstance(row, ArchiveRow):
            row.on_toggle()

    def select(self, session):
        if session is None:
            return
        self.current = session
        self.cfg.active_uid = session.meta.uid

        view = self.views.get(session.meta.uid)
        if view is None:
            view = ChatView(session)
            self.views[session.meta.uid] = view
            self.stack.add_named(view, session.meta.uid)
            self._trim_views(session.meta.uid)
        if session.meta.uid in self.view_order:
            self.view_order.remove(session.meta.uid)
        self.view_order.append(session.meta.uid)

        self.stack.set_visible_child(view)
        self._sync_header()

        row = self.rows.get(session.meta.uid)
        if row is not None and self.listbox.get_selected_row() is not row:
            self._syncing = True
            self.listbox.select_row(row)
            self._syncing = False
        view.composer.focus()

    def _trim_views(self, keep_uid):
        while len(self.view_order) > VIEW_CACHE:
            uid = self.view_order.pop(0)
            if uid == keep_uid:
                self.view_order.append(uid)
                break
            view = self.views.pop(uid, None)
            if view is not None:
                view.detach()
                self.stack.remove(view)

    def _sync_header(self):
        session = self.current
        if session is None:
            return
        self._syncing = True
        self.title_widget.set_title(session.meta.name or "New session")
        if session.pending_permission:
            subtitle = ("Waiting for your answer"
                        if session.pending_permission.get("questions")
                        else "Waiting for your approval")
        else:
            subtitle = STATE_SUBTITLES.get(session.state, "")
        if session.outbox:
            subtitle = f"{subtitle} · {len(session.outbox)} queued".lstrip(" ·")
        self.title_widget.set_subtitle(subtitle)
        self.folder_label.set_label(os.path.basename(session.meta.cwd) or session.meta.cwd)
        self.folder_button.set_tooltip_text(
            f"{short_path(session.meta.cwd)}\nClick to change the working folder"
        )
        self._sync_default_model(session)
        keys = [key for key, _ in MODELS]
        if session.meta.model in keys:
            self.model_drop.set_selected(keys.index(session.meta.model))
        modes = [key for key, _ in PERMISSION_MODES]
        if session.meta.permission_mode in modes:
            self.mode_drop.set_selected(modes.index(session.meta.permission_mode))
        self._syncing = False

    def _sync_default_model(self, session):
        """Name the model "Default" currently means, in the entry itself.

        What a live child reported beats what the settings file says, because
        an env var or a managed policy can have the last word after it. A
        session on a named model still shows the global default, since that is
        what choosing Default would get you.
        """
        resolved = (session.live_model if session.meta.model == "default"
                    else "") or self.cfg.default_model
        name = model_label(resolved)
        label = f"Default ({name})" if name else "Default"
        tooltip = ("Model for this session"
                   + (f" · Default is {name} — change it in Preferences"
                      if name else ""))
        self.model_drop.set_tooltip_text(tooltip)
        if label == self._default_shown:
            return
        self._default_shown = label
        # Splicing the selected row out resets the selection, so put it back.
        # _sync_header has _syncing set, which is what keeps that from being
        # read as the user choosing a model.
        selected = self.model_drop.get_selected()
        self.model_strings.splice(0, 1, [label])
        self.model_drop.set_selected(selected)

    # -------------------------------------------------------------- actions --

    def new_session(self):
        session = self.manager.new_session()
        self._rebuild_sidebar()
        self.select(session)
        self.change_folder(is_new=True)

    def interrupt_session(self):
        # Through the view, so Escape closes an open command menu before it
        # stops anything, and stopping also hands back anything queued.
        if self.current is None:
            return
        view = self.views.get(self.current.meta.uid)
        if view is not None:
            view.escape()
        elif self.current.busy:
            self.current.interrupt()

    def sleep_session(self):
        if self.current is None:
            return
        # Queued messages would wake it straight back up, which is not what
        # "free memory now" means; give them back instead.
        view = self.views.get(self.current.meta.uid)
        if view is not None:
            view.take_back_queue()
        if self.current.backend is None:
            widgets.toast(self, "Already asleep")
            return
        self.current.sleep(note="Slept — resumes on your next message")
        widgets.toast(self, "Memory released")

    def rename_session(self):
        if self.current is None:
            return
        widgets.prompt(self, "Rename session", "", "Rename",
                       self._apply_rename, text=self.current.meta.name)

    def _apply_rename(self, name):
        self.current.rename(name)
        self._refresh_row(self.current)

    def change_folder(self, is_new=False):
        # The dialog is asynchronous and the user can switch sessions while it
        # is open, so bind the target now rather than reading self.current back
        # in the callback.
        session = self.current
        if session is None:
            return
        # Where it already is, is not a choice worth offering.
        offered = [path for path in self.cfg.recent_folders()
                   if path != session.meta.cwd]
        if not offered:
            # Nothing remembered yet, so do not make them dismiss an empty
            # list on the way to the chooser they actually need.
            self._browse_for_folder(session)
            return
        widgets.choose_folder(
            self, offered,
            lambda path: self._move_session(session, path),
            lambda: self._browse_for_folder(session),
            heading="Folder for this chat" if is_new else "Change working folder",
        )

    def _browse_for_folder(self, session):
        dialog = Gtk.FileDialog(title="Choose a working folder")
        try:
            dialog.set_initial_folder(Gio.File.new_for_path(session.meta.cwd))
        except GLib.GError:
            pass

        def chosen(source, result):
            try:
                folder = source.select_folder_finish(result)
            except GLib.GError:
                return  # cancelled; a new session simply keeps its default folder
            if folder is None:
                return
            path = folder.get_path()
            if not path or path == session.meta.cwd:
                return
            self._move_session(session, path)

        dialog.select_folder(self, None, chosen)

    def _move_session(self, session, path):
        """Point a session at a different folder, from the next message on."""
        if session.backend is not None:
            session.sleep()

        # A Claude Code session is tied to the directory it started in, so a
        # move always starts a fresh one rather than resuming the old id. That
        # loses the model's memory of the conversation, which is worth saying
        # out loud instead of letting it be discovered in the next reply.
        had_history = bool(session.meta.claude_session_id or session.entries)
        session.meta.claude_session_id = ""
        session.meta.cwd = path
        self.cfg.last_cwd = path
        self.cfg.remember_folder(path)
        if session.meta.name in ("", "New session"):
            session.meta.name = os.path.basename(path) or path

        self.manager.save()
        self._refresh_row(session)
        if session is self.current:
            self._sync_header()

        if had_history:
            session.note(f"Working folder changed to {short_path(path)}. "
                         "The next message starts a fresh Claude Code session here, "
                         "so earlier context is no longer in play.")
        else:
            view = self.views.get(session.meta.uid)
            if view is not None:
                view.reload()  # the empty state names the folder
        widgets.toast(self, f"Now working in {short_path(path)}")

    def clear_session(self):
        if self.current is None:
            return
        widgets.confirm(
            self, "Clear this conversation?",
            "The transcript is deleted and the next message starts a fresh "
            "Claude Code session. The folder and settings stay as they are.",
            "Clear", lambda: self.current.clear_transcript(),
        )

    def delete_session(self):
        if self.current is None:
            return
        session = self.current
        widgets.confirm(
            self, f"Delete “{session.meta.name}”?",
            "The session and its transcript are removed from Claude Desk.",
            "Delete", lambda: self._do_delete(session),
        )

    def _do_delete(self, session):
        uid = session.meta.uid
        view = self.views.pop(uid, None)
        if view is not None:
            view.detach()
            self.stack.remove(view)
        if uid in self.view_order:
            self.view_order.remove(uid)
        callback = self._subs.pop(uid, None)
        if callback is not None:
            session.unsubscribe(callback)
        self.current = None
        self.manager.remove_session(session)
        if not self.manager.sessions:
            self.manager.new_session()
        self._rebuild_sidebar()
        self.select(self.manager.ordered_sessions()[0])

    def _on_model_changed(self, drop, _param):
        if self._syncing or self.current is None:
            return
        self.current.set_model(MODELS[drop.get_selected()][0])
        self.manager.save()

    def _on_mode_changed(self, drop, _param):
        if self._syncing or self.current is None:
            return
        self.current.set_permission_mode(PERMISSION_MODES[drop.get_selected()][0])
        self.manager.save()

    def _toggle_sidebar(self):
        # Hiding the child rather than dragging the handle to zero: the Paned
        # then gives the conversation everything, takes the handle away with
        # it, and still remembers the width to come back to.
        sidebar = self.paned.get_start_child()
        sidebar.set_visible(not sidebar.get_visible())

    def _set_compact(self, compact):
        """Move the model and mode dropdowns off the header, or back onto it.

        One box moves rather than two dropdowns, so neither the order they sit
        in nor the signals they carry has to be rebuilt either way.
        """
        if compact:
            if self.controls.get_parent() is self.header_end:
                self.header_end.remove(self.controls)
            self.controls.set_halign(Gtk.Align.END)
            self.control_bar.set_child(self.controls)
            self.control_bar.set_visible(True)
        else:
            self.control_bar.set_child(None)
            self.control_bar.set_visible(False)
            self.controls.set_halign(Gtk.Align.FILL)
            if self.controls.get_parent() is None:
                self.header_end.append(self.controls)

    def _on_sidebar_dragged(self, paned, _param):
        """Keep the width you dragged to, without a file write per pixel."""
        width = paned.get_position()
        if not paned.get_start_child().get_visible() or width < config.MIN_SIDEBAR_WIDTH:
            # Mid-toggle, or mid-allocation before the first real size.
            return
        self.cfg.sidebar_width = width
        if self._width_timer:
            GLib.source_remove(self._width_timer)
        self._width_timer = GLib.timeout_add(700, self._save_sidebar_width)

    def _save_sidebar_width(self):
        self._width_timer = 0
        self.cfg.save()
        return GLib.SOURCE_REMOVE

    # ---------------------------------------------------------- preferences --

    def show_preferences(self):
        dialog = Adw.PreferencesDialog(title="Preferences")
        # The Look page is the house's, so the themes and the glass are the
        # same rows in the same order as in every other app here.
        dialog.add(housestyle.look_page(self.cfg.house, self.glazing.restyle))
        page = Adw.PreferencesPage(title="Sessions",
                                   icon_name="utilities-terminal-symbolic")
        group = Adw.PreferencesGroup(
            title="Memory",
            description="A live Claude Code process holds about 450 MB. Sessions "
                        "over the limit sleep and resume automatically.",
        )

        live = Adw.SpinRow.new_with_range(1, 8, 1)
        live.set_title("Live sessions at once")
        live.set_value(self.cfg.max_live)
        live.connect("notify::value", lambda row, _p: self._set_max_live(int(row.get_value())))
        group.add(live)

        idle = Adw.SpinRow.new_with_range(0, 120, 5)
        idle.set_title("Sleep after idle")
        idle.set_subtitle("Minutes. 0 keeps sessions awake.")
        idle.set_value(self.cfg.idle_sleep_min)
        idle.connect("notify::value", lambda row, _p: self._set_idle(int(row.get_value())))
        group.add(idle)
        page.add(group)

        models = Adw.PreferencesGroup(
            title="Model",
            description="“Default” is Claude Code's own `model` setting, in "
                        "~/.claude/settings.json — shared with the terminal "
                        "and with every other session on this machine.",
        )
        # Re-read on opening: the terminal may have changed it since launch.
        self.cfg.default_model = config.read_default_model()
        choices = [("", "Whatever Claude Code picks"),
                   *((key, label) for key, label in MODELS if key != "default")]
        row = Adw.ComboRow(title="Default model",
                           model=Gtk.StringList.new([label for _, label in choices]))
        keys = [key for key, _ in choices]
        current = self.cfg.default_model
        row.set_selected(keys.index(current) if current in keys else 0)
        if current and current not in keys:
            # A pinned id rather than an alias — `claude-opus-5-20260101`. Say
            # so instead of showing it as unset and offering to overwrite it.
            row.set_subtitle(f"Currently {current}")
        row.connect("notify::selected",
                    lambda combo, _p: self._set_default_model(keys[combo.get_selected()]))
        models.add(row)
        page.add(models)

        appearance = Adw.PreferencesGroup(title="Conversation")
        thinking = Adw.SwitchRow(title="Show thinking", active=self.cfg.show_thinking)
        thinking.connect("notify::active", lambda row, _p: self._set_thinking(row.get_active()))
        appearance.add(thinking)
        page.add(appearance)

        dialog.add(page)
        dialog.present(self)

    def _set_max_live(self, value):
        self.cfg.max_live = value
        self.cfg.save()
        self._refresh_footer()

    def _set_idle(self, value):
        self.cfg.idle_sleep_min = value
        self.cfg.save()

    def _set_thinking(self, value):
        self.cfg.show_thinking = value
        self.cfg.save()

    def _set_default_model(self, name):
        if name == self.cfg.default_model:
            return
        if not config.write_default_model(name):
            widgets.toast(self, "Could not write ~/.claude/settings.json — "
                               "left it as it was")
            return
        self.cfg.default_model = name
        self._sync_header()
        # A session already running on Default keeps the model it was started
        # with, the same as changing a session's own model does.
        chose = model_label(name) or "Claude Code’s own choice"
        widgets.toast(self, f"Default is now {chose} — from each session's next start")

    def show_about(self):
        about = Adw.AboutDialog(
            application_name="Claude Desk",
            application_icon="applications-development",
            version="1.0",
            comments="A native front end for Claude Code. Same engine and tools "
                     "as the terminal, driven over its streaming JSON interface.",
            developer_name="Robin",
        )
        about.present(self)

    # --------------------------------------------------------------- closing --

    def _on_close(self, _window):
        # Stop listening first: whatever happens next must not be delivered
        # into widgets that are on their way out.
        self.manager.unwatch(*self._watch)
        self.geometry.save()
        if self._width_timer:
            GLib.source_remove(self._width_timer)
            self._width_timer = 0
        for session in self.manager.sessions:
            callback = self._subs.pop(session.meta.uid, None)
            if callback is not None:
                session.unsubscribe(callback)

        # The sessions belong to the application, not to this window. Closing
        # one of several must not stop the children the others are showing --
        # only the last window out shuts them down.
        app = self.get_application()
        others = [w for w in (app.get_windows() if app is not None else [])
                  if isinstance(w, MainWindow) and w is not self]
        if not others:
            self.manager.shutdown()
        else:
            self.manager.save()
        return False
