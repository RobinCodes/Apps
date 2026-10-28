"""Keeping the disk off the GTK main loop.

Answering a card is one line appended to a file, and that happens inline. Two
things are not that small: reading every note under the learning roots and
replaying the whole review log into a schedule — a few hundred milliseconds
on the school laptop, more after a term of notes — and drawing a sheet into a
PDF. Both go through here: work on a thread, result delivered back on the main
loop via GLib.idle_add.

The one rule for callers is that nothing on a worker thread may touch a
widget, or the `Deck` the window is showing. A refresh builds a new deck on
the worker and the window swaps it in on the main loop.
"""

from __future__ import annotations

import threading
import traceback

from gi.repository import GLib


def run(fn, on_done=None, on_error=None):
    """Call fn() on a worker thread; deliver its result on the main loop."""

    def worker():
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as a toast
            traceback.print_exc()
            if on_error:
                GLib.idle_add(_once, on_error, exc)
            return
        if on_done:
            GLib.idle_add(_once, on_done, result)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _once(fn, arg):
    fn(arg)
    return GLib.SOURCE_REMOVE


def main(fn, *args):
    """Schedule fn(*args) on the main loop from a worker thread."""
    GLib.idle_add(lambda: (fn(*args), GLib.SOURCE_REMOVE)[1])
