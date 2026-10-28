"""Application entry point.

The command-line answers (`--due`, `--print`) never get here — cli.py takes
them before GTK is imported. What does is the window, and `--study FILE`,
which Notedown's "study this note" sends: a second `mnemosyne` hands it to
the window already open, which reads the notes and starts on that note.
"""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from . import updater, widgets, winenv  # noqa: E402
from .cli import USAGE  # noqa: E402
from .library import Library  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "com.robin.Mnemosyne"

NEW_WINDOW_FLAGS = ("--new-window", "-n")


def parse_args(args):
    """`(new window?, the note to study or None, anything left)`, without a display.

    Kept out of the application object so it can be tested, and so that a
    flag is gone before anything downstream can mistake it for a file.
    """
    wants_new = False
    study = None
    rest = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in NEW_WINDOW_FLAGS:
            wants_new = True
        elif arg == "--study" and index + 1 < len(args):
            study = args[index + 1]
            index += 1
        elif arg.startswith("--study="):
            study = arg.split("=", 1)[1]
        else:
            rest.append(arg)
        index += 1
    return wants_new, study, rest


class MnemosyneApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.connect("command-line", self._on_command_line)
        # One library for the process: every window reviews the same cards,
        # and two copies of the deck would be twice the memory for nothing.
        self.library = Library()

    def do_startup(self):
        Adw.Application.do_startup(self)
        winenv.use_icon(self, "mnemosyne.ico")
        updater.watch(self, "Mnemosyne", "mnemosyne.py")
        widgets.install_css()

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self._quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

        new_window = Gio.SimpleAction.new("new-window", None)
        new_window.connect("activate", lambda *_: self.new_window().present())
        self.add_action(new_window)
        self.set_accels_for_action("app.new-window", ["<Control><Shift>n"])

    def new_window(self):
        return MainWindow(self, self.library)

    def front_window(self):
        """The window a request should land in, or None when there is none.

        Not props.active_window on its own: that is None whenever nothing of
        ours holds the focus, which is the ordinary state of this app at the
        moment Notedown asks it to study a note.
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
        windows = [w for w in self.get_windows() if isinstance(w, MainWindow)]
        if windows:
            for window in windows:
                window.close()
        else:
            self.quit()

    def _on_command_line(self, _app, command_line):
        wants_new, study, _rest = parse_args(command_line.get_arguments()[1:])
        window = self.new_window() if wants_new else (self.front_window()
                                                      or self.new_window())
        window.present()
        if study:
            # Relative to wherever the command was typed, which is not this
            # process's directory when it is a second instance handing over.
            cwd = command_line.get_cwd() or os.getcwd()
            path = study if os.path.isabs(study) else os.path.join(cwd, study)
            window.study_note(path)
        return 0


def main():
    # GApplication hands unknown options to the remote instance, which would
    # otherwise treat --help as something to open.
    for flag in sys.argv[1:]:
        if flag in ("-h", "--help"):
            print(USAGE.strip())
            return 0
    winenv.set_app_id(APP_ID)
    return MnemosyneApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
