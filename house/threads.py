"""Questions, and the logical threads that answer them (SYSTEM.md §10).

A question is kept rather than processed: asked at a moment, revisited, maybe
answered years later. A thread is how one gets answered — a free, directed
graph of thoughts, each a `## id · title` with text under it, each arrow a
line `-> id : label` inside the thought it leaves.

Both are Markdown files that several programs edit: Ariadne, the Anamnesis
viewer (its own `tools/threadlib.py`, to the same contract), a text editor. So
the rule here is the one `frontmatter.py` keeps for the front matter, applied
to the whole file:

* a file nobody changed is written back exactly as it was read;
* a thought nobody changed keeps its lines, headings below level 2, blank
  lines, `→` arrows and all; one whose arrows alone changed keeps its text
  and loses or gains only those lines;
* anything that is not a thought — text before the first one, other
  headings — is kept untouched;
* an arrow to a thought that does not exist is kept, and reported as
  dangling, never dropped;
* `layout:` is canvas positions only; a thread without it loses nothing.

Standard library only; vendored by `house/sync.py` with the other brain files.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from . import brain, frontmatter
from .frontmatter import FrontMatter

STATUSES = ("open", "living", "answered", "let-go")

NODE = re.compile(r"^##(?!#)[ \t]+([A-Za-z0-9-]+)[ \t]+(·|-)[ \t]+(.*?)[ \t]*$")
EDGE = re.compile(r"^[ \t]*(->|→)[ \t]*([A-Za-z0-9-]+)[ \t]*(?::[ \t]*(.*?))?[ \t]*$")
REVISITS = re.compile(r"^##[ \t]+Revisits[ \t]*$", re.I)
REVISIT = re.compile(r"^[ \t]*[-*][ \t]+(\d{4}-\d{2}-\d{2})[ \t]+(?:—|–|-|·|:)[ \t]+(.*?)[ \t]*$")

# Where generated ids count from: the seconds since 2026, in base 36. See `new_id`.
_ID_EPOCH = 1767225600
_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_last_clock = 0


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _stamp(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


# ----------------------------------------------------------------- threads --

@dataclass
class Edge:
    target: str
    label: str = ""

    def line(self, arrow: str = "->") -> str:
        return f"{arrow} {self.target}" + (f" : {self.label}" if self.label else "")


class Node:
    """One thought: its id, title, text, and the arrows that leave it."""

    def __init__(self, id: str, title: str = "", text: str = "", edges=None,
                 separator: str = " · "):
        self.id = id
        self.title = title
        self.text = text
        self.edges: list[Edge] = list(edges or [])
        self.separator = separator
        self._heading: str | None = None
        self._body: list[str] | None = None
        self._was: tuple | None = None

    def __repr__(self):  # pragma: no cover - debugging only
        return f"<Node {self.id} {self.title!r}>"

    def _snapshot(self):
        return (self.id, self.title, self.text, [Edge(e.target, e.label) for e in self.edges])

    @property
    def changed(self) -> bool:
        return self._was is None or self._snapshot() != self._was

    def lines(self) -> list[str]:
        if self._was is not None and (self.id, self.title) == self._was[:2]:
            heading = self._heading
        else:
            heading = f"## {self.id}{self.separator}{self.title}".rstrip()
        if self._was is not None and (self.text, self.edges) == self._was[2:]:
            body = list(self._body)
        elif self._was is not None and self.text == self._was[2]:
            body = self._rewired()
        else:
            body = self._fresh()
        return [heading] + body

    def _trailing(self) -> int:
        if self._body is None:
            return 1
        count = 0
        for line in reversed(self._body):
            if line.strip():
                break
            count += 1
        return count

    def _fresh(self) -> list[str]:
        arrow = "->"
        if self._body is not None and any(
                (m := EDGE.match(line)) and m.group(1) == "→" for line in self._body):
            arrow = "→"
        lines = self.text.split("\n") if self.text else []
        lines += [edge.line(arrow) for edge in self.edges]
        return lines + [""] * self._trailing()

    def _rewired(self) -> list[str]:
        """The text as it was, with only the arrow lines that changed changed."""
        remaining = [Edge(e.target, e.label) for e in self.edges]
        body, last_edge = [], -1
        for line in self._body:
            match = EDGE.match(line)
            if not match:
                body.append(line)
                continue
            target, label = match.group(2), match.group(3) or ""
            same = next((e for e in remaining if (e.target, e.label) == (target, label)), None)
            moved = same or next((e for e in remaining if e.target == target), None)
            if moved is None:
                continue                       # this arrow was taken away
            remaining.remove(moved)
            body.append(line if same else Edge(moved.target, moved.label).line(match.group(1)))
            last_edge = len(body) - 1
        if remaining:
            if last_edge < 0:
                last_edge = max((i for i, line in enumerate(body) if line.strip()),
                                default=-1)
            body[last_edge + 1:last_edge + 1] = [edge.line() for edge in remaining]
        return body


class Thread:
    """A logical thread: its front matter, the text before it, and its thoughts."""

    def __init__(self, front: FrontMatter | None = None, preamble=None, nodes=None,
                 path: Path | None = None):
        self.front = front if front is not None else FrontMatter([])
        self.preamble: list[str] = list(preamble if preamble is not None else [""])
        self.nodes: list[Node] = list(nodes or [])
        self.path = Path(path) if path else None
        self.newline = "\n"
        self._original: str | None = None
        self._was: tuple | None = None

    # -- reading and writing ------------------------------------------------

    @classmethod
    def parse(cls, text: str, path: Path | str | None = None) -> "Thread":
        newline = "\r\n" if "\r\n" in text else "\n"
        front, body = frontmatter.split(text.replace("\r\n", "\n"))
        thread = cls(front or FrontMatter(present=False), [], [], path)
        thread.newline = newline
        current = None
        for line in body.split("\n"):
            heading = NODE.match(line)
            if heading:
                current = Node(heading.group(1), heading.group(3),
                               separator=f" {heading.group(2)} ")
                current._heading = line
                current._body = []
                thread.nodes.append(current)
            elif current is None:
                thread.preamble.append(line)
            else:
                current._body.append(line)
        for node in thread.nodes:
            text_lines = []
            for line in node._body:
                match = EDGE.match(line)
                if match:
                    node.edges.append(Edge(match.group(2), match.group(3) or ""))
                else:
                    text_lines.append(line)
            node.text = "\n".join(text_lines).strip("\n")
            node._was = node._snapshot()
        thread._original = text
        thread._was = thread._state()
        return thread

    @classmethod
    def new(cls, title: str, answers=(), when: datetime | None = None) -> "Thread":
        thread = cls(FrontMatter(["kind: thread"]))
        thread.title = title
        if answers:
            thread.answers = list(answers)
        thread.front.set("created", _stamp(when or _now()))
        return thread

    def _state(self):
        return (list(self.preamble), [node.id for node in self.nodes])

    @property
    def changed(self) -> bool:
        return (self._original is None or self.front.dirty or self._state() != self._was
                or any(node.changed for node in self.nodes))

    def dump(self) -> str:
        """The file. Exactly as it was read when nothing in it changed."""
        if not self.changed:
            return self._original
        lines = list(self.preamble)
        for node in self.nodes:
            if node._was is None and lines and lines[-1].strip():
                lines.append("")             # a new thought, a blank line above it
            lines += node.lines()
        text = _head(self.front) + "\n".join(lines)
        if not text.endswith("\n"):
            text += "\n"
        return text.replace("\n", self.newline) if self.newline != "\n" else text

    @classmethod
    def load(cls, path: Path | str) -> "Thread":
        return cls.parse(_read(path), path)

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path or self.path)
        _write(path, self.dump())
        self.path = path
        return path

    # -- what the front matter says -----------------------------------------

    @property
    def title(self) -> str:
        if self.front.get("title"):
            return str(self.front.get("title"))
        return self.path.stem if self.path else ""

    @title.setter
    def title(self, value: str):
        self.front.set("title", value.strip())

    @property
    def answers(self) -> list[str]:
        value = self.front.get("answers", [])
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]

    @answers.setter
    def answers(self, titles):
        self.front.set("answers", [str(t) for t in titles] if titles else None)

    @property
    def created(self) -> str:
        return str(self.front.get("created", ""))

    @property
    def layout(self) -> dict[str, tuple[float, float]]:
        """Where each thought sits on the canvas; empty when none has been placed."""
        value = self.front.get("layout", {})
        out = {}
        if isinstance(value, dict):
            for key, where in value.items():
                try:
                    out[key] = (float(where[0]), float(where[1]))
                except (TypeError, ValueError, IndexError):
                    continue
        return out

    @layout.setter
    def layout(self, places: dict):
        kept = {key: [round(x), round(y)] for key, (x, y) in places.items()}
        self.front.set("layout", kept or None)

    # -- the graph ----------------------------------------------------------

    def node(self, id: str) -> Node | None:
        return next((node for node in self.nodes if node.id == id), None)

    def edges(self):
        """Every arrow, as (from, Edge)."""
        for node in self.nodes:
            for edge in node.edges:
                yield node, edge

    def dangling(self):
        ids = {node.id for node in self.nodes}
        return [(node, edge) for node, edge in self.edges() if edge.target not in ids]

    def new_id(self) -> str:
        """An id never used in this file before, as far as anyone can tell.

        Hand-written threads use a, b, c. Generated ones are the seconds since
        2026 in base 36, after a `t` — the same as `threadlib.py` on the
        Anamnesis side — and only ever go up. So an id freed by a deletion is
        never given to a different thought, which is what "never reused" asks,
        and two machines adding a thought to one thread do not collide.
        """
        global _last_clock
        taken = ({node.id for node in self.nodes}
                 | {edge.target for _, edge in self.edges()} | set(self.layout))
        number = max(int(time.time()) - _ID_EPOCH, _last_clock + 1)
        while True:
            digits, rest = "", number
            while rest:
                rest, digit = divmod(rest, 36)
                digits = _DIGITS[digit] + digits
            candidate = "t" + (digits or "0")
            if candidate not in taken:
                _last_clock = number
                return candidate
            number += 1

    def add(self, title: str, text: str = "", id: str | None = None) -> Node:
        node = Node(id or self.new_id(), title.strip(), text)
        self.nodes.append(node)
        return node

    def remove(self, id: str) -> tuple[Node, list[tuple[Node, Edge]]] | None:
        """Take a thought away, and the arrows into it. Returns them, for an undo."""
        node = self.node(id)
        if node is None:
            return None
        self.nodes.remove(node)
        incoming = []
        for other in self.nodes:
            for edge in list(other.edges):
                if edge.target == id:
                    other.edges.remove(edge)
                    incoming.append((other, edge))
        places = self.layout
        if id in places:
            del places[id]
            self.layout = places
        return node, incoming

    def restore(self, node: Node, incoming, index: int | None = None, where=None):
        """Put back what `remove` took."""
        self.nodes.insert(len(self.nodes) if index is None else index, node)
        for other, edge in incoming:
            if other in self.nodes:
                other.edges.append(edge)
        if where is not None:
            places = self.layout
            places[node.id] = where
            self.layout = places

    def connect(self, source: str, target: str, label: str = "") -> Edge | None:
        node = self.node(source)
        if node is None or source == target:
            return None
        existing = next((e for e in node.edges if e.target == target), None)
        if existing is not None:
            return existing
        edge = Edge(target, label.strip())
        node.edges.append(edge)
        return edge


# --------------------------------------------------------------- questions --

class Question:
    """A question kept: its front matter, its text, and its revisits."""

    def __init__(self, front: FrontMatter | None = None, lines=None,
                 path: Path | None = None):
        self.front = front if front is not None else FrontMatter([])
        self.lines: list[str] = list(lines if lines is not None else [""])
        self.path = Path(path) if path else None
        self.newline = "\n"
        self._original: str | None = None
        self._was: list[str] | None = None

    @classmethod
    def parse(cls, text: str, path: Path | str | None = None) -> "Question":
        newline = "\r\n" if "\r\n" in text else "\n"
        front, body = frontmatter.split(text.replace("\r\n", "\n"))
        question = cls(front or FrontMatter(present=False), body.split("\n"), path)
        question.newline = newline
        question._original = text
        question._was = list(question.lines)
        return question

    @classmethod
    def new(cls, title: str, text: str = "", when: datetime | None = None,
            tags=()) -> "Question":
        question = cls(FrontMatter(["kind: question"]))
        question.title = title
        question.front.set("asked", _stamp(when or _now()))
        question.front.set("status", "open")
        if tags:
            question.front.set("tags", list(tags))
        question.lines = ["", *text.strip().split("\n"), ""] if text.strip() else [""]
        return question

    @property
    def changed(self) -> bool:
        return self._original is None or self.front.dirty or self.lines != self._was

    def dump(self) -> str:
        if not self.changed:
            return self._original
        text = _head(self.front) + "\n".join(self.lines)
        if not text.endswith("\n"):
            text += "\n"
        return text.replace("\n", self.newline) if self.newline != "\n" else text

    @classmethod
    def load(cls, path: Path | str) -> "Question":
        return cls.parse(_read(path), path)

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path or self.path)
        _write(path, self.dump())
        self.path = path
        return path

    # -- the front matter ---------------------------------------------------

    @property
    def title(self) -> str:
        if self.front.get("title"):
            return str(self.front.get("title"))
        return self.path.stem if self.path else ""

    @title.setter
    def title(self, value: str):
        self.front.set("title", value.strip())

    @property
    def asked(self) -> str:
        return str(self.front.get("asked", ""))

    @property
    def asked_date(self) -> date | None:
        return _date(self.asked)

    @property
    def status(self) -> str:
        value = str(self.front.get("status", "open")).strip().lower()
        return value if value in STATUSES else "open"

    @status.setter
    def status(self, value: str):
        if value not in STATUSES:
            raise ValueError(f"status is one of {STATUSES}")
        self.front.set("status", value)

    @property
    def revisit(self) -> date | None:
        return _date(str(self.front.get("revisit", "")))

    @revisit.setter
    def revisit(self, day: date | None):
        self.front.set("revisit", day.isoformat() if day else None)

    @property
    def tags(self) -> list[str]:
        value = self.front.get("tags", [])
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]

    def due(self, today: date | None = None) -> bool:
        """Whether it is time to ask it again: a revisit date reached, and still open."""
        today = today or date.today()
        return (self.status in ("open", "living") and self.revisit is not None
                and self.revisit <= today)

    # -- the body -----------------------------------------------------------

    def _revisits_at(self) -> int:
        return next((i for i, line in enumerate(self.lines) if REVISITS.match(line)),
                    len(self.lines))

    @property
    def text(self) -> str:
        """The question in full: everything above `## Revisits`."""
        return "\n".join(self.lines[:self._revisits_at()]).strip("\n")

    @text.setter
    def text(self, value: str):
        if value.strip("\n") == self.text:
            return
        at = self._revisits_at()
        new = ["", *value.strip("\n").split("\n")] if value.strip() else []
        new.append("")
        self.lines[:at] = new

    @property
    def revisits(self) -> list[tuple[str, str]]:
        """(date, what was thought then), oldest first, as written."""
        at = self._revisits_at()
        found = []
        for line in self.lines[at + 1:]:
            if line.startswith("## "):
                break
            match = REVISIT.match(line)
            if match:
                found.append((match.group(1), match.group(2).strip()))
        return found

    def add_revisit(self, text: str, day: date | None = None) -> None:
        """A line in `## Revisits` — the section is made if this is the first."""
        line = f"- {(day or date.today()).isoformat()} — {' '.join(text.split())}"
        at = self._revisits_at()
        if at == len(self.lines):
            while self.lines and not self.lines[-1].strip():
                self.lines.pop()
            self.lines += ["", "## Revisits", "", line, ""]
            return
        last = at
        for index in range(at + 1, len(self.lines)):
            if self.lines[index].startswith("## "):
                break
            if REVISIT.match(self.lines[index]):
                last = index
        if last == at:
            # Under the heading, after the blank line that usually follows it.
            last = at + 1 if at + 1 < len(self.lines) and not self.lines[at + 1].strip() else at
        self.lines.insert(last + 1, line)

    def links(self) -> list[str]:
        """The targets of every `[[link]]` in the question."""
        return [link.target for link in brain.wikilinks("\n".join(self.lines))]


def _head(front: FrontMatter) -> str:
    """The front matter and the newline after it — nothing, for a file that had none."""
    return front.text() + "\n" if (front.present or front.lines or front.dirty) else ""


def _date(text: str) -> date | None:
    match = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})", text or "")
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


# ------------------------------------------------------------ the folders --

def questions_folder(where: brain.Places | None = None) -> Path:
    return (where or brain.places()).brain / "questions"


def threads_folder(where: brain.Places | None = None) -> Path:
    return (where or brain.places()).brain / "threads"


def load_questions(where: brain.Places | None = None) -> list[Question]:
    """Every question, the ones due to be asked again first, then newest first."""
    found = []
    for file in sorted(_notes_in(questions_folder(where))):
        try:
            found.append(Question.load(file))
        except (OSError, UnicodeDecodeError):
            continue
    return order_questions(found)


def order_questions(found, today: date | None = None) -> list[Question]:
    rank = {"open": 1, "living": 1, "answered": 2, "let-go": 3}
    return sorted(found, key=lambda q: (0 if q.due(today) else rank[q.status],
                                        _negated(q.asked)))


def _negated(text: str) -> tuple:
    # Newest first within a group, without parsing every timestamp format.
    return tuple(-ord(char) for char in text)


def load_threads(where: brain.Places | None = None) -> list[Thread]:
    found = []
    for file in sorted(_notes_in(threads_folder(where))):
        try:
            found.append(Thread.load(file))
        except (OSError, UnicodeDecodeError):
            continue
    return sorted(found, key=lambda t: t.title.casefold())


def _notes_in(folder: Path):
    if not folder.is_dir():
        return []
    return [file for file in folder.iterdir()
            if file.suffix.lower() == ".md" and not file.name.startswith(".")]


def new_question(title: str, text: str = "", *, when: datetime | None = None,
                 where: brain.Places | None = None, tags=()) -> Question:
    """Keep a question now: `questions/<title>.md`, saved at once."""
    question = Question.new(title, text, when or _now(), tags)
    folder = questions_folder(where)
    folder.mkdir(parents=True, exist_ok=True)
    question.save(brain._unique(folder / f"{brain.safe_name(title, 90)}.md"))
    return question


def new_thread(title: str, answers=(), *, when: datetime | None = None,
               where: brain.Places | None = None) -> Thread:
    """Start a thread: `threads/<title>.md`, so `[[its title]]` reaches it by name too."""
    thread = Thread.new(title, answers, when)
    folder = threads_folder(where)
    folder.mkdir(parents=True, exist_ok=True)
    thread.save(brain._unique(folder / f"{brain.safe_name(title, 90)}.md"))
    return thread


def threads_for(question: Question, threads, resolve=None) -> list[Thread]:
    """The threads that answer a question: by `answers:`, or by a `[[link]]` in it.

    `resolve(target)` is how a link becomes a file — `Notes.resolve`, where
    the index is to hand; without one a link is matched to a thread's title
    or file name the way the index would match it.
    """
    wanted = brain.key(question.title)
    linked = set()
    for target in question.links():
        path = resolve(target) if resolve else None
        if path is not None:
            linked.add(os.path.normcase(os.path.abspath(path)))
        linked.add(brain.key(target))
    found = []
    for thread in threads:
        names = {brain.key(thread.title)}
        if thread.path is not None:
            names |= {brain.key(thread.path.stem),
                      os.path.normcase(os.path.abspath(thread.path))}
        if wanted in {brain.key(a) for a in thread.answers} or names & linked:
            found.append(thread)
    return found


def load(path: Path | str):
    """A question or a thread, by what its front matter says it is."""
    text = _read(path)
    front, _ = frontmatter.split(text.replace("\r\n", "\n"))
    kind = str(front.get("kind", "")) if front else ""
    return (Question if kind == "question" else Thread).parse(text, path)


def _read(path) -> str:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return handle.read()


def _write(path: Path, text: str) -> None:
    """Written beside and moved into place, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.writing")
    with open(temporary, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(temporary, path)
