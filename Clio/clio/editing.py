"""What the fields of an editor mean, and the changes that are not just typing.

A date field takes §11's syntax — `2017-09`, `~1848-03-15`, `-0044` — and
forgives the two things nobody would type on purpose otherwise: a year not
padded to four digits, and `44 BC`. Whatever is typed, the file gets the strict
form, because the Anamnesis viewer reads the same file (SYSTEM.md §11).

No GTK here; the suite checks it without a display.
"""

from __future__ import annotations

import re

from .timelines import Board, Item, When, palette_colour, today_days

_BARE_YEAR = re.compile(r"(~)?(-)?(\d{1,4})")
_BC = re.compile(r"(~)?\s*(\d{1,4})\s*(?:BC|BCE|bc|bce|i\.?\s*e\.?)")

DATE_HELP = "A date is YYYY, YYYY-MM or YYYY-MM-DD; ~ for approximate, - for BC"


def read_date(text: str) -> When | None:
    """A date as typed, or None when it is not one."""
    text = (text or "").strip()
    when = When.parse(text)
    if when is not None:
        return when
    bare = _BARE_YEAR.fullmatch(text)
    if bare and int(bare.group(3)):
        return When(int(bare.group(3)) * (-1 if bare.group(2) else 1),
                    approx=bool(bare.group(1)))
    before = _BC.fullmatch(text)
    if before and int(before.group(2)):
        return When(-int(before.group(2)), approx=bool(before.group(1)))
    return None


def read_jump(text: str) -> When | None:
    """Where to jump: any date, or the word `today`."""
    if (text or "").strip().casefold() == "today":
        return When.from_days(today_days())
    return read_date(text)


def read_range(start_text: str, end_text: str, span: bool):
    """`(start, end, what is wrong)` from the fields; an empty end is a span still running."""
    start = read_date(start_text)
    if start is None:
        return None, None, DATE_HELP if (start_text or "").strip() else "It needs a date"
    if not span or not (end_text or "").strip():
        return start, None, ""
    end = read_date(end_text)
    if end is None:
        return start, None, DATE_HELP
    if end.end_day() < start.first_day():
        return start, None, "It ends before it starts"
    return start, end, ""


def read_categories(text: str) -> list[str]:
    """Comma-separated names, each once, without the braces someone may have typed."""
    found: list[str] = []
    for name in (text or "").split(","):
        name = name.strip().strip("{}").strip()
        if name and name not in found and not name.startswith("#"):
            found.append(name)
    return found


def read_notes(text: str) -> list[str]:
    """The notes field as an item's indented lines: no blank ones at the ends."""
    lines = [line.strip() for line in (text or "").replace("\r\n", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return [line for line in lines if line]


def span_around(items, today=None) -> Item:
    """The span a selection would be grouped into, not yet on any board.

    From the first thing's start to the last thing's end, at the precision those
    dates were written. The categories are the ones all of them share: a span of
    only school events is a school span, and a mixed one is left to be named.
    """
    items = list(items)
    first = min(items, key=lambda i: i.first_day())
    last = max(items, key=lambda i: i.end_day(today))
    if last.open_end:
        end = None
    elif last.span:
        end = last.end
    else:
        end = last.start
    shared = [name for name in first.categories
              if all(name in item.categories for item in items)]
    return Item(first.start, "", end=end, span=True, categories=shared)


def write_item(board: Board, item: Item, *, start: When, end: When | None, span: bool,
               title: str, categories, milestone: bool, notes) -> None:
    """Put an edit into an item on the board, keeping the section in date order."""
    moved = (span != item.span or str(start) != str(item.start))
    item.start, item.end, item.span = start, end if span else None, span
    item.title, item.categories = title, list(categories)
    item.milestone, item.notes = milestone, list(notes)
    if moved and board.remove(item) >= 0:
        board.add(item)


def find_again(board: Board, item: Item) -> Item | None:
    """The same event in a board that was read again while its editor was open."""
    for other in board.items:
        if other is item or (other.span == item.span and str(other.start) == str(item.start)
                             and other.title == item.title):
            return other
    return None


def rename_category(board: Board, old: str, new: str) -> None:
    """A category renamed: on every item that wears it, and in the colours."""
    if old == new or not new:
        return
    for item in board.items:
        if old in item.categories:
            names = [new if name == old else name for name in item.categories]
            item.categories = list(dict.fromkeys(names))
    colours = board.colours
    if old in colours:
        colours = {new if name == old else name: value for name, value in colours.items()}
        board.colours = colours


def category_rows(board: Board) -> list[tuple[str, str]]:
    """(name, colour) for the categories editor: the file's colour, else the palette's."""
    return [(name, board.category_colour(name)) for name in board.categories()]


def write_categories(board: Board, rows) -> None:
    """The editor's rows back into `colours:`.

    A category keeps a line only if it needs one: one whose colour is the one the
    palette would have given it anyway, and which some item already names, leaves
    the file alone, so a board that never had a `colours:` does not grow one for
    being looked at. A category nothing wears yet has to be written, or it would
    not exist.
    """
    used = {name for item in board.items for name in item.categories}
    written = board.colours
    colours = {}
    for name, colour in rows:
        if name in written or name not in used or colour.casefold() != palette_colour(name):
            colours[name] = colour
    if colours != written:
        board.colours = colours
