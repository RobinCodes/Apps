"""order — keep this machine laid out like every other one.

  order where                 this machine's name and the paths the Order uses
  order doctor                where this machine differs from the manifest
  order sync [--quiet]        clone what is missing, fast-forward what is behind,
                              sync the brain; what the timer runs
  order adopt [PATH…]         make existing folders the manifest's repositories,
                              keeping every local file
  order timer install|remove|status
  order apps install          shortcuts and file types for the apps (Windows: the
                              Start Menu; Linux: ~/.local/bin and .desktop files)
  order drive apply PLAN      carry out a reorganisation plan, journalled
  order drive undo JOURNAL    reverse one
  order drive check PLAN      what would go wrong, changing nothing
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import drive, gitrepo, manifest, timer

WINDOWS = os.name == "nt"
APPS_ROOT = Path(__file__).resolve().parent.parent.parent


def cache() -> Path:
    if WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    folder = base / "order"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def load_state() -> dict:
    try:
        return json.loads((cache() / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    (cache() / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def log(line: str) -> None:
    with (cache() / "sync.log").open("a", encoding="utf-8") as out:
        out.write(f"{datetime.now().isoformat(timespec='seconds')}  {line}\n")


# ---------------------------------------------------------------- where ----

def cmd_where(_args) -> int:
    print(f"machine   {manifest.machine()}")
    print(f"home      {manifest.home()}")
    print(f"brain     {manifest.brain()}")
    print(f"manifest  {manifest.manifest_path()}")
    print(f"apps      {APPS_ROOT}")
    print(f"python    {sys.executable}")
    print(f"timer     {timer.status()}")
    return 0


# ---------------------------------------------------------------- doctor ---

def _strays(known: set[Path]) -> list[Path]:
    """Repositories under ~/Projects the manifest does not list."""
    root = manifest.home() / "Projects"
    found = []
    if not root.is_dir():
        return found
    for folder, dirs, _files in os.walk(root):
        here = Path(folder)
        depth = len(here.relative_to(root).parts)
        if ".git" in dirs and here.resolve() not in known:
            found.append(here)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"
                   and depth < 4]
    return found


def cmd_doctor(args) -> int:
    try:
        m = manifest.load()
    except manifest.ManifestError as exc:
        print(f"no manifest: {exc}\n  clone the brain first (SYSTEM.md §3), then run this again.")
        return 2
    problems = 0
    print(f"The Order on {manifest.machine()} — manifest {m.source}\n")
    for place in m.places:
        if not (manifest.home() / place).is_dir():
            print(f"  missing place   ~/{place}")
            problems += 1
    for repo in m.for_this_machine():
        s = gitrepo.state(repo.where, fetch=args.fetch)
        label = f"~/{repo.path}"
        if not s.exists:
            print(f"  missing         {label}" + ("  (optional)" if repo.optional else ""))
            problems += 0 if repo.optional else 1
        elif not s.is_repo:
            print(f"  not a repo      {label}  → order adopt \"{label}\"")
            problems += 1
        elif not s.remote:
            print(f"  no remote       {label}  → order adopt \"{label}\"")
            problems += 1
        elif not gitrepo.same_remote(s.remote, repo.url):
            print(f"  other remote    {label}  is {s.remote}")
            problems += 1
        else:
            notes = []
            if s.dirty:
                notes.append(f"{s.dirty} local change(s)")
            if s.ahead:
                notes.append(f"{s.ahead} unpushed")
            if s.behind:
                notes.append(f"{s.behind} behind")
            if not s.upstream:
                notes.append("no upstream branch")
            if notes:
                print(f"  attention       {label}: {', '.join(notes)}")
                problems += 1 if (s.ahead or not s.upstream) else 0
            elif args.verbose:
                print(f"  ok              {label}")
    known = {r.where.resolve() for r in m.repositories}
    for stray in _strays(known):
        rel = stray.relative_to(manifest.home()).as_posix()
        print(f"  not in manifest ~/{rel}")
    inbox = manifest.brain() / "inbox"
    waiting = [p for p in inbox.iterdir() if not p.name.startswith(".")] if inbox.is_dir() else []
    if waiting:
        print(f"\n  inbox           {len(waiting)} waiting (SYSTEM.md §4.2)")
    state = load_state()
    print(f"\n  last sync       {state.get('last_sync', 'never')}")
    print(f"  timer           {timer.status()}")
    print("\nin order" if problems == 0 else f"\n{problems} thing(s) out of order")
    return 0 if problems == 0 else 1


# ---------------------------------------------------------------- sync -----

def _lock():
    path = cache() / "sync.lock"
    try:
        if path.exists() and time.time() - path.stat().st_mtime > 1800:
            path.unlink()
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return path
    except FileExistsError:
        return None


def console_python() -> str:
    """The console-subsystem Python beside whichever one is running.

    The timer runs `pythonw.exe`, which has no console. On Windows every console
    program it starts — git, and everything git starts — would then open a console
    window of its own and flash it on screen. Starting the brain's own work with
    `python.exe` and CREATE_NO_WINDOW instead gives that work one hidden console,
    which everything below it inherits, so nothing ever appears.
    """
    exe = sys.executable
    if WINDOWS and exe.lower().endswith("pythonw.exe"):
        console = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.isfile(console):
            return console
    return exe


def _brain_sync(repo, say) -> bool:
    anam = repo.where / "anam.py"
    if not anam.is_file():
        say(f"  brain: no anam.py in {repo.where}")
        return False
    done = subprocess.run([console_python(), str(anam), "sync"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          creationflags=gitrepo.NO_WINDOW)
    say(f"  brain: {(done.stdout or done.stderr).strip().splitlines()[-1] if (done.stdout or done.stderr).strip() else 'synced'}")
    return done.returncode == 0


def _maybe_refresh(m, say) -> None:
    """Start `anam refresh` in the background if the brain has not been refreshed lately."""
    state = load_state()
    last = state.get("last_refresh_epoch", 0)
    if time.time() - last < m.refresh_hours * 3600:
        return
    anam = manifest.brain() / "anam.py"
    if not anam.is_file():
        return
    out = (cache() / "refresh.log").open("a", encoding="utf-8")
    # A hidden console of its own (not DETACHED_PROCESS, which leaves it none, so
    # every tool and git call beneath it would open a window), in its own group so
    # it outlives this sync.
    kwargs = ({"creationflags": gitrepo.NO_WINDOW | 0x00000200} if WINDOWS
              else {"start_new_session": True})
    subprocess.Popen([console_python(), str(anam), "refresh"], stdout=out, stderr=out,
                     stdin=subprocess.DEVNULL, **kwargs)
    state["last_refresh_epoch"] = time.time()
    save_state(state)
    say("  brain: refresh started in the background")


def cmd_sync(args) -> int:
    say = (lambda line: None) if args.quiet else print
    lock = _lock()
    if lock is None:
        say("a sync is already running")
        return 0
    failures = 0
    try:
        try:
            m = manifest.load()
        except manifest.ManifestError as exc:
            say(f"no manifest: {exc}")
            log(f"no manifest: {exc}")
            return 2
        for place in m.places:
            (manifest.home() / place).mkdir(parents=True, exist_ok=True)
        # The brain first: it carries the manifest, and a newer manifest may list more.
        ordered = sorted(m.for_this_machine(), key=lambda r: r.sync != "push")
        for repo in ordered:
            s = gitrepo.state(repo.where)
            label = f"~/{repo.path}"
            if not s.exists:
                done = gitrepo.clone(repo.url, repo.where)
                if done.returncode:
                    if not repo.optional:
                        failures += 1
                        say(f"  clone failed  {label}: {done.stderr.strip().splitlines()[-1] if done.stderr.strip() else 'error'}")
                        log(f"clone failed {repo.path}")
                    continue
                say(f"  cloned        {label}")
                log(f"cloned {repo.path}")
                continue
            if not s.is_repo or not s.remote or not gitrepo.same_remote(s.remote, repo.url):
                say(f"  left alone    {label}  (run `order doctor`)")
                continue
            if repo.sync == "none":
                continue
            if repo.sync == "push":
                failures += 0 if _brain_sync(repo, say) else 1
                continue
            s = gitrepo.state(repo.where, fetch=True)
            if s.behind and not s.dirty and not s.ahead:
                done = gitrepo.fast_forward(repo.where)
                if done.returncode == 0:
                    say(f"  updated       {label}  (+{s.behind})")
                    log(f"fast-forwarded {repo.path} by {s.behind}")
                else:
                    say(f"  not updated   {label}: {done.stderr.strip()}")
            elif s.behind:
                say(f"  behind        {label}: {s.behind} new, but there is local work — left alone")
        state = load_state()
        state["last_sync"] = datetime.now().isoformat(timespec="seconds")
        state["last_sync_failures"] = failures
        save_state(state)
        _maybe_refresh(m, say)
        log(f"sync done, {failures} failure(s)")
        say("in step" if not failures else f"{failures} failure(s)")
        return 0 if not failures else 1
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- adopt ----

def cmd_adopt(args) -> int:
    m = manifest.load()
    wanted = {Path(os.path.expanduser(p)).resolve() for p in args.paths} if args.paths else None
    status = 0
    for repo in m.for_this_machine():
        if wanted is not None and repo.where.resolve() not in wanted:
            continue
        s = gitrepo.state(repo.where)
        if not s.exists or (s.is_repo and s.remote):
            continue
        ok, why = gitrepo.adopt(repo.where, repo.url)
        print(f"  {'adopted' if ok else 'not adopted':12s} ~/{repo.path}: {why}")
        status |= 0 if ok else 1
    return status


# ---------------------------------------------------------------- timer ----

def cmd_timer(args) -> int:
    if args.action == "status":
        print(timer.status())
        return 0
    ok, why = (timer.install if args.action == "install" else timer.remove)()
    print(why)
    return 0 if ok else 1


# ---------------------------------------------------------------- apps -----

LINUX_APPS = {
    # folder: (launcher inside it, command name, desktop file)
    "Claude Program": ("claude-desk", "claude-desk", "claude-desk.desktop"),
    "Fathom": ("fathom.py", "fathom", "fathom.desktop"),
    "Git Manager": ("git-manager", "git-manager", "git-manager.desktop"),
    "Hermione": ("hermione.py", "hermione", "hermione.desktop"),
    "LaRenderer": ("larenderer.py", "larenderer", "larenderer.desktop"),
    "Notedown": ("notedown.py", "notedown", "notedown.desktop"),
    "The Eye": ("the-eye.py", "the-eye", "the-eye.desktop"),
    "Terminal": ("prism.py", "prism", "prism.desktop"),
}


def cmd_apps(_args) -> int:
    if WINDOWS:
        script = APPS_ROOT / "windows" / "install.ps1"
        return subprocess.call(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                "-File", str(script), "-Desktop"])
    bin_dir = Path.home() / ".local" / "bin"
    apps_dir = Path.home() / ".local" / "share" / "applications"
    bin_dir.mkdir(parents=True, exist_ok=True)
    apps_dir.mkdir(parents=True, exist_ok=True)
    folders = dict(LINUX_APPS)
    # Apps added later follow the same shape: <folder>/<name>.py and <name>.desktop.
    for folder in APPS_ROOT.iterdir():
        if folder.is_dir() and folder.name not in folders:
            for desktop in folder.glob("*.desktop"):
                stem = desktop.stem
                launcher = next((p for p in (folder / f"{stem}.py", folder / stem) if p.is_file()), None)
                if launcher:
                    folders[folder.name] = (launcher.name, stem, desktop.name)
    for folder, (launcher, command, desktop) in sorted(folders.items()):
        src = APPS_ROOT / folder / launcher
        if not src.is_file():
            continue
        link = bin_dir / command
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(src)
        entry = APPS_ROOT / folder / desktop
        if entry.is_file():
            # The checked-in entries name Robin's home and checkout; this machine's
            # may differ, and a .desktop file pointing nowhere is silently dropped.
            text = entry.read_text(encoding="utf-8")
            text = text.replace("/home/robin/Projects/Programming/Apps", str(APPS_ROOT))
            text = text.replace("/home/robin", str(Path.home()))
            (apps_dir / desktop).write_text(text, encoding="utf-8")
        print(f"  {command:14s} → {src}")
    subprocess.call(["update-desktop-database", str(apps_dir)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 0


# ---------------------------------------------------------------- drive ----

def cmd_drive(args) -> int:
    path = Path(args.file).resolve()
    root = Path(args.root).resolve() if args.root else path.parent.parent
    if args.action == "undo":
        n = drive.undo(root, path)
        print(f"{n} move(s) reversed")
        return 0
    moves = drive.read_plan(path)
    problems = drive.check(root, moves)
    for move, why in problems:
        print(f"  ! {move.source} → {move.target}: {why}")
    if args.action == "check":
        print(f"{len(moves)} move(s), {len(problems)} problem(s)")
        return 0 if not problems else 1
    drive.apply(root, moves)
    return 0


# ---------------------------------------------------------------- main -----

def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(prog="order", description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="\n".join(__doc__.splitlines()[2:]))
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("where")
    doc = sub.add_parser("doctor")
    doc.add_argument("--fetch", action="store_true", help="ask every remote first (slower)")
    doc.add_argument("-v", "--verbose", action="store_true")
    syn = sub.add_parser("sync")
    syn.add_argument("--quiet", action="store_true")
    ado = sub.add_parser("adopt")
    ado.add_argument("paths", nargs="*")
    tim = sub.add_parser("timer")
    tim.add_argument("action", choices=("install", "remove", "status"))
    app = sub.add_parser("apps")
    app.add_argument("action", choices=("install",))
    drv = sub.add_parser("drive")
    drv.add_argument("action", choices=("apply", "undo", "check"))
    drv.add_argument("file")
    drv.add_argument("--root", help="the drive's root (default: the folder above the plan's folder)")
    args = parser.parse_args(argv)
    handlers = {"where": cmd_where, "doctor": cmd_doctor, "sync": cmd_sync,
                "adopt": cmd_adopt, "timer": cmd_timer, "apps": cmd_apps, "drive": cmd_drive}
    if not args.cmd:
        parser.print_help()
        return 0
    try:
        return handlers[args.cmd](args)
    except manifest.ManifestError as exc:
        print(f"order: {exc}", file=sys.stderr)
        return 2
