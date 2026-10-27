"""Cards, written where the knowledge lives — and the log of every answer given to them.

The format is the Order's (SYSTEM.md §7). A card is not made in a card editor;
it is a line in a note that already exists:

    Wilson-tétel :: (p−1)! ≡ −1 (mod p)            one card, front → back
    il gesso ::: kréta                              two cards, both directions
    Q: Miért kevés a gyökérnyomás?                  one card over several lines,
    A: Mert csak néhány méterre emel…               ending at a blank line
    A {{mitokondrium}} termeli az ATP-t.            one card per {{…}}
    \\begin{definition}[Gráf] … \\end{definition}     one card, in a .tex file

so writing the note *is* making the cards, and there is no separate step for a
tired evening to skip.

Three things live here, used by every app that schedules or shows cards:

* `parse_file` / `parse_text` — a note in, its cards out, each with a stable id;
* `ReviewLog` — the append-only answers, one JSON-lines file per machine,
  union-merged by git;
* `Deck` — cards plus log plus `fsrs`: what is due, what is new, what to show
  next, and the answer written back.

Standard library only.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import socket
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import fsrs

# ------------------------------------------------------------------ cards ---


@dataclass(frozen=True)
class Card:
    id: str
    form: str                    # line · both · both-r · qa · cloze · tex
    front: str
    back: str
    path: str                    # home-relative, forward slashes
    line: int                    # 1-based line of the card in its file
    deck: str
    context: tuple[str, ...] = ()        # the headings above it
    palace: str | None = None            # the palace's title, for a locus
    order: int | None = None             # the locus's position in the walk

    @property
    def reversed(self) -> bool:
        return self.form == "both-r"


def normalise(text: str) -> str:
    return " ".join(text.split())


def back_hash(back: str) -> str:
    return hashlib.sha1(normalise(back).encode("utf-8")).hexdigest()[:8]


def card_id(path: str, front: str, form: str, ordinal: int) -> str:
    raw = "\x1f".join((path, normalise(front), form, str(ordinal)))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# Front matter: the YAML block at the top of a note. Only a handful of keys
# matter to cards (`cards`, `deck`, `kind`, `title`), so a full YAML parser is
# used when one is installed and a forgiving key: value reader otherwise.
def split_front_matter(text: str) -> tuple[dict, str, int]:
    """`(front matter, body, number of lines the front matter took)`."""
    if not text.startswith("---"):
        return {}, text, 0
    end = re.search(r"^---\s*$", text[3:], re.M)
    if not end:
        return {}, text, 0
    block = text[3:3 + end.start()]
    body = text[3 + end.end():]
    if body.startswith("\n"):
        body = body[1:]
    consumed = text[:len(text) - len(body)].count("\n")
    data: dict = {}
    try:
        import yaml                                     # noqa: PLC0415
        loaded = yaml.safe_load(block)
        if isinstance(loaded, dict):
            data = loaded
    except Exception:                                   # noqa: BLE001
        data = _simple_yaml(block)
    return data, body, consumed


def _scalar(value: str):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return {"true": True, "false": False, "null": None, "~": None}.get(value.lower(), value)


def _simple_yaml(block: str) -> dict:
    """The part of YAML front matter actually written by hand, without PyYAML.

    `key: value`, `key: [a, b]`, and a key followed by `- item` lines. Anything
    cleverer is left as text; nothing here raises.
    """
    data: dict = {}
    current = None
    for line in block.splitlines():
        item = re.match(r"^\s+-\s+(.*)$", line) or re.match(r"^-\s+(.*)$", line)
        if item and current is not None:
            if not isinstance(data.get(current), list):
                data[current] = []
            data[current].append(_scalar(item.group(1)))
            continue
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        current, value = m.group(1), m.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            data[current] = [_scalar(v) for v in value[1:-1].split(",") if v.strip()]
        elif value:
            data[current] = _scalar(value)
        else:
            data[current] = None
    return data


_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST_MARK = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?")
_NUMBERED = re.compile(r"^\s*(\d{1,3})[.)]\s+")
_QA_Q = re.compile(r"^\s*(?:[-*+]\s+)?Q:\s?(.*)$")
_QA_A = re.compile(r"^\s*A:\s?(.*)$")
_CLOZE = re.compile(r"\{\{(.+?)\}\}")


def _mask(line: str) -> str:
    """The line with code spans and inline maths blanked out, same length.

    Separators and clozes are looked for in the mask, then cut from the real
    line at the same offsets — so `a::b` inside backticks or `$x::y$` is never
    mistaken for a card, and nothing is lost from the text that is.
    """
    out = list(line)

    def blank(start, end):
        for i in range(start, end):
            out[i] = "\0"

    for m in re.finditer(r"(`+)(.+?)\1", line):
        blank(m.start(), m.end())
    masked = "".join(out)
    for m in re.finditer(r"(?<!\\)\$(?!\s)([^$\n]+?)(?<![\s\\])\$", masked):
        blank(m.start(), m.end())
    return "".join(out)


def _separator(line: str) -> tuple[int, int, str] | None:
    """Where ` :: ` or ` ::: ` splits the line, outside code and maths."""
    masked = _mask(line)
    m = re.search(r"(?<=\S)\s+(:{2,3})\s+(?=\S)", masked)
    if not m:
        return None
    return m.start(), m.end(), "both" if len(m.group(1)) == 3 else "line"


def _clean(text: str) -> str:
    return _LIST_MARK.sub("", text, count=1).strip()


def deck_for(path: str, meta: dict) -> str:
    """The deck a note's cards belong to, unless its front matter names one.

    The folder path, read the way it would be said: `Projects/Studies/Biology`
    is *Studies/Biology*, and a file in a `decks` folder is a deck of its own.
    """
    if meta.get("deck"):
        return str(meta["deck"])
    parts = path.split("/")
    stem = os.path.splitext(parts[-1])[0]
    folders = parts[:-1]
    if folders and folders[-1].lower() == "decks":
        return stem
    for prefix in (["SecondBrain", "learning"], ["Projects"], ["SecondBrain"]):
        if folders[:len(prefix)] == prefix and len(folders) > len(prefix):
            folders = folders[len(prefix):]
            break
    return "/".join(folders) or stem


def parse_text(text: str, path: str) -> list[Card]:
    """Every card in one note. `path` is home-relative, with forward slashes."""
    if path.lower().endswith((".tex", ".ltx", ".latex")):
        return _parse_tex(text, path)
    meta, body, offset = split_front_matter(text)
    if meta.get("cards") is False or str(meta.get("cards", "")).lower() == "false":
        return []
    deck = deck_for(path, meta)
    palace = (str(meta.get("title") or os.path.splitext(path.rsplit("/", 1)[-1])[0])
              if str(meta.get("kind", "")).lower() == "palace" else None)

    cards: list[Card] = []
    seen: dict[tuple[str, str], int] = {}
    headings: list[tuple[int, str]] = []
    lines = body.split("\n")
    fence = None
    in_math = False
    in_comment = False
    paragraph: list[tuple[int, str]] = []

    def context():
        return tuple(title for _, title in headings)

    def emit(form, front, back, lineno, order=None):
        key = (form, normalise(front))
        ordinal = seen.get(key, 0)
        seen[key] = ordinal + 1
        cards.append(Card(
            id=card_id(path, front, form, ordinal), form=form,
            front=front.strip(), back=back.strip(), path=path,
            line=lineno + offset + 1, deck=deck, context=context(),
            palace=palace, order=order))

    def flush_paragraph():
        if not paragraph:
            return
        joined = "\n".join(text for _, text in paragraph)
        masked = "\n".join(_mask(text) for _, text in paragraph)
        spans = [(m.start(), m.end()) for m in _CLOZE.finditer(masked)]
        for index, (start, end) in enumerate(spans):
            front, back = [], []
            cursor = 0
            for other, (s2, e2) in enumerate(spans):
                inner = joined[s2 + 2:e2 - 2]
                answer, _, hint = inner.partition("|")
                front.append(joined[cursor:s2])
                back.append(joined[cursor:s2])
                if other == index:
                    front.append(f"[{hint.strip()}]" if hint.strip() else "[…]")
                    back.append(f"**{answer.strip()}**")
                else:
                    front.append(answer)
                    back.append(answer)
                cursor = e2
            front.append(joined[cursor:])
            back.append(joined[cursor:])
            emit("cloze", _clean("".join(front)), _clean("".join(back)),
                 paragraph[0][0])
        paragraph.clear()

    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        # Blocks no card is ever read out of.
        if in_comment:
            if "-->" in raw:
                in_comment = False
            i += 1
            continue
        if fence:
            if re.match(rf"^\s{{0,3}}{re.escape(fence[0])}{{{len(fence)},}}\s*$", raw):
                fence = None
            i += 1
            continue
        m = _FENCE.match(raw)
        if m:
            flush_paragraph()
            fence = m.group(1)
            i += 1
            continue
        if stripped.startswith("$$"):
            flush_paragraph()
            if not (stripped.endswith("$$") and len(stripped) > 2):
                in_math = not in_math
            i += 1
            continue
        if in_math:
            i += 1
            continue
        if stripped.startswith("<!--"):
            flush_paragraph()
            in_comment = "-->" not in stripped
            i += 1
            continue

        if not stripped:
            flush_paragraph()
            i += 1
            continue

        h = _HEADING.match(raw)
        if h:
            flush_paragraph()
            level = len(h.group(1))
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, h.group(2).strip()))
            i += 1
            continue

        q = _QA_Q.match(raw)
        if q:
            flush_paragraph()
            start = i
            question = [q.group(1)]
            i += 1
            while i < len(lines) and lines[i].strip() and not _QA_A.match(lines[i]):
                question.append(lines[i].strip())
                i += 1
            if i < len(lines) and _QA_A.match(lines[i]):
                answer = [_QA_A.match(lines[i]).group(1)]
                i += 1
                while i < len(lines) and lines[i].strip():
                    answer.append(lines[i].rstrip())
                    i += 1
                emit("qa", "\n".join(question), "\n".join(answer), start)
            continue

        if stripped.startswith("|") or stripped.startswith(">"):
            # A table row or a quoted line is a unit of its own; a cloze in it
            # is asked with the rest of that row, not the whole table.
            flush_paragraph()
            paragraph.append((i, raw))
            flush_paragraph()
            i += 1
            continue

        sep = _separator(raw)
        if sep:
            flush_paragraph()
            start, end, form = sep
            front, back = _clean(raw[:start]), raw[end:].strip()
            if front and back:
                number = _NUMBERED.match(raw)
                order = int(number.group(1)) if (palace and number) else None
                emit(form, front, back, i, order)
                if form == "both":
                    emit("both-r", back, front, i, order)
            i += 1
            continue

        if _LIST_MARK.match(raw):
            # Each list item is its own unit; indented lines below it join it.
            flush_paragraph()
        paragraph.append((i, raw))
        i += 1

    flush_paragraph()
    if palace:
        # A palace walks in written order even where the list is unnumbered.
        walked, position = [], 0
        for c in cards:
            if c.form in ("line", "both"):
                position = c.order if c.order is not None else position + 1
            if c.form in ("line", "both", "both-r") and c.order is None:
                c = Card(**{**c.__dict__, "order": position})
            walked.append(c)
        cards = walked
    return cards


# LaTeX: theorem-like environments become cards. The names are the ones the
# Digitalization notes use, in both languages, plus whatever a document
# declares with \newtheorem under a label that reads as one of them.
_TEX_KINDS = {
    "definition", "definicio", "definíció", "theorem", "tetel", "tétel", "lemma",
    "corollary", "kovetkezmeny", "következmény", "proposition", "allitas",
    "állítás", "axiom", "axioma", "axióma", "law", "torveny", "törvény",
}
_TEX_LABEL = re.compile(
    r"defin|theorem|t[eé]tel|lemma|corollar|k[oö]vetkezm|proposition|[aá]ll[ií]t[aá]s|axi[oó]m|t[oö]rv[eé]ny",
    re.I)


def _strip_tex_comments(text: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", text)


def _parse_tex(text: str, path: str) -> list[Card]:
    if re.search(r"^%\s*nocards\b", text, re.M):
        return []
    clean = _strip_tex_comments(text)
    kinds = set(_TEX_KINDS)
    labels: dict[str, str] = {}
    for m in re.finditer(r"\\newtheorem\*?\{(\w+)\}(?:\[\w+\])?\{([^}]*)\}", clean):
        if _TEX_LABEL.search(m.group(1)) or _TEX_LABEL.search(m.group(2)):
            kinds.add(m.group(1))
            labels[m.group(1)] = m.group(2)
    deck = deck_for(path, {})
    cards: list[Card] = []
    seen: dict[str, int] = {}
    sections: list[tuple[int, str]] = []
    pattern = re.compile(
        r"\\(section|subsection|subsubsection)\*?\{([^}]*)\}"
        r"|\\begin\{(\w+)\*?\}(?:\[([^\]]*)\])?(.*?)\\end\{\3\*?\}", re.S)
    for m in pattern.finditer(clean):
        if m.group(1):
            level = ("section", "subsection", "subsubsection").index(m.group(1))
            while sections and sections[-1][0] >= level:
                sections.pop()
            sections.append((level, m.group(2).strip()))
            continue
        env = m.group(3)
        if env.lower() not in kinds and env not in kinds:
            continue
        title = (m.group(4) or "").strip()
        body = m.group(5).strip()
        if not body:
            continue
        label = labels.get(env, env.capitalize())
        if title:
            front = f"{label}: {title}"
        else:
            first = re.split(r"(?<=[.!?])\s", " ".join(body.split()), maxsplit=1)[0]
            front = f"{label} — {first[:90]}{'…' if len(first) > 90 else ''}"
        ordinal = seen.get(front, 0)
        seen[front] = ordinal + 1
        line = clean.count("\n", 0, m.start()) + 1
        cards.append(Card(
            id=card_id(path, front, "tex", ordinal), form="tex", front=front,
            back=body, path=path, line=line, deck=deck,
            context=tuple(t for _, t in sections)))
    return cards


def parse_file(file: Path, home: Path) -> list[Card]:
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    try:
        rel = file.resolve().relative_to(home.resolve()).as_posix()
    except ValueError:
        rel = file.as_posix()
    return parse_text(text, rel)


# ------------------------------------------------------------- the log ----


def machine_name() -> str:
    """This machine's name in the logs: the Order's role name if one is set."""
    name = os.environ.get("ORDER_MACHINE") or socket.gethostname() or "unknown"
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "unknown"


def now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def parse_time(value: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


@dataclass
class Answer:
    when: datetime
    card: str
    rating: int
    ms: int = 0
    src: str = ""
    front: str = ""
    back: str = ""          # a short hash of the back, to recognise an edited front
    via: str = "screen"
    machine: str = ""

    def to_json(self) -> str:
        record = {"t": self.when.isoformat(timespec="seconds"), "card": self.card,
                  "r": self.rating}
        if self.ms:
            record["ms"] = int(self.ms)
        if self.src:
            record["src"] = self.src
        if self.front:
            record["front"] = self.front[:200]
        if self.back:
            record["bk"] = self.back
        if self.via != "screen":
            record["via"] = self.via
        return json.dumps(record, ensure_ascii=False)


class ReviewLog:
    """`learning/reviews/*.jsonl`: every answer ever given, on any machine.

    Append-only and one file per machine, so two machines never write the same
    file and git can union-merge whatever arrives. Lines that do not parse —
    a half-written line from a crash, a conflict marker — are skipped, never
    fatal: losing one answer is better than losing the schedule.
    """

    def __init__(self, folder: Path, machine: str | None = None):
        self.folder = Path(folder)
        self.machine = machine or machine_name()

    @property
    def own(self) -> Path:
        return self.folder / f"{self.machine}.jsonl"

    def read(self) -> list[Answer]:
        answers = []
        if not self.folder.is_dir():
            return answers
        for file in sorted(self.folder.glob("*.jsonl")):
            try:
                handle = file.open(encoding="utf-8")
            except OSError:
                continue
            with handle:
                for line in handle:
                    try:
                        rec = json.loads(line)
                        when = parse_time(rec["t"])
                        rating = int(rec["r"])
                    except (ValueError, KeyError, TypeError):
                        continue
                    if when is None or rating not in fsrs.RATINGS:
                        continue
                    answers.append(Answer(
                        when=when, card=str(rec.get("card", "")), rating=rating,
                        ms=int(rec.get("ms", 0) or 0), src=rec.get("src", ""),
                        front=rec.get("front", ""), back=rec.get("bk", ""),
                        via=rec.get("via", "screen"),
                        machine=file.stem))
        answers.sort(key=lambda a: a.when)
        return answers

    def append(self, answer: Answer) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        with self.own.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(answer.to_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())


# ------------------------------------------------------------ the deck ----


@dataclass
class Item:
    """A card and what FSRS makes of it at the moment."""

    card: Card
    memory: fsrs.Memory | None
    answers: list[Answer] = field(default_factory=list)

    @property
    def new(self) -> bool:
        return self.memory is None

    def due(self, at: datetime) -> bool:
        return self.memory is not None and self.memory.due <= at


class Deck:
    """Every card under the learning roots, scheduled.

    Built from the files and the log each time, never stored: `refresh()` is a
    scan plus a replay, cheap enough to run whenever a window opens.
    """

    def __init__(self, home: Path, roots: list[str], log: ReviewLog, *,
                 retention: float = 0.9, new_per_day: int = 20,
                 extensions=("md", "tex"), w=fsrs.DEFAULT_W):
        self.home = Path(home)
        self.roots = roots
        self.log = log
        self.retention = retention
        self.new_per_day = new_per_day
        self.extensions = tuple("." + e.lstrip(".").lower() for e in extensions)
        self.w = w
        self.items: dict[str, Item] = {}

    # -- scanning ---------------------------------------------------------
    def files(self):
        skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "build",
                "dist", ".cache", "reviews"}
        for root in self.roots:
            base = self.home / root
            if base.is_file():
                yield base
                continue
            for folder, dirs, names in os.walk(base):
                dirs[:] = sorted(d for d in dirs if d not in skip and not d.startswith("."))
                for name in sorted(names):
                    if name.lower().endswith(self.extensions):
                        yield Path(folder) / name

    def refresh(self) -> None:
        cards: dict[str, Card] = {}
        for file in self.files():
            for card in parse_file(file, self.home):
                cards.setdefault(card.id, card)
        answers = self.log.read()
        by_card: dict[str, list[Answer]] = {}
        for answer in answers:
            by_card.setdefault(answer.card, []).append(answer)
        self._reattach(cards, by_card)
        self.items = {}
        for cid, card in cards.items():
            history = by_card.get(cid, [])
            memory = fsrs.replay(((a.when, a.rating) for a in history),
                                 retention=self.retention, w=self.w, seed=cid)
            self.items[cid] = Item(card, memory, history)

    @staticmethod
    def _reattach(cards: dict[str, Card], by_card: dict[str, list[Answer]]) -> None:
        """Give edited cards back the history their old ids left behind.

        A card's id changes when its front is edited or its note renamed. The
        answers are not lost: an orphaned history goes to a card with no
        history of its own that is recognisably the same one — in the same
        note with an unchanged back, or in the same note with a front at least
        three-quarters alike, or anywhere with exactly the old front.
        """
        orphans = [cid for cid in by_card if cid not in cards]
        if not orphans:
            return
        fresh = {cid: c for cid, c in cards.items() if cid not in by_card}
        by_front: dict[str, list[str]] = {}
        by_src: dict[str, list[str]] = {}
        for cid, c in fresh.items():
            by_front.setdefault(normalise(c.front), []).append(cid)
            by_src.setdefault(c.path, []).append(cid)
        for old in orphans:
            # The newest answer that says where the card was and what it read.
            last = next((a for a in reversed(by_card[old]) if a.src or a.front),
                        by_card[old][-1])
            target = None
            candidates = [cid for cid in by_src.get(last.src, []) if cid in fresh]
            if candidates and last.back:
                same_back = [cid for cid in candidates
                             if back_hash(fresh[cid].back) == last.back]
                if len(same_back) == 1:
                    target = same_back[0]
            if target is None and candidates and last.front:
                scored = sorted(
                    ((difflib.SequenceMatcher(None, normalise(last.front),
                                              normalise(fresh[cid].front)).ratio(), cid)
                     for cid in candidates), reverse=True)
                if scored and scored[0][0] >= 0.75:
                    target = scored[0][1]
            if target is None and last.front:
                exact = [cid for cid in by_front.get(normalise(last.front), [])
                         if cid in fresh]
                if len(exact) == 1:
                    target = exact[0]
            if target:
                by_card[target] = by_card.pop(old)
                fresh.pop(target, None)

    # -- what to show -----------------------------------------------------
    def decks(self) -> list[str]:
        return sorted({item.card.deck for item in self.items.values()})

    def _select(self, deck: str | None):
        for item in self.items.values():
            if deck is None or item.card.deck == deck or item.card.deck.startswith(deck + "/"):
                yield item

    def introduced_today(self, at: datetime) -> int:
        start = fsrs._start_of_day(at)
        return sum(1 for item in self.items.values()
                   if item.answers and item.answers[0].when >= start)

    def queue(self, at: datetime | None = None, deck: str | None = None,
              new_limit: int | None = None) -> list[Item]:
        """Today's work: due cards, most overdue first, then a ration of new ones.

        Overdue is measured as lost retrievability rather than days late, so a
        fragile card one day late outranks a solid one a week late.
        """
        at = at or now()
        due = [item for item in self._select(deck) if item.due(at)]
        due.sort(key=lambda item: item.memory.retrievability(at))
        allowance = self.new_per_day if new_limit is None else new_limit
        allowance = max(0, allowance - self.introduced_today(at))
        fresh = [item for item in self._select(deck) if item.new]
        fresh.sort(key=lambda item: (item.card.path, item.card.line))
        # Both directions of one fact are not introduced on the same day.
        chosen, seen_lines = [], set()
        for item in fresh:
            if len(chosen) >= allowance:
                break
            spot = (item.card.path, item.card.line)
            if item.card.form == "both-r" and spot in seen_lines:
                continue
            seen_lines.add(spot)
            chosen.append(item)
        return due + chosen

    def counts(self, at: datetime | None = None, deck: str | None = None) -> dict:
        at = at or now()
        items = list(self._select(deck))
        return {
            "cards": len(items),
            "due": sum(1 for i in items if i.due(at)),
            "new": sum(1 for i in items if i.new),
            "learned": sum(1 for i in items if not i.new),
        }

    def answer(self, item: Item, rating: int, *, ms: int = 0, via: str = "screen",
               at: datetime | None = None) -> Item:
        """Record an answer, append it to this machine's log, and reschedule."""
        at = at or now()
        record = Answer(when=at, card=item.card.id, rating=rating, ms=ms,
                        src=item.card.path, front=item.card.front,
                        back=back_hash(item.card.back), via=via,
                        machine=self.log.machine)
        self.log.append(record)
        item.answers.append(record)
        item.memory = fsrs.review(item.memory, rating, at, retention=self.retention,
                                  w=self.w, seed=item.card.id)
        return item

    def palaces(self) -> dict[str, list[Item]]:
        walks: dict[str, list[Item]] = {}
        for item in self.items.values():
            if item.card.palace and item.card.form in ("line", "both"):
                walks.setdefault(item.card.palace, []).append(item)
        for items in walks.values():
            items.sort(key=lambda i: (i.card.order or 0, i.card.line))
        return walks


def strip_accents(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    return "".join(c for c in folded if not unicodedata.combining(c))
