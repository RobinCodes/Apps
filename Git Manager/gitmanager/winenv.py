"""Windows support: the platform differences the rest of the app shouldn't carry.

This app was written for Linux, and four things about Windows differ enough
to break it. All four are answered here so that no other module has to know
which operating system it is running on.

*Where files go.* XDG puts settings under ~/.config and state under
~/.local/share. Windows has two roots instead of two subdirectories:
%APPDATA% for what should follow the user between machines, and
%LOCALAPPDATA% for what should not. Settings roam; transcripts and caches
stay put. On Linux nothing below changes — the XDG variables still win.

*Where the tools are.* A Linux distribution puts git in /usr/bin, which is on
everyone's PATH. Windows installers put it in Program Files and edit the PATH
of interactive shells, which a process started from a shortcut does not
always inherit — and MSYS2's own login shell replaces PATH outright. So when
a tool is not on PATH, look where its installer actually puts it, and once
found, put that directory on PATH so the child processes see it too.

*Console windows.* A child process spawned from a windowed program gets a
console of its own unless it is told otherwise. Git Manager polls `git
status` every fifteen seconds; without CREATE_NO_WINDOW that is a black
rectangle blinking over the window for as long as the app is open.

*How a path is spelled.* One directory has one name on Linux. On Windows it
has several: git answers `rev-parse --show-toplevel` with forward slashes
whatever the platform, os.scandir and the file chooser hand back backslashes,
and the filesystem itself ignores case. Compared with `==`, the same
repository read from two of those sources looks like two repositories --
listed twice, and unmatchable against anything written to the config by the
other. canonical() and same_path() below are what every such comparison goes
through.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys

WINDOWS = os.name == "nt"

# Passed as `creationflags` to every child process. Zero elsewhere, so call
# sites can hand it over unconditionally.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if WINDOWS else 0


# ------------------------------------------------------------------ paths ----

def canonical(path: str) -> str:
    """One spelling per directory, whichever tool handed the path over.

    normpath settles the separator and any trailing one; expanduser is here
    because scan roots are typed into a text box by hand.
    """
    return os.path.normpath(os.path.expanduser(path)) if path else ""


def path_key(path: str) -> str:
    """A comparison key: canonical, and case-folded where the filesystem is."""
    return os.path.normcase(canonical(path))


def same_path(a: str, b: str) -> bool:
    """True when two spellings name the same directory."""
    return bool(a) and bool(b) and path_key(a) == path_key(b)


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
    """Directory for state — transcripts, caches, anything regenerable."""
    override = _xdg("XDG_DATA_HOME", app)
    if override:
        return override
    if WINDOWS:
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
            os.path.join("~", "AppData", "Local"))
        return os.path.join(root, app)
    return os.path.join(os.path.expanduser("~/.local/share"), app)


def cache_home(app: str) -> str:
    """Directory for throwaway work — build trees, rasterised pages."""
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


def _tex_dirs() -> list[str]:
    """MiKTeX and TeX Live, in the places their installers use."""
    dirs = []
    local = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
    dirs.append(os.path.join(local, r"Programs\MiKTeX\miktex\bin\x64"))
    for pf in _program_files():
        dirs.append(os.path.join(pf, r"MiKTeX\miktex\bin\x64"))
    # TeX Live installs per year and renamed its Windows directory in 2023.
    for pattern in (r"C:\texlive\*\bin\windows", r"C:\texlive\*\bin\win32"):
        dirs.extend(sorted(glob.glob(pattern), reverse=True))
    return dirs


def tool_dirs() -> list[str]:
    """Everywhere worth looking for a command line tool, best guess first."""
    if not WINDOWS:
        return [
            os.path.expanduser(p)
            for p in ("~/.local/bin", "~/.claude/local", "~/.npm-global/bin",
                      "~/.bun/bin", "/usr/local/bin", "/opt/homebrew/bin")
        ]

    home = os.path.expanduser("~")
    appdata = os.environ.get("APPDATA", os.path.join(home, r"AppData\Roaming"))
    local = os.environ.get("LOCALAPPDATA", os.path.join(home, r"AppData\Local"))

    dirs = [
        # npm's global prefix: where `npm i -g` puts claude.cmd, and not on the
        # PATH of a process started from a shortcut.
        appdata + r"\npm",
        os.path.join(local, r"Programs\claude"),
        os.path.join(home, r".local\bin"),
        os.path.join(home, r".claude\local"),
        os.path.join(home, r".bun\bin"),
        os.path.join(local, r"Microsoft\WindowsApps"),
    ]
    for pf in _program_files():
        dirs += [
            os.path.join(pf, r"Git\cmd"),
            os.path.join(pf, r"Git\bin"),
            os.path.join(pf, "GitHub CLI"),
            os.path.join(pf, "nodejs"),
        ]
    dirs.append(os.path.join(local, r"Programs\Git\cmd"))
    dirs.append(os.path.join(local, "GitHub CLI"))
    dirs += _tex_dirs()
    # The GTK stack this app runs on; also where poppler's pdftoppm lives.
    dirs.append(os.path.join(os.path.dirname(sys.executable)))
    dirs.append(r"C:\msys64\mingw64\bin")
    return dirs


def prepend_path(directory: str) -> None:
    """Put `directory` first on PATH, so children inherit the find as well."""
    path = os.environ.get("PATH", "")
    if directory and directory not in path.split(os.pathsep):
        os.environ["PATH"] = os.pathsep.join([directory, path]) if path else directory


def which(name: str) -> str | None:
    """shutil.which, plus the directories Windows installers actually use.

    A hit outside PATH is added to PATH, because the tool that was hard to
    find is usually one the child process will need to find as well — git
    calling git-credential-manager, npm calling node.
    """
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


def ensure_tools(*names: str) -> dict[str, str | None]:
    """Resolve several tools at startup, fixing PATH as a side effect."""
    return {name: which(name) for name in names}
