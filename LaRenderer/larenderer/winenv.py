"""Windows support: the platform differences the rest of the app shouldn't carry.

This app was written for Linux, and three things about Windows differ enough
to break it. All three are answered here so that no other module has to know
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


def set_app_id(app_id: str) -> None:
    """Tell Windows which app this process is, so the taskbar can tell them apart.

    To Windows every one of these apps is pythonw.exe, and without an ID of
    its own a window is grouped — and drawn — as whichever pythonw shortcut
    the taskbar met first, which is how a document opened from Explorer can
    sit on the taskbar wearing another app's icon. With one, the window is
    matched to the Start Menu shortcut carrying the same ID (windows/install.ps1
    sets it) and takes that shortcut's name and icon.
    """
    if not WINDOWS:
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(app_id))
    except (OSError, AttributeError):
        pass


# The checkout the app runs from: windows/icons is there, beside the apps.
_CHECKOUT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Loaded once per file and size, and kept: a window must not outlive its icon.
_icons: dict = {}


def use_icon(app, ico: str) -> None:
    """Give every window of `app` the app's own icon rather than pythonw's.

    GTK 4 on Windows sets no icon on a window — an icon name meant for the
    icon theme never reaches it — so each one falls back to its window class's,
    which GDK takes from pythonw.exe: the Python logo, in Alt+Tab and on the
    taskbar wherever no shortcut claims the window. `ico` is a file in the
    checkout's windows/icons, which windows/install.ps1 makes from the app's
    .svg. Without it this does nothing, as it does off Windows.
    """
    if not WINDOWS:
        return
    path = os.path.join(_CHECKOUT, "windows", "icons", ico)
    if not os.path.isfile(path):
        return

    def realized(window):
        _set_icon(window, path)

    def added(_app, window):
        if window.get_realized():
            realized(window)
        else:
            window.connect("realize", realized)

    app.connect("window-added", added)
    for window in app.get_windows():
        added(app, window)


def window_handle(window) -> int:
    """The HWND behind a realized GTK window.

    Asked of the typelib rather than of GdkWin32.Win32Surface.get_handle: a
    surface PyGObject meets before GdkWin32 is loaded is given a bare class of
    that name, without the method, and keeps it for the life of the process.
    """
    import gi

    gi.require_version("GdkWin32", "4.0")
    from gi.repository import GdkWin32  # noqa: F401 - loads the typelib

    surface = gi.Repository.get_default().find_by_name("GdkWin32", "Win32Surface")
    return surface.find_method("get_handle")(window.get_surface())


def _set_icon(window, path: str) -> None:
    try:
        import ctypes

        hwnd = window_handle(window)
        # A copy of user32 of its own, so the argument types set here are
        # nobody else's business.
        user32 = ctypes.WinDLL("user32")
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_uint)
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                        ctypes.c_void_p)
        user32.GetDpiForWindow.argtypes = (ctypes.c_void_p,)
        dpi = user32.GetDpiForWindow(hwnd) or 96
        # The big icon and the small one, each at the size Windows draws it
        # at on this window's display.
        for which, metric in ((1, 11), (0, 49)):          # ICON_BIG/SM_CXICON, ICON_SMALL/SM_CXSMICON
            size = user32.GetSystemMetricsForDpi(metric, dpi)
            if (path, size) not in _icons:
                _icons[(path, size)] = user32.LoadImageW(
                    None, path, 1, size, size, 0x10)      # IMAGE_ICON, LR_LOADFROMFILE
            if _icons[(path, size)]:
                user32.SendMessageW(hwnd, 0x0080, which, _icons[(path, size)])  # WM_SETICON
    except (ImportError, ValueError, TypeError, AttributeError, OSError):
        pass
