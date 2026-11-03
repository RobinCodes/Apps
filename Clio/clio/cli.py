"""The command line, answered before GTK is imported at all."""

from __future__ import annotations

USAGE = """Clio — timelines to write against: what happened, when, and the periods it belonged to.

Usage:
  clio                 the window
  clio BOARD.md        open a board
"""


def main(args) -> int | None:
    """An exit code for what is answered here; None hands over to the window."""
    if any(a in ("-h", "--help") for a in args):
        print(USAGE.strip())
        return 0
    if any(a in ("-V", "--version") for a in args):
        from . import __version__                              # noqa: PLC0415

        print(f"Clio {__version__}")
        return 0
    return None
