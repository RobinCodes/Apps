"""Hermione as the desktop's file chooser.

When a program says "choose a file" it does not open a chooser itself any
more; it asks `xdg-desktop-portal`, which asks whichever backend the desktop
has been told to use. This is that backend —
`org.freedesktop.impl.portal.FileChooser` — so the dialog that appears is
Hermione, with its places, its tabs, its search and its thumbnails, instead
of a second file manager with its own ideas about where Downloads is.

Three methods, which between them are every file dialog there is:

| `OpenFile`  | pick one or several existing files, or a folder |
| `SaveFile`  | a folder and a name to write |
| `SaveFiles` | a folder to write a known list of names into |

Each is answered asynchronously: the call is parked, a window goes up, and
the reply is sent when the user has answered. The portal is owed exactly one
reply per call — `chooser.ChooserWindow` guarantees that end of it — and the
`handle` it passes is an object path we are expected to export a
`Request` on, so that the asking program can hang up.

This runs in a process of its own, started on demand by D-Bus and gone a
minute after the last dialog closes. It is deliberately not the same process
as the user's own Hermione: a file chooser that dies takes a file chooser
with it, not the window somebody was working in.

**The bus name is taken before libadwaita is imported, and that order is not
a style choice.** `xdg-desktop-portal` builds its backend proxies while it
starts up, and `StartServiceByName` blocks it until the backend owns its
name. Importing libadwaita reads the system's colour scheme from
`org.freedesktop.portal.Settings` — which is the same daemon, synchronously,
with D-Bus's default 25-second timeout. Start GTK first and the two of them
wait for each other for exactly that long, the portal gives up with "Failed
to create file chooser proxy", and then it exports no FileChooser interface
at all until it is restarted: not a slow file dialog, *no* file dialog, for
every program on the desktop. So `main` takes the name with nothing but Gio
loaded, and only then brings up the toolkit.
"""

from __future__ import annotations

import os
import sys
import time

# Deliberately not Gtk or Adw at module scope — see the note above. `Gio`
# and `GLib` are pure GObject and cost nothing; everything else is imported
# by `_serve`, once the name is ours.
from gi.repository import Gio, GLib  # noqa: E402

BUS_NAME = "org.freedesktop.impl.portal.desktop.hermione"
OBJECT_PATH = "/org/freedesktop/portal/desktop"

# The toolkit application id, which is not the bus name. The windows want to
# be Hermione — same icon, same `StartupWMClass` — while the bus name has to
# be the one xdg-desktop-portal looks for. The application is NON_UNIQUE, so
# it registers no bus name of its own and cannot collide with the Hermione
# the user already has open.
APP_ID = "com.robin.Hermione"

# What RequestName answers. 1 is "it is yours", 4 is "it already was".
NAME_PRIMARY_OWNER, NAME_ALREADY_OWNER = 1, 4
DBUS_NAME_FLAG_DO_NOT_QUEUE = 0x4

# The portal's own vocabulary for how a call ended.
SUCCESS, CANCELLED, OTHER = 0, 1, 2

# How long the service hangs about with nothing to do before exiting. Long
# enough that picking two files in a row does not pay for a cold start,
# short enough that it is not a resident process.
IDLE_SECONDS = 90

FILE_CHOOSER_XML = """
<node>
  <interface name='org.freedesktop.impl.portal.FileChooser'>
    <method name='OpenFile'>
      <arg type='o' name='handle' direction='in'/>
      <arg type='s' name='app_id' direction='in'/>
      <arg type='s' name='parent_window' direction='in'/>
      <arg type='s' name='title' direction='in'/>
      <arg type='a{sv}' name='options' direction='in'/>
      <arg type='u' name='response' direction='out'/>
      <arg type='a{sv}' name='results' direction='out'/>
    </method>
    <method name='SaveFile'>
      <arg type='o' name='handle' direction='in'/>
      <arg type='s' name='app_id' direction='in'/>
      <arg type='s' name='parent_window' direction='in'/>
      <arg type='s' name='title' direction='in'/>
      <arg type='a{sv}' name='options' direction='in'/>
      <arg type='u' name='response' direction='out'/>
      <arg type='a{sv}' name='results' direction='out'/>
    </method>
    <method name='SaveFiles'>
      <arg type='o' name='handle' direction='in'/>
      <arg type='s' name='app_id' direction='in'/>
      <arg type='s' name='parent_window' direction='in'/>
      <arg type='s' name='title' direction='in'/>
      <arg type='a{sv}' name='options' direction='in'/>
      <arg type='u' name='response' direction='out'/>
      <arg type='a{sv}' name='results' direction='out'/>
    </method>
  </interface>
</node>
"""

REQUEST_XML = """
<node>
  <interface name='org.freedesktop.impl.portal.Request'>
    <method name='Close'/>
  </interface>
</node>
"""


# ------------------------------------------------------ reading the options ---

def _path_from_bytes(value) -> str:
    """`current_folder` and friends arrive as `ay`, nul-terminated.

    PyGObject hands `ay` back as a list of ints or as bytes depending on how
    it was packed, and the portal's own callers are inconsistent about the
    trailing nul, so both are taken and the nul is dropped either way.
    """
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    else:
        try:
            raw = bytes(bytearray(value))
        except (TypeError, ValueError):
            return ""
    return raw.split(b"\0", 1)[0].decode("utf-8", "surrogateescape")


def _filters(value) -> list:
    from . import chooser

    out = []
    for item in value or []:
        try:
            name, patterns = item
        except (TypeError, ValueError):
            continue
        out.append(chooser.Filter(
            name=str(name),
            patterns=[(int(kind), str(text)) for kind, text in patterns or []]))
    return out


def _choices(value) -> list:
    from . import chooser

    out = []
    for item in value or []:
        try:
            key, label, options, initial = item
        except (TypeError, ValueError):
            continue
        out.append(chooser.Choice(
            key=str(key), label=str(label),
            options=[(str(a), str(b)) for a, b in options or []],
            initial=str(initial)))
    return out


def _index_of_filter(filters, current) -> int:
    """Which of `filters` the caller asked to start on.

    Matched by name rather than by identity, because `current_filter` is
    sent as a whole filter again rather than as an index into the list, and
    is allowed not to be in the list at all.
    """
    if not current:
        return 0 if filters else -1
    try:
        name = str(current[0])
    except (TypeError, IndexError):
        return 0 if filters else -1
    for index, candidate in enumerate(filters):
        if candidate.name == name:
            return index
    return 0 if filters else -1


def ask_from_options(mode: str, title: str, options: dict):
    """Turn one portal call into the question the chooser understands."""
    from . import chooser, places

    filters = _filters(options.get("filters"))
    folder = _path_from_bytes(options.get("current_folder"))
    name = str(options.get("current_name") or "")
    if not name and mode == "save":
        # `current_file` is the whole path of the file being re-saved, which
        # says both where to start and what to call it.
        current = _path_from_bytes(options.get("current_file"))
        if current:
            folder = folder or os.path.dirname(current)
            name = os.path.basename(current)
    if not folder:
        folder = places.home()

    names = [_path_from_bytes(item) for item in options.get("files") or []]
    return chooser.Ask(
        mode=mode,
        title=str(title or ""),
        accept_label=str(options.get("accept_label") or ""),
        multiple=bool(options.get("multiple")),
        directory=bool(options.get("directory")),
        folder=folder,
        name=name,
        names=[os.path.basename(n) for n in names if n],
        filters=filters,
        current_filter=_index_of_filter(filters, options.get("current_filter")),
        choices=_choices(options.get("choices")))


def results_variant(answer) -> GLib.Variant:
    """The `a{sv}` the portal wants a successful call to come back with."""
    results = {
        "uris": GLib.Variant("as", answer.uris),
        "choices": GLib.Variant("a(ss)", answer.choices),
    }
    if answer.chosen_filter is not None:
        results["current_filter"] = GLib.Variant(
            "(sa(us))", (answer.chosen_filter.name,
                         [(int(k), t) for k, t in answer.chosen_filter.patterns]))
    return GLib.Variant("a{sv}", results)


# ------------------------------------------------------------- the service ---

class FileChooserPortal:
    """One D-Bus object, and one window per call in flight."""

    def __init__(self, app):
        # Set by `_build_app` once the toolkit is up; a call cannot arrive
        # before then, because the name is not ours until it is.
        self.app = app
        # What `main`'s idle watch reads to decide whether this process has
        # any reason to go on existing.
        self.in_flight = 0
        self.last_activity = time.monotonic()
        self._requests: dict = {}
        self._request_ids: dict[str, int] = {}
        self._connection: Gio.DBusConnection | None = None
        self._node = Gio.DBusNodeInfo.new_for_xml(FILE_CHOOSER_XML)
        self._request_node = Gio.DBusNodeInfo.new_for_xml(REQUEST_XML)

    def register(self, connection: Gio.DBusConnection) -> None:
        self._connection = connection
        connection.register_object(
            OBJECT_PATH, self._node.interfaces[0], self._on_call, None, None)

    # -- the three methods -------------------------------------------------

    def _on_call(self, _connection, _sender, _path, _interface, method,
                 params, invocation) -> None:
        mode = {"OpenFile": "open", "SaveFile": "save",
                "SaveFiles": "save-many"}.get(method)
        if mode is None:
            invocation.return_error_literal(
                Gio.dbus_error_quark(),
                Gio.DBusError.UNKNOWN_METHOD, f"no {method} here")
            return
        handle, _app_id, _parent, title, options = params.unpack()
        try:
            ask = ask_from_options(mode, title, options)
        except Exception as exc:                        # noqa: BLE001
            # A malformed call is the caller's problem, not a reason for the
            # whole service to fall over and take other dialogs with it.
            print(f"hermione portal: {method}: {exc}", file=sys.stderr)
            self._reply(invocation, OTHER, None)
            return
        self._open(handle, ask, invocation)

    def _open(self, handle: str, ask, invocation) -> None:
        from . import chooser

        # Counted for the life of the dialog, so the service cannot time
        # out and exit with a window still on screen.
        self.in_flight += 1
        self.last_activity = time.monotonic()

        def done(answer) -> None:
            self._forget(handle)
            self._reply(invocation,
                        SUCCESS if answer is not None else CANCELLED,
                        answer)
            self.in_flight -= 1
            self.last_activity = time.monotonic()

        window = chooser.present(self.app, ask, done)
        self._requests[handle] = window
        self._watch(handle)

    def _reply(self, invocation, response: int, answer) -> None:
        results = (results_variant(answer) if answer is not None
                   else GLib.Variant("a{sv}", {}))
        # `new_tuple` rather than a `(u@a{sv})` format string: PyGObject's
        # Variant constructor does not take the `@` that would let an
        # already-built variant be dropped into a format, and the error it
        # raises for it comes out inside a D-Bus method call, where it is a
        # reply that never arrives and a caller that waits for its timeout.
        invocation.return_value(
            GLib.Variant.new_tuple(GLib.Variant("u", response), results))

    # -- the Request object the caller can hang up on ----------------------

    def _watch(self, handle: str) -> None:
        """Export a `Request` at `handle` so the caller can close it.

        A caller that gives up — its own window closed, say — calls `Close`
        on this and expects the dialog to go away. Without the object the
        call fails and the dialog is left on screen with nobody waiting for
        it.
        """
        if self._connection is None:
            return
        try:
            self._request_ids[handle] = self._connection.register_object(
                handle, self._request_node.interfaces[0],
                lambda *args: self._on_request_call(handle, *args), None, None)
        except GLib.Error as exc:
            print(f"hermione portal: cannot export {handle}: {exc}",
                  file=sys.stderr)

    def _on_request_call(self, handle, _connection, _sender, _path, _interface,
                         method, _params, invocation) -> None:
        if method != "Close":
            invocation.return_error_literal(
                Gio.dbus_error_quark(),
                Gio.DBusError.UNKNOWN_METHOD, f"no {method} here")
            return
        invocation.return_value(None)
        window = self._requests.get(handle)
        if window is not None:
            window.close_from_portal()

    def _forget(self, handle: str) -> None:
        self._requests.pop(handle, None)
        registration = self._request_ids.pop(handle, 0)
        if registration and self._connection is not None:
            self._connection.unregister_object(registration)


def claim_name(connection: Gio.DBusConnection) -> bool:
    """Take the bus name, synchronously, before the toolkit is loaded.

    `RequestName` by hand rather than through `GApplication`: the name has
    to be ours before anything imports libadwaita, and a GApplication takes
    its name as part of a startup that imports the toolkit first. Done here
    it is one blocking call with only Gio loaded, which is what lets
    xdg-desktop-portal finish starting — see the note at the top.
    """
    reply = connection.call_sync(
        "org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus", "RequestName",
        GLib.Variant("(su)", (BUS_NAME, DBUS_NAME_FLAG_DO_NOT_QUEUE)),
        GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, -1, None)
    return reply.unpack()[0] in (NAME_PRIMARY_OWNER, NAME_ALREADY_OWNER)


def _build_app(portal: FileChooserPortal):
    """The toolkit, and the application the dialogs belong to.

    A plain `Adw.Application` with handlers rather than a subclass, because
    the class statement for a subclass would need `Adw` at module scope and
    that is the import this whole file is arranged to postpone.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    from . import housestyle, style, winenv
    from .app import ACCELERATORS
    from .config import Config

    app = Adw.Application(application_id=APP_ID,
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.config = Config()

    def started(_app) -> None:
        winenv.use_icon(app, "hermione.ico")
        style.install(app.config, housestyle.system_is_dark())
        housestyle.system().watch(lambda dark: style.update(app.config, dark))

        # The menus name these and a chooser has no use for either — but an
        # action a menu names and cannot find is a warning per menu built,
        # so they exist and say no.
        for name in ("quit", "new-window"):
            action = Gio.SimpleAction.new(name, None)
            action.set_enabled(False)
            app.add_action(action)

        for action, keys in ACCELERATORS.items():
            app.set_accels_for_action(action, keys)

    app.connect("startup", started)
    # Being activated with nothing to pick is not an error and is not a
    # reason to put a window up: the dialogs come from method calls.
    app.connect("activate", lambda _a: None)
    # `register` rather than `run`: registering is what emits `startup` and
    # initialises GTK, and it is all a window needs from an application.
    # `run` would return the moment it was called — a GApplication with no
    # window and nothing holding it has nothing to wait for, and this
    # process has to wait for a method call it has not been sent yet.
    app.register(None)
    portal.app = app
    return app


def main() -> int:
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as exc:
        print(f"hermione portal: no session bus: {exc}", file=sys.stderr)
        return 1

    portal = FileChooserPortal(None)
    # Registered before the name is taken, so a call that arrives the
    # instant we own it has somewhere to land.
    portal.register(connection)
    if not claim_name(connection):
        # Somebody else is already serving. Two backends answering the same
        # calls is worse than none.
        print(f"hermione portal: {BUS_NAME} is already owned", file=sys.stderr)
        return 0

    _build_app(portal)

    # Our own loop, and our own idea of when to stop. A dialog on screen or
    # a reply still owed is a reason to stay; a minute and a half of
    # neither is not. Long enough that picking two files in a row does not
    # pay for a cold start, short enough that this is not resident.
    loop = GLib.MainLoop()

    def idle_enough() -> bool:
        if portal.in_flight == 0 and (time.monotonic() - portal.last_activity
                                      > IDLE_SECONDS):
            loop.quit()
            return False
        return True

    GLib.timeout_add_seconds(10, idle_enough)
    loop.run()
    return 0
