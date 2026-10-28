"""Mnemosyne as a room of Mind Palace.

Mind Palace is the one window every app here can also live in, a room each
(`Mind Palace/ROOMS.md`). A room is this module: a title, an icon, a place in
the order, and `build(host)`, which gives back an object holding the whole UI
as one widget.

Nothing is rebuilt for it. The room is the ordinary window built with the
host in hand (window.py, *It may not be the window*): the same six pages and
the same activities, under a bar of the room's own buttons rather than a
header bar, without glass of its own, and with the window — never presented,
never given to the application — standing behind the widget as its "win"
action group. Run on its own, Mnemosyne is exactly what it was.

What the palace gets:

* `widget` — the whole room, no header bar;
* `actions` — the window's actions, for the palace to insert as "room";
* `accels` — the keys, as `room.*`, for the palace to apply while the room is
  the one on show (inside the room they also work on their own);
* `open(path)` — `"study:<note>"` studies that note's cards, the palace's way
  of saying what `mnemosyne --study <note>` says; anything else is declined,
  because a Markdown file is Notedown's to open, not this room's;
* `save_state()` and `can_close()` — every answer is written the moment it is
  given, so there is never anything to lose by closing.

What it uses of the host: `host.open(path)` to open a card's note in whichever
room opens notes, and `host.ask_claude(prompt, files)` for Explain and Ask —
the palace's Claude, which every room shares. Toasts and dialogs stay inside
the room's own widget, as they do in the window.
"""

from __future__ import annotations

import os

TITLE = "Mnemosyne"
ICON = "mnemosyne"
ORDER = 40

STUDY = "study:"


class Room:
    def __init__(self, host):
        from .library import Library                         # noqa: PLC0415
        from .window import SHORTCUTS, MainWindow            # noqa: PLC0415

        self.host = host
        self.window = MainWindow(getattr(host, "app", None), Library(), host=host)
        self.widget = self.window.room_widget
        self.actions = self.window
        self.accels = [(action.replace("win.", "room.", 1), list(keys))
                       for action, keys in SHORTCUTS]

    def open(self, path) -> bool:
        text = str(path)
        if not text.startswith(STUDY):
            return False
        note = text[len(STUDY):]
        if not os.path.isfile(note):
            return False
        self.window.study_note(note)
        return True

    def save_state(self) -> None:
        # The page it was on and the card size; a room has no size of its own.
        self.window.config.save()

    def can_close(self) -> bool:
        return True


def build(host) -> Room:
    return Room(host)
