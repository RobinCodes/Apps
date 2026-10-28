"""Running `order sync` on its own: a systemd user timer on Linux, a scheduled task on Windows.

Every ten minutes, plus two minutes after boot on Linux. Both run as the user,
need no administrator, and are removed by `order timer remove` without a trace.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS = os.name == "nt"
TASK = r"Order\Sync"
UNIT = "order-sync"
ENTRY = Path(__file__).resolve().parent.parent / "order.py"
NO_WINDOW = 0x08000000 if WINDOWS else 0


def _python(windowless: bool = True) -> str:
    exe = sys.executable
    if WINDOWS and windowless and exe.lower().endswith("python.exe"):
        quiet = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(quiet):
            return quiet
    return exe


def _run(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", creationflags=NO_WINDOW)
    except OSError as exc:
        return subprocess.CompletedProcess(args, 127, "", str(exc))


def _systemd_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "systemd" / "user"


def service_text() -> str:
    return (
        "[Unit]\n"
        "Description=The Order: keep this machine in step with the manifest\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={_python(False)} {ENTRY} sync --quiet\n"
        "Nice=10\n"
        "IOSchedulingClass=idle\n")


TIMER_TEXT = (
    "[Unit]\n"
    "Description=Run order sync every ten minutes\n\n"
    "[Timer]\n"
    "OnBootSec=2min\n"
    "OnUnitActiveSec=10min\n"
    "Persistent=true\n\n"
    "[Install]\n"
    "WantedBy=timers.target\n")


def install() -> tuple[bool, str]:
    if WINDOWS:
        command = f'"{_python()}" "{ENTRY}" sync --quiet'
        done = _run(["schtasks", "/Create", "/F", "/TN", TASK, "/SC", "MINUTE", "/MO", "10",
                     "/TR", command])
        return done.returncode == 0, (done.stdout or done.stderr).strip()
    if not shutil.which("systemctl"):
        return False, "no systemctl: run `order sync` from cron or the session's autostart"
    folder = _systemd_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{UNIT}.service").write_text(service_text(), encoding="utf-8")
    (folder / f"{UNIT}.timer").write_text(TIMER_TEXT, encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"])
    done = _run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"])
    return done.returncode == 0, (done.stderr or done.stdout).strip() or "enabled"


def remove() -> tuple[bool, str]:
    if WINDOWS:
        done = _run(["schtasks", "/Delete", "/F", "/TN", TASK])
        return done.returncode == 0, (done.stdout or done.stderr).strip()
    _run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"])
    for suffix in ("service", "timer"):
        try:
            (_systemd_dir() / f"{UNIT}.{suffix}").unlink()
        except OSError:
            pass
    _run(["systemctl", "--user", "daemon-reload"])
    return True, "removed"


def status() -> str:
    if WINDOWS:
        done = _run(["schtasks", "/Query", "/TN", TASK, "/FO", "LIST"])
        if done.returncode:
            return "not installed"
        fields = dict(line.split(":", 1) for line in done.stdout.splitlines() if ":" in line)
        nxt = next((v.strip() for k, v in fields.items() if "Next Run" in k or "Következő" in k), "")
        return f"installed (every 10 minutes){'; next ' + nxt if nxt else ''}"
    if not (_systemd_dir() / f"{UNIT}.timer").exists():
        return "not installed"
    done = _run(["systemctl", "--user", "is-active", f"{UNIT}.timer"])
    return f"installed, {done.stdout.strip() or 'unknown'}"
