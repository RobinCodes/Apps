"""Fathom as a room of Mind Palace, and the reference for how to write one.

Mind Palace is the one window every app here can also live in, a room each
(`Mind Palace/ROOMS.md`). A room is this module: a title, an icon, a place in
the order, and `build(host)`, which gives back an object holding the whole UI as
one widget.

Nothing is rebuilt for it. The room is the ordinary window built with the host
in hand and never presented (window.py, *It may not be the window*): the same
tree, map and start page, under a bar of the room's own buttons rather than a
header bar, without glass of its own, and with the window standing behind the
widget as its "win" action group. Run on its own, Fathom is exactly what it was.

What the palace gets:

* `widget` — the whole room, no header bar;
* `actions` — the window's actions, for the palace to insert as "room";
* `accels` — the keys, as `room.*`, for the palace to apply while the room is the
  one on show;
* `open(path)` — a folder is scanned; anything else is declined, because a file
  is its own app's to open, not a disk scanner's;
* `context()` — what is selected, or else what was scanned, for Ask Claude;
* `save_state()` and `shutdown()` — the split and the settings, and any scan in
  flight stopped; `can_close()` is always yes, since a scan is only ever a cache.

What it uses of the host: `host.set_subtitle` to say what is scanned, under the
room's name in the header bar. Toasts and dialogs stay inside the room's own widget, as
they do in the window.
"""

from __future__ import annotations

import os

TITLE = "Fathom"
ICON = "fathom"
ORDER = 70


class Room:
    def __init__(self, host):
        from .window import ACCELERATORS, MainWindow         # noqa: PLC0415

        self.host = host
        # No application: an application keeps running while it has a window,
        # and a room must not keep the palace alive after the palace has closed.
        self.window = MainWindow(None, host=host)
        self.widget = self.window.room_widget
        self.actions = self.window
        self.accels = [(action.replace("win.", "room.", 1), list(keys))
                       for action, keys in ACCELERATORS]
        self.window.connect("notify::title", lambda *_: self._say_what_is_showing())

    def _say_what_is_showing(self) -> None:
        title = self.window.get_title() or ""
        self.host.set_subtitle("" if title == TITLE else title.removesuffix(f" — {TITLE}"))

    def open(self, path) -> bool:
        if not os.path.isdir(str(path)):
            return False
        self.window.start_scan(str(path))
        return True

    def context(self) -> list[str]:
        window = self.window
        node = window._selected
        if node is not None and getattr(node, "path", ""):
            return [node.path]
        return [window.result.path] if window.result is not None else []

    def save_state(self) -> None:
        self.window.save_state()

    def can_close(self) -> bool:
        return True

    def shutdown(self) -> None:
        self.window.cancel_scan()


def build(host) -> Room:
    return Room(host)
