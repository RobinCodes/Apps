"""LaRenderer's line to Git Manager.

No git runs in this process. The Git menu, its shortcuts, the status letters
in the file tree and the Overleaf sync all ask Git Manager, over the session
bus, to do it. That keeps one app responsible for git — its error messages,
its credential rules, its locking — and a Git Manager window that is open
sees every commit made from here at once.

Nothing is started until it is needed. The first Git feature used in a
session looks for a Git Manager on the bus; if none is running, it starts one
with `git-manager --background`, which has no window and exits by itself once
this app has gone. One that is already open is used as it is.

Where there is no session bus to speak over (Windows), the same operations
run in-process instead: Git Manager's `service` module has no GTK in it, so
it is imported from the Git Manager folder and called on a worker thread.
Either way the caller gets the same answers through the same callbacks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from gi.repository import Gio, GLib

from . import jobs, winenv

BUS_NAME = "com.robin.GitManager"
OBJECT_PATH = "/com/robin/GitManager/Service"
INTERFACE = "com.robin.GitManager.Service"
ERROR_PREFIX = "com.robin.GitManager.Error."

START_SECONDS = 20
LOCAL_TIMEOUT_MS = 2 * 60 * 1000
# A first clone of a project with a few hundred megabytes of figures, over a
# slow link, is the longest thing anyone will ask for.
NETWORK_TIMEOUT_MS = 20 * 60 * 1000

# The remote says the method isn't there: a Git Manager from before it could
# serve anything, still running in the user's session.
_OUTDATED = (
    "org.freedesktop.DBus.Error.UnknownMethod",
    "org.freedesktop.DBus.Error.UnknownObject",
    "org.freedesktop.DBus.Error.UnknownInterface",
)
_GONE = (
    "org.freedesktop.DBus.Error.NoReply",
    "org.freedesktop.DBus.Error.ServiceUnknown",
    "org.freedesktop.DBus.Error.NameHasNoOwner",
)


class GitError(Exception):
    """A git operation that did not work, with a kind to act on.

    Kinds come from Git Manager ("auth", "conflict", "nothing", "norepo",
    "unknown", "failed") plus the ones this side adds: "outdated", "gone",
    "missing" (no Git Manager to be found) and "timeout".
    """

    def __init__(self, message, kind="failed"):
        super().__init__(message)
        self.kind = kind


def launcher() -> str | None:
    """Where Git Manager's launcher is, or None.

    The symlink on PATH first, since that is the one the desktop uses; then
    the folder beside this one, which is where it lives in the repository
    these apps share.
    """
    found = winenv.which("git-manager")
    if found:
        return found
    here = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    sibling = os.path.join(os.path.dirname(here), "Git Manager", "git-manager")
    return sibling if os.path.isfile(sibling) else None


def _translate(exc: GLib.Error) -> GitError:
    remote = Gio.DBusError.get_remote_error(exc) or ""
    message = exc.message or ""
    # "GDBus.Error:com.robin.GitManager.Error.Auth: A sign-in is needed"
    if message.startswith("GDBus.Error:") and ": " in message:
        message = message.split(": ", 1)[1]
    if remote.startswith(ERROR_PREFIX):
        return GitError(message, remote[len(ERROR_PREFIX):].lower() or "failed")
    if remote in _OUTDATED:
        return GitError(
            "The Git Manager that is running is from before it could take requests "
            "from LaRenderer. Close it, and the next Git action will start a new one.",
            "outdated")
    if remote in _GONE:
        return GitError("Git Manager stopped before it answered.", "gone")
    if exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT):
        return GitError("Git Manager took too long to answer.", "timeout")
    return GitError(message or "Git Manager could not be reached.", "failed")


class Link:
    """One app's connection to Git Manager, started on first use.

    `call()` is the whole interface. Requests made before Git Manager is up
    queue, and go out in order once it answers. `on_ready` is called each
    time a connection is made, so the window can show git state that it had
    no way to ask for before.
    """

    def __init__(self):
        self.connection: Gio.DBusConnection | None = None
        self.state = "idle"          # idle | starting | ready
        self.used = False            # has anything asked yet this session?
        self.on_ready = None
        self._queue: list = []
        self._watch = 0
        self._owned = False          # does anything own the name right now?
        self._spawned = False
        self._attaching = False
        self._deadline = 0
        self._module = None          # Git Manager's service, when in-process

    @property
    def in_process(self) -> bool:
        return self._module is not None

    # -- the interface ------------------------------------------------------

    def call(self, operation, on_done=None, on_error=None, network=False, **arguments):
        """Ask Git Manager for `operation`; the answer comes back on the main loop."""
        self.used = True
        request = (operation, arguments, on_done, on_error, network)
        if self.state == "ready":
            self._send(*request)
            return
        self._queue.append(request)
        if self.state == "idle":
            self._start()

    def close(self):
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
            self._watch = 0
        self._cancel_deadline()

    # -- getting a Git Manager ----------------------------------------------

    def _start(self):
        self.state = "starting"
        if self.connection is None:
            try:
                self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            except GLib.Error:
                self.connection = None
        if self.connection is None:
            self._start_in_process()
            return

        self._deadline = GLib.timeout_add_seconds(START_SECONDS, self._gave_up)
        if not self._watch:
            # The watch reports the name's current state straight away —
            # appeared if some Git Manager already owns it, vanished if none
            # does — and every change after that.
            self._watch = Gio.bus_watch_name_on_connection(
                self.connection, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
                self._appeared, self._vanished)
        elif self._owned:
            # One is running and was not answering for us yet — a window
            # opened by hand since the last one went away, say.
            self._appeared(self.connection, BUS_NAME, "")
        else:
            self._spawn()

    def _spawn(self):
        path = launcher()
        if not path:
            self._fail_all(GitError(
                "Git Manager is not installed where LaRenderer looks for it "
                "(git-manager on PATH, or a “Git Manager” folder beside this one).",
                "missing"))
            return
        self._spawned = True
        try:
            # The same interpreter as this app, rather than the script's own
            # shebang: that is the Python known to have GTK on Windows.
            subprocess.Popen(
                [sys.executable, path, "--background"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=not winenv.WINDOWS,
                creationflags=winenv.NO_WINDOW,
            )
        except OSError as exc:
            self._spawned = False
            self._fail_all(GitError(f"Git Manager could not be started: {exc}", "missing"))

    def _appeared(self, _connection, _name, _owner):
        self._owned = True
        if self.state != "starting" or self._attaching:
            return
        self._attach(tries=10)

    def _attach(self, tries):
        self._attaching = True

        def attached(connection, result, _data):
            try:
                connection.call_finish(result)
            except GLib.Error as exc:
                error = _translate(exc)
                # One we started ourselves cannot be out of date. It is still
                # putting its service on the bus; ask again in a moment.
                if error.kind == "outdated" and self._spawned and tries > 1:
                    GLib.timeout_add(300, lambda: self._attach(tries - 1) and False)
                    return
                self._attaching = False
                self._fail_all(error)
                return
            self._attaching = False
            self._cancel_deadline()
            self.state = "ready"
            self._spawned = False
            queued, self._queue = self._queue, []
            for request in queued:
                self._send(*request)
            if self.on_ready:
                self.on_ready()

        # Attaching is what keeps a background Git Manager alive for exactly
        # as long as this process is: it watches our name and leaves with it.
        self.connection.call(
            BUS_NAME, OBJECT_PATH, INTERFACE, "Attach", None, None,
            Gio.DBusCallFlags.NONE, 10_000, None, attached, None)

    def _vanished(self, _connection, _name):
        self._owned = False
        if self.state == "ready":
            # Its window was closed, or it exited with nothing attached. The
            # next request starts another.
            self.state = "idle"
            return
        if self.state == "starting" and not self._spawned:
            self._spawn()

    def _gave_up(self):
        self._deadline = 0
        if self.state != "ready":
            self._fail_all(GitError(
                f"Git Manager did not start within {START_SECONDS} seconds.", "timeout"))
        return GLib.SOURCE_REMOVE

    def _cancel_deadline(self):
        if self._deadline:
            GLib.source_remove(self._deadline)
            self._deadline = 0

    def _fail_all(self, error):
        self._cancel_deadline()
        self.state = "idle"
        self._spawned = False
        queued, self._queue = self._queue, []
        for _operation, _arguments, _done, on_error, _network in queued:
            if on_error:
                on_error(error)

    def _start_in_process(self):
        module = _import_service()
        if module is None:
            self._fail_all(GitError(
                "There is no session bus to reach Git Manager on, and its folder "
                "is not where LaRenderer looks for it.", "missing"))
            return
        self._module = module
        self.state = "ready"
        queued, self._queue = self._queue, []
        for request in queued:
            self._send(*request)
        if self.on_ready:
            self.on_ready()

    # -- sending ------------------------------------------------------------

    def _send(self, operation, arguments, on_done, on_error, network):
        if self._module is not None:
            self._send_in_process(operation, arguments, on_done, on_error)
            return

        def finished(connection, result, _data):
            try:
                reply = connection.call_finish(result)
            except GLib.Error as exc:
                error = _translate(exc)
                if error.kind in ("gone", "outdated"):
                    self.state = "idle"
                if on_error:
                    on_error(error)
                return
            try:
                payload = json.loads(reply.unpack()[0] or "{}")
            except ValueError:
                payload = {}
            if on_done:
                on_done(payload)

        self.connection.call(
            BUS_NAME, OBJECT_PATH, INTERFACE, "Call",
            GLib.Variant("(ss)", (operation, json.dumps(arguments))),
            GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE,
            NETWORK_TIMEOUT_MS if network else LOCAL_TIMEOUT_MS,
            None, finished, None)

    def _send_in_process(self, operation, arguments, on_done, on_error):
        module = self._module
        if operation == "show":
            # The one thing that needs a window: start Git Manager proper.
            path = launcher()
            try:
                subprocess.Popen([sys.executable, path, arguments.get("path") or "."],
                                 creationflags=winenv.NO_WINDOW)
            except (OSError, TypeError) as exc:
                if on_error:
                    on_error(GitError(f"Git Manager could not be started: {exc}", "missing"))
                return
            if on_done:
                on_done({})
            return

        def failed(exc):
            if on_error:
                on_error(GitError(str(exc), getattr(exc, "kind", "failed")))

        jobs.run(lambda: module.run(operation, arguments), on_done, failed)


def _import_service():
    """Git Manager's service module, imported from its own folder."""
    path = launcher()
    if not path:
        return None
    folder = os.path.dirname(os.path.realpath(path))
    if folder not in sys.path:
        sys.path.append(folder)
    try:
        from gitmanager import service  # noqa: PLC0415 - only when there is no bus
    except Exception:  # noqa: BLE001 - any failure means "not available"
        return None
    return service
