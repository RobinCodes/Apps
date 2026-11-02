"""Ariadne as a room of Mind Palace.

Mind Palace is the one window every app here can also live in, a room each.
A room is this module: a title, an icon, a place in the order, and
`build(host)`, which gives back the whole UI as one widget.

Nothing is rebuilt for it. The room's widget is `AriadneView`, the same view
Ariadne's own window puts its header bar over; the palace gets:

* `widget` — the view, with no header bar of its own;
* `actions` — its `room` action group, for the palace to insert as "room"
  (the view already wears it under that name, so its own buttons work too);
* `accels` — the keys, as `room.*`, for the palace to apply while the room
  is the one on show;
* `open(path)` — a question's or a thread's file opens here; anything else
  is declined, because a note is Notedown's to open;
* `save_state()` — what was open, and whether the list was showing;
  `can_close()` — always: every edit is on disk a moment after it is made.

Of the host it uses `show(title)`, to say what the room is showing, and
`open(path)`, to send a `[[link]]` to a note on to whichever room opens notes.
Toasts and dialogs stay inside the view, as they do in the window, and the
stylesheet is Ariadne's own rules alone (`widgets.install_room_css`), so that
hosting it restyles nothing of anyone else's.
"""

from __future__ import annotations

TITLE = "Ariadne"
ICON = "ariadne"
ORDER = 50


class Room:
    def __init__(self, host):
        from . import widgets                                   # noqa: PLC0415
        from .config import Config                              # noqa: PLC0415
        from .view import ACCELERATORS, AriadneView, refresh_notes_once  # noqa: PLC0415

        widgets.install_room_css()
        self.host = host
        self.view = AriadneView(Config(), host=host)
        refresh_notes_once()
        self.widget = self.view
        self.actions = self.view.actions
        self.accels = [(action, list(keys)) for action, keys in ACCELERATORS]

    def open(self, path) -> bool:
        return self.view.open(path)

    def save_state(self) -> None:
        self.view.save_state()

    def can_close(self) -> bool:
        self.view.question_page.flush()
        self.view.thread_page.flush()
        return True


def build(host) -> Room:
    return Room(host)
