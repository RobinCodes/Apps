"""Application entry point."""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from . import updater, winenv  # noqa: E402
from .window import PalaceWindow  # noqa: E402

APP_ID = "com.robin.MindPalace"


def split_arguments(args):
    """(room to show, things to open) from a command line. No display needed to test it."""
    room, targets, rest = None, [], list(args)
    while rest:
        arg = rest.pop(0)
        if arg == "--room" and rest:
            room = rest.pop(0)
        elif arg.startswith("--room="):
            room = arg[len("--room="):]
        else:
            targets.append(arg)
    return room, targets


class PalaceApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.connect("command-line", self._on_command_line)

    def do_startup(self):
        Adw.Application.do_startup(self)
        winenv.use_icon(self, "mind-palace.ico")
        updater.watch(self, "Mind Palace", "mind-palace.py")
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self._quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def front_window(self):
        """One palace per process: a request from outside lands in the one that is open."""
        for window in self.get_windows():
            if isinstance(window, PalaceWindow):
                return window
        return None

    def do_activate(self):
        (self.front_window() or PalaceWindow(self)).present()

    def _quit(self):
        # Through the window, so a room that is busy gets to say so.
        window = self.front_window()
        if window is not None:
            window.close()
        else:
            self.quit()

    def _on_command_line(self, _app, command_line):
        room, targets = split_arguments(command_line.get_arguments()[1:])
        window = self.front_window() or PalaceWindow(self)
        window.present()
        if room:
            window.show_room(room)
        for target in targets:
            # Relative to where the command was run, not to where the palace was.
            if not os.path.isabs(target) and os.path.exists(
                    os.path.join(command_line.get_cwd() or "", target)):
                target = os.path.join(command_line.get_cwd() or "", target)
            window.open_target(target)
        return 0


USAGE = """Mind Palace — every app here in one window, each a room.

Usage:
  mind-palace [FILE ...]
  mind-palace --room NAME

A file opens in whichever room opens that kind of thing, and on the desktop if
none does. A second `mind-palace` hands what it was given to the window already
open. Inside: Ctrl+K searches everything, Ctrl+Shift+A asks Claude, Ctrl+1…9 go
to a room.
"""


def main():
    # GApplication hands unknown options to the remote instance, which would
    # otherwise treat --help as a file and try to open it.
    for flag in sys.argv[1:]:
        if flag in ("-h", "--help"):
            print(USAGE.strip())
            return 0
        if flag in ("-V", "--version"):
            from . import __version__

            print(f"Mind Palace {__version__}")
            return 0

    winenv.set_app_id(APP_ID)
    return PalaceApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
