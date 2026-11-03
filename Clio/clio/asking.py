"""Asking Claude for a board: what is asked, and how the answer becomes ghost events.

"Add the Árpád kings" is a list of dated lines, and the board's own format
(SYSTEM.md §11) is exactly that — so Claude is asked to answer in it, and the
answer is read with `timelines.parse_lines`, the parser the file is read
with. What comes back is shown as ghosts on the axis until it is accepted;
anything the board already has (the same date and title) is left out.

No GTK here; the suite checks it without a display.
"""

from __future__ import annotations

import re

from . import brain, timelines
from .timelines import Board, Item

_FENCED = re.compile(r"^\s*(`{3,}|~{3,})[^\n]*\n(.*?)^\s*\1\s*$", re.M | re.S)


def prompt(board: Board, request: str) -> str:
    names = ", ".join(board.categories()) or "none yet"
    return (f"This is a timeline board, \"{board.title}\", in Robin's second brain. "
            f"Robin asks: {request.strip()}\n\n"
            f"Answer only with lines in the board's own format (SYSTEM.md §11): an "
            f"event as `- YYYY-MM-DD · Title {{category}}`, a period as "
            f"`- YYYY .. YYYY · Title {{category}}`, a date only as precise as is "
            f"known (`YYYY`, `YYYY-MM` or `YYYY-MM-DD`), `~` before an approximate "
            f"one, a leading `-` for a year before the common era (`-0044-03-15`), "
            f"and ` !` at the end of a turning point. Use the board's categories "
            f"where they fit: {names}. Leave out anything the board already has. "
            f"Write titles in the language the board is written in. No commentary, "
            f"no code fences, and do not edit any file.")


def read(reply: str, board: Board) -> list[Item]:
    """The events and spans in Claude's answer that the board does not have yet."""
    fenced = _FENCED.findall(reply or "")
    body = "\n".join(inner for _, inner in fenced) if fenced else (reply or "")
    have = {(str(item.start), brain.key(item.title)) for item in board.items}
    found, seen = [], set()
    for item in timelines.parse_lines(body):
        mark = (str(item.start), brain.key(item.title))
        if mark in have or mark in seen or not item.title:
            continue
        seen.add(mark)
        found.append(item)
    return found
