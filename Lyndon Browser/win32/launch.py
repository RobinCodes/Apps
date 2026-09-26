"""Start Lyndon, and keep it current — the Windows shortcut runs this.

The Python apps beside Lyndon update by fast-forwarding the checkout they run
from. Lyndon runs a compiled dist/, so for it a new version is not the source
but a build of it, and building takes a minute. So this never makes anyone
wait for one:

1. A build finished last time, in dist-next/, is swapped in for dist/ — now,
   while Lyndon is not running, because Windows will not replace an .exe
   that is. If it is running, the swap waits for a later start.
2. The checkout is fast-forwarded to commits fetched earlier (updater.py,
   the same file the Python apps carry).
3. Lyndon starts, from dist/.
4. Then, with Lyndon already up, this fetches; and if dist/ was not built
   from the source now checked out, builds that into dist-next/ with MSYS2's
   make. The next start picks it up at step 1.

Only the very first start — no dist/ at all — builds before anything
appears, because until then there is nothing to show.

What dist/ was built from is kept beside it in `.source`: the git tree of
this folder, so a commit that only touches the other apps is not a reason to
rebuild Lyndon. Local edits that are not committed never trigger a build; a
checkout being worked on is rebuilt by hand, as before.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import updater  # noqa: E402

ROOT = os.path.dirname(HERE)                 # "Lyndon Browser"
DIST = os.path.join(ROOT, "dist")
NEXT = os.path.join(ROOT, "dist-next")
STAMP = ".source"
LOCK = os.path.join(ROOT, ".update.lock")
# A lock older than this belongs to a build that died, not one still running.
LOCK_STALE = 30 * 60

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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


def swap_in_next() -> None:
    """dist-next/ becomes dist/, if it is finished and dist/ is free."""
    for name in os.listdir(ROOT):
        if name.startswith("dist-old"):
            shutil.rmtree(os.path.join(ROOT, name), ignore_errors=True)
    if not built_from(NEXT) or not os.path.isfile(os.path.join(NEXT, "lyndon.exe")):
        return
    old = os.path.join(ROOT, f"dist-old-{os.getpid()}")
    try:
        if os.path.isdir(DIST):
            os.rename(DIST, old)
    except OSError:
        return                      # Lyndon is running from it; next time
    try:
        os.rename(NEXT, DIST)
    except OSError as exc:
        os.rename(old, DIST)        # put back what was working
        log(f"could not swap in the new build: {exc}")
        return
    shutil.rmtree(old, ignore_errors=True)
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


def build(into: str, source: str) -> bool:
    """`make dist` into `into`, stamped with `source` only if it succeeded."""
    root = msys2()
    if root is None:
        log("no MSYS2 to build with")
        return False
    if not take_lock():
        return False
    try:
        env = dict(os.environ, MSYSTEM="MINGW64", CHERE_INVOKING="1")
        script = ('cd "$(cygpath -u "$1")" && '
                  'make -f win32/Makefile dist DISTDIR="$2"')
        done = subprocess.run(
            [os.path.join(root, "usr", "bin", "bash.exe"), "-lc", script, "build",
             ROOT, os.path.basename(into)],
            env=env, capture_output=True, text=True, creationflags=NO_WINDOW,
            stdin=subprocess.DEVNULL, timeout=30 * 60)
        if done.returncode != 0 or not os.path.isfile(os.path.join(into, "lyndon.exe")):
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

def start(args) -> None:
    subprocess.Popen([os.path.join(DIST, "lyndon.exe"), *args], cwd=DIST,
                     close_fds=True)


def main() -> int:
    args = sys.argv[1:]
    swap_in_next()
    updater.apply()

    if not os.path.isfile(os.path.join(DIST, "lyndon.exe")):
        source = source_id()
        if source is None or not build(DIST, source):
            return 1
    start(args)

    # Lyndon is up; the rest happens behind it.
    if updater.disabled():
        return 0
    if updater.check():
        updater.apply()
    source = source_id()
    if source and source != built_from(DIST) and source != built_from(NEXT):
        build(NEXT, source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
