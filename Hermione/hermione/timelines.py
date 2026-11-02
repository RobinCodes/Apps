"""Timeline boards (SYSTEM.md §11): a life, a history course, a project, written against.

A board is a Markdown file of dated list items. An event is
`- 2017-09 · First day at school {school}`; a span — drawn as the `}` brace
that groups everything inside it — is `- 2017-09 .. 2025-06 · Primary school`.
Dates are as precise as they are written, `~` makes one approximate, and a
leading `-` is a year before the common era.

Several programs edit a board — Clio, the Anamnesis viewer, a text editor —
so the rules `frontmatter.py` keeps for the front matter hold for the body
too: a board nobody changed is written back exactly as it was read, an item
nobody changed keeps its own lines, and sections and text this module does not
understand are carried through untouched.

Dates are day numbers rather than `datetime.date`, because a history board
starts before the year 1: `When.first_day()` counts days in the proleptic
Gregorian calendar, which every year, negative or not, has a place in.

Standard library only; vendored by `house/sync.py` with the other brain files.
"""

from __future__ import annotations

import os
import re
import zlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import brain, frontmatter
from .frontmatter import Block, FrontMatter

DATE = r"~?-?\d{4}(?:-\d{2}(?:-\d{2})?)?"
SPAN = re.compile(rf"^-[ \t]+({DATE})[ \t]*\.\.[ \t]*(?:({DATE})[ \t]+)?(?:·|-)[ \t]+(.*?)[ \t]*$")
EVENT = re.compile(rf"^-[ \t]+({DATE})[ \t]+(?:·|-)[ \t]+(.*?)[ \t]*$")
TAIL = re.compile(r"^(.*?)(?:[ \t]*\{([^{}]*)\})?(?:[ \t]+(!))?[ \t]*$")
SECTION = re.compile(r"^##(?!#)[ \t]+(.*?)[ \t]*$")

# The house's muted hues, for a category the board gives no colour of its own.
# Chosen by name, so the same category is the same colour on every machine and
# in every program that draws it.
PALETTE = ("#c0623b", "#4f7cac", "#6b8f4e", "#8a6bb0", "#b8862f",
           "#3f8f8a", "#b0506f", "#5e7a8c", "#a0713c", "#4c8f6b")


def palette_colour(name: str) -> str:
    return PALETTE[zlib.crc32(name.strip().casefold().encode("utf-8")) % len(PALETTE)]


# ------------------------------------------------------------------ dates --

def days_from_civil(year: int, month: int, day: int) -> int:
    """Days since 1970-01-01 of an astronomical-year Gregorian date (H. Hinnant)."""
    year -= month <= 2
    era = year // 400
    yoe = year - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_from_days(days: int) -> tuple[int, int, int]:
    """The astronomical (year, month, day) of a day number."""
    days += 719468
    era = days // 146097
    doe = days - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    year = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    return year + (month <= 2), month, day


def astronomical(year: int) -> int:
    """A year as written — -44 is 44 BC — as astronomers count it (-43)."""
    return year if year > 0 else year + 1


def written(year: int) -> int:
    """The inverse: an astronomical year as a board writes it."""
    return year if year > 0 else year - 1


def _month_days(year: int, month: int) -> int:
    after = (year + 1, 1) if month == 12 else (year, month + 1)
    return days_from_civil(*after, 1) - days_from_civil(year, month, 1)


@dataclass(frozen=True)
class When:
    """A date as precise as it was written: a year, a month, or a day."""

    year: int                    # as written: -44 is 44 BC
    month: int | None = None
    day: int | None = None
    approx: bool = False

    @classmethod
    def parse(cls, text: str) -> "When | None":
        match = re.fullmatch(r"(~)?(-)?(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", (text or "").strip())
        if not match:
            return None
        year = int(match.group(3)) * (-1 if match.group(2) else 1)
        month = int(match.group(4)) if match.group(4) else None
        day = int(match.group(5)) if match.group(5) else None
        if month is not None and not 1 <= month <= 12:
            return None
        if day is not None and not 1 <= day <= _month_days(astronomical(year), month):
            return None
        return cls(year, month, day, bool(match.group(1)))

    @classmethod
    def from_date(cls, day: date) -> "When":
        return cls(day.year, day.month, day.day)

    @classmethod
    def from_days(cls, days: int, precision: str = "day") -> "When":
        year, month, day = civil_from_days(int(days))
        year = written(year)
        if precision == "year":
            return cls(year)
        if precision == "month":
            return cls(year, month)
        return cls(year, month, day)

    def __str__(self) -> str:
        sign = "-" if self.year < 0 else ""
        text = f"{'~' if self.approx else ''}{sign}{abs(self.year):04d}"
        if self.month is not None:
            text += f"-{self.month:02d}"
            if self.day is not None:
                text += f"-{self.day:02d}"
        return text

    @property
    def precision(self) -> str:
        return "day" if self.day is not None else "month" if self.month is not None else "year"

    def first_day(self) -> int:
        return days_from_civil(astronomical(self.year), self.month or 1, self.day or 1)

    def end_day(self) -> int:
        """The day after the last one this date covers."""
        year = astronomical(self.year)
        if self.day is not None:
            return self.first_day() + 1
        if self.month is not None:
            return self.first_day() + _month_days(year, self.month)
        return days_from_civil(year + 1, 1, 1)

    def middle(self) -> float:
        return (self.first_day() + self.end_day()) / 2

    def label(self) -> str:
        """For people: `15 March 44 BC`, `September 2017`, `c. 2017`."""
        months = ("January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December")
        year = f"{-self.year} BC" if self.year < 0 else str(self.year)
        text = year
        if self.month is not None:
            text = f"{months[self.month - 1]} {year}"
            if self.day is not None:
                text = f"{self.day} {text}"
        return ("c. " if self.approx else "") + text


def today_days(today: date | None = None) -> int:
    today = today or date.today()
    return days_from_civil(today.year, today.month, today.day)


# ------------------------------------------------------------------ items --

class Item:
    """An event, or a span (`end` set, or open when `span` and no `end`)."""

    def __init__(self, start: When, title: str = "", *, end: When | None = None,
                 span: bool = False, categories=(), colour: str | None = None,
                 milestone: bool = False, notes=()):
        self.start = start
        self.end = end
        self.span = span or end is not None
        self.title = title
        self.categories = [c for c in categories if c]
        self.colour = colour
        self.milestone = milestone
        self.notes = list(notes)
        self._lines: list[str] | None = None
        self._was: tuple | None = None

    def __repr__(self):  # pragma: no cover - debugging only
        return f"<{'Span' if self.span else 'Event'} {self.start} {self.title!r}>"

    def _snapshot(self):
        return (self.span, str(self.start), str(self.end or ""), self.title,
                tuple(self.categories), self.colour, self.milestone, tuple(self.notes))

    @property
    def changed(self) -> bool:
        return self._was is None or self._snapshot() != self._was

    @property
    def open_end(self) -> bool:
        return self.span and self.end is None

    def first_day(self) -> int:
        return self.start.first_day()

    def end_day(self, today: date | None = None) -> int:
        if not self.span:
            return self.start.end_day()
        if self.end is None:
            return today_days(today) + 1
        return max(self.end.end_day(), self.start.end_day())

    def contains(self, other: "Item", today: date | None = None) -> bool:
        """Whether `other` falls inside this span's range (for grouping and nesting)."""
        return (self.span and other is not self
                and self.first_day() <= other.first_day()
                and other.end_day(today) <= self.end_day(today))

    def tail(self) -> str:
        names = list(self.categories) + ([self.colour] if self.colour else [])
        text = f" {{{', '.join(names)}}}" if names else ""
        return text + (" !" if self.milestone else "")

    def head(self) -> str:
        if self.span:
            dates = f"{self.start} .. {self.end}" if self.end else f"{self.start} .."
        else:
            dates = str(self.start)
        return f"- {dates} · {self.title}{self.tail()}"

    def lines(self) -> list[str]:
        if not self.changed:
            return list(self._lines)
        return [self.head()] + [f"  {note}" if note else "" for note in self.notes]


def parse_item(line: str) -> Item | None:
    """One list item's first line, or None when it is not an event or a span."""
    span = SPAN.match(line)
    if span:
        start = When.parse(span.group(1))
        end = When.parse(span.group(2)) if span.group(2) else None
        if start is None or (span.group(2) and end is None):
            return None
        item = Item(start, end=end, span=True)
        rest = span.group(3)
    else:
        event = EVENT.match(line)
        if not event:
            return None
        start = When.parse(event.group(1))
        if start is None:
            return None
        item = Item(start)
        rest = event.group(2)
    tail = TAIL.match(rest)
    item.title = tail.group(1).strip()
    for name in (tail.group(2) or "").split(","):
        name = name.strip()
        if re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", name):
            item.colour = name
        elif name:
            item.categories.append(name)
    item.milestone = bool(tail.group(3))
    return item


# ------------------------------------------------------------------ board --

class Board:
    """A board: its front matter, and its body as raw lines and parsed items."""

    def __init__(self, front: FrontMatter | None = None, path: Path | None = None):
        self.front = front if front is not None else FrontMatter([])
        self.path = Path(path) if path else None
        self.newline = "\n"
        # The body, in order: ("raw", line) for anything kept as it is, and
        # ("item", Item, section) for each event or span.
        self.parts: list[tuple] = []
        self._original: str | None = None
        self._was: list | None = None

    # -- reading and writing ------------------------------------------------

    @classmethod
    def parse(cls, text: str, path: Path | str | None = None) -> "Board":
        newline = "\r\n" if "\r\n" in text else "\n"
        front, body = frontmatter.split(text.replace("\r\n", "\n"))
        board = cls(front or FrontMatter(present=False), path)
        board.newline = newline
        section = None
        current = None
        for line in body.split("\n"):
            heading = SECTION.match(line)
            if heading:
                section = heading.group(1).strip().lower()
                current = None
                board.parts.append(("raw", line))
                continue
            if section in ("events", "spans"):
                if current is not None and line.strip() and line[0] in " \t":
                    current._lines.append(line)
                    current.notes.append(line.strip())
                    continue
                item = parse_item(line)
                if item is not None:
                    item._lines = [line]
                    current = item
                    board.parts.append(("item", item, section))
                    continue
            current = None
            board.parts.append(("raw", line))
        for item in board.items:
            item._was = item._snapshot()
        board._original = text
        board._was = board._shape()
        return board

    @classmethod
    def new(cls, title: str, born: When | None = None) -> "Board":
        board = cls(FrontMatter(["kind: timeline"]))
        board.title = title
        if born is not None:
            board.born = born
        board.parts = [("raw", line) for line in ("", "## Events", "", "## Spans", "")]
        return board

    def _shape(self):
        return [part[1] if part[0] == "raw" else id(part[1]) for part in self.parts]

    @property
    def changed(self) -> bool:
        return (self._original is None or self.front.dirty or self._shape() != self._was
                or any(item.changed for item in self.items))

    def dump(self) -> str:
        if not self.changed:
            return self._original
        lines = []
        for part in self.parts:
            lines += [part[1]] if part[0] == "raw" else part[1].lines()
        head = self.front.text() + "\n" if (self.front.present or self.front.lines) else ""
        text = head + "\n".join(lines)
        if not text.endswith("\n"):
            text += "\n"
        return text.replace("\n", self.newline) if self.newline != "\n" else text

    @classmethod
    def load(cls, path: Path | str) -> "Board":
        with open(path, encoding="utf-8-sig", newline="") as handle:
            return cls.parse(handle.read(), path)

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path or self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.writing")
        with open(temporary, "w", encoding="utf-8", newline="") as handle:
            handle.write(self.dump())
        os.replace(temporary, path)
        self.path = path
        return path

    # -- the front matter ---------------------------------------------------

    @property
    def title(self) -> str:
        if self.front.get("title"):
            return str(self.front.get("title"))
        return self.path.stem if self.path else ""

    @title.setter
    def title(self, value: str):
        self.front.set("title", value.strip())

    @property
    def born(self) -> When | None:
        return When.parse(str(self.front.get("born", "")))

    @born.setter
    def born(self, value: When | None):
        self.front.set("born", str(value) if value else None)

    @property
    def colours(self) -> dict[str, str]:
        value = self.front.get("colours", {})
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}

    @colours.setter
    def colours(self, value: dict):
        self.front.set("colours", Block({k: v for k, v in value.items() if k and v}) or None)

    # -- the items ----------------------------------------------------------

    @property
    def items(self) -> list[Item]:
        return [part[1] for part in self.parts if part[0] == "item"]

    @property
    def events(self) -> list[Item]:
        return [item for item in self.items if not item.span]

    @property
    def spans(self) -> list[Item]:
        return [item for item in self.items if item.span]

    def categories(self) -> list[str]:
        """Every category the board names, those with colours first, in their order."""
        names = list(self.colours)
        for item in self.items:
            for name in item.categories:
                if name not in names:
                    names.append(name)
        return names

    def category_colour(self, name: str) -> str:
        return self.colours.get(name) or palette_colour(name)

    def colour_of(self, item: Item) -> str:
        if item.colour:
            return item.colour
        if item.categories:
            return self.category_colour(item.categories[0])
        return "#7f7f7f"

    def add(self, item: Item) -> Item:
        """Into its section, in date order; the section is made if there is none."""
        section = "spans" if item.span else "events"
        indices = [i for i, part in enumerate(self.parts)
                   if part[0] == "item" and part[2] == section]
        heading = next((i for i, part in enumerate(self.parts)
                        if part[0] == "raw" and SECTION.match(part[1])
                        and SECTION.match(part[1]).group(1).strip().lower() == section),
                       None)
        if heading is None:
            while self.parts and self.parts[-1] == ("raw", ""):
                self.parts.pop()
            self.parts += [("raw", ""), ("raw", f"## {section.title()}")]
            heading = len(self.parts) - 1
            self.parts.append(("raw", ""))
        # Before the first item that comes later, or after the last one —
        # and under the heading when the section has none yet.
        position = indices[0] if indices else heading + 1
        for index in indices:
            if self.parts[index][1].first_day() <= item.first_day():
                position = index + 1
        self.parts.insert(position, ("item", item, section))
        return item

    def remove(self, item: Item) -> int:
        """Take an item out; returns where it was, for an undo."""
        for index, part in enumerate(self.parts):
            if part[0] == "item" and part[1] is item:
                del self.parts[index]
                return index
        return -1

    def restore(self, item: Item, index: int) -> None:
        section = "spans" if item.span else "events"
        self.parts.insert(max(0, min(index, len(self.parts))), ("item", item, section))

    def lanes(self, spans=None, today: date | None = None) -> dict[int, int]:
        """How far out from the events each span's brace sits, by `id(span)`.

        Spans inside spans nest outwards, brace beside brace: a span's lane is
        one past the lanes of every span it holds, and two that only overlap
        never share one. Placed shortest first, so what is held is placed
        before what holds it.
        """
        spans = list(self.spans if spans is None else spans)
        order = sorted(spans, key=lambda s: (s.end_day(today) - s.first_day(), s.first_day()))
        lanes: dict[int, int] = {}
        placed: list[Item] = []
        for span in order:
            lane = 1 + max((lanes[id(o)] for o in placed if span.contains(o, today)),
                           default=-1)
            busy = {lanes[id(o)] for o in placed
                    if o.first_day() < span.end_day(today)
                    and span.first_day() < o.end_day(today)}
            while lane in busy:
                lane += 1
            lanes[id(span)] = lane
            placed.append(span)
        return lanes


# ---------------------------------------------------------------- folders --

def folder(where: brain.Places | None = None) -> Path:
    return (where or brain.places()).brain / "timelines"


def load_boards(where: brain.Places | None = None) -> list[Board]:
    base = folder(where)
    if not base.is_dir():
        return []
    boards = []
    for file in sorted(base.iterdir()):
        if file.suffix.lower() != ".md" or file.name.startswith("."):
            continue
        try:
            boards.append(Board.load(file))
        except (OSError, UnicodeDecodeError):
            continue
    return sorted(boards, key=lambda b: b.title.casefold())


def new_board(title: str, born: When | None = None,
              where: brain.Places | None = None) -> Board:
    board = Board.new(title, born)
    base = folder(where)
    base.mkdir(parents=True, exist_ok=True)
    board.save(brain._unique(base / f"{brain.safe_name(title, 90)}.md"))
    return board


def parse_lines(text: str) -> list[Item]:
    """Every event and span in a piece of text — what Claude proposes, say.

    More forgiving than a board's own section, because an answer is not a
    file: a line may be indented, or bulleted with `*` or `+`.
    """
    found = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        item = parse_item(re.sub(r"^\s*[-*+](?=[ \t])", "-", line))
        if item is not None:
            found.append(item)
    return found
