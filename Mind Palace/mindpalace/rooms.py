"""The rooms: which apps there are, which of them can live in the palace, and where a file goes.

No GTK in here. What the palace knows about the apps beside it is read off
the disk the way a person would read it — the `.desktop` file says what an app
is called and what it is for, the launcher beside it is how it starts, and a
`room.py` in its package is the app saying it can live in here (ROOMS.md).

`room.py` is *read*, not imported, to fill the sidebar: its names are literals,
and `ast` can take them out of the file without running a line of the app. On a
3 GB machine that is the difference between a sidebar of twelve apps and a
process holding twelve apps; a room is only imported, and built, the first time
it is visited or a file is routed to it.

Where a file goes (`route`) is decided in the same terms: a note whose front
matter says what `kind:` of thing it is goes first to a room that says it
keeps that kind (a timeline to Clio, a question to Ariadne); then any room or
app that opens its extension; a folder goes to whoever opens folders; a
`scheme:` target (`study:<note>`, the palace's form of `mnemosyne --study`) to
whoever declared the scheme. The palace tries them in that order and falls back
to the desktop.
"""

from __future__ import annotations

import ast
import importlib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Mind Palace/mindpalace/rooms.py -> the Apps checkout.
CHECKOUT = Path(__file__).resolve().parent.parent.parent

FOLDER = "/"            # in `opens`: this app opens folders
URL = "http:"           # in `opens`: this app opens web links (http and https)

MARKDOWN = (".md", ".markdown", ".mdown", ".mkd", ".mkdn", ".mdwn", ".mdtxt", ".mdtext")
LATEX = (".tex", ".ltx", ".latex", ".sty", ".cls", ".dtx")
WEB = (".htm", ".html", ".shtml", ".xht", ".xhtml")
PICTURES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".heic",
            ".heif", ".avif", ".jxl", ".svg", ".mp4", ".mkv", ".webm", ".mov", ".avi",
            ".m4v", ".mp3", ".flac", ".ogg", ".opus", ".wav", ".m4a")

# What the palace knows about the apps of this repository when they do not
# say it themselves: where each sits in the sidebar (a room's own ORDER wins),
# what it opens (added to a room's own OPENS), what it is the keeper of (the
# `kind:` in a note's front matter, SYSTEM.md §8, §10, §11), how a scheme
# target becomes its command line when it is a door, and what keeps it from
# running at all on this platform. Folders not named here still appear, after
# these, from their .desktop files.
KNOWN = {
    "Notedown":       {"order": 10, "opens": MARKDOWN},
    "Ariadne":        {"order": 20, "kinds": ("question", "thread")},
    "Clio":           {"order": 30, "kinds": ("timeline",)},
    "Mnemosyne":      {"order": 40, "opens": ("study:",),
                       "argv": {"study:": ["--study"]}},
    "Hermione":       {"order": 50, "opens": (FOLDER,)},
    "The Eye":        {"order": 60, "opens": PICTURES},
    "Fathom":         {"order": 70},
    "LaRenderer":     {"order": 80, "opens": LATEX},
    "Git Manager":    {"order": 90},
    "Claude Program": {"order": 100},
    "Terminal":       {"order": 110, "posix_only":
                       "Prism needs VTE, and VTE has no Windows build."},
    "Lyndon Browser": {"order": 120, "opens": (*WEB, URL)},
}

# Folders in the checkout that are not apps, and the palace itself.
NOT_APPS = {"house", "windows", "Mind Palace", "Order"}

UNKNOWN_ORDER = 200


@dataclass(frozen=True)
class Spec:
    """One entry in the sidebar: a native room, an app's room, or a door."""

    title: str
    order: int
    kind: str                           # "native" · "room" · "door"
    folder: Path | None = None          # the app's folder in the checkout
    package: str | None = None          # the package holding room.py
    launcher: Path | None = None        # how the app starts on its own
    icon: Path | None = None            # an image file, or None
    icon_name: str = ""                 # a themed icon, for the native rooms
    comment: str = ""                   # what it is, in a line
    generic: str = ""                   # what kind of thing it is
    opens: tuple[str, ...] = ()         # extensions, schemes, FOLDER, URL
    kinds: tuple[str, ...] = ()         # front-matter kinds it keeps
    argv: dict = field(default_factory=dict, hash=False, compare=False)
    unavailable: str = ""               # why it cannot run here, if it cannot

    def matches(self, name: str) -> bool:
        return self.title.casefold() == name.casefold()


# ------------------------------------------------------------ reading ----

def room_meta(path: Path) -> dict:
    """TITLE, ICON, ORDER, OPENS and KINDS out of a room.py, without running it."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return {}
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("TITLE", "ICON", "ORDER", "OPENS", "KINDS"):
            try:
                found[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError):
                continue
    return found


def read_desktop(path: Path) -> dict:
    """The [Desktop Entry] group of a .desktop file, as a dict of its keys."""
    entry, inside = {}, False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return entry
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            if inside:
                break           # the actions come after, and have Names of their own
            inside = line == "[Desktop Entry]"
            continue
        if inside and "=" in line and not line.startswith("#"):
            name, _, value = line.partition("=")
            entry.setdefault(name.strip(), value.strip())
    return entry


def _launcher(folder: Path, names) -> Path | None:
    for name in names:
        if not name:
            continue
        for candidate in (folder / f"{name}.py", folder / name):
            if candidate.is_file():
                return candidate
    return None


def _icon(folder: Path, names) -> Path | None:
    for name in names:
        if not name:
            continue
        for candidate in (folder / name, folder / f"{name}.svg", folder / f"{name}.png"):
            if candidate.is_file() and candidate.suffix.lower() in (".svg", ".png"):
                return candidate
    data = folder / "data" / "icons"
    if data.is_dir():
        for name in names:
            if name:
                # Not Path(name).stem: an icon called org.lyndon.Browser has
                # dots in its name that are not an extension.
                wanted = name if name.lower().endswith(".svg") else f"{name}.svg"
                for found in sorted(data.rglob(wanted)):
                    return found
    return None


def _room_package(folder: Path) -> str | None:
    try:
        children = sorted(folder.iterdir())
    except OSError:
        return None
    for child in children:
        if child.is_dir() and (child / "__init__.py").is_file() \
                and (child / "room.py").is_file():
            return child.name
    return None


def _tuple(value) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return ()


def spec_for(folder: Path, platform: str = os.name) -> Spec | None:
    """What one folder of the checkout is in the palace, or None if it is not an app."""
    desktops = sorted(folder.glob("*.desktop")) + sorted((folder / "data").glob("*.desktop"))
    entry, launcher, stem = {}, None, ""
    for desktop in desktops:
        entry = read_desktop(desktop)
        exec_name = Path((entry.get("Exec") or "").split(" ")[0]).name
        launcher = _launcher(folder, (desktop.stem, exec_name))
        if launcher is not None:
            stem = desktop.stem
            break
    if launcher is None:
        return None

    known = KNOWN.get(folder.name, {})
    package = _room_package(folder)
    meta = room_meta(folder / package / "room.py") if package else {}
    order = meta.get("ORDER")
    if not isinstance(order, int):
        order = known.get("order", UNKNOWN_ORDER)
    title = meta.get("TITLE") if isinstance(meta.get("TITLE"), str) else None
    icon_names = (meta.get("ICON") if isinstance(meta.get("ICON"), str) else "",
                  entry.get("Icon", ""), stem, launcher.stem)
    unavailable = known.get("posix_only", "") if platform == "nt" else ""

    def merged(key, own):
        out = list(_tuple(own))
        for extra in known.get(key, ()):
            if extra not in out:
                out.append(extra)
        return tuple(out)

    return Spec(
        title=title or entry.get("Name") or folder.name,
        order=order,
        kind="room" if package else "door",
        folder=folder, package=package, launcher=launcher,
        icon=_icon(folder, icon_names),
        comment=entry.get("Comment", ""), generic=entry.get("GenericName", ""),
        opens=merged("opens", meta.get("OPENS")),
        kinds=merged("kinds", meta.get("KINDS")),
        argv=dict(known.get("argv", {})),
        unavailable=unavailable)


HOME = Spec(title="Home", order=0, kind="native", icon_name="go-home-symbolic",
            comment="Today at a glance: what is due, what is waiting, what changed.")


def discover(checkout: Path = CHECKOUT, platform: str = os.name,
             skip: set[str] | None = None) -> list[Spec]:
    """Every room and door, in sidebar order, with Home first.

    Two apps claiming one title is resolved by keeping the first in order —
    the sidebar, the shortcuts and `host.show(title)` all address rooms by
    title, so a title has to mean one room.
    """
    skip = NOT_APPS if skip is None else skip
    found = []
    try:
        folders = sorted(checkout.iterdir())
    except OSError:
        folders = []
    for folder in folders:
        if not folder.is_dir() or folder.name.startswith(".") or folder.name in skip:
            continue
        spec = spec_for(folder, platform)
        if spec is not None:
            found.append(spec)
    found.sort(key=lambda s: (s.order, s.title.casefold()))
    out, seen = [HOME], {HOME.title.casefold()}
    for spec in found:
        if spec.title.casefold() not in seen:
            seen.add(spec.title.casefold())
            out.append(spec)
    return out


# ------------------------------------------------------------ loading ----

def load(spec: Spec):
    """A room's module, imported now because it is wanted now.

    The app's folder goes on the end of the path, not the front: the palace's
    own modules must keep their names, and every app's package has a name of
    its own, so nothing in one can shadow anything in another.
    """
    folder = str(spec.folder)
    if folder not in sys.path:
        sys.path.append(folder)
    return importlib.import_module(f"{spec.package}.room")


# ------------------------------------------------------------ routing ----

_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]+):(?!//)")


def scheme_of(target: str) -> str | None:
    """`study:` for "study:/path/note.md"; None for a path, a drive letter or a URL."""
    match = _SCHEME.match(target)
    if not match or os.path.exists(target):
        return None
    return match.group(1).lower() + ":"


def is_url(target: str) -> bool:
    return bool(re.match(r"^https?://", target, re.IGNORECASE))


def front_matter_kind(target: str) -> str | None:
    """The `kind:` a Markdown note declares about itself, if it declares one."""
    path = Path(target)
    if path.suffix.lower() not in MARKDOWN or not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return None
    if not head.startswith("---"):
        return None
    for line in head.splitlines()[1:]:
        if line.strip() == "---":
            break
        match = re.match(r"^kind\s*:\s*['\"]?([\w-]+)['\"]?\s*(?:#.*)?$", line)
        if match:
            return match.group(1).lower()
    return None


def route(target: str, specs: list[Spec], kind: str | None = None) -> list[Spec]:
    """Who should be offered `target`, in the order they should be offered it."""
    if is_url(target):
        return [s for s in specs if URL in s.opens]
    scheme = scheme_of(target)
    if scheme:
        return [s for s in specs if scheme in s.opens]
    path = Path(target)
    if path.is_dir():
        return [s for s in specs if FOLDER in s.opens]
    ext = path.suffix.lower()
    keepers = [s for s in specs if kind and kind in s.kinds]
    openers = [s for s in specs if ext and ext in s.opens and s not in keepers]
    return keepers + openers


def door_argv(spec: Spec, target: str | None = None) -> list[str]:
    """What follows the launcher on a door's command line, for a target or for none."""
    if not target:
        return []
    scheme = scheme_of(target)
    if scheme:
        flag = spec.argv.get(scheme)
        return [*flag, target[len(scheme):]] if flag is not None else []
    return [target]
