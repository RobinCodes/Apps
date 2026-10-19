"""Keeping the app current with the repository it runs from.

Nothing is installed: the app runs straight out of a git checkout, so the
newest version of it is whatever the checkout's upstream branch holds, and
updating is a fast-forward to that. This module does the fast-forward.

It comes in two halves so that starting the app never waits on the network:

* `apply()` runs in the launcher, before any of the app's own modules are
  imported. If an earlier run fetched commits, it fast-forwards to them. That
  is local git only — a few milliseconds — and the app then loads the new
  code as if it had always been there.
* `watch()` runs once the window is up. A few seconds in, a worker thread
  fetches; if the upstream has moved, every window gets a toast offering a
  restart, and the restart comes back through `apply()`.

It never costs anyone their work. Only a fast-forward is ever attempted, so a
checkout with commits of its own — someone working in it — is left exactly
as it is; git itself refuses when a local edit would be overwritten; and a
checkout without an upstream, a detached HEAD or no git at all simply means
no updates. Setting APPS_NO_UPDATE=1 turns the whole thing off.

This file is the same in every app of the repository, the way each carries its
own winenv.py: an app must still run when its folder is copied out alone.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading

WINDOWS = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if WINDOWS else 0

# The folder holding the package and its launcher — where git is asked from.
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Long enough that the window is drawn and settled before a fetch competes
# with it for the disk; short enough that the offer arrives while it matters.
CHECK_DELAY = 5


def disabled() -> bool:
    return os.environ.get("APPS_NO_UPDATE", "") not in ("", "0")


# -------------------------------------------------------------------- git ----

def _git_exe() -> str | None:
    found = shutil.which("git")
    if found or not WINDOWS:
        return found
    # A process started from a shortcut does not always see the PATH an
    # installer edited, so look where the installers put it.
    home = os.path.expanduser("~")
    candidates = [os.path.join(os.environ.get(var, ""), "Git", "cmd", "git.exe")
                  for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")
                  if os.environ.get(var)]
    candidates += [
        os.path.join(os.environ.get("LOCALAPPDATA", os.path.join(home, r"AppData\Local")),
                     r"Programs\Git\cmd\git.exe"),
        r"C:\msys64\usr\bin\git.exe",
    ]
    return next((c for c in candidates if os.path.isfile(c)), None)


def _git(*args, timeout=20) -> str | None:
    """git's stdout, stripped — or None for any failure at all."""
    git = _git_exe()
    if git is None:
        return None
    env = dict(os.environ)
    # A fetch that needs a password has nobody to ask. Without these it would
    # hang on a prompt no one can see, or put up a credential window from
    # nowhere a few seconds after the app opened.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    try:
        done = subprocess.run(
            [git, "-C", APP_DIR, *args], capture_output=True, text=True,
            timeout=timeout, env=env, creationflags=NO_WINDOW,
            stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _counts() -> tuple[int, int] | None:
    """(behind, ahead) against the upstream, or None when there is none."""
    if _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") is None:
        return None
    out = _git("rev-list", "--left-right", "--count", "@{u}...HEAD")
    try:
        behind, ahead = (int(n) for n in out.split())
    except (AttributeError, ValueError):
        return None
    return behind, ahead


def _can_fast_forward(counts) -> bool:
    return counts is not None and counts[0] > 0 and counts[1] == 0


# ------------------------------------------------------------------ apply ----

def apply() -> int:
    """Fast-forward to commits already fetched. Returns how many it took."""
    if disabled():
        return 0
    counts = _counts()
    if not _can_fast_forward(counts):
        return 0
    if _git("merge", "--ff-only", "--quiet", "@{u}") is None:
        return 0
    return counts[0]


def check() -> int:
    """Fetch, and say how many commits a restart would bring in. Blocks."""
    if disabled():
        return 0
    if _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") is None:
        return 0
    _git("fetch", "--quiet", timeout=90)
    counts = _counts()
    return counts[0] if _can_fast_forward(counts) else 0


# ------------------------------------------------------------ the window ----

def watch(app, name: str, launcher: str) -> None:
    """Check once, a little after startup, and offer a restart if it pays.

    `launcher` is the script beside the package that starts the app — the
    restart goes back through it, and so through `apply()`.
    """
    if disabled():
        return
    from gi.repository import GLib

    def found(count):
        _offer(app, name, launcher, count)
        return GLib.SOURCE_REMOVE

    def work():
        count = check()
        if count:
            GLib.idle_add(found, count)

    def start():
        threading.Thread(target=work, daemon=True).start()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add_seconds(CHECK_DELAY, start)


def _offer(app, name, launcher, count):
    from gi.repository import Adw

    def restart(_toast):
        restart_when_closed(app, launcher)
        # The app's own quit, so an unsaved document still gets its prompt;
        # if that is cancelled nothing restarts until the app next closes.
        app.activate_action("quit", None)

    for window in app.get_windows():
        overlay = getattr(window, "toasts", None)
        if overlay is None:
            continue
        toast = Adw.Toast(title=f"A newer {name} is ready", button_label="Restart",
                          timeout=0)
        toast.connect("button-clicked", restart)
        overlay.add_toast(toast)


def restart_when_closed(app, launcher: str) -> None:
    if getattr(app, "_updater_restart", False):
        return
    app._updater_restart = True
    app.connect("shutdown", lambda *_: relaunch(launcher))


# A second process that waits for this one to be gone before starting the app
# again. Starting it directly would race the old instance for the application
# name, and the new one would hand its arguments over to the dying one and exit.
_WAITER = r"""
import os, subprocess, sys, time
pid = int(sys.argv[1])
if os.name == "nt":
    import ctypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    k.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    k.CloseHandle.argtypes = (ctypes.c_void_p,)
    h = k.OpenProcess(0x00100000, False, pid)
    if h:
        k.WaitForSingleObject(h, 30000)
        k.CloseHandle(h)
else:
    for _ in range(300):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.1)
subprocess.Popen(sys.argv[2:], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
"""


def relaunch(launcher: str) -> None:
    """Start the app again, through its launcher, once this process has exited."""
    command = [sys.executable, "-c", _WAITER, str(os.getpid()),
               sys.executable, os.path.join(APP_DIR, launcher)]
    kwargs = {"cwd": os.getcwd(), "close_fds": True, "stdin": subprocess.DEVNULL}
    if WINDOWS:
        kwargs["creationflags"] = NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)
    except OSError:
        pass
