"""Application entry point."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from . import widgets  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "com.robin.LaRenderer"


class LaRendererApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        )
        self.connect("command-line", self._on_command_line)

    def do_startup(self):
        Adw.Application.do_startup(self)
        widgets.install_css()
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self._quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_activate(self):
        window = self.props.active_window or MainWindow(self)
        window.present()

    def _quit(self):
        # Go through the window so an unsaved document still gets its prompt.
        window = self.props.active_window
        if window:
            window.close()
        else:
            self.quit()

    def _on_command_line(self, _app, command_line):
        # A path argument opens that document once the window is up.
        args = command_line.get_arguments()[1:]
        self.activate()
        if args:
            window = self.props.active_window
            if window:
                window.select_path_arg(args[0])
        return 0


USAGE = """LaRenderer — a LaTeX editor with a live preview.

Usage:
  larenderer [FILE.tex]

With no argument it reopens whatever you had open last. Everything else lives
in the window — see "Keyboard shortcuts" in the main menu.
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

            print(f"LaRenderer {__version__}")
            return 0

    return LaRendererApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
