"""Application entry point."""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from . import backend, config, widgets  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "com.robin.ClaudeDesk"


class ClaudeDeskApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.cfg = None
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

    def do_activate(self):
        if self.cfg is None:
            self.cfg = config.Config()
        window = self.props.active_window or MainWindow(self, self.cfg)
        window.present()

    def _quit(self):
        window = self.props.active_window
        if window is not None:
            window.manager.shutdown()
        self.quit()

    def _on_command_line(self, _app, command_line):
        args = command_line.get_arguments()[1:]
        self.activate()
        window = self.props.active_window
        if window is None or not args:
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


def main():
    if backend.resolve_bin() is None:
        _report_missing_cli(
            f"Claude Desk needs the `{backend.CLAUDE_BIN}` command on your PATH.\n"
            "Install Claude Code, or point CLAUDE_DESK_BIN at the binary."
        )
        return 1
    return ClaudeDeskApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
