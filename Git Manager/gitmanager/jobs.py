"""Keeping git and network calls off the GTK main loop.

Every call into gitcmd or github can block for seconds — a fetch over a slow
link, a status walk over a repo with 800 untracked files. Running any of it on
the main loop freezes the window, so it all goes through here: work on a
thread, result delivered back on the main loop via GLib.idle_add.
"""

from __future__ import annotations

import threading
import traceback

from gi.repository import GLib


# Installed by the window at startup. A caller that passes no on_error still
# has its exception land here rather than nowhere: under pythonw.exe there is
# no console for traceback.print_exc() to print to, so "no handler" otherwise
# means the failure is invisible.
on_unhandled = None


def run(fn, on_done=None, on_error=None):
    """Call fn() on a worker thread; deliver its result on the main loop."""

    def worker():
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as a toast
            traceback.print_exc()
            handler = on_error or on_unhandled
            if handler:
                GLib.idle_add(_once, handler, exc)
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


class Serial:
    """A one-at-a-time queue, so N repos refresh without N threads at once."""

    def __init__(self):
        self._lock = threading.Lock()
        self._queue = []
        self._running = False

    def submit(self, fn, on_done=None, on_error=None):
        with self._lock:
            self._queue.append((fn, on_done, on_error))
            if self._running:
                return
            self._running = True
        self._pump()

    def _pump(self):
        with self._lock:
            if not self._queue:
                self._running = False
                return
            fn, on_done, on_error = self._queue.pop(0)

        def done(result):
            if on_done:
                on_done(result)
            self._pump()

        def failed(exc):
            if on_error:
                on_error(exc)
            self._pump()

        run(fn, done, failed)

    def clear(self):
        with self._lock:
            self._queue.clear()
