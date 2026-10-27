"""Application entry point."""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib  # noqa: E402

from . import (config as configuration, effects, player, shortcuts,  # noqa: E402
               updater, widgets, winenv)
from .theme import get as get_theme  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "com.robin.TheEye"

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


class TheEyeApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        # One settings file and one set of remembered positions for the whole
        # process, shared by every window: two windows that disagreed about
        # where you stopped watching something would be worse than useless.
        self.config = configuration.Config()
        self.state = configuration.State()
        self.connect("command-line", self._on_command_line)

    def do_startup(self):
        Adw.Application.do_startup(self)
        winenv.use_icon(self, "the-eye.ico")
        updater.watch(self, "The Eye", "the-eye.py")
        widgets.install_css(get_theme(self.config["theme"]),
                            self.config["translucency"])
        if not player.init():
            # Not fatal: the photograph half of the app works without it, and
            # saying so once is better than failing per file.
            print("the-eye: GStreamer could not start; video will not play",
                  file=sys.stderr)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self._quit())
        self.add_action(quit_action)

        new_window = Gio.SimpleAction.new("new-window", None)
        new_window.connect("activate", lambda *_: self.new_window().present())
        self.add_action(new_window)

        # Every accelerator comes from the one table, so a key cannot be
        # bound to an action that does not exist.
        for action, keys in shortcuts.accelerators():
            self.set_accels_for_action(action, list(keys))

    def new_window(self):
        """Another window, on whatever it is pointed at.

        Windows share the settings and the remembered positions and nothing
        else: a reel, what is playing in it and the grade on it all belong to
        the window that opened them, which is what makes a second window
        useful — one folder in each.
        """
        return MainWindow(self, self.config, self.state)

    def front_window(self):
        """The window a request from outside should land in, or None.

        Not `props.active_window` on its own: that is None whenever nothing of
        ours holds the focus, which is the ordinary state of an app at the
        moment a file manager asks it to open something. Reading that as "no
        windows open" is how a second window appears every time you
        double-click a photograph.
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

    def do_shutdown(self):
        # The shared offscreen renderer has to be unrealized before the
        # process goes away or GSK asserts on its way out.
        effects.shutdown()
        self.config.save()
        self.state.save()
        Adw.Application.do_shutdown(self)

    def _quit(self):
        # Through the windows, so that a video's position is written down and
        # an export in flight gets its confirmation.
        windows = [w for w in self.get_windows() if isinstance(w, MainWindow)]
        if windows:
            for window in windows:
                window.close()
        else:
            self.quit()

    def _on_command_line(self, _app, command_line):
        wants_new, args = split_new_window_flag(
            command_line.get_arguments()[1:])
        window = (self.new_window() if wants_new
                  else (self.front_window() or self.new_window()))
        window.present()
        if args:
            # Relative paths are relative to wherever the command was typed,
            # which is not this process's directory when it is a second
            # instance handing its arguments over.
            cwd = command_line.get_cwd() or os.getcwd()
            target = args[0]
            if not os.path.isabs(target):
                target = os.path.join(cwd, target)
            window.open_path(target)
        elif self.state.recent_files():
            # Nothing asked for: carry on from the last thing looked at, which
            # is almost always what was wanted.
            window.open_path(self.state.recent_files()[0])
        elif self.state.recent_folders():
            window.open_path(self.state.recent_folders()[0])
        else:
            window.open_path(_pictures_folder())
        return 0


def _pictures_folder() -> str:
    """Somewhere to start on a machine that has never run this before."""
    pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    for candidate in (pictures, os.path.expanduser("~/Pictures"),
                      os.path.expanduser("~")):
        if candidate and os.path.isdir(candidate):
            return candidate
    return os.getcwd()


USAGE = """The Eye — a viewer for photographs and video.

Usage:
  the-eye [FILE OR FOLDER]
  the-eye -n, --new-window [FILE OR FOLDER]    in a window of its own

With no argument it reopens whatever was last looked at, and failing that your
Pictures folder. A file is opened as a position in its folder, so Next and
Previous work from the moment it appears. A second `the-eye` hands what it was
given to the window already open; -n gives it one of its own.

Everything else lives in the window — press ? for the list of keys.
"""


def list_types(out=None):
    """What this machine can open, one `kind .ext` a line.

    For windows/install.ps1, which registers The Eye for exactly these rather
    than for a list written down once on a machine with more loaders than
    this one. Not in the usage text: it is for the installer, not for people.
    """
    from . import media

    out = out or sys.stdout
    types = media.claimed_types(player.init() and player.available())
    for kind in ("photo", "raw", "video", "audio"):
        for extension in sorted(types[kind]):
            print(f"{kind} {extension}", file=out)


def main():
    # Before GApplication sees them: it hands unknown options to the remote
    # instance, which would otherwise treat --help as a file to open.
    for flag in sys.argv[1:]:
        if flag in ("-h", "--help"):
            print(USAGE.strip())
            return 0
        if flag in ("-V", "--version"):
            from . import __version__

            print(f"The Eye {__version__}")
            return 0
        if flag == "--list-types":
            list_types()
            return 0

    winenv.set_app_id(APP_ID)
    return TheEyeApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
