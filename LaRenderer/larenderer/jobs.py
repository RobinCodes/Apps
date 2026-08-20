"""Keeping TeX and poppler off the GTK main loop.

A compile is a fifth of a second on a small document and several seconds on a
thesis; rasterising a page is another eighty milliseconds. Running any of it
inline would freeze the window mid-keystroke, so it all goes through here:
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


class Serial:
    """A one-at-a-time queue, so twelve visible pages don't open twelve threads.

    Work is tagged with a generation. Bumping the generation drops everything
    still queued from before it — which is what a recompile wants, since those
    pages are about to be re-rendered anyway.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._queue: list = []
        self._running = False
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    def bump(self) -> int:
        with self._lock:
            self._generation += 1
            self._queue.clear()
            return self._generation

    def submit(self, fn, on_done=None, generation=None):
        with self._lock:
            gen = self._generation if generation is None else generation
            self._queue.append((fn, on_done, gen))
            if self._running:
                return
            self._running = True
        self._pump()

    def _pump(self):
        with self._lock:
            while self._queue:
                fn, on_done, gen = self._queue.pop(0)
                if gen == self._generation:
                    break
            else:
                self._running = False
                return
            current = self._generation

        def done(result):
            if on_done and current == self._generation:
                on_done(result)
            self._pump()

        def failed(_exc):
            self._pump()

        run(fn, done, failed)

    def clear(self):
        with self._lock:
            self._queue.clear()
