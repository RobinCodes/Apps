"""The reel: which pictures are in play, and what comes next.

A viewer is almost never opened on a file. It is opened on a file *in a
folder*, and the folder is the point — the next picture, the previous one, the
one you were looking at before, the order they go in. So the unit here is not
a file but a `Reel`: an ordered list of everything viewable in one place,
with a cursor.

Three things make this more than a sorted list.

*It is built off the main loop.* A folder of twenty thousand photographs on a
spinning disk takes seconds to read. `scan()` is blocking and is called
through `jobs.run`; nothing here touches a widget.

*The order is stable.* "Sort by date" on two files written in the same second
must still produce the same order every time, or a picture moves while you
are looking at it. Every sort key ends with the name, which is unique inside
a folder, so every order is total.

*A random order is a shuffle, not a die roll.* Pressing "next" in random mode
walks a permutation and visits everything once before repeating, because the
alternative — a fresh random choice each time — shows you the same picture
twice in five and feels broken.
"""

from __future__ import annotations

import os
import random
import unicodedata

from . import media

SORTS = ("name", "date", "size", "kind", "random")


class Item:
    """One viewable file, and what `os.scandir` already knew about it.

    The stat fields are taken from the scan rather than looked up later: the
    directory entry carries them on every filesystem that matters, and asking
    again for each of twenty thousand files is the difference between a scan
    that takes a moment and one that takes a minute.
    """

    __slots__ = ("path", "name", "kind", "size", "modified", "_sortname")

    def __init__(self, path, name, kind, size, modified):
        self.path = path
        self.name = name
        self.kind = kind                # "photo" | "raw" | "video" | "audio"
        self.size = size
        self.modified = modified
        self._sortname = natural_key(name)

    def is_video(self) -> bool:
        return self.kind == "video"

    def is_audio(self) -> bool:
        return self.kind == "audio"

    def is_playable(self) -> bool:
        return self.kind in ("video", "audio")

    def is_photo(self) -> bool:
        return self.kind in ("photo", "raw")

    def folder(self) -> str:
        return os.path.dirname(self.path)

    def stem(self) -> str:
        return os.path.splitext(self.name)[0]

    def __repr__(self):
        return f"Item({self.name!r}, {self.kind})"


# Digits are compared as numbers so that `IMG_9.jpg` comes before
# `IMG_10.jpg`. Every file manager does this and every program that does not
# gets complained about.
def natural_key(name: str):
    """A sort key where runs of digits compare as numbers.

    Case is folded and accents are normalised first, so that `Ähre` sorts next
    to `Ahre` rather than after `Zebra` — which is what a byte-wise sort does
    and what nobody expects.
    """
    folded = unicodedata.normalize("NFKD", name).casefold()
    parts = []
    digits = ""
    for char in folded:
        if char.isdigit():
            digits += char
        else:
            if digits:
                parts.append((0, int(digits), ""))
                digits = ""
            parts.append((1, 0, char))
    if digits:
        parts.append((0, int(digits), ""))
    return parts


def scan(path: str, recurse: bool = False, hidden: bool = False,
         kinds=None) -> tuple[list[Item], str]:
    """Everything viewable in a folder. Blocking — use `jobs.run`.

    Returns the items and a message about anything that went wrong, which is
    usually a permission denied on one subfolder of many and should not stop
    the rest being shown.
    """
    found: list[Item] = []
    trouble = ""
    roots = [path]
    seen_dirs = set()
    while roots:
        folder = roots.pop(0)
        try:
            real = os.path.realpath(folder)
        except OSError:
            continue
        if real in seen_dirs:
            # A symlink loop. Following one forever is a real way to hang an
            # app on somebody's home directory.
            continue
        seen_dirs.add(real)
        try:
            entries = list(os.scandir(folder))
        except OSError as exc:
            if folder == path:
                return [], str(exc)
            trouble = trouble or f"Some folders could not be read ({exc.strerror})"
            continue
        for entry in entries:
            name = entry.name
            if not hidden and name.startswith("."):
                continue
            try:
                if entry.is_dir(follow_symlinks=True):
                    if recurse:
                        roots.append(entry.path)
                    continue
                if not entry.is_file(follow_symlinks=True):
                    continue
            except OSError:
                continue
            kind = media.kind(name)
            if kind is None or (kinds and kind not in kinds):
                continue
            try:
                stat = entry.stat(follow_symlinks=True)
                size, modified = stat.st_size, stat.st_mtime
            except OSError:
                size, modified = 0, 0.0
            found.append(Item(entry.path, name, kind, size, modified))
    return found, trouble


def sort_items(items: list[Item], order: str, descending: bool = False,
               seed: int = 0) -> list[Item]:
    """A copy of `items` in the asked-for order.

    Every key ends with the natural name, which is unique within a folder, so
    no two items ever compare equal and the order cannot shift between two
    sorts of the same list. With `recurse` on, names can repeat across
    subfolders, so the full path goes on the end as well.
    """
    if order == "random":
        shuffled = list(items)
        random.Random(seed).shuffle(shuffled)
        return shuffled

    if order == "date":
        key = lambda item: (item.modified, item._sortname, item.path)   # noqa: E731
    elif order == "size":
        key = lambda item: (item.size, item._sortname, item.path)       # noqa: E731
    elif order == "kind":
        # Photographs, then videos, then sound, each group by name: the groups
        # are what somebody sorting by kind is looking for.
        rank = {"photo": 0, "raw": 1, "video": 2, "audio": 3}
        key = lambda item: (rank.get(item.kind, 9), item._sortname, item.path)  # noqa: E731
    else:
        key = lambda item: (item._sortname, item.path)                  # noqa: E731

    ordered = sorted(items, key=key)
    return list(reversed(ordered)) if descending else ordered


class Reel:
    """An ordered list of items and a cursor into it.

    The cursor is held as an index, but every method that moves it works from
    the *item* — so re-sorting the reel keeps you on the picture you were
    looking at rather than on the fourth one, whatever the fourth one is now.
    """

    def __init__(self, items=None, order="name", descending=False,
                 wrap=True, seed=0):
        self.order = order
        self.descending = descending
        self.wrap = wrap
        self.seed = seed or random.randrange(1, 1 << 30)
        self.items: list[Item] = []
        self.index = -1
        self.trouble = ""
        if items:
            self.set_items(items)

    # -- contents ---------------------------------------------------------

    def set_items(self, items, keep: str = ""):
        """Replace the contents, staying on `keep` if it is still there."""
        self.items = sort_items(list(items), self.order, self.descending,
                                self.seed)
        self.index = self.index_of(keep) if keep else (0 if self.items else -1)
        if self.index < 0 and self.items:
            self.index = 0

    def resort(self, order=None, descending=None):
        """Change the order without losing your place."""
        here = self.current()
        if order is not None:
            self.order = order if order in SORTS else "name"
        if descending is not None:
            self.descending = bool(descending)
        if self.order == "random":
            # A new shuffle each time random is chosen, or choosing it twice
            # would give the same order and look like it had not worked.
            self.seed = random.randrange(1, 1 << 30)
        self.items = sort_items(self.items, self.order, self.descending, self.seed)
        self.index = self.index_of(here.path) if here else (0 if self.items else -1)

    def __len__(self):
        return len(self.items)

    def __bool__(self):
        return bool(self.items)

    def index_of(self, path: str) -> int:
        for index, item in enumerate(self.items):
            if item.path == path:
                return index
        return -1

    def current(self) -> Item | None:
        if 0 <= self.index < len(self.items):
            return self.items[self.index]
        return None

    def at(self, index: int) -> Item | None:
        if 0 <= index < len(self.items):
            return self.items[index]
        return None

    def paths(self) -> list[str]:
        return [item.path for item in self.items]

    # -- moving -----------------------------------------------------------

    def go_to(self, index: int) -> Item | None:
        if not self.items:
            return None
        self.index = max(0, min(len(self.items) - 1, index))
        return self.current()

    def go_to_path(self, path: str) -> Item | None:
        index = self.index_of(path)
        if index >= 0:
            self.index = index
            return self.current()
        return None

    def step(self, by: int = 1) -> Item | None:
        """Move `by` places. Returns None when there is nowhere to go.

        Returning None rather than staying put is what lets the window tell
        the difference between "here is the next picture" and "that was the
        last one" — which matters because the second one has to end a
        slideshow rather than restart it.
        """
        if not self.items:
            return None
        target = self.index + by
        if 0 <= target < len(self.items):
            self.index = target
            return self.current()
        if not self.wrap:
            return None
        self.index = target % len(self.items)
        return self.current()

    def first(self) -> Item | None:
        return self.go_to(0)

    def last(self) -> Item | None:
        return self.go_to(len(self.items) - 1)

    def has_next(self) -> bool:
        return bool(self.items) and (self.wrap or self.index < len(self.items) - 1)

    def has_previous(self) -> bool:
        return bool(self.items) and (self.wrap or self.index > 0)

    def remove(self, path: str) -> Item | None:
        """Drop an item — after it has been deleted or moved away.

        The cursor stays where it was, which now means the *next* picture, so
        deleting your way through a folder does not send you back to the
        start. When the last item goes the cursor steps back instead.
        """
        index = self.index_of(path)
        if index < 0:
            return self.current()
        self.items.pop(index)
        if not self.items:
            self.index = -1
            return None
        if index < self.index or self.index >= len(self.items):
            self.index = max(0, min(self.index - 1 if index <= self.index
                                    else self.index, len(self.items) - 1))
        return self.current()

    # -- counting ---------------------------------------------------------

    def counts(self) -> dict:
        tally = {"photo": 0, "raw": 0, "video": 0, "audio": 0}
        for item in self.items:
            if item.kind in tally:
                tally[item.kind] += 1
        return tally

    def summary(self) -> str:
        """"3 of 128" — what the title bar shows."""
        if not self.items:
            return ""
        return f"{self.index + 1} of {len(self.items)}"
