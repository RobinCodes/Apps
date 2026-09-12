"""Keeping the filesystem off the GTK main loop.

A scan is a second and a half on a warm home directory and a minute on a cold
external disk, and a treemap layout of a hundred thousand rectangles is a
tenth of a second on its own. None of it can run inline without the window
going grey, so it all goes through here: work on a thread, answer delivered
back on the main loop.
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


class Throttle:
    """Let a worker thread update the window without flooding it.

    A scan reports progress ten times a second, and each report is an idle
    callback; if the main loop is busy laying out a treemap they queue up and
    the window spends its time redrawing a counter nobody is reading. This
    keeps only the newest one.
    """

    def __init__(self, fn):
        self._fn = fn
        self._lock = threading.Lock()
        self._pending = None
        self._scheduled = False

    def __call__(self, value):
        with self._lock:
            self._pending = value
            if self._scheduled:
                return
            self._scheduled = True
        GLib.idle_add(self._deliver)

    def _deliver(self):
        with self._lock:
            value = self._pending
            self._pending = None
            self._scheduled = False
        if value is not None:
            self._fn(value)
        return GLib.SOURCE_REMOVE
