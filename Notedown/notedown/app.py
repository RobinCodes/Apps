"""Application entry point."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from . import widgets  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "com.robin.Notedown"

NEW_WINDOW_FLAGS = ("--new-window", "-n")


def split_new_window_flag(args):
    """Pull the new-window flag out of a command line.

    Kept out of the application object so it can be tested without a display,
    and so the flag is gone before anything downstream can mistake it for a
    file to open.
    """
    wants_new = False
    rest = []
    for arg in args:
        if arg in NEW_WINDOW_FLAGS:
            wants_new = True
        else:
            rest.append(arg)
    return wants_new, rest


def _ordered_unique(paths):
    seen = set()
    out = []
    for path in paths:
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return out


class NotedownApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.connect("command-line", self._on_command_line)
        # Documents belonging to windows that closed earlier in this run.
        self._closed_paths = []

    def do_startup(self):
        Adw.Application.do_startup(self)
        widgets.install_css()

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self._quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

        new_window = Gio.SimpleAction.new("new-window", None)
        new_window.connect("activate", lambda *_: self.new_window().present())
        self.add_action(new_window)
        # Ctrl+N is a new document, which is the far commoner thing to want;
        # a whole window is the shifted version of it.
        self.set_accels_for_action("app.new-window", ["<Control><Shift>n"])

    def new_window(self):
        """Another window, with its own tabs and its own preview.

        Two windows are for two documents side by side, so nothing that
        belongs to a document is shared — only the settings file.
        """
        self._closed_paths = []
        return MainWindow(self)

    def front_window(self):
        """The window a request should land in, or None when there is none.

        Not props.active_window on its own: that is None whenever nothing of
        ours holds the focus, which is the ordinary state of an editor at the
        moment a file manager asks it to open a document. Reading that as "no
        window open" is how a second one appears on every double-click.
        """
        window = self.props.active_window
        if isinstance(window, MainWindow):
            return window
        for candidate in self.get_windows():
            if isinstance(candidate, MainWindow):
                return candidate
        return None

    def remember_session(self, closing=None):
        """The documents to put back next time, across every window.

        A window that wrote only its own tabs would mean whichever one closed
        last silently forgot every other window's work — and windows close one
        at a time even when the whole app is quitting, so the last one is
        always the smallest.
        """
        windows = [w for w in self.get_windows() if isinstance(w, MainWindow)]
        if closing is not None and closing not in windows:
            windows.append(closing)

        paths = list(self._closed_paths)
        for window in windows:
            paths.extend(d.path for d in window.documents if d.path)

        if closing is not None:
            self._closed_paths.extend(d.path for d in closing.documents if d.path)
            self._closed_paths = _ordered_unique(self._closed_paths)

        return _ordered_unique(paths)

    def do_activate(self):
        window = self.front_window() or self.new_window()
        window.present()

    def _quit(self):
        # Go through the windows so an unsaved document still gets its prompt
        # — app.quit() would take them down without asking anyone.
        windows = [w for w in self.get_windows() if isinstance(w, MainWindow)]
        if windows:
            for window in windows:
                window.close()
        else:
            self.quit()

    def _on_command_line(self, _app, command_line):
        # A path argument opens that document once the window is up. Resolved
        # against the directory the command was typed in, which is not this
        # process's when it is a second `notedown` handing over its arguments.
        wants_new, args = split_new_window_flag(command_line.get_arguments()[1:])
        window = self.new_window() if wants_new else (self.front_window()
                                                      or self.new_window())
        window.present()
        for argument in args:
            path = command_line.create_file_for_arg(argument).get_path()
            window.open_path(path or argument)
        return 0


USAGE = """Notedown — a Markdown editor with the rendered page beside the text.

Usage:
  notedown [FILE.md ...]
  notedown -n, --new-window [FILE]
                             the same, in a window of its own

With no argument it reopens whatever you had open last. A second `notedown`
hands its file to the window already open; -n gives it one of its own, which
is how you get two documents side by side. Ctrl+Shift+N does the same from
inside (Ctrl+N is a new document). Everything else lives in the window — see
"Keyboard shortcuts" in the main menu.
"""


def main():
    # GApplication hands unknown options to the remote instance, which would
    # otherwise treat --help as a filename and open nothing.
    for flag in sys.argv[1:]:
        if flag in ("-h", "--help"):
            print(USAGE.strip())
            return 0
        if flag in ("-V", "--version"):
            from . import __version__

            print(f"Notedown {__version__}")
            return 0

    return NotedownApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
