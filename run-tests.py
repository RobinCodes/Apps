#!/usr/bin/env python3
"""Every test in the repository, from one command.

    python3 run-tests.py            everything available on this machine
    python3 run-tests.py --list     what it would run, and what it would skip

Each app's suite still runs on its own — this only saves running six of them
by hand. A suite that needs something this machine hasn't got (a TeX
distribution, a C compiler, the GTK bindings) is reported as skipped rather
than failed, because "not installed here" and "broken" are different answers
and only one of them is worth acting on.

On Windows the five Python apps need MSYS2's Python, because that is where
the GTK4 bindings are; run this with it — `C:\\msys64\\ucrt64\\bin\\python.exe
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
    def __init__(self, name, kind, argv, cwd, needs=(), env=None, wants=()):
        self.name = name
        self.kind = kind          # "python", "c" or "powershell"
        self.argv = argv
        self.cwd = cwd
        self.needs = needs        # executables that must exist to run at all
        self.env = env or {}      # on top of the environment every suite gets
        self.wants = wants        # libraries, checked by the functions above

    def missing(self):
        return [tool for tool in self.needs if not shutil.which(tool)]


def have_vte():
    """Prism is the only app here that needs VTE, and it is not usually installed."""
    try:
        import gi

        gi.require_version("Vte", "3.91")
        from gi.repository import Vte  # noqa: F401
    except Exception:
        return False
    return True


def have_gtk():
    """The four Python apps import GTK at module level and cannot run without it."""
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
    so look there first: win32/Makefile is written for MSYS2's make, and the
    rest of MSYS2 is found from where it is, so another make that comes
    first on PATH (Chocolatey installs one) will not do.
    """
    found = None
    if WINDOWS:
        for guess in (r"C:\msys64\usr\bin\make.exe", r"C:\tools\msys64\usr\bin\make.exe"):
            if os.path.exists(guess):
                found = guess
                break
    found = found or shutil.which("make") or shutil.which("mingw32-make")
    if not found:
        return None
    return [found] + (["-f", makefile] if makefile else [])


def msys2_path(make):
    """PATH as an MSYS2 UCRT64 shell has it, for MSYS2's make run from outside one.

    Started from Windows, make inherits the Windows PATH, where win32/Makefile
    finds no pkg-config, gcc or which, and the tests it builds cannot load
    the GLib DLLs they link against. The MSYS2 is the one make came from:
    C:\\msys64\\usr\\bin\\make.exe -> C:\\msys64.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(make)))
    # UCRT64 is where MSYS2 keeps GTK4 now; MINGW64 is what older installs have.
    env = "ucrt64" if os.path.isdir(os.path.join(root, "ucrt64", "bin")) else "mingw64"
    return os.pathsep.join([os.path.join(root, env, "bin"),
                            os.path.join(root, "usr", "bin"),
                            os.environ.get("PATH", "")])


def suites():
    found = []
    for app, script in (
        # The house style first: it is what the other eight are drawn in, and
        # a drift between the copies vendored into them is worth knowing
        # about before any of their own failures.
        ("House style", os.path.join("house", "test_house.py")),
        ("Brain", os.path.join("house", "test_brain.py")),
        ("Order", os.path.join("Order", "tests", "test_order.py")),
        ("Claude Desk", os.path.join("Claude Program", "tests", "test_claudedesk.py")),
        ("Git Manager", os.path.join("Git Manager", "tests", "test_gitmanager.py")),
        ("LaRenderer", os.path.join("LaRenderer", "tests", "test_larenderer.py")),
        ("Mnemosyne", os.path.join("Mnemosyne", "tests", "test_mnemosyne.py")),
        ("Fathom", os.path.join("Fathom", "tests", "test_fathom.py")),
        ("Hermione", os.path.join("Hermione", "tests", "test_hermione.py")),
        ("Notedown", os.path.join("Notedown", "tests", "test_notedown.py")),
        ("Prism", os.path.join("Terminal", "tests", "test_prism.py")),
        ("The Eye", os.path.join("The Eye", "tests", "test_theeye.py")),
    ):
        path = os.path.join(ROOT, script)
        if os.path.exists(path):
            # The house suite's own folder is its working directory; every
            # app's is the folder above its tests.
            cwd = (os.path.dirname(path) if app in ("House style", "Brain")
                   else os.path.dirname(os.path.dirname(path)))
            found.append(Suite(app, "python", [sys.executable, path], cwd=cwd,
                               wants=("vte",) if app == "Prism" else ()))

    if WINDOWS:
        # What windows/install.ps1 writes to the registry, written to a
        # sandbox of it: the keys it shares with every other program.
        found.append(Suite("Windows installer", "powershell",
                           ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", os.path.join(ROOT, "windows", "test-install.ps1")],
                           cwd=ROOT, needs=("powershell",)))

    lyndon = os.path.join(ROOT, "Lyndon Browser")
    if os.path.isdir(lyndon):
        # The Windows build has no WebKitGTK to link against and vice versa, so
        # each platform runs the check target that exists for it.
        makefile = os.path.join("win32", "Makefile") if WINDOWS else None
        argv = make_argv(makefile)
        if argv:
            found.append(Suite("Lyndon Browser", "c", argv + ["check"], cwd=lyndon,
                               needs=("gcc",) if not WINDOWS else (),
                               env={"PATH": msys2_path(argv[0])} if WINDOWS else None))
        else:
            found.append(Suite("Lyndon Browser", "c", None, cwd=lyndon, needs=("make",)))
    return found


def run(suite, env):
    started = time.monotonic()
    try:
        done = subprocess.run(
            suite.argv, cwd=suite.cwd, env={**env, **suite.env},
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
    vte = have_vte()
    passed, failed, skipped = [], [], []

    for suite in suites():
        gone = suite.missing()
        if suite.argv is None or gone:
            skipped.append((suite.name, "no " + ", ".join(gone or ["make"])))
            continue
        if suite.kind == "python" and not gtk:
            skipped.append((suite.name, "no GTK4 bindings for this interpreter"))
            continue
        if "vte" in suite.wants and not vte:
            # The suite would run its own half and skip the rest, which
            # reads as a pass; saying so here is the honest answer.
            skipped.append((suite.name, "no VTE GTK4 bindings (install vte4)"))
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
