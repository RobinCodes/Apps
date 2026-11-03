"""Every question and thread, read from the brain and kept in step with it.

The files are the truth (SYSTEM.md §1): Ariadne holds what it last read and
what it last wrote, and nothing else. A file changed by something else — the
Anamnesis viewer, a text editor, `order sync` bringing another machine's
edit — is noticed by its stamp (modification time and size) differing from
the one Ariadne remembers, and read again.

No GTK here; the suite checks it without a display.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from . import brain, threads
from .threads import Question, Thread


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


def _same(a, b) -> bool:
    return _key(a) == _key(b)


class Library:
    def __init__(self, where: brain.Places | None = None):
        self.where = where
        self.questions: list[Question] = []
        self.threads: list[Thread] = []
        self._stamps: dict[str, tuple[int, int] | None] = {}

    @property
    def questions_folder(self) -> Path:
        return threads.questions_folder(self.where)

    @property
    def threads_folder(self) -> Path:
        return threads.threads_folder(self.where)

    def exists(self) -> bool:
        return (self.where or brain.places()).exists()

    def refresh(self) -> None:
        self.questions = threads.load_questions(self.where)
        self.threads = threads.load_threads(self.where)
        self._stamps = {_key(item.path): stamp(item.path)
                        for item in [*self.questions, *self.threads]}

    def on_disk(self) -> dict[str, tuple[int, int] | None]:
        """What is in the two folders right now, by path."""
        found = {}
        for folder in (self.questions_folder, self.threads_folder):
            if folder.is_dir():
                for file in folder.iterdir():
                    if file.suffix.lower() == ".md" and not file.name.startswith("."):
                        found[_key(file)] = stamp(file)
        return found

    def remember(self, item) -> None:
        self._stamps[_key(item.path)] = stamp(item.path)

    def save(self, item) -> None:
        """Write a question or a thread, and remember that this was us."""
        item.save()
        self.remember(item)
        self._settle(item)

    def _settle(self, item):
        """A file just written is what was read, as far as `changed` is concerned."""
        fresh = type(item).parse(item.dump(), item.path)
        item._original, item.front.dirty = fresh._original, False
        if isinstance(item, Thread):
            item._was = item._state()
            for node in item.nodes:
                fresh_node = fresh.node(node.id)
                if fresh_node is not None:
                    node._heading, node._body = fresh_node._heading, fresh_node._body
                    node._was = node._snapshot()
        else:
            item._was = list(item.lines)

    def changed_elsewhere(self, item) -> bool:
        """Whether the file is no longer the one Ariadne last read or wrote."""
        return item.path is not None and stamp(item.path) != self._stamps.get(_key(item.path))

    def reread(self, item):
        """A fresh copy of a question or thread from its file, or None if it is gone."""
        if item.path is None or not item.path.is_file():
            return None
        fresh = type(item).load(item.path)
        self.remember(item)
        bag = self.questions if isinstance(item, Question) else self.threads
        for index, old in enumerate(bag):
            if old is item:
                bag[index] = fresh
        return fresh

    def find(self, path):
        for item in [*self.questions, *self.threads]:
            if item.path is not None and _same(item.path, path):
                return item
        return None

    def threads_for(self, question: Question) -> list[Thread]:
        return threads.threads_for(question, self.threads,
                                   resolve=lambda t: self.resolve(t, question.path))

    def questions_of(self, thread: Thread) -> list[Question]:
        wanted = {brain.key(title) for title in thread.answers}
        return [q for q in self.questions if brain.key(q.title) in wanted]

    def resolve(self, target: str, near=None):
        """The file a `[[link]]` names: a question or thread first, then any note."""
        wanted = brain.key(target.replace("\\", "/").rsplit("/", 1)[-1])
        for item in [*self.questions, *self.threads]:
            names = {brain.key(item.title)}
            if item.path is not None:
                names.add(brain.key(item.path.stem))
            if wanted in names:
                return item.path
        try:
            return brain.Notes.shared().resolve(target, near=near)
        except (sqlite3.Error, OSError):
            return None
