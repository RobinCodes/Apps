"""Application entry point.

Prism needs VTE, and VTE is the one dependency that is not already on a
machine running the other apps in this folder. So the import is checked here
rather than left to fail somewhere in the middle of building a window: a
missing typelib produces a ValueError from gi.require_version that says
nothing about what to install, and the answer is one package.
"""

from __future__ import annotations

import os
import signal
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import updater  # noqa: E402

APP_ID = "com.robin.Prism"

# The actions that belong to the application rather than to a window. Named
# here rather than only inside do_startup so the test suite can check the
# menu against them: an "app.something" in the menu model that nothing
# registers is a dead menu item, and nothing else would notice.
APP_ACTIONS = ("quit", "about")

MISSING_VTE = """Prism needs VTE's GTK 4 build, which is not installed.

    sudo pacman -S vte4                 (Arch)
    sudo apt install libvte-2.91-gtk4-0 (Debian, Ubuntu)
    sudo dnf install vte291-gtk4        (Fedora)

Everything else it needs is already here.
"""


def report_missing_vte() -> int:
    """Say that VTE is missing, somewhere the person will actually see it.

    Printing to stderr is right when this was typed at a prompt and useless
    when it was not: started from a key binding or a desktop entry there is
    nobody reading stderr, and all that happens is that nothing happens. So
    when stderr is not a terminal, the same words go on the screen instead.
    """
    print(MISSING_VTE.strip(), file=sys.stderr)
    try:
        if sys.stderr.isatty():
            return 1
    except (AttributeError, ValueError):
        pass

    try:
        from gi.repository import Gtk

        app = Adw.Application(application_id=APP_ID + ".Missing",
                              flags=Gio.ApplicationFlags.NON_UNIQUE)

        def activate(instance):
            instance.hold()
            alert = Gtk.AlertDialog(
                modal=False,
                message="Prism needs VTE's GTK 4 build",
                detail=MISSING_VTE.split("\n", 1)[1].strip(),
                buttons=["Close"])

            def done(dialog, result):
                try:
                    dialog.choose_finish(result)
                except GLib.Error:
                    pass
                instance.release()
                instance.quit()

            alert.choose(None, None, done)

        app.connect("activate", activate)
        app.run([])
    except Exception:
        # A message about a missing dependency must never be the thing that
        # crashes; the stderr line above has already been written.
        pass
    return 1


def have_vte() -> bool:
    try:
        gi.require_version("Vte", "3.91")
        from gi.repository import Vte  # noqa: F401
    except (ValueError, ImportError):
        return False
    return True


class PrismApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        from .config import Config

        self.config = Config()
        self.connect("command-line", self._on_command_line)
        self._opened_once = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        self._catch_signals()
        # A few seconds after the window is up, on a worker thread: if the
        # checkout's upstream has moved, every window gets a toast offering a
        # restart. APPS_NO_UPDATE=1 turns it off.
        updater.watch(self, "Prism", "prism.py")

        # Registered from the list rather than beside it, so the list cannot
        # drift out of date: each name is the method that runs it.
        for name in APP_ACTIONS:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate",
                           lambda _a, _p, fn=getattr(self, "_" + name): fn())
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control><Shift>q"])

    def _catch_signals(self):
        """Write the session out when something asks us to stop.

        SIGTERM does not run GApplication's shutdown, so a logout, a
        `systemctl --user stop`, or anything else that ends the session the
        polite way would take the tabs with it and leave nothing to put back
        — which is exactly how a sibling app in this folder lost a window
        full of work. Going through `quit()` instead gives every window its
        ordinary close, which is where the session is written.
        """
        def stop(*_args):
            self.quit()
            return GLib.SOURCE_REMOVE

        for number in (signal.SIGTERM, signal.SIGHUP):
            try:
                GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, stop)
            except (AttributeError, ValueError, OSError):
                pass

    def new_window(self, cwd=None, restore=False, command=None):
        from .window import MainWindow

        return MainWindow(self, cwd=cwd, restore=restore, command=command)

    def front_window(self):
        from .window import MainWindow

        window = self.props.active_window
        if isinstance(window, MainWindow):
            return window
        for candidate in self.get_windows():
            if isinstance(candidate, MainWindow):
                return candidate
        return None

    def do_activate(self):
        window = self.front_window()
        if window is None:
            # Only the first window of a run puts the last session back; a
            # second one asked for later is a new window, not a repeat.
            window = self.new_window(restore=not self._opened_once)
            self._opened_once = True
        window.present()

    def _quit(self):
        for window in list(self.get_windows()):
            window.close()

    def do_shutdown(self):
        """Last chance to write anything down.

        `quit()` takes the windows down without asking them to close, so the
        close handler that normally saves the session does not run. Saving
        here as well is harmless when it already has — the file is written
        atomically and says the same thing twice.
        """
        for window in self.get_windows():
            saver = getattr(window, "save_state", None)
            if saver is not None:
                saver()
        Adw.Application.do_shutdown(self)

    def _about(self):
        from . import __version__

        about = Adw.AboutDialog(
            application_name="Prism",
            application_icon="utilities-terminal",
            version=__version__,
            developer_name="Robin",
            comments="A terminal made of glass.",
            license_type=Gtk.License.MIT_X11)
        window = self.front_window()
        if window is not None:
            about.present(window)

    def _on_command_line(self, _app, command_line):
        """`prism [DIR]`, `prism -n`, `prism -e CMD ...`.

        Everything after -e is the command, verbatim and unsplit, because
        that is how every program that launches a terminal passes one and
        re-parsing it would break the first argument containing a space.
        """
        options = command_line.get_arguments()[1:]
        wants_new = False
        run = None
        rest = []
        index = 0
        while index < len(options):
            argument = options[index]
            if argument in ("-n", "--new-window"):
                wants_new = True
            elif argument in ("-e", "--command", "-x"):
                run = options[index + 1:]
                break
            else:
                rest.append(argument)
            index += 1

        cwd = command_line.get_cwd()
        for argument in rest:
            where = command_line.create_file_for_arg(argument).get_path()
            if where and os.path.isdir(where):
                cwd = where

        window = self.front_window()
        if window is None or wants_new:
            window = self.new_window(
                cwd=cwd, restore=window is None and not self._opened_once,
                command=run)
            self._opened_once = True
        else:
            window.new_tab(cwd=cwd, command=run)
        window.present()
        return 0

USAGE = """Prism — a terminal made of glass.

Usage:
  prism [DIRECTORY]
  prism -n, --new-window [DIRECTORY]   the same, in a window of its own
  prism -e COMMAND [ARG ...]           run a command in it
                             (-x is the same; Xfce's helpers use that spelling)

With no argument it reopens the tabs you had last time. A second `prism`
opens a tab in the window already there; -n gives it a window of its own.

Ctrl+Shift+P lists every command there is, which is the fastest way to find
out what this does. Ctrl+Shift+? prints the shortcuts.
"""


def main():
    for flag in sys.argv[1:]:
        if flag in ("-h", "--help"):
            print(USAGE.strip())
            return 0
        if flag in ("-V", "--version"):
            from . import __version__

            print(f"Prism {__version__}")
            return 0

    if not have_vte():
        return report_missing_vte()

    return PrismApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
