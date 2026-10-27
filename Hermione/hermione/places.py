"""The sidebar's contents: where a person keeps things.

Four kinds of place, and they come from four different authorities, which is
the whole reason this module exists.

*The user's own folders* are XDG's, read from `user-dirs.dirs` so that a
machine whose Downloads folder is called `Hämtningar` says so. GLib knows
these, and is asked rather than told.

*Bookmarks* are ours, in our own settings — but on first run they are seeded
from `~/.config/gtk-3.0/bookmarks`, the file GTK's own file chooser and
Nautilus share. Somebody who already bookmarked six folders should find them
here, and should not find this app quietly rewriting the file every other
program reads.

*Volumes* come from GIO's volume monitor, live: a stick plugged in appears
without a refresh, and `mount`, `unmount` and `eject` are its business too.

*The trash* is one row that has to know how full it is.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gio  # noqa: E402

from . import winenv  # noqa: E402

# The XDG directories worth a row of their own, in the order they are shown.
# "Templates" and "Public" are left out on purpose: almost nobody uses them,
# and a sidebar is only useful while it is short.
USER_DIRS = (
    (GLib.UserDirectory.DIRECTORY_DESKTOP, "Desktop", "user-desktop-symbolic"),
    (GLib.UserDirectory.DIRECTORY_DOWNLOAD, "Downloads", "folder-download-symbolic"),
    (GLib.UserDirectory.DIRECTORY_DOCUMENTS, "Documents", "folder-documents-symbolic"),
    (GLib.UserDirectory.DIRECTORY_PICTURES, "Pictures", "folder-pictures-symbolic"),
    (GLib.UserDirectory.DIRECTORY_MUSIC, "Music", "folder-music-symbolic"),
    (GLib.UserDirectory.DIRECTORY_VIDEOS, "Videos", "folder-videos-symbolic"),
)


class Place:
    """One row of the sidebar."""

    def __init__(self, name: str, path: str, icon: str, kind: str = "folder",
                 *, mount=None, volume=None, drive=None, detail: str = "",
                 uri: str = ""):
        self.name = name
        self.path = path
        self.icon = icon
        self.kind = kind          # home | user | bookmark | volume | trash | network | root
        self.mount = mount
        self.volume = volume
        self.drive = drive
        self.detail = detail      # the second line: free space, a device name
        self.uri = uri or (GLib.filename_to_uri(path, None) if path else "")

    @property
    def mounted(self) -> bool:
        if self.kind != "volume":
            return True
        return bool(self.path)

    @property
    def can_eject(self) -> bool:
        if self.drive is not None and self.drive.can_eject():
            return True
        if self.volume is not None and self.volume.can_eject():
            return True
        return self.mount is not None and self.mount.can_unmount()

    @property
    def removable(self) -> bool:
        return self.drive is not None and self.drive.is_removable()

    def __repr__(self) -> str:
        return f"<Place {self.kind} {self.name!r} {self.path!r}>"


def home() -> str:
    return os.path.expanduser("~")


def path_from_argument(argument: str) -> str:
    """Turn a command-line argument into a local path.

    Whatever opens a folder hands the handler what the desktop entry's `%U`
    asked for, which is a URI — `file:///home/you/Downloads`, percent-encoded
    — and not a path. Treating that as a path gets you a nonsense relative
    one inside the current directory, which is what being made somebody's
    default file manager turns from a curiosity into the first thing they
    see. Anything that is not a `file:` URI is taken as a path, because that
    is what it is when the app is started from a shell.
    """
    argument = argument.strip()
    if not argument:
        return ""
    lowered = argument.lower()
    if lowered.startswith("file://"):
        try:
            path = GLib.filename_from_uri(argument)[0] or ""
            # GLib answers in the system's own separator; everything else
            # here speaks Python's, which under MSYS2 is `/` even on Windows.
            return os.path.abspath(path) if path else ""
        except GLib.Error:
            # A URI we cannot decode is still better read as a path than
            # dropped on the floor.
            return argument
    if "://" in argument[:12]:
        # sftp://, smb://, trash:// — real places, but not ones this can list.
        return ""
    return os.path.abspath(os.path.expanduser(argument))


def user_dirs() -> list[Place]:
    """The XDG folders that exist, under the names the user's locale gives them."""
    places = [Place("Home", home(), "user-home-symbolic", "home")]
    seen = {home()}
    for which, fallback, icon in USER_DIRS:
        path = GLib.get_user_special_dir(which)
        if not path or path in seen or not os.path.isdir(path):
            continue
        seen.add(path)
        places.append(Place(os.path.basename(path) or fallback, path, icon, "user"))
    return places


def gtk_bookmark_file() -> str:
    root = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(root, "gtk-3.0", "bookmarks")


def read_gtk_bookmarks() -> list[tuple[str, str]]:
    """GTK's own bookmarks, as (name, path). Only the local ones.

    The file is one URI per line with an optional label after a space. A
    bookmark pointing at `sftp://` is real but not somewhere this app can
    list, so it is left to the program that can.
    """
    out = []
    try:
        with open(gtk_bookmark_file(), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        uri, _, label = line.partition(" ")
        if not uri.startswith("file://"):
            continue
        try:
            path = GLib.filename_from_uri(uri)[0]
        except GLib.Error:
            continue
        if path and os.path.isdir(path):
            out.append((label.strip() or os.path.basename(path.rstrip("/")) or path,
                        path))
    return out


def bookmarks(config) -> list[Place]:
    """The user's bookmarks, seeded from GTK's the first time.

    The seeding happens once and is remembered by the fact that the list is
    no longer empty — a bookmark deliberately removed must not come back on
    the next launch.
    """
    if not config["bookmarks"] and not config.get("bookmarks_seeded"):
        for name, path in read_gtk_bookmarks():
            config.add_bookmark(path, name)
        config["bookmarks_seeded"] = True
    places = []
    for mark in config["bookmarks"]:
        path = mark.get("path", "")
        if not path:
            continue
        name = mark.get("name") or os.path.basename(path.rstrip(os.sep)) or path
        there = os.path.isdir(path)
        places.append(Place(name, path, "starred-symbolic" if there
                            else "dialog-warning-symbolic", "bookmark",
                            detail="" if there else "missing"))
    return places


def volumes(monitor: Gio.VolumeMonitor | None = None) -> list[Place]:
    """Discs, sticks and partitions, mounted or not.

    A volume that is mounted is shown at its mount point and can be browsed;
    one that is not is shown anyway, because clicking it to mount it is how a
    person expects to use a file manager, and GIO can do that.
    """
    monitor = monitor or Gio.VolumeMonitor.get()
    places: list[Place] = []
    claimed: set[str] = set()

    for volume in monitor.get_volumes():
        mount = volume.get_mount()
        path = ""
        detail = ""
        if mount is not None:
            root = mount.get_root()
            path = root.get_path() or ""
            if path:
                claimed.add(path)
                detail = free_space_text(path)
        drive = volume.get_drive()
        icon = _icon_name(volume.get_symbolic_icon()) or "drive-harddisk-symbolic"
        places.append(Place(volume.get_name() or "Volume", path, icon, "volume",
                            mount=mount, volume=volume, drive=drive, detail=detail))

    # Mounts with no volume behind them: network shares, MTP phones, anything
    # gvfs has made up. They are real places and nothing else lists them.
    for mount in monitor.get_mounts():
        if mount.get_volume() is not None:
            continue
        root = mount.get_root()
        path = root.get_path() or ""
        uri = root.get_uri() or ""
        if path and path in claimed:
            continue
        remote = not path or uri.split(":")[0] not in ("file",)
        icon = _icon_name(mount.get_symbolic_icon()) or (
            "folder-remote-symbolic" if remote else "drive-removable-media-symbolic")
        places.append(Place(mount.get_name() or "Mount", path, icon,
                            "network" if remote else "volume",
                            mount=mount, uri=uri,
                            detail=free_space_text(path) if path else ""))
    return places


def _icon_name(icon) -> str:
    if isinstance(icon, Gio.ThemedIcon):
        names = icon.get_names()
        return names[0] if names else ""
    return ""


def free_space_text(path: str) -> str:
    from .units import human_size

    try:
        usage = os.statvfs(path) if not winenv.WINDOWS else None
    except OSError:
        return ""
    if usage is not None:
        free = usage.f_bavail * usage.f_frsize
        total = usage.f_blocks * usage.f_frsize
    else:
        import shutil

        try:
            info = shutil.disk_usage(path)
        except OSError:
            return ""
        free, total = info.free, info.total
    if not total:
        return ""
    return f"{human_size(free)} free"


def roots() -> list[Place]:
    """The top of the tree, which is a different thing on each platform."""
    if winenv.WINDOWS:
        out = []
        import string

        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                out.append(Place(drive, drive, "drive-harddisk-symbolic", "root",
                                 detail=free_space_text(drive)))
        return out
    return [Place("Computer", "/", "drive-multidisk-symbolic", "root",
                  detail=free_space_text("/"))]


def trash_place(count: int = -1) -> Place:
    # count is -1 while nobody has looked yet, which is not the same as full.
    icon = "user-trash-full-symbolic" if count > 0 else "user-trash-symbolic"
    detail = ""
    if count > 0:
        detail = f"{count} item" + ("s" if count != 1 else "")
    return Place("Trash", "", icon, "trash", detail=detail)


def sidebar(config, monitor: Gio.VolumeMonitor | None = None) -> list[tuple[str, list[Place]]]:
    """The whole sidebar, as (heading, places). Headings may be empty.

    Assembled in one place so that the order is one decision rather than a
    dozen `append` calls scattered through the window.
    """
    groups: list[tuple[str, list[Place]]] = [("", user_dirs())]
    marks = bookmarks(config)
    if marks:
        groups.append(("Bookmarks", marks))
    discs = volumes(monitor)
    local = [p for p in discs if p.kind == "volume"]
    remote = [p for p in discs if p.kind == "network"]
    groups.append(("Devices", roots() + local))
    if remote:
        groups.append(("Network", remote))
    return groups


# ------------------------------------------------------------- mounting ----

def mount(place: Place, on_done) -> None:
    """Mount a volume, then call on_done(path, error).

    Asynchronous because mounting a disc can take seconds and asking for a
    password takes as long as a person takes. `Gtk.MountOperation` would be
    better still, but it needs a window, so the caller hands one in via
    `operation` when it has one.
    """
    if place.volume is None:
        on_done("", "Nothing to mount")
        return

    def finished(volume, task):
        try:
            volume.mount_finish(task)
        except GLib.Error as exc:
            on_done("", exc.message or "Could not mount it")
            return
        mounted = volume.get_mount()
        root = mounted.get_root() if mounted is not None else None
        on_done(root.get_path() if root is not None else "", "")

    place.volume.mount(Gio.MountMountFlags.NONE, None, None, finished)


def unmount(place: Place, on_done) -> None:
    """Unmount or eject, whichever this thing supports. on_done(error)."""
    def finisher(ejecting):
        def finished(source, task):
            try:
                if ejecting:
                    source.eject_with_operation_finish(task)
                else:
                    source.unmount_with_operation_finish(task)
            except GLib.Error as exc:
                on_done(exc.message or "Could not unmount it")
                return
            on_done("")
        return finished

    flags = Gio.MountUnmountFlags.NONE
    # Ejecting is the stronger request and the one a person means by the
    # button on a USB stick, so it is tried first where it is offered.
    if place.drive is not None and place.drive.can_eject():
        place.drive.eject_with_operation(flags, None, None, finisher(True))
        return
    if place.volume is not None and place.volume.can_eject():
        place.volume.eject_with_operation(flags, None, None, finisher(True))
        return
    if place.mount is not None and place.mount.can_unmount():
        place.mount.unmount_with_operation(flags, None, None, finisher(False))
        return
    on_done("That cannot be unmounted")
