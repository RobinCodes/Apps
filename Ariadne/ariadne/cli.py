"""The command line, answered before GTK is imported at all.

`ariadne --ask "Why is there something rather than nothing?"` keeps a
question without opening a window: a keyboard shortcut of the desktop's own
can run it, which is as close to "one key from anywhere" as an app can get —
the moment is caught, and the window is for later. With nothing after it,
`--ask` opens the window straight on the question field instead (app.py).
"""

from __future__ import annotations

import sys

USAGE = """Ariadne — the questions worth keeping, and the logical threads that answer them.

Usage:
  ariadne                      the window
  ariadne FILE.md              open a question or a thread
  ariadne --ask                the window, straight on a new question
  ariadne --ask "QUESTION"     keep a question now, without a window
"""


def main(args) -> int | None:
    """An exit code for what is answered here; None hands over to the window."""
    if any(a in ("-h", "--help") for a in args):
        print(USAGE.strip())
        return 0
    if any(a in ("-V", "--version") for a in args):
        from . import __version__                              # noqa: PLC0415

        print(f"Ariadne {__version__}")
        return 0
    if args and args[0] == "--ask" and len(args) > 1:
        from . import brain, threads                           # noqa: PLC0415

        words = " ".join(" ".join(args[1:]).split())
        if not words:
            return None
        if not brain.places().exists():
            print("There is no second brain on this machine to keep it in.", file=sys.stderr)
            return 1
        question = threads.new_question(words)
        print(question.path)
        return 0
    return None
