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

from . import config, widgets  # noqa: E402
from .backend import MODELS, PERMISSION_MODES  # noqa: E402
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
    def __init__(self, session):
        super().__init__()
        self.session = session

        box = Gtk.Box(spacing=10, margin_top=8, margin_bottom=8, margin_start=10, margin_end=6)
        self.dot = Gtk.Box(css_classes=["dot", "dot-asleep"], valign=Gtk.Align.CENTER)
        box.append(self.dot)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                              css_classes=["session-name"])
        self.meta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE,
                              css_classes=["session-meta"])
        text.append(self.name)
        text.append(self.meta)
        box.append(text)

        self.spinner = Gtk.Spinner(valign=Gtk.Align.CENTER)
        box.append(self.spinner)
        self.set_child(box)
        self.refresh()

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
        self.set_tooltip_text(f"{short_path(meta.cwd)}\n{tooltip}")


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, cfg):
        super().__init__(application=app, title="Claude Desk",
                         default_width=1120, default_height=740)
        self.cfg = cfg
        self.manager = Manager(cfg)
        self.manager.on_changed = self._on_manager_changed
        self.rows = {}
        self.views = {}
        self.view_order = []
        self.current = None
        self._syncing = False

        self.toasts = Adw.ToastOverlay()
        self.split = Adw.OverlaySplitView(sidebar_width_fraction=0.26, max_sidebar_width=340)
        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        self._install_shortcuts()
        self.connect("close-request", self._on_close)

        self._rebuild_sidebar()
        first = next((s for s in self.manager.sessions if s.meta.uid == cfg.active_uid),
                     self.manager.sessions[0] if self.manager.sessions else None)
        if first is None:
            first = self.manager.new_session()
            self._rebuild_sidebar()
        self.select(first)

    # ---------------------------------------------------------------- build --

    def _build_sidebar(self):
        header = Adw.HeaderBar(css_classes=["flat"])
        header.set_title_widget(Adw.WindowTitle(title="Sessions"))
        header.pack_start(widgets.icon_button("list-add-symbolic", "New session (Ctrl+N)",
                                              self.new_session))

        menu = Gio.Menu()
        menu.append("Preferences", "win.preferences")
        menu.append("About Claude Desk", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Menu"))

        self.listbox = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._on_row_selected)

        scroller = Gtk.ScrolledWindow(vexpand=True, child=self.listbox)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.memory_label = Gtk.Label(xalign=0, css_classes=["session-meta"],
                                      margin_start=12, margin_end=12,
                                      margin_top=6, margin_bottom=8)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(scroller)
        box.append(Gtk.Separator())
        box.append(self.memory_label)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(box)
        return view

    def _build_content(self):
        self.title_widget = Adw.WindowTitle(title="Claude Desk")
        header = Adw.HeaderBar()
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

        self.model_drop = Gtk.DropDown.new_from_strings([label for _, label in MODELS])
        self.model_drop.set_tooltip_text("Model")
        self.model_drop.connect("notify::selected", self._on_model_changed)
        header.pack_end(self.model_drop)

        self.mode_drop = Gtk.DropDown.new_from_strings([label for _, label in PERMISSION_MODES])
        self.mode_drop.set_tooltip_text("Permission mode")
        self.mode_drop.connect("notify::selected", self._on_mode_changed)
        header.pack_end(self.mode_drop)

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
        header.pack_end(Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu,
                                       tooltip_text="Session"))

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=90)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
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
                if index < len(self.manager.sessions):
                    self.select(self.manager.sessions[index])
                return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    # ------------------------------------------------------------- sessions --

    def _rebuild_sidebar(self):
        self._syncing = True
        child = self.listbox.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.listbox.remove(child)
            child = following
        self.rows.clear()
        for session in self.manager.sessions:
            row = SessionRow(session)
            self.rows[session.meta.uid] = row
            self.listbox.append(row)
            session.subscribe(lambda _w, _d, s=session: self._refresh_row(s))
        self._syncing = False
        self._refresh_footer()

    def _refresh_row(self, session):
        row = self.rows.get(session.meta.uid)
        if row is not None:
            row.refresh()
        if session is self.current:
            self._sync_header()
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
        if session is not None:
            self._refresh_row(session)
        else:
            self._refresh_footer()

    def _on_row_selected(self, _listbox, row):
        if self._syncing or row is None:
            return
        self.select(row.session)

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
        keys = [key for key, _ in MODELS]
        if session.meta.model in keys:
            self.model_drop.set_selected(keys.index(session.meta.model))
        modes = [key for key, _ in PERMISSION_MODES]
        if session.meta.permission_mode in modes:
            self.mode_drop.set_selected(modes.index(session.meta.permission_mode))
        self._syncing = False

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
        self.manager.remove_session(session)
        if not self.manager.sessions:
            self.manager.new_session()
        self._rebuild_sidebar()
        self.select(self.manager.sessions[0])

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
        self.split.set_show_sidebar(not self.split.get_show_sidebar())

    # ---------------------------------------------------------- preferences --

    def show_preferences(self):
        dialog = Adw.PreferencesDialog(title="Preferences")
        page = Adw.PreferencesPage()
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
        self.manager.shutdown()
        return False
