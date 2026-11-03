"""Every board, read from the brain and kept in step with it.

The files are the truth (SYSTEM.md §1): Clio holds what it last read and what
it last wrote, and nothing else. A board changed by something else — the
Anamnesis viewer, a text editor, `order sync` bringing another machine's edit
— is noticed by its stamp (modification time and size) differing from the one
Clio remembers, and read again.

No GTK here; the suite checks it without a display.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from . import brain, timelines
from .timelines import Board


def stamp(path) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _key(path) -> str:
    # One spelling per file: a listed folder and a parsed path can disagree about
    # the slashes on MSYS2's Python, and then nothing would ever look unchanged.
    return os.path.normcase(os.path.abspath(str(path)))


class Library:
    def __init__(self, where: brain.Places | None = None):
        self.where = where
        self.boards: list[Board] = []
        self._stamps: dict[str, tuple[int, int] | None] = {}

    @property
    def folder(self) -> Path:
        return timelines.folder(self.where)

    def holds(self, path) -> bool:
        return _key(Path(path).parent) == _key(self.folder)

    def exists(self) -> bool:
        return (self.where or brain.places()).exists()

    def refresh(self) -> None:
        self.boards = timelines.load_boards(self.where)
        self._stamps = {_key(board.path): stamp(board.path) for board in self.boards}

    def on_disk(self) -> dict[str, tuple[int, int] | None]:
        found = {}
        if self.folder.is_dir():
            for file in self.folder.iterdir():
                if file.suffix.lower() == ".md" and not file.name.startswith("."):
                    found[_key(file)] = stamp(file)
        return found

    def remember(self, board: Board) -> None:
        self._stamps[_key(board.path)] = stamp(board.path)

    def save(self, board: Board) -> None:
        """Write a board, and remember that this was us."""
        board.save()
        self.remember(board)

    def changed_elsewhere(self, board: Board) -> bool:
        return board.path is not None and stamp(board.path) != self._stamps.get(_key(board.path))

    def find(self, path):
        for board in self.boards:
            if board.path is not None and _key(board.path) == _key(path):
                return board
        return None

    def new_board(self, title, born=None) -> Board:
        board = timelines.new_board(title, born, where=self.where)
        self.boards.append(board)
        self.remember(board)
        return board

    def resolve(self, target: str, near=None):
        """The file a `[[link]]` in an event's notes names, through the Order's index."""
        try:
            return brain.Notes.shared().resolve(target, near=near)
        except (sqlite3.Error, OSError):
            return None
