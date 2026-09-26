"""Application entry point."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from . import widgets  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "com.robin.Fathom"

NEW_WINDOW_FLAGS = ("--new-window", "-n")


def split_new_window_flag(args):
    """Pull the new-window flag out of a command line.

    Kept out of the application object so it can be tested without a display,
    and so the flag is gone before anything downstream can mistake it for a
    folder to scan.
    """
    wants_new = False
    rest = []
    for arg in args:
        if arg in NEW_WINDOW_FLAGS:
            wants_new = True
        else:
            rest.append(arg)
    return wants_new, rest


class FathomApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.connect("command-line", self._on_command_line)

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
        self.set_accels_for_action("app.new-window", ["<Control>n"])

    def new_window(self):
        """Another window, scanning whatever it is pointed at.

        Windows share nothing but the settings file: a scan, its results and
        the map drawn from them all belong to the window that started them,
        which is what makes two of them useful — one disk in each.
        """
        return MainWindow(self)

    def front_window(self):
        """The window a request from outside should land in, or None.

        Not props.active_window on its own: that is None whenever nothing of
        ours holds the focus, which is the ordinary state of an app at the
        moment a launcher or a file manager asks it for something. Reading
        that as "no windows open" is how a second window appears every time
        somebody opens a folder.
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
        # Go through the windows, so a scan in flight is stopped and the
        # geometry is written down before the process goes away.
        windows = [w for w in self.get_windows() if isinstance(w, MainWindow)]
        if windows:
            for window in windows:
                window.close()
        else:
            self.quit()

    def _on_command_line(self, _app, command_line):
        wants_new, args = split_new_window_flag(command_line.get_arguments()[1:])
        window = self.new_window() if wants_new else (self.front_window()
                                                      or self.new_window())
        window.present()
        if args:
            window.select_path_arg(args[0])
        return 0


USAGE = """Fathom — a disk scanner.

Usage:
  fathom [FOLDER]
  fathom -n, --new-window [FOLDER]    in a window of its own

With no argument it opens on the list of filesystems, and reopens whatever was
scanned last. A second `fathom` hands what it was given to the window already
open; -n gives it one of its own, which is how you get two disks on screen at
once. Ctrl+N does the same from inside. Everything else lives in the window —
see "Keyboard shortcuts" in the main menu.
"""


def main():
    # GApplication hands unknown options to the remote instance, which would
    # otherwise treat --help as a path and try to scan it.
    for flag in sys.argv[1:]:
        if flag in ("-h", "--help"):
            print(USAGE.strip())
            return 0
        if flag in ("-V", "--version"):
            from . import __version__

            print(f"Fathom {__version__}")
            return 0

    return FathomApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
