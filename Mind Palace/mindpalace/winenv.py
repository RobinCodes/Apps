"""Windows support: the platform differences the rest of the app shouldn't carry.

Four things about Windows differ enough to matter here, and all of them are
answered in this file so that no other module has to know which operating
system it is running on.

*Where files go.* XDG puts settings under ~/.config; Windows has %APPDATA%
for what should follow the user between machines. The palace keeps only its
own window and which room was open last — every room's settings stay in that
app's own file, and everything worth syncing lives in the brain.

*Starting another app.* A door opens an app in a window of its own, on the
same interpreter. On Windows that has to be `pythonw.exe` rather than
`python.exe`, and the child has to be told `CREATE_NO_WINDOW`, or a console
window flashes up behind every app the palace opens.

*Opening a file.* `open_file` is for when there is no window to ask GTK from:
`os.startfile` on Windows, `xdg-open` everywhere else.

*The taskbar.* To Windows every app here is pythonw.exe; `set_app_id` and
`use_icon` give the palace its own name and icon there.
"""

from __future__ import annotations

import os
import subprocess
import sys

WINDOWS = os.name == "nt"

# Passed as `creationflags` to every child process. Zero elsewhere, so call
# sites can hand it over unconditionally.
NO_WINDOW = 0x08000000 if WINDOWS else 0


# ------------------------------------------------------------------ paths ----

def _xdg(var: str, app: str) -> str | None:
    """An XDG variable, honoured on every platform when it is actually set.

    The test suite points them at a scratch directory so a test run cannot
    touch real settings, and silently ignoring that on Windows would make the
    tests write to the user's live configuration.
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


# --------------------------------------------------------------- children ----

def gui_python() -> str:
    """The interpreter a windowed child should run on: this one, windowless."""
    exe = sys.executable
    if WINDOWS and exe.lower().endswith("python.exe"):
        quiet = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.isfile(quiet):
            return quiet
    return exe


def spawn(argv: list[str], cwd: str | None = None) -> bool:
    """Start a program that outlives the palace, with no console. False if it would not start."""
    try:
        subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=NO_WINDOW, start_new_session=not WINDOWS)
    except OSError:
        return False
    return True


def open_file(path: str) -> bool:
    """Hand a file to whatever this desktop opens it with. False if nothing would."""
    if not path or not os.path.exists(path):
        return False
    if WINDOWS:
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError:
            return False
        return True
    import shutil

    opener = shutil.which("xdg-open") or shutil.which("open")
    return bool(opener) and spawn([opener, path])


# ---------------------------------------------------------------- taskbar ----

def set_app_id(app_id: str) -> None:
    """Tell Windows which app this process is, so the taskbar can tell them apart.

    Without an ID of its own a window is grouped — and drawn — as whichever
    pythonw shortcut the taskbar met first. With one, it is matched to the
    Start Menu shortcut carrying the same ID (windows/install.ps1 sets it).
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
    """Give every window of `app` the palace's own icon rather than pythonw's.

    GTK 4 on Windows sets no icon on a window, so each one falls back to the
    Python logo in Alt+Tab and on the taskbar. `ico` is a file in the
    checkout's windows/icons, which windows/install.ps1 makes from the .svg.
    Without it this does nothing, as it does off Windows.
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
    """The HWND behind a realized GTK window, asked of the typelib (see Fathom's)."""
    import gi

    gi.require_version("GdkWin32", "4.0")
    from gi.repository import GdkWin32  # noqa: F401 - loads the typelib

    surface = gi.Repository.get_default().find_by_name("GdkWin32", "Win32Surface")
    return surface.find_method("get_handle")(window.get_surface())


def _set_icon(window, path: str) -> None:
    try:
        import ctypes

        hwnd = window_handle(window)
        user32 = ctypes.WinDLL("user32")
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.LoadImageW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_uint)
        user32.SendMessageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                        ctypes.c_void_p)
        user32.GetDpiForWindow.argtypes = (ctypes.c_void_p,)
        dpi = user32.GetDpiForWindow(hwnd) or 96
        for which, metric in ((1, 11), (0, 49)):          # ICON_BIG/SM_CXICON, ICON_SMALL/SM_CXSMICON
            size = user32.GetSystemMetricsForDpi(metric, dpi)
            if (path, size) not in _icons:
                _icons[(path, size)] = user32.LoadImageW(
                    None, path, 1, size, size, 0x10)      # IMAGE_ICON, LR_LOADFROMFILE
            if _icons[(path, size)]:
                user32.SendMessageW(hwnd, 0x0080, which, _icons[(path, size)])  # WM_SETICON
    except (ImportError, ValueError, TypeError, AttributeError, OSError):
        pass
