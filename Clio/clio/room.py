"""Clio as a room of Mind Palace.

Mind Palace is the one window every app here can also live in, a room each. A
room is this module: a title, an icon, a place in the order, and `build(host)`,
which gives back the whole UI as one widget.

Nothing is rebuilt for it. The room's widget is `ClioView`, the same view Clio's
own window puts its header bar over; the palace gets:

* `widget` — the view, with no header bar of its own;
* `actions` — its `room` action group, for the palace to insert as "room" (the
  view already wears it under that name, so its own buttons work too);
* `accels` — the keys, as `room.*`, for the palace to apply while the room is the
  one on show;
* `open(path)` — a board's file opens here; anything else is declined, because a
  note is Notedown's to open;
* `save_state()` — what was open, and whether the list was showing;
  `can_close()` — always: every edit is on disk the moment it is saved.

Of the host it uses `set_subtitle`, to say which board the room is showing, and
`open(path)`, to send a `[[link]]` in an event's notes on to whichever room opens
notes. Toasts and dialogs stay inside the view, as they do in the window, and the
stylesheet is Clio's own rules alone (`widgets.install_room_css`), so that hosting
it restyles nothing of anyone else's.

The palace keeps a few keys for itself whatever has the focus (ROOMS.md), and
three of Clio's are among them. There they mean what they mean everywhere in the
palace, so the room does not ask for them: Ask Claude is on the board's own bar
and its menu, and the list of boards has its own button when it is hidden.
"""

from __future__ import annotations

TITLE = "Clio"
ICON = "clio"
ORDER = 30
KINDS = ("timeline",)

# Caught by the palace before a room can see them (ROOMS.md).
PALACE_KEYS = {"<Control><Shift>a", "F9", "<Control>question"}


class Room:
    def __init__(self, host):
        from . import widgets                                   # noqa: PLC0415
        from .config import Config                              # noqa: PLC0415
        from .view import ACCELERATORS, ClioView, refresh_notes_once  # noqa: PLC0415

        widgets.install_room_css()
        self.host = host
        self.view = ClioView(Config(), host=host)
        refresh_notes_once()
        self.widget = self.view
        self.actions = self.view.actions
        self.accels = [(action, [k for k in keys if k not in PALACE_KEYS])
                       for action, keys in ACCELERATORS
                       if any(k not in PALACE_KEYS for k in keys)]

    def open(self, path) -> bool:
        return self.view.open(path)

    def save_state(self) -> None:
        self.view.save_state()

    def can_close(self) -> bool:
        return True

    def shown(self) -> None:
        self.view.check_disk()

    def shutdown(self) -> None:
        self.view.cancel_work()

    def context(self) -> list[str]:
        current = self.view.current
        return [str(current.path)] if current is not None and current.path else []


def build(host) -> Room:
    return Room(host)
