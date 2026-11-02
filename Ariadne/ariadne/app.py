"""Application entry point.

`--ask` with words after it is answered by cli.py before GTK is imported;
what reaches here is the window, `--ask` on its own (the window, straight on
a new question), and files to open. A second `ariadne` hands all of that to
the window already open.
"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib  # noqa: E402

from . import updater, widgets, winenv  # noqa: E402
from .view import refresh_notes_once  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "com.robin.Ariadne"


def parse_args(args):
    """`(ask?, files)`, without a display."""
    ask = "--ask" in args
    return ask, [a for a in args if a != "--ask" and not a.startswith("-")]


class AriadneApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.connect("command-line", self._on_command_line)

    def do_startup(self):
        Adw.Application.do_startup(self)
        winenv.use_icon(self, "ariadne.ico")
        updater.watch(self, "Ariadne", "ariadne.py")
        widgets.install_css()
        # [[links]] in thoughts resolve through the Order's note index.
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
        ask, files = parse_args(command_line.get_arguments()[1:])
        window = self.window()
        window.present()
        for argument in files:
            path = command_line.create_file_for_arg(argument).get_path()
            if path and not window.view.open(path):
                widgets.toast(window.view, f"Not a question or a thread: {argument}")
        if ask:
            GLib.idle_add(lambda: (window.view.keep_question(), False)[1])
        return 0


def main():
    winenv.set_app_id(APP_ID)
    return AriadneApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
