"""Where Prism keeps its files.

The other apps in this folder carry a winenv module because they run on
Windows too. Prism does not: it is built on VTE, which is a Unix pseudo
terminal widget, and there is no VTE for Windows to port it to. So this is
only the XDG half of that module — kept as its own file anyway, because the
test suite points the three XDG variables at a scratch directory and every
path in the app has to come from somewhere that honours them.
"""

from __future__ import annotations

import os

APP = "prism"


def _home(var: str, fallback: str) -> str:
    root = os.environ.get(var) or os.path.expanduser(fallback)
    return os.path.join(root, APP)


def config_home() -> str:
    """Settings — the file worth carrying to another machine."""
    return _home("XDG_CONFIG_HOME", "~/.config")


def data_home() -> str:
    """State — the session to put back, and anything else worth keeping."""
    return _home("XDG_DATA_HOME", "~/.local/share")


def cache_home() -> str:
    """Throwaway work."""
    return _home("XDG_CACHE_HOME", "~/.cache")
