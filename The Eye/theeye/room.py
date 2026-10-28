"""The Eye as a room of the Mind Palace.

The Mind Palace is one window with every app in it, beside the standalone
apps rather than instead of them. An app joins it through a module like this
one: a title, an icon, a place in the order, and `build(host)`, which returns
the room — the app's whole interface as one widget, with no header bar of its
own, and the actions and keys that go with it.

The room is the same window The Eye opens on its own, built with
`hosted=True` and never presented: its content is taken out and handed to
the palace, and the window stays behind as the object its actions belong to.
Being hosted changes only what being a window would have meant:

* **The stylesheet is scoped** to the room's content (`ROOM_SCOPE`). On its
  own The Eye restyles the whole display — buttons, labels, popovers — and
  forces light or dark to match its theme; in a palace that would be every
  other room's buttons too.
* **Dialogs and files** are parented on the content, and so land in the
  palace's window. Escape, Q and Ctrl+W close what is in front — a dialog,
  the palette, the pages, the question — and never the palace.
* **The glass bar** loses its close button and stops being a handle that
  drags the window, which is the palace's to move.
* **The size** it was left at is not saved, because this window was never
  on screen; the narrow layout follows the room's width instead.

Everything else is The Eye as it is: the same settings file, the same
remembered positions, the same panels.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw  # noqa: E402

from . import config as configuration, media, player, shortcuts  # noqa: E402
from .app import starting_point  # noqa: E402
from .window import NARROW, ROOM_SCOPE, MainWindow, guard_typing  # noqa: E402

TITLE = "The Eye"
ICON = "the-eye"
ORDER = 60


class Room:
    """The Eye's interface, its actions and its keys, for the palace to show."""

    def __init__(self, host):
        self.host = host
        player.init()
        self.config = configuration.Config()
        self.state = configuration.State()
        # No application: a window that has one is in its list of windows,
        # and an unpresented window there is one the palace might present.
        self.window = MainWindow(None, self.config, self.state, hosted=True)
        content = self.window.get_content()
        self.window.set_content(None)
        content.add_css_class(ROOM_SCOPE)

        # The window's own breakpoint measures a window that is never on
        # screen, so the room measures itself.
        self.widget = Adw.BreakpointBin(child=content)
        self.widget.set_size_request(360, 240)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"max-width: {NARROW}px"))
        narrow.connect("apply", lambda *_: self.window._set_narrow(True))
        narrow.connect("unapply", lambda *_: self.window._set_narrow(False))
        self.widget.add_breakpoint(narrow)
        # The code says win.*; the palace inserts the same group as room.
        self.widget.insert_action_group("win", self.window)
        self.widget.connect("map", lambda *_: self._guard_host())

        self.actions = self.window
        self.accels = [("room." + action.split(".", 1)[1], list(keys))
                       for action, keys in shortcuts.accelerators()
                       if action.startswith("win.")]
        self.window.open_path(starting_point(self.state))

    def _guard_host(self):
        """The palace's own window, made to let a text field have its keys
        before the shortcuts — the same thing The Eye does for its own."""
        root = self.widget.get_root()
        if root is not None and hasattr(root, "observe_controllers"):
            guard_typing(root)

    # -- the contract ------------------------------------------------------

    def open(self, path) -> bool:
        """Open a picture, a video, a playlist or a folder; False for anything else."""
        path = os.path.abspath(os.path.expanduser(str(path)))
        if not (os.path.isdir(path) or media.is_viewable(path) or media.is_playlist(path)):
            return False
        self.window.open_path(path)
        return True

    def save_state(self):
        self.window._remember_position()
        self.config.save()
        self.state.save()

    def can_close(self) -> bool:
        """Not while a video is being exported or pages are still being read."""
        export = self.window._export
        if export is not None and export.running():
            return False
        return not self.window.importer.filer.busy()

    def shutdown(self):
        """Put the player away — for a palace that closes a room before quitting."""
        self.save_state()
        self.window.engine.shutdown()
        self.window.destroy()


def build(host) -> Room:
    return Room(host)
