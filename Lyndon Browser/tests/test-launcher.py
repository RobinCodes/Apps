#!/usr/bin/env python3
"""test-launcher.py — the decisions lyndon.py makes about builds.

   make check

Swapping a build in is the one thing here that can leave someone without a
browser: get it wrong and dist/ is gone, or it is a half-written folder, or
the running Lyndon keeps offering a restart that changes nothing. None of
that involves git or the network, so all of it is testable, and this is where
it is tested.

The build itself is not — that is `make dist`, and the rest of `make check`
already has an opinion about whether the browser compiles.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import importlib.util

spec = importlib.util.spec_from_file_location("lyndon_launcher",
                                              os.path.join(ROOT, "lyndon.py"))
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

failures = 0
checks = 0


def check(ok: bool, what: str) -> None:
    global failures, checks
    checks += 1
    if ok:
        print(f"  ok    {what}")
    else:
        print(f"  FAIL  {what}")
        failures += 1


class Sandbox:
    """lyndon.py pointed at a temp folder instead of the real checkout."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="lyndon-launch-")
        self.saved = {name: getattr(launcher, name)
                      for name in ("ROOT", "DIST", "NEXT", "READY", "CHECKED", "LOCK")}
        launcher.ROOT = self.dir
        launcher.DIST = os.path.join(self.dir, "dist")
        launcher.NEXT = os.path.join(self.dir, "dist-next")
        launcher.READY = os.path.join(self.dir, ".update-ready")
        launcher.CHECKED = os.path.join(self.dir, ".update-checked")
        launcher.LOCK = os.path.join(self.dir, ".update.lock")
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(launcher, name, value)
        shutil.rmtree(self.dir, ignore_errors=True)

    def make_build(self, folder: str, source: str | None, binary: bool = True):
        os.makedirs(folder, exist_ok=True)
        if binary:
            with open(os.path.join(folder, launcher.EXE), "w") as fh:
                fh.write(source or "")
        if source is not None:
            with open(os.path.join(folder, ".source"), "w") as fh:
                fh.write(source + "\n")

    def binary_says(self, folder: str) -> str | None:
        try:
            with open(os.path.join(folder, launcher.EXE)) as fh:
                return fh.read()
        except OSError:
            return None


def test_swap_takes_a_finished_build():
    with Sandbox() as box:
        box.make_build(launcher.DIST, "old")
        box.make_build(launcher.NEXT, "new")
        open(launcher.READY, "w").write("new\n")

        launcher.swap_in_next()

        check(box.binary_says(launcher.DIST) == "new", "a finished build becomes dist/")
        check(not os.path.isdir(launcher.NEXT), "and dist-next/ is gone")
        check(launcher.built_from(launcher.DIST) == "new", "its .source comes with it")
        check(not os.path.exists(launcher.READY),
              "the note to the browser is taken down with it")


def test_swap_refuses_an_unfinished_build():
    # A build killed partway leaves the folder and the binary but never the
    # stamp, because the stamp is written last. Swapping that in is how you
    # end up with a browser that does not start.
    with Sandbox() as box:
        box.make_build(launcher.DIST, "old")
        box.make_build(launcher.NEXT, None)      # binary, no .source

        launcher.swap_in_next()

        check(box.binary_says(launcher.DIST) == "old", "an unstamped build is left alone")
        check(os.path.isdir(launcher.NEXT), "and is still there to finish")

    with Sandbox() as box:
        box.make_build(launcher.DIST, "old")
        box.make_build(launcher.NEXT, "new", binary=False)   # .source, no binary

        launcher.swap_in_next()

        check(box.binary_says(launcher.DIST) == "old",
              "nor is a stamp with no binary under it")


def test_swap_with_nothing_waiting():
    with Sandbox() as box:
        box.make_build(launcher.DIST, "old")
        launcher.swap_in_next()
        check(box.binary_says(launcher.DIST) == "old",
              "the ordinary start, with no new build, changes nothing")


def test_old_builds_are_cleared_up():
    with Sandbox() as box:
        box.make_build(launcher.DIST, "old")
        box.make_build(launcher.NEXT, "new")
        leftover = os.path.join(box.dir, "dist-old-999")
        os.makedirs(leftover)

        launcher.swap_in_next()

        check(not os.path.isdir(leftover),
              "a dist-old from a start that died is swept up")
        check(sorted(n for n in os.listdir(box.dir) if n.startswith("dist")) == ["dist"],
              "and nothing else is left behind")


def test_ready_is_only_cleared_by_a_swap():
    # The browser offers a restart for as long as this file is there. If a
    # start cleared it without swapping anything in, the offer would vanish
    # and the build would sit in dist-next/ unnoticed for ever.
    with Sandbox() as box:
        box.make_build(launcher.DIST, "old")
        box.make_build(launcher.NEXT, None)
        open(launcher.READY, "w").write("new\n")

        launcher.swap_in_next()

        check(os.path.exists(launcher.READY),
              "a swap that did not happen leaves the offer standing")


def test_fetch_is_throttled():
    with Sandbox():
        check(launcher.due_a_check(), "the first start of the day reaches the network")
        check(not launcher.due_a_check(), "the next one, a moment later, does not")

        # Every link opened in another program comes through here.
        check(not any(launcher.due_a_check() for _ in range(5)),
              "and neither do the five links clicked after it")

        old = time.time() - launcher.CHECK_EVERY - 1
        os.utime(launcher.CHECKED, (old, old))
        check(launcher.due_a_check(), "half an hour later it does again")


def test_mark_and_clear():
    with Sandbox():
        launcher.mark_ready("abc123")
        check(open(launcher.READY).read().strip() == "abc123",
              "the note says which build is waiting")
        launcher.clear_ready()
        check(not os.path.exists(launcher.READY), "and goes away when asked")
        launcher.clear_ready()
        check(True, "clearing one that is not there is not an error")


def test_lock_is_exclusive():
    with Sandbox():
        check(launcher.take_lock(), "the first start may build")
        check(not launcher.take_lock(), "a second one, while it runs, may not")
        os.remove(launcher.LOCK)

        # A build killed by a reboot would otherwise block every later one.
        open(launcher.LOCK, "w").close()
        old = time.time() - launcher.LOCK_STALE - 1
        os.utime(launcher.LOCK, (old, old))
        check(launcher.take_lock(), "a lock left by a build that died is broken")


def main() -> int:
    print("the launcher")
    test_swap_takes_a_finished_build()
    test_swap_refuses_an_unfinished_build()
    test_swap_with_nothing_waiting()
    test_old_builds_are_cleared_up()
    test_ready_is_only_cleared_by_a_swap()
    test_fetch_is_throttled()
    test_mark_and_clear()
    test_lock_is_exclusive()

    print(f"\n{checks} checks, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
