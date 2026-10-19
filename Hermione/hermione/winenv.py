"""Windows support, and the other tool-hunting the rest of the app shouldn't carry.

This app was written for Linux. Three things differ enough elsewhere to break
it, and all three are answered here so no other module has to know which
operating system it is running on.

*Where files go.* XDG puts settings under ~/.config, state under
~/.local/share and throwaway work under ~/.cache. Windows has two roots
instead of three directories: %APPDATA% for what should follow the user
between machines, and %LOCALAPPDATA% for what should not. Settings roam; the
thumbnail cache stays put.

*Which tools exist.* A file manager is the one app here that is expected to
reach for whatever the machine has — an archiver, a terminal, a search tool —
and to carry on without it. `which` looks where installers actually put
things, and `tool` remembers the answer so a menu being built does not stat
the same twelve directories again.

*Taskbar identity and window icons.* The same two Windows problems the rest of
the suite has: every app here is `pythonw.exe` to Windows, and GTK 4 sets no
window icon on it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

WINDOWS = os.name == "nt"

# Passed to every subprocess: on Windows a console program started from a GUI
# app flashes a black window otherwise.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if WINDOWS else 0


# --------------------------------------------------------------- where ----

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


def cache_home(app: str = "") -> str:
    """Directory for throwaway work. With no app name, the user's cache root.

    The root matters here: freedesktop thumbnails live in a shared directory
    under it, and a thumbnail this app generates is one every other file
    manager on the machine can then use.
    """
    override = _xdg("XDG_CACHE_HOME", app)
    if override:
        return override
    if WINDOWS:
        return os.path.join(data_home(app or "hermione"), "cache")
    root = os.path.expanduser("~/.cache")
    return os.path.join(root, app) if app else root


# --------------------------------------------------------------- tools ----

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
            for p in ("~/.local/bin", "/usr/local/bin", "/usr/bin", "/bin",
                      "/opt/homebrew/bin")
        ]

    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA", os.path.join(home, r"AppData\Local"))
    dirs = [
        os.path.join(home, r".local\bin"),
        os.path.join(local, r"Microsoft\WindowsApps"),
    ]
    for pf in _program_files():
        dirs.append(os.path.join(pf, "7-Zip"))
        dirs.append(os.path.join(pf, "Git", "usr", "bin"))
        dirs.append(os.path.join(pf, "Git", "cmd"))
    # The GTK stack this app runs on; MSYS2 ships a good deal else besides.
    dirs.append(os.path.dirname(sys.executable))
    dirs.append(r"C:\msys64\mingw64\bin")
    dirs.append(r"C:\msys64\usr\bin")
    return dirs


def prepend_path(directory: str) -> None:
    """Put `directory` first on PATH, so children inherit the find as well."""
    path = os.environ.get("PATH", "")
    if directory and directory not in path.split(os.pathsep):
        os.environ["PATH"] = os.pathsep.join([directory, path]) if path else directory


def which(name: str) -> str | None:
    """shutil.which, plus the directories installers actually use."""
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


_tools: dict[str, str | None] = {}


def tool(name: str) -> str | None:
    """`which`, answered once per process.

    Menus ask what exists every time they are built, and the answer does not
    change while the app is running — a tool installed mid-session is worth a
    restart, not twelve `stat` calls per right-click.
    """
    if name not in _tools:
        _tools[name] = which(name)
    return _tools[name]


def forget_tools() -> None:
    """Drop the cache above. For the tests, which fake tools in and out."""
    _tools.clear()


def run(argv, **kwargs):
    """subprocess.run with this platform's "no console window, please"."""
    kwargs.setdefault("creationflags", NO_WINDOW) if WINDOWS else None
    return subprocess.run(argv, **kwargs)


# A terminal to open a folder in, best first. Each entry says how that
# terminal is told which directory to start in, because they disagree: most
# take a working directory, a few only take a command to run.
TERMINALS = (
    ("ghostty", ["--working-directory={dir}"]),
    ("kitty", ["--directory={dir}"]),
    ("alacritty", ["--working-directory={dir}"]),
    ("foot", ["--working-directory={dir}"]),
    ("wezterm", ["start", "--cwd", "{dir}"]),
    ("gnome-terminal", ["--working-directory={dir}"]),
    ("konsole", ["--workdir", "{dir}"]),
    ("xfce4-terminal", ["--working-directory={dir}"]),
    ("tilix", ["--working-directory={dir}"]),
    ("xterm", ["-e", "cd {dir} && exec $SHELL"]),
)

WINDOWS_TERMINALS = (
    ("wt", ["-d", "{dir}"]),
    ("pwsh", ["-NoExit", "-Command", "Set-Location -LiteralPath '{dir}'"]),
    ("powershell", ["-NoExit", "-Command", "Set-Location -LiteralPath '{dir}'"]),
)


def terminal_argv(directory: str, preferred: str = "") -> list[str] | None:
    """A command line that opens a terminal sitting in `directory`.

    `preferred` is the user's own setting and wins when it is there; it may be
    a whole command line with {dir} in it, for a terminal this list has never
    heard of.
    """
    if preferred.strip():
        if "{dir}" in preferred:
            parts = preferred.replace("{dir}", directory).split()
            exe = which(parts[0])
            return [exe] + parts[1:] if exe else None
        exe = which(preferred.strip())
        if exe:
            for name, args in TERMINALS + WINDOWS_TERMINALS:
                if os.path.basename(preferred.strip()).startswith(name):
                    return [exe] + [a.format(dir=directory) for a in args]
            return [exe]

    table = WINDOWS_TERMINALS if WINDOWS else TERMINALS
    for name, args in table:
        exe = tool(name)
        if exe:
            return [exe] + [a.format(dir=directory) for a in args]
    if WINDOWS:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/K", f"cd /d {directory}"]
    return None


# ------------------------------------------------------- windows dressing ---

def set_app_id(app_id: str) -> None:
    """Tell Windows which app this process is, so the taskbar can tell them apart.

    To Windows every one of these apps is pythonw.exe, and without an ID of
    its own a window is grouped — and drawn — as whichever pythonw shortcut
    the taskbar met first, which is how a folder opened from Explorer can sit
    on the taskbar wearing another app's icon.
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
        for which_icon, metric in ((1, 11), (0, 49)):     # ICON_BIG/SM_CXICON, ICON_SMALL
            size = user32.GetSystemMetricsForDpi(metric, dpi)
            if (path, size) not in _icons:
                _icons[(path, size)] = user32.LoadImageW(
                    None, path, 1, size, size, 0x10)      # IMAGE_ICON, LR_LOADFROMFILE
            if _icons[(path, size)]:
                user32.SendMessageW(hwnd, 0x0080, which_icon, _icons[(path, size)])
    except (ImportError, ValueError, TypeError, AttributeError, OSError):
        pass
