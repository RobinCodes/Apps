"""The window: a sidebar of rooms, one header bar, and whichever room is showing.

*One of everything.* There is one header bar, showing the room's title, and one
slab of glass; a room brings neither (ROOMS.md). There is one set of palace-wide
keys, caught before anything in a room can see them: Ctrl+K searches, Ctrl+Shift+A
asks Claude, Ctrl+1…9 go to a room, whichever room is showing.

*Nothing until it is wanted.* Reading the sidebar imports no app (rooms.py), and a
room is imported and built the first time it is visited or a file is routed to it.
Once built it stays built, so going back to it is instant and it is as you left it.

*A room's keys are its own while it is showing.* The room's accelerators go on the
application as it comes into view and come off as it leaves, and its actions are
inserted on this window as "room" for the same stretch, because that is where the
application's shortcuts look for them. So Ctrl+O means one thing in one room and
another in the next, and nothing has to know about anything else.

*It has to fit in a tiling column.* Below 720px the sidebar folds over the rooms
instead of standing beside them, so the window works at 455px; the suite checks.
"""

from __future__ import annotations

import os
import traceback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from . import __version__, door, home, housestyle, rooms, widgets, winenv  # noqa: E402
from .ask import AskPanel  # noqa: E402
from .config import Config  # noqa: E402
from .host import Host  # noqa: E402
from .search import SearchDialog  # noqa: E402

NARROW = 720


class Slot:
    """A room's place in the palace: what it is, and what it has become so far."""

    def __init__(self, spec):
        self.spec = spec
        self.room = None
        self.page = None          # the room's widget, once it is in the stack
        self.subtitle = ""
        self.row = None           # its row in the sidebar


class PalaceWindow(Adw.ApplicationWindow):
    def __init__(self, app, specs=None, ask_command=None):
        super().__init__(application=app, title="Mind Palace")
        self.config = Config()
        self.specs = list(specs) if specs is not None else rooms.discover()
        self.slots = {spec.title: Slot(spec) for spec in self.specs}
        self.current: Slot | None = None
        self.launched: list = []        # (spec, target), for the suite to read

        self.geometry = housestyle.Geometry(self, self.config, minimum=(360, 360))
        self.geometry.restore()

        self._build_actions()
        self._build_ui(ask_command)
        self.connect("close-request", self._on_close)
        GLib.idle_add(self._start_up)

    # ------------------------------------------------------------ building --

    def _build_ui(self, ask_command) -> None:
        self.toasts = Adw.ToastOverlay()

        self.title_widget = Adw.WindowTitle(title="Mind Palace")
        header = Adw.HeaderBar(title_widget=self.title_widget)
        self.sidebar_button = Gtk.ToggleButton(icon_name="sidebar-show-symbolic",
                                               tooltip_text="The rooms  (F9)")
        header.pack_start(self.sidebar_button)

        menu = Gio.Menu()
        first = Gio.Menu()
        first.append("Search the palace", "palace.search")
        first.append("Ask Claude", "palace.ask")
        menu.append_section(None, first)
        rest = Gio.Menu()
        rest.append("Preferences", "palace.preferences")
        rest.append("Keyboard shortcuts", "palace.shortcuts")
        rest.append("About Mind Palace", "palace.about")
        menu.append_section(None, rest)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Main menu"))
        self.ask_button = Gtk.ToggleButton(icon_name="chat-message-new-symbolic",
                                           tooltip_text="Ask Claude  (Ctrl+Shift+A)")
        self.ask_button.connect("toggled", self._on_ask_toggled)
        header.pack_end(self.ask_button)
        header.pack_end(Gtk.Button(icon_name="system-search-symbolic",
                                   tooltip_text="Search the palace  (Ctrl+K)",
                                   action_name="palace.search"))

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=120, hhomogeneous=False,
                               vhomogeneous=False, hexpand=True, vexpand=True)

        self.room_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE,
                                     css_classes=["navigation-sidebar", "palace-sidebar"])
        self.room_list.connect("row-activated", self._on_row_activated)
        for slot in self.slots.values():
            slot.row = self._make_row(slot.spec)
            self.room_list.append(slot.row)
        sidebar = Gtk.ScrolledWindow(child=self.room_list,
                                     hscrollbar_policy=Gtk.PolicyType.NEVER)

        self.split = Adw.OverlaySplitView(sidebar=sidebar, content=self.stack,
                                          min_sidebar_width=170, max_sidebar_width=220,
                                          show_sidebar=bool(self.config["sidebar_shown"]))
        self.sidebar_button.set_active(self.split.get_show_sidebar())
        self.sidebar_button.bind_property(
            "active", self.split, "show-sidebar",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)

        outer = Adw.ToolbarView()
        outer.add_top_bar(header)
        outer.set_content(self.split)
        self.toasts.set_child(outer)

        self.glazing = housestyle.Glazing(self, self.config, extra=widgets.CSS,
                                          app_class="mindpalace")
        self.glazing.wrap(self.toasts)

        self.ask_panel = AskPanel(self, command=ask_command)
        self.glazing.overlay.add_overlay(self.ask_panel)

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.new_length(
            Adw.BreakpointConditionLengthType.MAX_WIDTH, NARROW, Adw.LengthUnit.PX))
        narrow.add_setter(self.split, "collapsed", True)
        narrow.add_setter(self.split, "show-sidebar", False)
        self.add_breakpoint(narrow)

    def _make_row(self, spec) -> Gtk.ListBoxRow:
        box = Gtk.Box(spacing=10)
        box.append(widgets.room_icon(spec))
        name = widgets.label(spec.title, ellipsize=True)
        name.set_hexpand(True)
        box.append(name)
        if spec.kind == "door":
            mark = Gtk.Image(icon_name="window-new-symbolic", pixel_size=12,
                             css_classes=["door-mark"],
                             tooltip_text="Opens in a window of its own")
            box.append(mark)
        row = Gtk.ListBoxRow(child=box, tooltip_text=spec.comment or None)
        return row

    def _build_actions(self) -> None:
        # Not win.*: a room's window is often inserted as "win", and the palace's
        # own names must not be anything a room could shadow or be shadowed by.
        group = Gio.SimpleActionGroup()
        for name, handler in (
            ("search", lambda *_: self.show_search()),
            ("ask", lambda *_: self.toggle_ask()),
            ("sidebar", lambda *_: self.toggle_sidebar()),
            ("preferences", lambda *_: self.show_preferences()),
            ("shortcuts", lambda *_: self.show_shortcuts()),
            ("about", lambda *_: self.show_about()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            group.add_action(action)
        for number in range(1, 10):
            action = Gio.SimpleAction.new(f"room-{number}", None)
            action.connect("activate", lambda _a, _p, n=number: self.show_room_number(n))
            group.add_action(action)
        self.insert_action_group("palace", group)

        # In the capture phase, so these work with the focus anywhere in any room,
        # and no room's Ctrl+K can get there first.
        keys = Gtk.ShortcutController(scope=Gtk.ShortcutScope.LOCAL)
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        for action, accelerators in (
            ("palace.search", ("<Control>k",)),
            ("palace.ask", ("<Control><Shift>a",)),
            ("palace.sidebar", ("F9",)),
            ("palace.preferences", ("<Control>comma",)),
            ("palace.shortcuts", ("<Control>question",)),
            *((f"palace.room-{n}", (f"<Control>{n}",)) for n in range(1, 10)),
        ):
            for accelerator in accelerators:
                keys.add_shortcut(Gtk.Shortcut.new(
                    Gtk.ShortcutTrigger.parse_string(accelerator),
                    Gtk.NamedAction.new(action)))
        self.add_controller(keys)

    # ------------------------------------------------------------ the rooms --

    def _start_up(self) -> bool:
        wanted = self.config["last_room"] if self.config["reopen_last_room"] else "Home"
        if self.current is None and not self.show_room(wanted):
            self.show_room(rooms.HOME.title)
        return GLib.SOURCE_REMOVE

    def _slot(self, title: str) -> Slot | None:
        for slot in self.slots.values():
            if slot.spec.matches(title):
                return slot
        return None

    def show_room(self, title: str) -> bool:
        """Bring a room forward, building it if this is the first time. False if none."""
        slot = self._slot(title)
        if slot is None:
            return False
        if slot is not self.current:
            self._leave_current()
            self._ensure_built(slot)
            self.stack.set_visible_child(slot.page)
            self.current = slot
            self.title_widget.set_title(slot.spec.title)
            self.title_widget.set_subtitle(slot.subtitle)
            self._take_keys(slot)
            self.config["last_room"] = slot.spec.title
            self.room_list.select_row(slot.row)
            shown = getattr(slot.room, "shown", None)
            if callable(shown):
                _quietly(shown)
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        return True

    def show_room_number(self, number: int) -> bool:
        specs = self.specs
        return self.show_room(specs[number - 1].title) if number <= len(specs) else False

    def _ensure_built(self, slot: Slot):
        if slot.room is None:
            slot.room = self._build_room(slot)
            slot.page = slot.room.widget
            actions = getattr(slot.room, "actions", None)
            if actions is not None:
                slot.page.insert_action_group("room", actions)
            self.stack.add_child(slot.page)
        return slot.room

    def _build_room(self, slot: Slot):
        spec = slot.spec
        host = Host(self, spec.title)
        if spec.kind == "native":
            return home.HomeRoom(host)
        if spec.kind == "door":
            return door.DoorRoom(spec, self.launch)
        # One room failing to build must cost that room, not the palace.
        try:
            room = rooms.load(spec).build(host)
            if not isinstance(room.widget, Gtk.Widget):
                raise TypeError("the room's widget is not a widget")
            return room
        except Exception as exc:  # noqa: BLE001 - shown on the room's own page
            traceback.print_exc()
            return door.BrokenRoom(spec, str(exc) or exc.__class__.__name__, self.launch)

    def _leave_current(self) -> None:
        slot, self.current = self.current, None
        if slot is None:
            return
        app = self.get_application()
        if app is not None:
            for action, _keys in _accels(slot.room):
                app.set_accels_for_action(action, [])
        self.insert_action_group("room", None)
        hidden = getattr(slot.room, "hidden", None)
        if callable(hidden):
            _quietly(hidden)

    def _take_keys(self, slot: Slot) -> None:
        actions = getattr(slot.room, "actions", None)
        if actions is not None:
            self.insert_action_group("room", actions)
        app = self.get_application()
        if app is not None:
            for action, keys in _accels(slot.room):
                app.set_accels_for_action(action, _parsable(keys))

    def _on_row_activated(self, _list, row) -> None:
        for slot in self.slots.values():
            if slot.row is row:
                self.show_room(slot.spec.title)
                return

    def set_subtitle(self, title: str, text: str) -> None:
        slot = self._slot(title)
        if slot is None:
            return
        slot.subtitle = text
        if slot is self.current:
            self.title_widget.set_subtitle(text)

    def toggle_sidebar(self) -> None:
        self.split.set_show_sidebar(not self.split.get_show_sidebar())

    # ------------------------------------------------------------- routing --

    def open_target(self, target: str, source: str | None = None) -> bool:
        """A file, a folder, a link or a `scheme:` target, to whoever should have it.

        Whoever keeps it or opens it is asked, in order; a room may decline by
        returning False, and then the next is asked. `source` is the room that
        asked, which is not asked back: a room passing on what it declined must
        not be handed it again.
        """
        target = str(target)
        kind = rooms.front_matter_kind(target)
        for spec in rooms.route(target, self.specs, kind):
            if source is not None and spec.matches(source):
                continue
            slot = self.slots[spec.title]
            room = self._ensure_built(slot)
            opener = getattr(room, "open", None)
            if not callable(opener):
                continue
            try:
                taken = opener(target)
            except Exception:  # noqa: BLE001 - the next one may still open it
                traceback.print_exc()
                taken = False
            if taken:
                if spec.kind != "door":
                    self.show_room(spec.title)
                return True
        return self._open_on_the_desktop(target)

    def _open_on_the_desktop(self, target: str) -> bool:
        if rooms.is_url(target):
            Gtk.UriLauncher.new(target).launch(self, None, None)
            return True
        if rooms.scheme_of(target) or not os.path.exists(target):
            self.toast(f"Nothing here opens {os.path.basename(target) or target}")
            return False
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(target))

        def launched(source, result):
            try:
                source.launch_finish(result)
            except GLib.Error:
                if not winenv.open_file(target):
                    self.toast(f"Nothing here opens {os.path.basename(target)}")

        launcher.launch(self, None, launched)
        return True

    def launch(self, spec, target: str | None = None) -> bool:
        """Open a door: the app's own launcher, on this interpreter, in its own window."""
        argv = [winenv.gui_python(), str(spec.launcher), *rooms.door_argv(spec, target)]
        started = winenv.spawn(argv, cwd=str(spec.folder))
        if started:
            self.launched.append((spec, target))
            self.toast(f"{spec.title} opened in its own window")
        else:
            self.toast(f"{spec.title} would not start")
        return started

    # ---------------------------------------------------------- the host's --

    def toast(self, text: str, undo=None) -> None:
        toast = Adw.Toast(title=text, timeout=4)
        if undo is not None:
            toast.set_button_label("Undo")
            toast.connect("button-clicked", lambda _t: undo())
        self.toasts.add_toast(toast)

    def _context(self) -> list[str]:
        """The files the room on show says it is showing, for Claude."""
        context = getattr(self.current.room, "context", None) if self.current else None
        if not callable(context):
            return []
        try:
            return [str(path) for path in context() or []]
        except Exception:  # noqa: BLE001 - Claude can still be asked
            traceback.print_exc()
            return []

    def ask_claude(self, prompt: str = "", files=()) -> None:
        files = list(files) or self._context()
        self.ask_button.set_active(True)
        if prompt:
            self.ask_panel.ask(prompt, files)
        else:
            self.ask_panel.open(files)

    def toggle_ask(self) -> None:
        self.ask_button.set_active(not self.ask_button.get_active())

    def _on_ask_toggled(self, button) -> None:
        if button.get_active():
            if not self.ask_panel.get_visible():
                self.ask_panel.open(self._context())
        elif self.ask_panel.get_visible():
            self.ask_panel.close()

    def ask_closed(self) -> None:
        self.ask_button.set_active(False)

    # ---------------------------------------------------------------- dialogs --

    def show_search(self) -> None:
        SearchDialog(self).present(self)

    def show_preferences(self) -> None:
        dialog = Adw.PreferencesDialog(title="Preferences")
        dialog.add(housestyle.look_page(self.config, self.glazing.restyle))
        page = Adw.PreferencesPage(title="The palace", icon_name="go-home-symbolic")
        group = Adw.PreferencesGroup(title="Opening")
        again = Adw.SwitchRow(title="Come back to the last room",
                              subtitle="Otherwise the palace always opens on Home.",
                              active=bool(self.config["reopen_last_room"]))

        def changed(row, _param):
            self.config["reopen_last_room"] = row.get_active()
            self.config.save()

        again.connect("notify::active", changed)
        group.add(again)
        page.add(group)
        dialog.add(page)
        dialog.present(self)

    def show_shortcuts(self) -> None:
        rows = (
            ("Ctrl+K", "Search the palace"),
            ("Ctrl+Shift+A", "Ask Claude about what is showing"),
            ("Ctrl+1 … 9", "Go to a room, in the sidebar's order"),
            ("F9", "Show or hide the rooms"),
            ("Ctrl+,", "Preferences"),
            ("Ctrl+Q", "Quit"),
        )
        dialog = Adw.Dialog(title="Keyboard shortcuts", content_width=420, content_height=360)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        group = Adw.PreferencesGroup(margin_top=10, margin_bottom=16,
                                     margin_start=16, margin_end=16,
                                     description="A room's own keys work while it is showing.")
        for keys, what in rows:
            row = Adw.ActionRow(title=what)
            row.add_suffix(Gtk.Label(label=keys, css_classes=["dim-label", "monospace"]))
            group.add(row)
        view.set_content(Gtk.ScrolledWindow(child=group, vexpand=True))
        dialog.set_child(view)
        dialog.present(self)

    def show_about(self) -> None:
        Adw.AboutDialog(
            application_name="Mind Palace", application_icon="mind-palace",
            version=__version__, developer_name="Robin",
            comments="Every app here in one window, each a room. "
                     "The standalone apps stay too.",
            license_type=Gtk.License.MIT_X11).present(self)

    # ---------------------------------------------------------------- closing --

    def _on_close(self, _window) -> bool:
        for slot in self.slots.values():
            can_close = getattr(slot.room, "can_close", None)
            if slot.room is not None and callable(can_close):
                try:
                    allowed = can_close()
                except Exception:  # noqa: BLE001 - a broken room must not trap the window
                    traceback.print_exc()
                    allowed = True
                if not allowed:
                    self.show_room(slot.spec.title)
                    self.toast(f"{slot.spec.title} is still working")
                    return True
        self.ask_panel.cancel()
        for slot in self.slots.values():
            if slot.room is None:
                continue
            for name in ("save_state", "shutdown"):
                method = getattr(slot.room, name, None)
                if callable(method):
                    _quietly(method)
        if not self.split.get_collapsed():
            self.config["sidebar_shown"] = self.split.get_show_sidebar()
        self.geometry.save()
        self.config.save()
        return False


def _accels(room) -> list:
    return list(getattr(room, "accels", None) or [])


def _parsable(keys) -> list[str]:
    """The keys GTK can read. One it cannot makes it refuse all of that action's keys."""
    return [key for key in keys if Gtk.accelerator_parse(key)[0]]


def _quietly(call) -> None:
    """A room's own trouble is printed and stays the room's."""
    try:
        call()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
