"""Windows support: the platform differences the rest of the app shouldn't carry.

This app was written for Linux, and two things about Windows differ enough to
break it. Both are answered here so that no other module has to know which
operating system it is running on.

*Where files go.* XDG puts settings under ~/.config and state under
~/.local/share. Windows has two roots instead of two subdirectories:
%APPDATA% for what should follow the user between machines, and
%LOCALAPPDATA% for what should not. Settings and the recent-files list roam;
the rendered-image cache stays put. On Linux nothing below changes — the XDG
variables still win.

*Where the tools are.* Notedown needs nothing installed to edit or preview a
document; everything it renders, it renders itself. The one optional tool is
pandoc, for exports to formats this app has no business writing by hand, and a
Windows installer puts that in Program Files and edits the PATH of interactive
shells only — which a process started from a shortcut does not inherit.
"""

from __future__ import annotations

import os
import subprocess
import sys

WINDOWS = os.name == "nt"

# Passed as `creationflags` to every child process. Zero elsewhere, so call
# sites can hand it over unconditionally.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if WINDOWS else 0


# ------------------------------------------------------------------ paths ----

def _xdg(var: str, app: str) -> str | None:
    """An XDG variable, honoured on every platform when it is actually set.

    Windows has its own answer for these directories and normally uses it.
    But something that goes to the trouble of setting XDG_CONFIG_HOME means
    it — the test suite points all three at a scratch directory so a test run
    cannot touch real settings — and silently ignoring that on Windows would
    make the tests write to the user's live configuration.
    """
    root = os.environ.get(var)
    return os.path.join(root, app) if root else None


def config_home(app: str) -> str:
    """Directory for settings — the ones worth carrying to another machine."""
    override = _xdg("XDG_CONFIG_HOME", app)
    if override:
        return override
    if WINDOWS:
        root = os.environ.get("APPDATA") or os.path.expanduser(
            os.path.join("~", "AppData", "Roaming"))
        return os.path.join(root, app)
    return os.path.join(os.path.expanduser("~/.config"), app)


def data_home(app: str) -> str:
    """Directory for state — anything worth keeping but not worth roaming."""
    override = _xdg("XDG_DATA_HOME", app)
    if override:
        return override
    if WINDOWS:
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
            os.path.join("~", "AppData", "Local"))
        return os.path.join(root, app)
    return os.path.join(os.path.expanduser("~/.local/share"), app)


def cache_home(app: str) -> str:
    """Directory for throwaway work — scaled images, export scratch."""
    override = _xdg("XDG_CACHE_HOME", app)
    if override:
        return override
    if WINDOWS:
        return os.path.join(data_home(app), "cache")
    return os.path.join(os.path.expanduser("~/.cache"), app)


# ------------------------------------------------------------------ tools ----

def _program_files() -> list[str]:
    seen = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(var)
        if value and value not in seen:
            seen.append(value)
    return seen or [r"C:\Program Files"]


def tool_dirs() -> list[str]:
    """Everywhere worth looking for a command line tool, best guess first."""
    if not WINDOWS:
        return [
            os.path.expanduser(p)
            for p in ("~/.local/bin", "/usr/local/bin", "/opt/homebrew/bin")
        ]

    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", os.path.join(home, r"AppData\Local"))
    dirs = [
        os.path.join(local, r"Pandoc"),
        os.path.join(home, r".local\bin"),
        os.path.join(local, r"Microsoft\WindowsApps"),
    ]
    for pf in _program_files():
        dirs.append(os.path.join(pf, "Pandoc"))
    # The GTK stack this app runs on.
    dirs.append(os.path.dirname(sys.executable))
    dirs.append(r"C:\msys64\mingw64\bin")
    return dirs


def prepend_path(directory: str) -> None:
    """Put `directory` first on PATH, so children inherit the find as well."""
    path = os.environ.get("PATH", "")
    if directory and directory not in path.split(os.pathsep):
        os.environ["PATH"] = os.pathsep.join([directory, path]) if path else directory


def which(name: str) -> str | None:
    """shutil.which, plus the directories Windows installers actually use."""
    import shutil

    found = shutil.which(name)
    if found:
        return found
    for directory in tool_dirs():
        if not directory or not os.path.isdir(directory):
            continue
        found = shutil.which(name, path=directory)
        if found:
            prepend_path(directory)
            return found
    return None
