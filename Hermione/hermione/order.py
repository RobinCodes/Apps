"""The file side of the Order: the inbox, the places, and the brain.

SYSTEM.md gives Hermione four jobs in Robin's Order, and this module is the
part of each that is not a widget, so the suite can check them without a
display:

*Send to Inbox.* The one capture point is `~/SecondBrain/inbox`, and a file
goes there by an ordinary move — the same `ops.Job` as a drag, recorded the
same way, so `Ctrl+Z` puts it back where it was. A name already taken in the
inbox is kept beside, never asked about: capturing is meant to be one action.

*Processing the inbox.* Each thing in it belongs somewhere, and SYSTEM.md §2
says where: a Studies subject, Documents, or a project area. `destinations()`
lists the ones that exist on this machine and `suggest()` puts the likely
ones first, so the usual answer is one key. Claude can be asked for its own
suggestion (`placement_prompt`, `parse_placements`); nothing moves until the
person accepts it.

*The brain in the palette and in Properties.* `brain.search()` and the note
index, run off the GTK thread, and `note_facts()` for "is this file a note,
and what is it called there".

*Markdown.* Notedown lives in the same checkout. When nothing on the machine
claims Markdown, it is what opens it.

Everything here asks `brain.places()`, which honours `ORDER_HOME` — which is
how the suite builds a brain of its own in a scratch folder.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import brain, claude, ops, winenv

MARKDOWN = (".md", ".markdown", ".mdown", ".mkd", ".mkdn", ".mdwn")

# What a "document" is for the purpose of a suggestion: something written to
# be read, by SYSTEM.md's own definition.
DOCUMENT_TYPES = (".pdf", ".doc", ".docx", ".odt", ".rtf", ".ppt", ".pptx",
                  ".odp", ".xls", ".xlsx", ".ods", ".epub")
CODE_TYPES = (".py", ".c", ".h", ".js", ".ts", ".html", ".css", ".sh", ".rs",
              ".go", ".java", ".cpp", ".json", ".toml", ".yaml", ".yml")

# A class note is named by its date: ISO, or the Hungarian form Studies
# already uses (SYSTEM.md §2, "Names").
_DATED = re.compile(r"^\d{4}[-.]\d{2}[-.]\d{2}")


def here() -> brain.Places | None:
    """The Order on this machine, or None where there is no brain."""
    where = brain.places()
    return where if where.exists() else None


# --------------------------------------------------------------- the inbox ---

def _keep_both(_source: str, _target: str, _is_dir: bool):
    return ops.RENAME


def inbox_job(paths: list[str], where: brain.Places | None = None) -> ops.Job:
    """The move that sends `paths` to the inbox, ready for `host.run_job`."""
    where = where or brain.places()
    where.inbox.mkdir(parents=True, exist_ok=True)
    return ops.Job(ops.MOVE, paths, str(where.inbox), resolver=_keep_both)


def inbox_count(where: brain.Places | None = None) -> int:
    """How many things are waiting; -1 where there is no brain to ask."""
    where = where or brain.places()
    if not where.exists():
        return -1
    try:
        return len(brain.inbox_items(where))
    except OSError:
        return 0


def inbox_items(where: brain.Places | None = None) -> list[str]:
    """The inbox, oldest first, as paths.

    Normalised: under MSYS2's Python, `Path.iterdir` joins a child on with
    a backslash while every other path in the app is spelled with `/`, and
    paths are compared as strings here.
    """
    try:
        return [os.path.normpath(str(p)) for p in brain.inbox_items(where)]
    except OSError:
        return []


@dataclass(frozen=True)
class Destination:
    label: str          # "Studies › Biology"
    path: str
    group: str          # "studies", "documents" or "projects"


def destinations(where: brain.Places | None = None) -> list[Destination]:
    """Where an inbox item can go, from SYSTEM.md §2, as this machine has it.

    Every Studies subject, Documents, and every project area — only the ones
    that exist, because filing into a folder the layout does not have yet is
    a decision about the layout, not about the file.
    """
    where = where or brain.places()
    found: list[Destination] = []
    for name in _folders(where.studies):
        found.append(Destination(f"Studies › {name}",
                                 os.path.join(where.studies, name), "studies"))
    documents = where.projects / "Documents"
    if documents.is_dir():
        found.append(Destination("Documents", str(documents), "documents"))
    for name in _folders(where.projects):
        if name in ("Studies", "Documents"):
            continue
        found.append(Destination(f"Projects › {name}",
                                 os.path.join(where.projects, name), "projects"))
    return found


def _folders(parent) -> list[str]:
    try:
        names = [entry.name for entry in os.scandir(parent)
                 if entry.is_dir() and not entry.name.startswith(".")]
    except OSError:
        return []
    return sorted(names, key=str.casefold)


def suggest(path: str, places_: list[Destination]) -> list[Destination]:
    """`places_`, the likeliest home for `path` first.

    Cheap guesses that are usually right, in SYSTEM.md's order of questions:
    a subject named in the file name or its first lines is a class note; a
    dated Markdown note leans that way too; a PDF or an office file is a
    document; a LaTeX file belongs to Overleaf, code to Programming. Ties
    keep the list's own order, so the numbers stay put for most items.
    """
    name = os.path.basename(path.rstrip("/\\"))
    lowered = name.lower()
    folded = brain.key(name)
    head = brain.key(_head(path)) if lowered.endswith(MARKDOWN + (".txt", ".tex")) else ""
    is_dir = os.path.isdir(path)

    def score(place: Destination) -> int:
        last = brain.key(place.label.rsplit("›", 1)[-1])
        points = 0
        if place.group == "studies":
            if last and (last in folded or (head and last in head)):
                points += 50
            if lowered.endswith(MARKDOWN) and _DATED.match(name):
                points += 10
        elif place.group == "documents":
            if lowered.endswith(DOCUMENT_TYPES):
                points += 30
            elif lowered.endswith(MARKDOWN) and not _DATED.match(name):
                points += 5
        elif place.group == "projects":
            if last == "overleaf" and lowered.endswith((".tex", ".bib", ".cls", ".sty")):
                points += 40
            if last == "programming" and (lowered.endswith(CODE_TYPES)
                                          or (is_dir and os.path.isdir(
                                              os.path.join(path, ".git")))):
                points += 35
            if last and last in folded:
                points += 20
        return points

    ranked = sorted(enumerate(places_), key=lambda pair: (-score(pair[1]), pair[0]))
    return [place for _index, place in ranked]


def _head(path: str, limit: int = 2048) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""


# ---------------------------------------------------------------- Notedown ---

def is_markdown(path: str) -> bool:
    return path.lower().endswith(MARKDOWN)


def notedown_entry() -> str:
    """Notedown's launcher, when it is in the same Apps checkout as this."""
    apps = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    entry = os.path.join(apps, "Notedown", "notedown.py")
    return entry if os.path.isfile(entry) else ""


def system_default_for(path: str):
    """The app the desktop opens this kind of file with, or None."""
    from gi.repository import Gio

    guess, _certain = Gio.content_type_guess(path, None)
    if not guess:
        return None
    try:
        return Gio.AppInfo.get_default_for_type(guess, False)
    except Exception:                                  # noqa: BLE001
        return None


def wants_notedown(path: str, default_for=None) -> bool:
    """Should opening this file mean Notedown?

    Only for Markdown, only when Notedown is in the checkout, and only when
    the desktop has no answer of its own — a person who chose an editor for
    Markdown has chosen, and this is not the place to overrule them.
    """
    if not is_markdown(path) or not notedown_entry():
        return False
    return (default_for or system_default_for)(path) is None


def notedown_argv(path: str) -> list[str]:
    exe = sys.executable
    if winenv.WINDOWS and exe.lower().endswith("python.exe"):
        exe = exe[:-len("python.exe")] + "pythonw.exe"   # no console behind it
    return [exe, notedown_entry(), path]


def open_in_notedown(path: str) -> tuple[bool, str]:
    if not notedown_entry():
        return False, "Notedown is not in this checkout"
    try:
        subprocess.Popen(notedown_argv(path), cwd=os.path.dirname(path) or None,
                         creationflags=winenv.NO_WINDOW,
                         start_new_session=not winenv.WINDOWS)
    except OSError as exc:
        return False, exc.strerror or "Notedown would not start"
    return True, ""


# ---------------------------------------------------------------- the brain ---

_refreshed = 0.0
_refresh_lock = threading.Lock()


def refresh_notes(max_age: float = 60.0) -> None:
    """Bring the shared note index up to date, unless it was just done.

    A walk of the note roots: cheap after the first time, but never cheap
    enough for the GTK thread. Call it from a worker.
    """
    global _refreshed
    with _refresh_lock:
        if time.monotonic() - _refreshed < max_age and _refreshed:
            return
        try:
            brain.Notes.shared().refresh()
        except Exception:                              # noqa: BLE001
            return
        _refreshed = time.monotonic()


def warm_up() -> None:
    """Refresh the note index on a thread of its own, if there is a brain."""
    if here() is not None:
        threading.Thread(target=refresh_notes, daemon=True,
                         name="hermione-notes").start()


def search(text: str, limit: int = 40) -> list[brain.Hit]:
    try:
        return brain.search(text, limit=limit)
    except Exception:                                  # noqa: BLE001
        # A brain.db being rebuilt by Anamnesis mid-query is not worth a
        # traceback in a palette; it is worth an empty answer.
        return []


@dataclass(frozen=True)
class NoteFacts:
    title: str
    node_id: str | None
    kind: str | None


def note_facts(path: str, where: brain.Places | None = None) -> NoteFacts | None:
    """What the brain calls this file, if it is one of its notes.

    A note is Markdown under one of the roots the Order looks for notes in
    (`brain.note_roots`); a Markdown file in Downloads is just a file.
    """
    if not is_markdown(path):
        return None
    where = where or brain.places()
    if not where.exists():
        return None
    if not any(ops.is_inside(path, str(root)) for root in brain.note_roots(where)):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read(256 * 1024)
    except OSError:
        return None
    meta = brain.split_front_matter(text)[0]
    node_id = str(meta["id"]) if meta.get("id") else None
    kind = str(meta["kind"]) if meta.get("kind") else None
    return NoteFacts(brain.title_of(text, Path(path)), node_id, kind)


def backlinks(path: str) -> list[brain.Backlink]:
    """The notes that link to this one. Reads the disk: call from a worker."""
    refresh_notes()
    notes = brain.Notes.shared()
    try:
        notes.update(Path(path))
        return notes.backlinks(path)
    except Exception:                                  # noqa: BLE001
        return []


# ------------------------------------------------------------------ Claude ---

# Replaced by the suite with a stand-in for the `claude` command; None means
# whatever `claude.find()` turns up.
CLAUDE_COMMAND: list[str] | None = None


def claude_available() -> bool:
    return CLAUDE_COMMAND is not None or claude.available()


def ask(prompt: str, *, files=(), cwd=None, on_text=None, on_done=None) -> claude.Ask:
    """Start a read-only request to Claude Code; see `claude.Ask`."""
    return claude.Ask(prompt, files=files, cwd=cwd, on_text=on_text,
                      on_done=on_done, command=CLAUDE_COMMAND).start()


def placement_prompt(items: list[str], places_: list[Destination],
                     where: brain.Places | None = None) -> str:
    """What Claude is asked when it is asked where the inbox should go."""
    where = where or brain.places()
    listed = "\n".join(f"- {p.label}: {where.relative(p.path)}" for p in places_)
    things = "\n".join(f"- {os.path.basename(i)}" for i in items)
    return (
        "These are waiting in my inbox. Following SYSTEM.md §2 (\"What goes where\"), "
        "say where each one belongs. Read a file when its name is not enough.\n\n"
        f"The places that exist:\n{listed}\n\n"
        f"The items (in {where.relative(where.inbox)}):\n{things}\n\n"
        "Answer with only a JSON array, one object per item: "
        '{"item": "<file name exactly as listed>", "place": "<one of the paths '
        'above, or \\"inbox\\" if it should stay to become a node>", '
        '"why": "<a few words>"}. Change nothing on disk.')


def parse_placements(text: str, items: list[str], places_: list[Destination],
                     where: brain.Places | None = None) -> dict[str, tuple[Destination | None, str]]:
    """Claude's answer, as {item path: (where it should go, why)}.

    Only places from the list are taken: an answer naming a folder that is
    not there, or not one of the Order's, is shown as advice and never
    becomes a move. "inbox" (leave it to become a node) maps to None.
    """
    where = where or brain.places()
    by_rel = {where.relative(p.path).casefold(): p for p in places_}
    by_label = {p.label.casefold(): p for p in places_}
    by_name = {os.path.basename(i): i for i in items}
    found: dict[str, tuple[Destination | None, str]] = {}
    for entry in _json_objects(text):
        item = by_name.get(str(entry.get("item", "")).strip())
        if item is None:
            continue
        place = str(entry.get("place", "")).strip().replace("\\", "/").strip("/")
        why = str(entry.get("why", "")).strip()
        if place.casefold() in ("inbox", where.relative(where.inbox).casefold()):
            found[item] = (None, why or "leave it to become a node")
            continue
        if place.startswith("~/"):
            place = place[2:]
        chosen = by_rel.get(place.casefold()) or by_label.get(place.casefold())
        if chosen is not None:
            found[item] = (chosen, why)
        elif place:
            found[item] = (None, f"{place} — {why}" if why else place)
    return found


def _json_objects(text: str) -> list[dict]:
    """The objects in a reply that may have prose or a code fence around them."""
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start:end + 1])
            if isinstance(value, list):
                return [v for v in value if isinstance(v, dict)]
        except ValueError:
            pass
    objects = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if line.startswith("{") and line.endswith("}"):
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                objects.append(value)
    return objects
