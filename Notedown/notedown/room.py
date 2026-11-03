"""Notedown as a room of Mind Palace.

Mind Palace is the one window every app here can also live in, a room each
(`Mind Palace/ROOMS.md`). A room is this module: a title, an icon, a place in
the order, and `build(host)`, which gives back an object holding the whole UI
as one widget.

Nothing is rebuilt for it. The room is the ordinary window built with the host
in hand (window.py, *It may not be the window*): the same tabs, panes and menus,
under a bar of the room's own buttons rather than a header bar, without glass of
its own, and with the window — never presented, never given to the application —
standing behind the widget as its "win" action group. Run on its own, Notedown is
exactly what it was.

What the palace gets:

* `widget` — the whole room, no header bar;
* `actions` — the window's actions, for the palace to insert as "room";
* `accels` — the keys, as `room.*`, for the palace to apply while the room is the
  one on show;
* `open(path)` — a Markdown file opens in a tab here; anything else is declined;
* `context()` — the note in front, which is what Ask Claude is about;
* `save_state()` — the open tabs and the text size, a room having no size of its
  own; `can_close()` — not while a tab has changes that are not on disk, and then
  it asks, inside the room, what to do with them.

What it uses of the host: `host.open("study:<note>")` for Study This Note, so the
note's cards come up in Mnemosyne's room rather than a window of their own;
`host.ask_claude` for Ask Claude, the panel every room shares; and `host.set_subtitle`
to say which note is in front. Toasts and dialogs stay inside the room's own widget.

The palace keeps a few keys for itself whatever has the focus (ROOMS.md), and some
of Notedown's are among them: Ctrl+K, which here makes a link; Ctrl+1 to Ctrl+7, the
headings; Ctrl+`,`, Ctrl+?, F9 and Ctrl+Shift+A. There they mean what they mean
everywhere in the palace, so the room does not ask for them, and what they did here
is all in the bar's two menus.
"""

from __future__ import annotations

import os

TITLE = "Notedown"
ICON = "notedown"
ORDER = 10
OPENS = (".md", ".markdown", ".mdown", ".mkd", ".mkdn", ".mdwn", ".mdtxt", ".mdtext",
         ".text", ".rmd", ".qmd")

# Caught by the palace before a room can see them (ROOMS.md).
PALACE_KEYS = {"<Control>k", "<Control>comma", "<Control>question", "F9",
               "<Control><Shift>a"}


class Room:
    def __init__(self, host):
        from . import brain, jobs                               # noqa: PLC0415
        from .window import ACCELERATORS, MainWindow            # noqa: PLC0415

        self.host = host
        self.window = MainWindow(getattr(host, "app", None), host=host)
        self.widget = self.window.room_widget
        self.actions = self.window
        keys = list(ACCELERATORS) + [(f"win.view-{mode}", [f"<Alt>{index}"]) for index, mode
                                     in enumerate(("editor", "split", "preview"), start=1)]
        self.accels = [(action.replace("win.", "room.", 1), kept) for action, kept
                       in ((a, [k for k in wanted if k not in PALACE_KEYS]) for a, wanted in keys)
                       if kept]
        # The Order's index, brought up to date once, off the main loop; when it
        # has been, the links on the page look again. The application does this
        # for its own windows, and here there is none of its own.
        jobs.run(brain.Notes.shared().refresh, lambda _changed: self.window.notes_changed(),
                 lambda _exc: None)

    def open(self, path) -> bool:
        text = str(path)
        if not text.lower().endswith(OPENS) or not os.path.isfile(text):
            return False
        return self.window.open_path(text) is not None

    def save_state(self) -> None:
        self.window.config["open_files"] = [d.path for d in self.window.documents if d.path]
        self.window._remember_geometry()

    def can_close(self) -> bool:
        return self.window.can_leave()

    def shown(self) -> None:
        self.window._on_active_changed()

    def shutdown(self) -> None:
        for document in self.window.documents:
            document.close()

    def context(self) -> list[str]:
        path = self.window.path
        return [path] if path else []


def build(host) -> Room:
    return Room(host)
