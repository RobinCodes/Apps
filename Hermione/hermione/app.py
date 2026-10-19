"""Application entry point.

The accelerator table is the interesting part of this file. Everything the
window and the panes can do is a `Gio.Action`, and the keys are bound here in
one list rather than scattered through the widgets that implement them — so
the answer to "what does Ctrl+D do" is one grep, and the shortcuts window and
the real bindings cannot drift apart without it being obvious.

Windows share the settings object, which is loaded once. Two windows writing
the same file at the same moment would otherwise each save their own idea of
the bookmarks.
"""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from . import housestyle, places, style, updater, winenv  # noqa: E402
from .config import Config  # noqa: E402
from .window import MainWindow  # noqa: E402

APP_ID = "com.robin.Hermione"

NEW_WINDOW_FLAGS = ("--new-window", "-n")

# action -> keys. Window actions are "win.", application ones "app.".
ACCELERATORS = {
    "app.quit": ["<Control>q"],
    "app.new-window": ["<Control>n"],

    "win.new-tab": ["<Control>t"],
    "win.close-tab": ["<Control>w"],
    "win.next-tab": ["<Control>Tab", "<Control>Page_Down"],
    "win.previous-tab": ["<Control><Shift>Tab", "<Control>Page_Up"],
    "win.split": ["F3"],
    "win.other-pane": ["F6"],
    "win.sidebar": ["F9"],
    "win.palette": ["<Control><Shift>p"],

    "win.back": ["<Alt>Left"],
    "win.forward": ["<Alt>Right"],
    "win.up": ["<Alt>Up"],
    "win.home": ["<Alt>Home"],
    "win.refresh": ["<Control>r"],
    "win.edit-path": ["<Control>l"],
    "win.find": ["<Control>f"],

    "win.copy": ["<Control>c"],
    "win.cut": ["<Control>x"],
    "win.paste": ["<Control>v"],
    "win.rename": ["F2"],
    "win.new-folder": ["<Control><Shift>n"],
    "win.duplicate": ["<Control>d"],
    "win.undo": ["<Control>z"],
    "win.redo": ["<Control><Shift>z", "<Control>y"],

    "win.select-all": ["<Control>a"],
    "win.select-none": ["<Control><Shift>a"],
    "win.invert-selection": ["<Control>i"],

    "win.properties": ["<Alt>Return"],
    "win.toggle-hidden": ["<Control>h"],
    "win.toggle-preview": ["F7"],
    "win.list-view": ["<Control>1"],
    "win.grid-view": ["<Control>2"],
    "win.zoom-in": ["<Control>plus", "<Control>equal", "<Control>KP_Add"],
    "win.zoom-out": ["<Control>minus", "<Control>KP_Subtract"],
    "win.terminal": ["<Control><Alt>t"],
    "win.open-with": ["<Control>Return"],

    "win.settings": ["<Control>comma"],
    "win.shortcuts": ["<Control>question", "<Control>slash"],
}


def split_new_window_flag(args):
    """Pull the new-window flag out of a command line.

    Kept out of the application object so it can be tested without a display,
    and so the flag is gone before anything downstream can mistake it for a
    folder to open.
    """
    wants_new = False
    rest = []
    for argument in args:
        if argument in NEW_WINDOW_FLAGS:
            wants_new = True
        else:
            rest.append(argument)
    return wants_new, rest


class HermioneApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.config = Config()
        self.connect("command-line", self._on_command_line)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        winenv.use_icon(self, "hermione.ico")
        # A few seconds after the window is up, on a worker thread: if the
        # checkout's upstream has moved, every window gets a toast offering a
        # restart. APPS_NO_UPDATE=1 turns it off.
        updater.watch(self, "Hermione", "hermione.py")

        # The sheet is generated, and which theme it is generated from can
        # depend on whether the desktop is light or dark, so it is
        # regenerated when the desktop changes its mind. The question is put
        # to the portal rather than to AdwStyleManager, whose answer stops
        # being the desktop's as soon as the sheet forces a colour scheme.
        style.install(self.config, housestyle.system_is_dark())
        housestyle.system().watch(
            lambda dark: style.update(self.config, dark))

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_a: self._quit())
        self.add_action(quit_action)

        new_window = Gio.SimpleAction.new("new-window", None)
        new_window.connect("activate",
                           lambda *_a: self.new_window().present())
        self.add_action(new_window)

        for action, keys in ACCELERATORS.items():
            self.set_accels_for_action(action, keys)

    def new_window(self, path: str = "", restore: bool = False) -> MainWindow:
        """Another window. They share the settings and nothing else."""
        return MainWindow(self, path, restore)

    def front_window(self) -> MainWindow | None:
        """The window a request from outside should land in, or None.

        Not `props.active_window` on its own: that is None whenever nothing of
        ours holds the focus, which is the ordinary state of an app at the
        moment a launcher or another program asks it to open a folder. Reading
        that as "no windows open" is how a second window appears every time.
        """
        window = self.props.active_window
        if isinstance(window, MainWindow):
            return window
        for candidate in self.get_windows():
            if isinstance(candidate, MainWindow):
                return candidate
        return None

    def do_activate(self) -> None:
        window = self.front_window()
        if window is None:
            window = self.new_window(restore=True)
        window.present()

    def _quit(self) -> None:
        # Through the windows, so a job in flight is stopped and the session
        # is written down before the process goes away.
        windows = [w for w in self.get_windows() if isinstance(w, MainWindow)]
        if windows:
            for window in windows:
                window.close()
        else:
            self.quit()

    def _on_command_line(self, _app, command_line) -> int:
        wants_new, args = split_new_window_flag(
            command_line.get_arguments()[1:])
        existing = self.front_window()
        if wants_new or existing is None:
            window = self.new_window(restore=existing is None and not args)
        else:
            window = existing
        window.present()
        if args:
            window.select_path_arg(args[0])
            for extra in args[1:]:
                where = places.path_from_argument(extra)
                if where and os.path.isdir(where):
                    window.new_tab(where)
        return 0


USAGE = """Hermione — a file manager.

Usage:
  hermione [FOLDER | FILE]
  hermione -n, --new-window [FOLDER]    in a window of its own
  hermione --portal                     serve the desktop's file chooser

With no argument it reopens the tabs it had last time. Given a folder it goes
there; given a file it goes to the folder and selects it. A second `hermione`
hands what it was given to the window already open; -n gives it one of its
own. Everything else lives in the window — Ctrl+Shift+P finds any of it, and
"Keyboard shortcuts" in the menu lists the keys.

`--portal` is not for typing. It is what D-Bus starts when something on the
desktop asks for a file to be chosen; see `portal/README.md`.
"""


def main() -> int:
    # GApplication hands unknown options to the remote instance, which would
    # otherwise treat --help as a path and try to open it.
    for flag in sys.argv[1:]:
        if flag in ("-h", "--help"):
            print(USAGE.strip())
            return 0
        if flag in ("-V", "--version"):
            from . import __version__

            print(f"Hermione {__version__}")
            return 0

    # A fallback: the launcher and `python3 -m hermione` both route here
    # before this module is imported, because importing it imports
    # libadwaita and the portal must own its bus name first (see
    # portal.py). Reaching the flag this late still serves, but it has
    # already paid the cost, so the entry points are where it belongs.
    if "--portal" in sys.argv[1:]:
        from . import portal

        return portal.main()

    winenv.set_app_id(APP_ID)
    return HermioneApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
