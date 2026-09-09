#!/usr/bin/env python3
"""Every test in the repository, from one command.

    python3 run-tests.py            everything available on this machine
    python3 run-tests.py --list     what it would run, and what it would skip

Each app's suite still runs on its own — this only saves running four of them
by hand. A suite that needs something this machine hasn't got (a TeX
distribution, a C compiler, the GTK bindings) is reported as skipped rather
than failed, because "not installed here" and "broken" are different answers
and only one of them is worth acting on.

On Windows the three Python apps need MSYS2's Python, because that is where
the GTK4 bindings are; run this with it — `C:\\msys64\\mingw64\\bin\\python.exe
run-tests.py` — or the GTK suites will report themselves skipped.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
WINDOWS = os.name == "nt"


class Suite:
    def __init__(self, name, kind, argv, cwd, needs=()):
        self.name = name
        self.kind = kind          # "python" or "c"
        self.argv = argv
        self.cwd = cwd
        self.needs = needs        # executables that must exist to run at all

    def missing(self):
        return [tool for tool in self.needs if not shutil.which(tool)]


def have_gtk():
    """The three Python apps import GTK at module level and cannot run without it."""
    try:
        import gi  # noqa: F401

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401
    except Exception:
        return False
    return True


def make_argv(makefile=None):
    """GNU make, named the way this platform has it.

    MSYS2 ships make under usr/bin and it is not usually on a Windows PATH,
    so look there before giving up.
    """
    found = shutil.which("make") or shutil.which("mingw32-make")
    if not found and WINDOWS:
        for guess in (r"C:\msys64\usr\bin\make.exe", r"C:\tools\msys64\usr\bin\make.exe"):
            if os.path.exists(guess):
                found = guess
                break
    if not found:
        return None
    return [found] + (["-f", makefile] if makefile else [])


def suites():
    found = []
    for app, script in (
        ("Claude Desk", os.path.join("Claude Program", "tests", "test_claudedesk.py")),
        ("Git Manager", os.path.join("Git Manager", "tests", "test_gitmanager.py")),
        ("LaRenderer", os.path.join("LaRenderer", "tests", "test_larenderer.py")),
    ):
        path = os.path.join(ROOT, script)
        if os.path.exists(path):
            found.append(Suite(app, "python", [sys.executable, path],
                               cwd=os.path.dirname(os.path.dirname(path))))

    lyndon = os.path.join(ROOT, "Lyndon Browser")
    if os.path.isdir(lyndon):
        # The Windows build has no WebKitGTK to link against and vice versa, so
        # each platform runs the check target that exists for it.
        makefile = os.path.join("win32", "Makefile") if WINDOWS else None
        argv = make_argv(makefile)
        if argv:
            found.append(Suite("Lyndon Browser", "c", argv + ["check"], cwd=lyndon,
                               needs=("gcc",) if not WINDOWS else ()))
        else:
            found.append(Suite("Lyndon Browser", "c", None, cwd=lyndon, needs=("make",)))
    return found


def run(suite, env):
    started = time.monotonic()
    try:
        done = subprocess.run(
            suite.argv, cwd=suite.cwd, env=env,
            capture_output=True, encoding="utf-8", errors="replace", timeout=1800,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run: {exc}", "", time.monotonic() - started
    # Kept apart: a suite's verdict is the last thing it printed to stdout, and
    # GTK writes its warnings to stderr. Run them together and every summary
    # line reads "Gtk-CRITICAL", whatever the suite actually said.
    both = (done.stdout or "") + (done.stderr or "")
    return done.returncode == 0, both, (done.stdout or ""), time.monotonic() - started


def main():
    # Before argparse, not after: --help prints the docstring above and exits
    # from inside parse_args, and a Windows console still defaults to a legacy
    # code page that cannot spell an em dash.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true",
                        help="say what would run, and run nothing")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show each suite's own output as well as the verdict")
    args = parser.parse_args()

    env = {**os.environ}
    # The suites print the unicode they exist to check, and they inherit this
    # console rather than one of their own.
    env["PYTHONIOENCODING"] = "utf-8"

    gtk = have_gtk()
    passed, failed, skipped = [], [], []

    for suite in suites():
        gone = suite.missing()
        if suite.argv is None or gone:
            skipped.append((suite.name, "no " + ", ".join(gone or ["make"])))
            continue
        if suite.kind == "python" and not gtk:
            skipped.append((suite.name, "no GTK4 bindings for this interpreter"))
            continue
        if args.list:
            print(f"  would run  {suite.name}")
            continue

        print(f"── {suite.name} " + "─" * max(0, 56 - len(suite.name)))
        ok, output, spoken, seconds = run(suite, env)
        if args.verbose or not ok:
            print(output.rstrip())
        else:
            # The last line a suite prints is its own verdict.
            tail = [ln for ln in spoken.splitlines() if ln.strip()]
            print("   " + (tail[-1].strip() if tail else "(no output)"))
        print(f"   {'PASS' if ok else 'FAIL'}  in {seconds:.1f}s\n")
        (passed if ok else failed).append(suite.name)

    if args.list:
        for name, why in skipped:
            print(f"  would skip {name}  ({why})")
        return 0

    print("─" * 60)
    if passed:
        print(f"passed   {', '.join(passed)}")
    if skipped:
        for name, why in skipped:
            print(f"skipped  {name}  ({why})")
    if failed:
        print(f"FAILED   {', '.join(failed)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
