"""Application entry point.

`--help` and `--version` are answered by cli.py before GTK is imported; what
reaches here is the window, and boards to open. A second `clio` hands them to the
window already open.
"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from . import updater, widgets, winenv  # noqa: E402
from .view import refresh_notes_once  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "com.robin.Clio"


def parse_args(args):
    """The boards to open, without a display."""
    return [a for a in args if not a.startswith("-") or a[1:2].isdigit()]


class ClioApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.connect("command-line", self._on_command_line)

    def do_startup(self):
        Adw.Application.do_startup(self)
        winenv.use_icon(self, "clio.ico")
        updater.watch(self, "Clio", "clio.py")
        widgets.install_css()
        # [[links]] in an event's notes resolve through the Order's note index.
        refresh_notes_once()
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: [w.close() for w in self.get_windows()])
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def window(self):
        for window in self.get_windows():
            if isinstance(window, MainWindow):
                return window
        return MainWindow(self)

    def do_activate(self):
        self.window().present()

    def _on_command_line(self, _app, command_line):
        window = self.window()
        window.present()
        for argument in parse_args(command_line.get_arguments()[1:]):
            path = command_line.create_file_for_arg(argument).get_path()
            if path and not window.view.open(path):
                widgets.toast(window.view, f"Not a timeline board: {argument}")
        return 0


def main():
    winenv.set_app_id(APP_ID)
    return ClioApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
