"""Keeping the disk off the GTK main loop.

Most of what this app does is fast enough to do inline — parsing a page of
Markdown is under a millisecond, and the preview is built from ordinary
widgets. Two things are not: reading or writing a file that turns out to live
on a slow disk, and running an export through pandoc. Both go through here:
work on a thread, result delivered back on the main loop via GLib.idle_add.
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

    threading.Thread(target=worker, daemon=True).start()


def _once(fn, arg):
    fn(arg)
    return GLib.SOURCE_REMOVE


def main(fn, *args):
    """Schedule fn(*args) on the main loop from a worker thread."""
    GLib.idle_add(lambda: (fn(*args), GLib.SOURCE_REMOVE)[1])
