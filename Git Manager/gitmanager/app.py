"""Application entry point."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib  # noqa: E402

from . import jobs, service, updater, widgets, winenv  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "com.robin.GitManager"

NEW_WINDOW_FLAGS = ("--new-window", "-n")


def split_new_window_flag(args):
    """Pull the new-window flag out of a command line.

    Kept out of the application object so it can be tested without a display,
    and so the flag is gone before anything downstream can mistake it for a
    path to a repository.
    """
    wants_new = False
    rest = []
    for arg in args:
        if arg in NEW_WINDOW_FLAGS:
            wants_new = True
        else:
            rest.append(arg)
    return wants_new, rest

# How long a background instance waits for the app that started it to say
# hello before deciding nobody wants it. The caller attaches within a second
# of the name appearing; this only matters if it died in between.
UNCLAIMED_SECONDS = 60


class GitManagerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.connect("command-line", self._on_command_line)
        self.service = None
        self._background = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        winenv.use_icon(self, "git-manager.ico")
        updater.watch(self, "Git Manager", "git-manager")
        widgets.install_css()
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

        new_window = Gio.SimpleAction.new("new-window", None)
        new_window.connect("activate", lambda *_: self.new_window().present())
        self.add_action(new_window)
        self.set_accels_for_action("app.new-window", ["<Control>n"])

        # Anything failing on a worker thread without a handler of its own
        # still has to reach somebody. This belongs to the application and not
        # to a window: there is one slot, so the last window built would
        # otherwise own it for all of them -- and go on owning it after being
        # closed, leaving every later failure to be reported on a window that
        # is no longer there.
        jobs.on_unhandled = self._report_unhandled

    def do_dbus_register(self, connection, object_path):
        # The service object goes on the bus here rather than at startup,
        # because this runs *before* GApplication claims the name. A caller
        # watching for the name to appear would otherwise ask for the service
        # in the gap between the two and be told it does not exist.
        #
        # Every Git Manager serves -- a window opened by hand as much as one
        # started for LaRenderer -- so a caller never needs a second process
        # when one is already running.
        if not Adw.Application.do_dbus_register(self, connection, object_path):
            return False
        try:
            self.service = service.Host(
                connection,
                on_show=self._service_show,
                on_changed=self._service_changed,
                on_idle=self._stop_background,
            )
        except GLib.Error:
            self.service = None
        return True

    def do_dbus_unregister(self, connection, object_path):
        # Also reached by a second instance that found the name taken and is
        # about to hand its command line to the first.
        if self.service is not None:
            self.service.close()
            self.service = None
        Adw.Application.do_dbus_unregister(self, connection, object_path)

    def new_window(self):
        """Another window over the same repositories.

        Nothing is shared but the settings file and the scan: two windows are
        for looking at two repositories at once, so each keeps its own
        selection, its own job queue and its own view.
        """
        return MainWindow(self)

    def front_window(self):
        """The window a request should land in, or None when there is none.

        Not props.active_window on its own: that is None whenever nothing of
        ours holds the focus, which is the ordinary state of this app at the
        moment LaRenderer asks it to show a repository. Reading that as "no
        window open" is how a second one appears on every request.
        """
        window = self.props.active_window
        if isinstance(window, MainWindow):
            return window
        for candidate in self.get_windows():
            if isinstance(candidate, MainWindow):
                return candidate
        return None

    def _report_unhandled(self, exc):
        window = self.front_window()
        if window is not None:
            widgets.error_toast(window, exc, context="Background task failed")

    def do_activate(self):
        window = self.front_window() or self.new_window()
        window.present()

    def _on_command_line(self, _app, command_line):
        args = command_line.get_arguments()[1:]
        # Started for another app's sake: no window, just the service, kept
        # alive until the app that asked for it is gone. Sent to an instance
        # that is already running, it only adds that promise. Checked before
        # the window flag, because --background is a request for no window at
        # all and the two together would contradict each other.
        if "--background" in args:
            self._start_background()
            return 0
        # A path argument selects that repository once the window is up.
        wants_new, args = split_new_window_flag(args)
        window = self.new_window() if wants_new else (self.front_window()
                                                      or self.new_window())
        window.present()
        if args:
            window.select_path_arg(args[0])
        return 0

    # ---------------------------------------------------------- background --

    def _start_background(self):
        if self._background:
            return
        self._background = True
        self.hold()
        GLib.timeout_add_seconds(UNCLAIMED_SECONDS, self._stop_if_unclaimed)

    def _stop_if_unclaimed(self):
        if self.service is None or not self.service.attached:
            self._stop_background()
        return GLib.SOURCE_REMOVE

    def _stop_background(self):
        """The last caller left. Exit, unless someone has a window open."""
        if self._background:
            self._background = False
            self.release()

    def _service_show(self, path):
        window = self.front_window() or self.new_window()
        window.present()
        if path:
            window.select_path_arg(path)

    def _service_changed(self, repo):
        for window in self.get_windows():
            if isinstance(window, MainWindow):
                window.repo_changed_elsewhere(repo)


USAGE = """Git Manager — every git repository on this machine, in one window.

Usage:
  git-manager [PATH]          open the window, with PATH's repository selected
  git-manager -n, --new-window [PATH]
                              the same, in a window of its own
  git-manager --background    serve other apps (LaRenderer) without a window

A second `git-manager` hands its path to the window already open; -n gives it
one of its own, which is how you get two repositories on screen at once.
Ctrl+N does the same from inside.
"""


def main():
    # GApplication hands unknown options on to the primary instance, which
    # would otherwise take --help for a path and select nothing.
    if any(flag in ("-h", "--help") for flag in sys.argv[1:]):
        print(USAGE.strip())
        return 0
    winenv.set_app_id(APP_ID)
    return GitManagerApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
