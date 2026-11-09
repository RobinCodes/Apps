"""Hermione as a room of the Mind Palace.

The Mind Palace is one window with every app in it, beside the standalone
apps rather than instead of them. An app joins it through a module like this
one: a title, an icon, a place in the order, and `build(host)`, which returns
the room — the app's whole interface as one widget, with no header bar of its
own, and the actions and keys that go with it.

The room is the same window Hermione opens on its own, built with
`hosted=True` and never presented: its content is lifted into the palace, and
the window stays behind as the object its actions belong to. It has no
application, so the palace never finds it in its list of windows. Being
hosted changes only what being a window meant:

* **The stylesheet is kept inside the room** (`style.ROOM_SCOPE`). On its own
  Hermione restyles the whole display and forces light or dark to match its
  theme; in a palace that would be every other room's lists and popovers too.
* **The bar loses the window's buttons** and stops being a handle: the close
  button would close the palace, and the window to drag is the palace's.
* **Keys and dialogs live on the content.** Every `win.*` accelerator is
  offered as `room.*`, and every dialog is presented on a widget inside the
  room, so it lands in the palace's window.
* **Files open in the palace first.** Activating a file asks `host.open`, so a
  note goes to the Notedown room and a picture to The Eye's; what no room
  takes goes to the desktop as before.
* **No size is remembered**, no tab is torn off into a window of its own, and
  the narrow layout follows the room's width through a breakpoint of its own.

Everything else is Hermione as it is: the same settings, the same tabs back
from last time, the same inbox, palette and undo.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw  # noqa: E402

from . import housestyle, places, style  # noqa: E402
from .app import ACCELERATORS  # noqa: E402
from .config import Config  # noqa: E402
from .window import MainWindow  # noqa: E402

TITLE = "Hermione"
ICON = "hermione"
ORDER = 50


class Room:
    """Hermione's interface, its actions and its keys, for the palace to show."""

    def __init__(self, host):
        self.host = host
        self.config = Config()
        # In the palace's theme, which is the one in force: see style.room_scheme.
        style.update(self.config, housestyle.system_is_dark(), hosted=True)
        housestyle.system().watch(
            lambda dark: style.update(self.config, dark, hosted=True))

        self.window = MainWindow(None, restore=True, config=self.config, hosted=True)
        self.window.open_elsewhere = self._open_elsewhere
        content = self.window.get_content()
        self.window.set_content(None)
        content.add_css_class(style.ROOM_SCOPE)

        # The window's own breakpoint would measure a window never on screen,
        # so the room measures itself.
        self.widget = Adw.BreakpointBin(child=content)
        self.widget.set_size_request(360, 320)
        self.widget.add_breakpoint(self.window.make_breakpoint())
        # The code says win.*; the palace inserts the same group as room.
        self.widget.insert_action_group("win", self.window)
        self.widget.connect("map", lambda *_a: self._follow_focus())
        self.widget.connect("unmap", lambda *_a: self._stop_following())
        self._focus_handler = None
        self._root = None

        self.actions = self.window
        self.accels = [("room." + name.split(".", 1)[1], list(keys))
                       for name, keys in ACCELERATORS.items() if name.startswith("win.")]

    # -- the window's focus is the palace's now ---------------------------

    def _follow_focus(self) -> None:
        """Keep the toolbar on whichever pane has the focus — the palace's focus.

        On its own the window follows its own focus; hosted, its focus never
        changes, so the room listens to the window it is in.
        """
        self._stop_following()
        root = self.widget.get_root()
        if root is not None:
            self._root = root
            self._focus_handler = root.connect(
                "notify::focus-widget", lambda *_a: self.window.sync_header())
        self.window.sync_header()

    def _stop_following(self) -> None:
        if self._root is not None and self._focus_handler is not None:
            self._root.disconnect(self._focus_handler)
        self._root = self._focus_handler = None

    def _open_elsewhere(self, path: str) -> bool:
        try:
            return bool(self.host.open(path))
        except Exception:                              # noqa: BLE001
            return False                               # the desktop, then

    # -- the contract -------------------------------------------------------

    def open(self, path) -> bool:
        """Go to a folder, or to a file's folder with the file selected."""
        where = places.path_from_argument(str(path))
        if not where or not os.path.exists(where):
            return False
        self.window.select_path_arg(where)
        return True

    def save_state(self) -> None:
        """The tabs, the last folder and the settings — what closing would save."""
        window = self.window
        window._save_session()
        self.config["show_sidebar"] = window.split.get_show_sidebar()
        pane = window.current_pane()
        if pane is not None:
            self.config["last_folder"] = pane.path
        self.config.save()

    def can_close(self) -> bool:
        """Not while a copy, a move or anything else is still running."""
        return not self.window.jobs

    def restyle(self) -> None:
        """The palace's theme or glass has changed: the sheet and the light again."""
        style.update(self.config, housestyle.system_is_dark(), hosted=True)
        self.window._relight()

    def shutdown(self) -> None:
        """For a palace that closes a room before quitting."""
        self.save_state()
        self._stop_following()
        self.window.ask_panel.stop()
        for pane in self.window.all_panes():
            pane.model.shutdown()
        self.window.destroy()


def build(host) -> Room:
    return Room(host)
