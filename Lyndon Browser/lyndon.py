#!/usr/bin/env python3
"""Start Lyndon, and keep it current — this is what the shortcut runs.

The Python apps beside Lyndon update by fast-forwarding the checkout they run
from, and the new code is simply what imports next time. Lyndon is compiled,
so for it a new version is not the source but a build of it, and building
takes a minute. Nothing here ever makes anyone wait for one:

1. A build finished last time, in dist-next/, is swapped in for dist/ — now,
   while it is not the binary anyone is running.
2. The checkout is fast-forwarded to commits fetched earlier (tools/updater.py,
   the same file the Python apps carry).
3. Lyndon starts, from dist/.
4. Then, with Lyndon already up, this fetches; and if dist/ was not built from
   the source now checked out, builds that into dist-next/. When that lands it
   writes .update-ready, which the running Lyndon is watching: it offers a
   *Restart*, and the restart comes back through here at step 1.

Only the very first start — no dist/ at all — builds before anything appears,
because until then there is nothing to show.

What dist/ was built from is kept beside it in `.source`: the git tree of this
folder, so a commit that only touches the other apps is not a reason to
rebuild Lyndon. Local edits that are not committed never trigger a build; a
checkout being worked on is rebuilt by hand, as before.

Lyndon is a browser, so this runs on every link anything else opens, not just
when somebody launches it. That is why the fetch is throttled: the second
start within half an hour does steps 1-3 and stops.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "tools"))

import updater  # noqa: E402

WINDOWS = os.name == "nt"

ROOT = HERE
DIST = os.path.join(ROOT, "dist")
NEXT = os.path.join(ROOT, "dist-next")
EXE = "lyndon.exe" if WINDOWS else "lyndon"
STAMP = ".source"
# Written when a build is waiting, removed when it is swapped in. The running
# browser watches this one file and needs nothing else from us.
READY = os.path.join(ROOT, ".update-ready")
CHECKED = os.path.join(ROOT, ".update-checked")
LOCK = os.path.join(ROOT, ".update.lock")
# A lock older than this belongs to a build that died, not one still running.
LOCK_STALE = 30 * 60
# How often a start is allowed to reach the network. A link clicked from
# another program comes through here too.
CHECK_EVERY = 30 * 60

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if WINDOWS else 0


def log(message: str) -> None:
    """A line in update.log beside dist/, for when an update does not arrive."""
    try:
        with open(os.path.join(ROOT, "update.log"), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + message + "\n")
    except OSError:
        pass


# ------------------------------------------------------------ the builds ----

def source_id() -> str | None:
    """The committed tree of this folder — what a build is a build of."""
    return updater._git("rev-parse", "HEAD:./")


def built_from(folder: str) -> str | None:
    try:
        with open(os.path.join(folder, STAMP), encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def mark_ready(source: str) -> None:
    try:
        with open(READY, "w", encoding="utf-8") as fh:
            fh.write(source + "\n")
    except OSError:
        pass


def clear_ready() -> None:
    try:
        os.remove(READY)
    except OSError:
        pass


def swap_in_next() -> None:
    """dist-next/ becomes dist/, if it is finished and dist/ is free."""
    for name in os.listdir(ROOT):
        if name.startswith("dist-old"):
            shutil.rmtree(os.path.join(ROOT, name), ignore_errors=True)
    if not built_from(NEXT) or not os.path.isfile(os.path.join(NEXT, EXE)):
        return
    old = os.path.join(ROOT, f"dist-old-{os.getpid()}")
    try:
        if os.path.isdir(DIST):
            os.rename(DIST, old)
    except OSError:
        return                      # Windows: Lyndon is running from it
    try:
        os.rename(NEXT, DIST)
    except OSError as exc:
        os.rename(old, DIST)        # put back what was working
        log(f"could not swap in the new build: {exc}")
        return
    shutil.rmtree(old, ignore_errors=True)
    clear_ready()
    log(f"now running the build of {built_from(DIST)}")


def msys2() -> str | None:
    roots = [os.environ.get("MSYS2_ROOT"), r"C:\msys64",
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "msys64"),
             os.path.expanduser(r"~\msys64"), r"C:\tools\msys64"]
    return next((r for r in roots
                 if r and os.path.isfile(os.path.join(r, "usr", "bin", "bash.exe"))), None)


def take_lock() -> bool:
    try:
        if time.time() - os.path.getmtime(LOCK) > LOCK_STALE:
            os.remove(LOCK)
    except OSError:
        pass
    try:
        os.close(os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except OSError:
        return False                # another start is already building


def _build_command(into: str) -> tuple[list[str], dict] | None:
    """How to run `make dist` here. The two platforms differ only in this."""
    target = os.path.basename(into)
    if not WINDOWS:
        return ([shutil.which("make") or "make", "dist", f"DISTDIR={target}",
                 f"-j{os.cpu_count() or 2}"],
                {"cwd": ROOT, "env": dict(os.environ)})

    root = msys2()
    if root is None:
        log("no MSYS2 to build with")
        return None
    script = ('cd "$(cygpath -u "$1")" && '
              'make -f win32/Makefile dist DISTDIR="$2"')
    return ([os.path.join(root, "usr", "bin", "bash.exe"), "-lc", script, "build",
             ROOT, target],
            {"env": dict(os.environ, MSYSTEM="MINGW64", CHERE_INVOKING="1"),
             "creationflags": NO_WINDOW})


def build(into: str, source: str) -> bool:
    """`make dist` into `into`, stamped with `source` only if it succeeded."""
    plan = _build_command(into)
    if plan is None:
        return False
    if not take_lock():
        return False
    command, kwargs = plan
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30 * 60, **kwargs)
        if done.returncode != 0 or not os.path.isfile(os.path.join(into, EXE)):
            log("build failed:\n" + (done.stdout + done.stderr)[-4000:])
            return False
        with open(os.path.join(into, STAMP), "w", encoding="utf-8") as fh:
            fh.write(source + "\n")
        log(f"built {source} into {os.path.basename(into)}")
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"build failed: {exc}")
        return False
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


# ------------------------------------------------------------------ main ----

def due_a_check() -> bool:
    """Throttle the network: see CHECK_EVERY."""
    try:
        if time.time() - os.path.getmtime(CHECKED) < CHECK_EVERY:
            return False
    except OSError:
        pass
    try:
        with open(CHECKED, "w", encoding="utf-8"):
            pass
    except OSError:
        pass
    return True


def wait_for(pid: int) -> None:
    """Let the Lyndon that asked for this restart finish going away first."""
    for _ in range(300):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)


def start(args) -> None:
    """Run the browser, telling it how to come back here."""
    env = dict(os.environ,
               LYNDON_LAUNCHER=os.path.realpath(__file__),
               LYNDON_UPDATE_STAMP=READY,
               LYNDON_PYTHON=sys.executable)
    kwargs = {"cwd": DIST, "close_fds": True, "env": env}
    if not WINDOWS:
        # Outlive this process: the browser is the point, and the build below
        # is only what happens behind it.
        kwargs["start_new_session"] = True
    subprocess.Popen([os.path.join(DIST, EXE), *args], **kwargs)


def main() -> int:
    args = sys.argv[1:]

    # `--after PID` is a restart asked for from inside the browser.
    if args[:1] == ["--after"] and len(args) >= 2:
        try:
            wait_for(int(args[1]))
        except ValueError:
            pass
        args = args[2:]

    swap_in_next()
    updater.apply()

    if not os.path.isfile(os.path.join(DIST, EXE)):
        source = source_id()
        if source is None or not build(DIST, source):
            return 1
    start(args)

    # Lyndon is up; the rest happens behind it.
    if updater.disabled() or not due_a_check():
        return 0
    if updater.check():
        updater.apply()
    source = source_id()
    if source and source != built_from(DIST) and source != built_from(NEXT):
        if build(NEXT, source):
            mark_ready(source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
