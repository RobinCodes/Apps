"""The Order, as the writing surface sees it — everything about it that is not a widget.

`brain.py` knows the layout, the index and the inbox, and `cards.py` knows what
a card is; both are the house's, vendored byte for byte. What is here is the
part that is Notedown's own: which file a link in *this* editor means, what to
type when a link is completed, where a note made from a link goes, whether
Mnemosyne is beside us, and what in an answer from Claude is a card.

None of it needs a display, so all of it is tested without one.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from . import brain, cards, markdown

NOTE_EXTENSIONS = (".md", ".markdown")

# The Apps checkout this copy of Notedown runs from: Mnemosyne, when it is
# installed, is a sibling of Notedown in it.
CHECKOUT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ------------------------------------------------------------------ links ----

def index_ready(notes: brain.Notes) -> bool:
    """Whether the index can be read now, without waiting for it to be built.

    On the first run of all, or after its layout changes, the index has no
    tables until the start-up refresh has made them, and a read on the main
    loop would wait behind that refresh — for the whole of it, on a brain
    being read for the first time. So it is asked, read-only, first.
    """
    try:
        con = sqlite3.connect(f"file:{Path(notes.db_path).as_posix()}?mode=ro",
                              uri=True, timeout=0.5)
        try:
            version = con.execute("PRAGMA user_version").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return False
    return version == brain.SCHEMA_VERSION


def resolve(notes: brain.Notes | None, target: str, near: str | None = None) -> str:
    """The file `[[target]]` means from the note at `near`, or "" for none yet.

    The index first, because it knows titles and aliases. Then a file of that
    name beside the linking note, which is what a link means in a folder of
    notes the Order does not index — a project's docs, a stick — and what a
    note just made from a link is until the index has read it. `notes` is
    None while the index is not there to ask.
    """
    try:
        found = notes.resolve(target, near=near) if notes is not None else None
    except sqlite3.Error:
        found = None
    if found is not None and Path(found).is_file():
        return str(found)
    if near:
        folder = os.path.dirname(near)
        stem = target.strip().replace("\\", "/")
        for extension in ("",) + NOTE_EXTENSIONS:
            candidate = os.path.normpath(os.path.join(folder, stem + extension))
            if candidate.lower().endswith(NOTE_EXTENSIONS) and os.path.isfile(candidate):
                return candidate
    return ""


def linkable(name: str) -> bool:
    """Whether `name` can stand between `[[` and `]]` as it is."""
    return bool(name.strip()) and not any(c in name for c in "[]|#^\n")


def link_text(title: str, path: str, near: str | None, resolve_from) -> str:
    """What to type for a link to the note at `path`, from the note at `near`.

    Its title when the title is enough — when following it would arrive at
    this note and not at another of the same name — and otherwise the way
    there from here, with the title as its label, so it still reads as one.
    """
    name = title if linkable(title) else os.path.splitext(os.path.basename(path))[0]
    if not near or same_file(resolve_from(name, near), path):
        return name
    try:
        way = os.path.relpath(os.path.splitext(path)[0], os.path.dirname(near))
    except ValueError:                   # another drive, on Windows
        return name
    return f"{way.replace(os.sep, '/')}|{name}"


def same_file(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def new_note_path(folder: str, target: str) -> str:
    """Where the note `[[target]]` asks for is made: beside the linking one.

    A target with slashes in it is a way there, and is followed — each step
    made a name both Windows and Linux will take.
    """
    parts = [brain.safe_name(part) for part in target.replace("\\", "/").split("/")
             if part.strip() and part.strip() not in (".", "..")]
    name = parts[-1] if parts else "Untitled"
    if not name.lower().endswith(NOTE_EXTENSIONS):
        name += ".md"
    return os.path.join(folder, *parts[:-1], name)


def unwritten(document) -> list[str]:
    """The targets this note links to that no note answers to, each once."""
    seen, out = set(), []
    for node in markdown.wiki_links(document) if document is not None else []:
        if not node.url and brain.key(node.text) not in seen:
            seen.add(brain.key(node.text))
            out.append(node.text)
    return out


# ----------------------------------------------------------------- titles ----

_titles: dict[tuple[str, float], str] = {}


def title_for(path: str) -> str:
    """A note's title, from the top of the file, remembered until it changes."""
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return os.path.splitext(os.path.basename(path))[0]
    if (path, stamp) not in _titles:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                head = handle.read(4096)
        except OSError:
            head = ""
        _titles[(path, stamp)] = brain.title_of(head, Path(path))
    return _titles[(path, stamp)]


def folder_of(path: str) -> str:
    """Where a note is, the way the Order says it: home-relative."""
    return os.path.dirname(brain.places().relative(path)) or "~"


# -------------------------------------------------------------- Mnemosyne ----

def mnemosyne(checkout: str | None = None) -> str | None:
    """Mnemosyne's script, when it is installed beside Notedown; else None."""
    script = os.path.join(checkout or CHECKOUT, "Mnemosyne", "mnemosyne.py")
    return script if os.path.isfile(script) else None


def study_command(path: str, script: str, python: str) -> list[str]:
    """`mnemosyne --study <note>`, on the interpreter Notedown itself runs on."""
    return [python, script, "--study", os.path.abspath(path)]


# ------------------------------------------------------- cards from Claude ----

CARDS_PROMPT = (
    "Read this note and propose flashcards for what in it is worth remembering, "
    "in the card format of SYSTEM.md §7: `Front :: Back` for one fact one way, "
    "`Front ::: Back` for a fact worth asking both ways, a `Q:` line and an `A:` "
    "line for a longer answer, and `{{gap}}` in a sentence for a cloze. Write "
    "them in the note's own language. Leave out anything the note already has a "
    "card for. Reply with the cards only — no headings, no commentary, no code "
    "fences — one card to a line, or a Q:/A: pair followed by a blank line. Do "
    "not edit any file."
)

_FENCED = re.compile(r"^\s*(`{3,}|~{3,})[^\n]*\n(.*?)^\s*\1\s*$", re.M | re.S)


def proposed_cards(reply: str, existing: str = "") -> tuple[str, int]:
    """The part of Claude's reply that is cards, less the ones the note has.

    Read with `cards.py`, like everything else here: a line is kept when the
    scheduler would make a card of it, so the text that is added is exactly
    the cards that are counted, and an aside like "Here are some cards:" is
    dropped. Returns the text to add and how many cards it makes.
    """
    fenced = _FENCED.findall(reply or "")
    body = "\n\n".join(inner for _, inner in fenced) if fenced else (reply or "")
    lines = body.replace("\r\n", "\n").split("\n")
    have = {cards.normalise(card.front).casefold()
            for card in cards.parse_text(existing, "note.md")}

    found = cards.parse_text(body, "note.md")
    starts = {card.line - 1 for card in found}
    kept: list[str] = []
    taken: set[int] = set()
    for card in found:
        start = card.line - 1
        # The two directions of a `:::` share a line, and so do the gaps of
        # one sentence; each line is added once.
        if start in taken or cards.normalise(card.front).casefold() in have:
            continue
        taken.add(start)
        if card.form in ("line", "both"):
            kept.append(lines[start].rstrip())
            continue
        if card.form == "qa":
            end = start + card.front.count("\n") + card.back.count("\n") + 1
        else:
            # A sentence with gaps runs to the blank line, or to the next
            # line that is a card of its own.
            end = start
            while end + 1 < len(lines) and lines[end + 1].strip() \
                    and end + 1 not in starts:
                end += 1
        unit = "\n".join(line.rstrip() for line in lines[start:end + 1])
        if kept and kept[-1] != "":
            kept.append("")
        kept.extend([unit, ""])
    while kept and not kept[-1]:
        kept.pop()
    text = "\n".join(kept)
    # Counted again from the text that will be written: the number on the
    # button is the number Mnemosyne will find.
    return text, len(cards.parse_text(text, "note.md")) if text else 0
