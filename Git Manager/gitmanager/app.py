"""Application entry point."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from . import widgets  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "com.robin.GitManager"


class GitManagerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.connect("command-line", self._on_command_line)

    def do_startup(self):
        Adw.Application.do_startup(self)
        widgets.install_css()
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_activate(self):
        window = self.props.active_window or MainWindow(self)
        window.present()

    def _on_command_line(self, _app, command_line):
        # A path argument selects that repository once the window is up.
        args = command_line.get_arguments()[1:]
        self.activate()
        if args:
            window = self.props.active_window
            if window:
                window.select_path_arg(args[0])
        return 0


def main():
    return GitManagerApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
