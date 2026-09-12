"""Windows support: the platform differences the rest of the app shouldn't carry.

This app was written for Linux, and two things about Windows differ enough to
break it. Both are answered here so that no other module has to know which
operating system it is running on.

*Where files go.* XDG puts settings under ~/.config and state under
~/.local/share. Windows has two roots instead of two subdirectories:
%APPDATA% for what should follow the user between machines, and
%LOCALAPPDATA% for what should not. Settings roam; the scan cache stays put.
On Linux nothing below changes — the XDG variables still win.

*What a "drive" is.* A Unix machine has one tree with filesystems mounted into
it, and /proc/mounts says where. Windows has lettered volumes and an API to
enumerate them. `volumes.py` asks here which world it is in.

Fathom shells out to nothing — the walk is `os.scandir` and the deleting is
GIO — so unlike the other apps in this repository there is no tool-hunting
here, and no console windows to suppress.
"""

from __future__ import annotations

import os

WINDOWS = os.name == "nt"


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
    """Directory for state — anything regenerable that should survive a restart."""
    override = _xdg("XDG_DATA_HOME", app)
    if override:
        return override
    if WINDOWS:
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
            os.path.join("~", "AppData", "Local"))
        return os.path.join(root, app)
    return os.path.join(os.path.expanduser("~/.local/share"), app)


def cache_home(app: str) -> str:
    """Directory for throwaway work."""
    override = _xdg("XDG_CACHE_HOME", app)
    if override:
        return override
    if WINDOWS:
        return os.path.join(data_home(app), "cache")
    return os.path.join(os.path.expanduser("~/.cache"), app)
