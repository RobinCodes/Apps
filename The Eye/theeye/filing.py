"""Where a page goes, and what it is written into.

Two destinations, the two the Order has for a page of class notes
(SYSTEM.md §2, §4):

* **A subject.** The page image goes to `Studies/<Subject>/pages/` as
  `YYYY-MM-DD-<n>.jpg` — the lesson's day and the next free number — and the
  page goes into that day's note, `Studies/<Subject>/YYYY-MM-DD.md`, under a
  `## Pages` heading: the image, a comment saying where it came from, and its
  text. The note is made if it is not there yet. If the day already has a
  note in the Hungarian form, `2026.10.27.md`, that is the note — existing
  names are not changed (§2, Names).
* **The inbox.** The image is captured with `brain.capture_file`, and a note
  beside it with `brain.capture_text`, carrying the provenance in its front
  matter: the photograph, when it was taken, the page it says it is, and who
  read it. Filing it into a subject is then the daily pass's job (§4.2).

The text arrives later than the image — Claude takes a while over a page —
so a page is written in two steps. `file_page` puts the image and its entry
in place at once, so that nothing is lost if the app is closed meanwhile, and
`add_text` puts the words under the image when they come.

How the words are written depends on who read them. Claude's transcription
is Markdown and goes in as it is, its headings held below `##` so a page can
never end the section it is in. Tesseract's is rough text with no structure,
and it goes in a fenced block: until it is rewritten (§6, "within 3 days")
nothing in it should be a card, and a `::` misread out of handwriting would
otherwise go straight into the schedule.

The originals are never touched: a page is always a new file made from them.

Palaces are here too (§8): a palace is a note in `learning/palaces/` with
`kind: palace`, its loci an ordered list of cards, and a photograph of the
real place sits under its locus as an indented `![](…)`. The photograph is
copied into a folder named after the palace beside it — as a JPEG of at most
1600 pixels, because it syncs through git like everything else in the brain.
"""

from __future__ import annotations

import datetime
import os
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import brain, reading
from .cards import split_front_matter

INBOX = "Inbox"
PAGES = "pages"
HEADING = "## Pages"


def _where(where=None) -> brain.Places:
    return where or brain.places()


def destination(relative: str) -> str:
    """A relative path as a Markdown link destination.

    In angle brackets when it has a space or a bracket in it — CommonMark's
    own way to write one, which Notedown reads — and bare otherwise, which
    is every page image and most loci.
    """
    return f"<{relative}>" if re.search(r"[\s()<>]", relative) else relative


# -------------------------------------------------------------- subjects ----

def subjects(where=None) -> list[str]:
    """The inbox, then every subject folder under Studies, by name."""
    studies = _where(where).studies
    found = []
    if studies.is_dir():
        found = sorted((entry.name for entry in studies.iterdir()
                        if entry.is_dir() and not entry.name.startswith((".", "_"))),
                       key=lambda name: brain.key(name))
    return [INBOX] + found


def day_note(subject: str, day: datetime.date, where=None) -> Path:
    """`Studies/<Subject>/YYYY-MM-DD.md`, or the day's note already there."""
    folder = _where(where).studies / subject
    iso = folder / f"{day:%Y-%m-%d}.md"
    if iso.exists():
        return iso
    for name in (f"{day:%Y.%m.%d}..md", f"{day:%Y.%m.%d}.md"):
        if (folder / name).exists():
            return folder / name
    return iso


def next_image(folder: Path, day: datetime.date) -> Path:
    """`YYYY-MM-DD-<n>.jpg` in `folder`, with the next number free that day."""
    taken = [0]
    if folder.is_dir():
        pattern = re.compile(rf"^{day:%Y-%m-%d}-(\d+)\.jpe?g$", re.I)
        for entry in folder.iterdir():
            m = pattern.match(entry.name)
            if m:
                taken.append(int(m.group(1)))
    return folder / f"{day:%Y-%m-%d}-{max(taken) + 1}.jpg"


# ------------------------------------------------------------- the entry ----

@dataclass
class Filed:
    note: Path          # the note the page is in
    image: Path         # the page image
    link: str           # the image as the note links to it
    subject: str


def provenance(source: str | Path | None, taken: datetime.datetime | None,
               reader: str, label: str = "") -> str:
    """The comment under a page's image: where it came from, who read it."""
    bits = []
    if label:
        bits.append(label)
    if source:
        bits.append(f"from {brain.places().relative(source)}")
    if taken:
        bits.append(f"taken {taken:%Y-%m-%d %H:%M}")
    bits.append(f"read by {reading.NAMES.get(reader, reader)}")
    # Two hyphens would end the comment early.
    return "<!-- " + " · ".join(bits).replace("--", "‐‐") + " -->"


def format_text(text: str, reader: str) -> str:
    """The words of a page, as the note should hold them."""
    text = (text or "").strip()
    if not text:
        return ""
    if reader == "claude":
        return reading.tidy_markdown(text)
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}text\n{text}\n{fence}"


def _write(path: Path, text: str) -> None:
    """A note written whole, or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def new_note(subject: str, day: datetime.date, topic: str = "") -> str:
    title = f"{topic} · {day:%Y-%m-%d}" if topic else f"{subject} · {day:%Y-%m-%d}"
    return f"# {title}\n\n{HEADING}\n"


def add_page(note: Path, link: str, comment: str, body: str = "",
             fresh: str = "") -> None:
    """Put one page at the end of a note's `## Pages`, making either if needed.

    The section ends at the next heading of level one or two, so a page is
    added after the pages already there and before whatever was written below
    them since.
    """
    text = _read(note) or fresh or f"{HEADING}\n"
    entry = f"![]({link})\n{comment}\n" + (f"\n{body}\n" if body else "")
    lines = text.split("\n")
    start = next((i for i, line in enumerate(lines)
                  if line.strip().lower() == HEADING.lower()), None)
    if start is None:
        text = text.rstrip("\n") + f"\n\n{HEADING}\n\n{entry}"
    else:
        end = len(lines)
        fence = False
        for i in range(start + 1, len(lines)):
            if lines[i].lstrip().startswith(("```", "~~~")):
                fence = not fence
            if not fence and re.match(r"^#{1,2}\s", lines[i]):
                end = i
                break
        before = "\n".join(lines[:end]).rstrip("\n")
        after = "\n".join(lines[end:]).strip("\n")
        text = before + "\n\n" + entry + ("\n" + after + "\n" if after else "")
    _write(note, text)


def add_text(note: Path, link: str, body: str) -> bool:
    """Put a page's words under its image, once they have been read.

    The image line is found by its link, which is unique, and the words go
    after the comment beneath it. False if the image is no longer in the
    note — it was moved or rewritten meanwhile — in which case nothing is
    changed.
    """
    if not body:
        return True
    lines = _read(note).split("\n")
    target = f"![]({link})"
    for i, line in enumerate(lines):
        if line.strip() == target:
            at = i + 1
            if at < len(lines) and lines[at].lstrip().startswith("<!--"):
                at += 1
            lines[at:at] = ["", *body.split("\n")]
            _write(note, "\n".join(lines))
            return True
    return False


def update_meta(note: Path, values: dict) -> bool:
    """Set front-matter fields of a note, leaving every other line alone.

    For the inbox note of a page, once its header line has been read: the
    lesson's day, its topic and its page number were not known when the
    note was made. Empty values change nothing.
    """
    text = _read(note)
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---", 3)
    if end < 0:
        return False
    lines = text[4:end].split("\n")
    for key, value in values.items():
        line = brain._yaml_line(key, value).rstrip("\n")
        if not line:
            continue
        at = next((i for i, existing in enumerate(lines)
                   if existing.split(":", 1)[0].strip() == key), None)
        if at is None:
            lines.append(line)
        else:
            lines[at] = line
    _write(note, "---\n" + "\n".join(lines) + text[end:])
    return True


def file_page(jpeg: bytes, subject: str, day: datetime.date, *, source=None,
              taken: datetime.datetime | None = None, reader: str = "none",
              header: reading.Header | None = None, text: str = "",
              where=None) -> Filed:
    """Put a straightened page in its place. Blocking; quick.

    `text` is the page's words when they are already known; otherwise they
    are added later with `add_text`.
    """
    p = _where(where)
    header = header or reading.Header()
    label = header.label()
    body = format_text(text, reader)
    comment = provenance(source, taken, reader, label)
    if subject == INBOX:
        return _to_inbox(jpeg, day, source, taken, reader, header, body, comment, p)
    folder = p.studies / subject
    image = next_image(folder / PAGES, day)
    image.parent.mkdir(parents=True, exist_ok=True)
    _write_bytes(image, jpeg)
    note = day_note(subject, day, p)
    link = destination(f"{PAGES}/{image.name}")
    add_page(note, link, comment, body, new_note(subject, day, header.topic))
    return Filed(note, image, link, subject)


def _write_bytes(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _to_inbox(jpeg, day, source, taken, reader, header, body, comment, p) -> Filed:
    scratch = Path(tempfile.mkdtemp(prefix="the-eye-page-"))
    try:
        loose = next_image(p.inbox, day)
        staged = scratch / loose.name
        staged.write_bytes(jpeg)
        image = brain.capture_file(staged, move=True, origin="the-eye", where=p)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    title = "Notebook page" + (f" · {header.label()}" if header.label()
                               else f", {day:%Y-%m-%d}")
    meta = {"kind": "notebook-page", "lesson": f"{day:%Y-%m-%d}",
            "topic": header.topic, "page": header.page,
            "image": image.name, "reader": reader,
            "source": brain.places().relative(source) if source else "",
            "taken": taken.isoformat(timespec="seconds") if taken else ""}
    link = destination(image.name)
    entry = f"![]({link})\n{comment}\n" + (f"\n{body}\n" if body else "")
    note = brain.capture_text(entry, title, origin="the-eye", app="The Eye",
                              where=p, meta=meta)
    return Filed(note, image, link, INBOX)


# --------------------------------------------------------------- palaces ----

@dataclass(frozen=True)
class Palace:
    path: Path
    title: str


@dataclass(frozen=True)
class Locus:
    number: int
    name: str
    item: str
    line: int           # where it is in the note, counting from 0


def palace_folder(where=None) -> Path:
    return _where(where).learning / "palaces"


def palaces(where=None) -> list[Palace]:
    """Every note in `learning/palaces/` that says `kind: palace`."""
    folder = palace_folder(where)
    found = []
    if folder.is_dir():
        for path in sorted(folder.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, _body, _lines = split_front_matter(text)
            if str(meta.get("kind", "")).strip().lower() == "palace":
                found.append(Palace(path, brain.title_of(text, path)))
    return sorted(found, key=lambda palace: brain.key(palace.title))


def create_palace(title: str, where=None) -> Path:
    """A new, empty palace note, named after its title."""
    title = " ".join(title.split()) or "Palace"
    folder = palace_folder(where)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{brain.safe_name(title)}.md"
    n = 2
    while path.exists():
        path = folder / f"{brain.safe_name(title)} ({n}).md"
        n += 1
    _write(path, f"---\nkind: palace\ntitle: {brain._yaml_str(title)}\n---\n\n")
    return path


_LOCUS = re.compile(r"^(\d+)[.)]\s+(.*?)\s*$")


def loci(path: Path) -> list[Locus]:
    """A palace's loci, in order: its numbered list items outside code."""
    found = []
    fence = False
    for index, line in enumerate(_read(path).split("\n")):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        m = None if fence else _LOCUS.match(line)
        if not m:
            continue
        name, _sep, item = m.group(2).partition("::")
        found.append(Locus(int(m.group(1)), name.strip(), item.strip(), index))
    return found


def slug(text: str, limit: int = 40) -> str:
    """`Bejárati ajtó` as `bejarati-ajto`: what a locus is called on disk."""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = re.sub(r"[^a-z0-9]+", "-", folded.casefold()).strip("-")
    return folded[:limit].strip("-") or "locus"


def link_locus(palace: Path, locus: str, photo: str | Path, item: str = "",
               copy=None) -> Path:
    """Put a photograph under a locus of a palace; the locus is added if new.

    `locus` is a locus's name as the palace has it, or a new one, which goes
    at the end of the walk — as `n. name :: item` if an item was given and as
    `n. name` otherwise, which is not a card until something is put there.
    The photograph is copied by `copy(source, destination)`, a compact JPEG
    by default, into `<palace>/NN-<locus>.jpg` beside the note. Returns the
    copy.
    """
    palace = Path(palace)
    text = _read(palace)
    lines = text.split("\n")
    known = loci(palace)
    match = next((l for l in known if brain.key(l.name) == brain.key(locus)), None)
    if match is None:
        number = (max(l.number for l in known) + 1) if known else 1
        entry = f"{number}. {locus.strip()}" + (f" :: {item.strip()}" if item.strip() else "")
        if known:
            at = _end_of_item(lines, known[-1].line)
            lines[at:at] = [entry]
        else:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += ["", entry, ""] if lines else [entry, ""]
        _write(palace, "\n".join(lines))
        known = loci(palace)
        match = next(l for l in known if brain.key(l.name) == brain.key(locus))

    lines = _read(palace).split("\n")
    folder = palace.parent / palace.stem
    folder.mkdir(parents=True, exist_ok=True)
    copied = folder / f"{match.number:02d}-{slug(match.name)}.jpg"
    n = 2
    while copied.exists():
        copied = folder / f"{match.number:02d}-{slug(match.name)}-{n}.jpg"
        n += 1
    (copy or compact_copy)(Path(photo), copied)

    marker = re.match(r"^(\d+[.)]\s+)", lines[match.line])
    indent = " " * (len(marker.group(1)) if marker else 3)
    at = _end_of_item(lines, match.line)
    lines[at:at] = [f"{indent}![]({destination(palace.stem + '/' + copied.name)})"]
    _write(palace, "\n".join(lines))
    return copied


def _end_of_item(lines: list[str], start: int) -> int:
    """The line after a list item and everything indented under it."""
    at = start + 1
    while at < len(lines) and lines[at].startswith((" ", "\t")) and lines[at].strip():
        at += 1
    return at


def compact_copy(source: Path, destination: Path, longest: int = 1600,
                 quality: int = 85) -> None:
    """A photograph as a JPEG no larger than `longest`, the right way up."""
    from . import paper

    pixbuf = paper.load(str(source), longest)
    ok_types = (".jpg", ".jpeg")
    if (source.suffix.lower() in ok_types and max(pixbuf.get_width(), pixbuf.get_height())
            < longest and source.stat().st_size < 600_000):
        shutil.copy2(source, destination)
        return
    if pixbuf.get_has_alpha():
        from . import export

        pixbuf = export._flatten(pixbuf)
    result = pixbuf.save_to_bufferv("jpeg", ["quality"], [str(quality)])
    _write_bytes(destination, bytes(result[1] if isinstance(result, tuple) else result))
