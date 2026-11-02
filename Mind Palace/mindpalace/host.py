"""The host: the palace, as one room sees it. ROOMS.md is the contract this keeps."""

from __future__ import annotations


class Host:
    """What `build(host)` is given. One per room, so `set_subtitle` knows whose it is."""

    def __init__(self, palace, title: str):
        self._palace = palace
        self.title = title

    @property
    def app(self):
        """The running application."""
        return self._palace.get_application()

    @property
    def parent(self):
        """A widget to present dialogs from: the palace's window."""
        return self._palace

    def toast(self, text: str, undo=None) -> None:
        """A message on the palace's toast overlay; `undo` adds an Undo button that calls it."""
        self._palace.toast(text, undo)

    def open(self, path) -> bool:
        """Open a file, a folder or a `scheme:` target in the right room, else the desktop."""
        return self._palace.open_target(str(path), source=self.title)

    def show(self, title: str) -> bool:
        """Go to the room called `title`. False if there is none."""
        return self._palace.show_room(title)

    def ask_claude(self, prompt: str = "", files=()) -> None:
        """Open the palace's Claude panel; a prompt is asked at once, about `files`."""
        self._palace.ask_claude(prompt, files)

    def set_subtitle(self, text: str = "") -> None:
        """What this room is showing, under its title in the header bar."""
        self._palace.set_subtitle(self.title, text)
