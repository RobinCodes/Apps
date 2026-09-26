"""Application entry point."""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from . import backend, config, widgets  # noqa: E402
from .manager import Manager  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "com.robin.ClaudeDesk"

NEW_WINDOW_FLAGS = ("--new-window", "-n")


def split_new_window_flag(args):
    """Pull the new-window flag out of a command line.

    Kept out of the application object so it can be tested without a display,
    and so the flag is gone before anything downstream can mistake it for a
    folder to start a session in.
    """
    wants_new = False
    rest = []
    for arg in args:
        if arg in NEW_WINDOW_FLAGS:
            wants_new = True
        else:
            rest.append(arg)
    return wants_new, rest


class ClaudeDeskApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.cfg = None
        self.manager = None
        self.connect("command-line", self._on_command_line)

    def do_startup(self):
        Adw.Application.do_startup(self)
        widgets.install_css()
        display = Gdk.Display.get_default()
        if display is not None:
            widgets.ensure_icons(display)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self._quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

        new_window = Gio.SimpleAction.new("new-window", None)
        new_window.connect("activate", lambda *_: self.new_window().present())
        self.add_action(new_window)
        # Ctrl+N is already a new session, which is the far commoner thing to
        # want; a whole window is the shifted version of it.
        self.set_accels_for_action("app.new-window", ["<Control><Shift>n"])

    def _ensure_backend(self):
        """The settings and the sessions, made once and shared by every window.

        The Manager owns the live Claude processes and rations them against a
        budget. One per window would mean a second set of children over the
        same sessions, twice the budget, and two writers of one file.
        """
        if self.cfg is None:
            self.cfg = config.Config()
        if self.manager is None:
            self.manager = Manager(self.cfg)
        return self.cfg, self.manager

    def new_window(self):
        """Another window onto the same sessions.

        The sessions are the application's, so both windows list all of them
        and either can drive any one; what a window keeps to itself is which
        session it is showing.
        """
        cfg, manager = self._ensure_backend()
        return MainWindow(self, cfg, manager)

    def front_window(self):
        """The window a request should land in, or None when there is none.

        Not props.active_window on its own: that is None whenever nothing of
        ours holds the focus, which is the ordinary state of this app at the
        moment a terminal hands it a folder. Reading that as "no window open"
        is how a second one appears on every invocation.
        """
        window = self.props.active_window
        if isinstance(window, MainWindow):
            return window
        for candidate in self.get_windows():
            if isinstance(candidate, MainWindow):
                return candidate
        return None

    def do_activate(self):
        window = self.front_window() or self.new_window()
        window.present()

    def _quit(self):
        # One shutdown for the one Manager, however many windows are showing
        # it: every child is stopped and the transcripts are flushed once.
        if self.manager is not None:
            self.manager.shutdown()
        self.quit()

    def _on_command_line(self, _app, command_line):
        wants_new, args = split_new_window_flag(command_line.get_arguments()[1:])
        window = self.new_window() if wants_new else (self.front_window()
                                                      or self.new_window())
        window.present()
        if not args:
            return 0
        path = os.path.abspath(args[0])
        if os.path.isfile(path):
            path = os.path.dirname(path)
        if os.path.isdir(path):
            session = window.manager.new_session(cwd=path)
            session.meta.name = os.path.basename(path) or path
            window.manager.save()
            window._rebuild_sidebar()
            window.select(session)
        return 0


def _report_missing_cli(message):
    """Say it on stderr, and on screen if there is no terminal to say it in.

    Launched from a menu entry stderr goes to ~/.xsession-errors, where the
    app looks like it silently refused to start. That is worth a dialog.
    """
    print(message, file=sys.stderr)
    if sys.stderr.isatty():
        return
    app = Adw.Application(application_id=APP_ID + ".Error")

    def show(_app):
        dialog = Adw.MessageDialog(heading="Claude Code not found", body=message)
        dialog.add_response("close", "Close")
        dialog.connect("response", lambda *_: app.quit())
        app.add_window(dialog)
        dialog.present()

    app.connect("activate", show)
    app.run([])


USAGE = """Claude Desk — several Claude Code sessions in one native window.

Usage:
  claude-desk [FOLDER]
  claude-desk -n, --new-window [FOLDER]    in a window of its own

With a folder, a session is started in it. A second `claude-desk` hands what
it was given to the window already open; -n gives it one of its own, which is
how you get two sessions on screen at once. Ctrl+Shift+N does the same from
inside (Ctrl+N is a new session in the window you are in). The sessions
themselves are shared: both windows list all of them, and either can drive any
one of them.
"""


def main():
    # GApplication hands unknown options to the running instance, which would
    # otherwise take --help for a folder and start a session in it.
    for flag in sys.argv[1:]:
        if flag in ("-h", "--help"):
            print(USAGE.strip())
            return 0
        if flag in ("-V", "--version"):
            from . import __version__

            print(f"Claude Desk {__version__}")
            return 0

    if backend.resolve_bin() is None:
        _report_missing_cli(
            f"Claude Desk needs the `{backend.CLAUDE_BIN}` command on your PATH.\n"
            "Install Claude Code, or point CLAUDE_DESK_BIN at the binary."
        )
        return 1
    return ClaudeDeskApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
