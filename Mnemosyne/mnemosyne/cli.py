"""The command line: the answers other programs ask for, without a window.

    mnemosyne --due            two numbers, "due new", for a badge
    mnemosyne --print          today's sheet as a PDF, opened in the viewer
    mnemosyne --print --no-open
                               the same, and only the path printed

Anything else — no arguments, `--study FILE`, `-n` — is the window's, and
`main` returns None to say so; the launcher then starts GTK. That split is
the point of this module: it imports no toolkit, so `--due` costs a scan of
the notes and a replay of the log and nothing more.
"""

from __future__ import annotations

import os
import sys

USAGE = """Mnemosyne — the cards in your notes, on screen or on paper.

Usage:
  mnemosyne                  open the window on today's reviews
  mnemosyne --study FILE     study one note's cards
  mnemosyne --due            print "due new": today's due cards and new ones
  mnemosyne --print          print today's sheet (a PDF, opened in the viewer)
  mnemosyne --print --no-open
                             write the sheet and print where it went

A second `mnemosyne` hands its request to the window already open. Everything
else lives in the window: Space shows the answer, 1-4 rate it.
"""


def main(argv: list[str]) -> int | None:
    flags = set(argv)
    if flags & {"-h", "--help"}:
        print(USAGE.strip())
        return 0
    if flags & {"-V", "--version"}:
        from . import __version__                           # noqa: PLC0415

        print(f"Mnemosyne {__version__}")
        return 0
    if "--due" in flags:
        return due()
    if "--print" in flags:
        return print_sheet(open_it="--no-open" not in flags)
    return None


def due() -> int:
    from .library import Library                            # noqa: PLC0415

    library = Library()
    if not library.exists():
        print("0 0")
        return 0
    library.refresh()
    counts = library.counts()
    print(f"{counts['due']} {counts['new']}")
    return 0


def print_sheet(open_it: bool = True) -> int:
    from . import paper, winenv                              # noqa: PLC0415
    from .config import Config                               # noqa: PLC0415
    from .library import Library                             # noqa: PLC0415

    _console_utf8()
    library = Library()
    if not library.exists():
        print(f"mnemosyne: there is no brain at {library.places.brain}", file=sys.stderr)
        return 1
    library.refresh()
    config = Config()
    sheet = library.print_today(include_new=bool(config["sheet_include_new"]))
    if sheet is None:
        print("mnemosyne: nothing is due today", file=sys.stderr)
        return 1
    path = paper.render_sheet(sheet, sheet_path(sheet.id),
                              float(config["sheet_font_size"]), library.places.home)
    print(path)
    if open_it:
        winenv.open_file(str(path))
    return 0


def sheet_path(sheet_id: str) -> str:
    from . import winenv                                     # noqa: PLC0415

    return os.path.join(winenv.cache_home("mnemosyne"), "sheets", f"{sheet_id}.pdf")


def _console_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
