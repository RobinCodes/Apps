"""The Order, as seen from inside an app: where things are, and what links to what.

Every app that joins the second brain gets this file, vendored by
`house/sync.py` the way it gets the house style. It knows four things:

* **the layout** (SYSTEM.md §2) — `places()` says where the brain, the inbox,
  the projects and the learning folder are on this machine, Arch or Windows,
  with nothing configured;
* **the notes** — every Markdown note under the brain and the note-bearing
  projects, by title, alias, file name and node id, so that `[[Wiki links]]`
  resolve, backlinks can be listed and a quick switcher has something to
  switch between (`Notes`);
* **the brain's own index** — `search()` asks Anamnesis's `index/brain.db`
  when it exists, which ranks thought above traces, and falls back to the
  note titles when it does not;
* **the inbox** — `capture_text()` and `capture_file()`, the one place a thing
  goes when it has not been filed yet (SYSTEM.md §4.1).

Nothing in it needs Anamnesis running. The note index is a small SQLite file
in the user's cache folder, refreshed by modification time, so a refresh after
the first is a directory walk and a handful of reads. Standard library only.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import sys
import threading
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .cards import split_front_matter

# ------------------------------------------------------------- places ----


@dataclass(frozen=True)
class Places:
    home: Path          # ~ — /home/robin, or %USERPROFILE%
    projects: Path      # ~/Projects
    brain: Path         # ~/SecondBrain
    vault: Path         # ~/SecondBrain/vault/nodes — Anamnesis's nodes
    inbox: Path         # ~/SecondBrain/inbox
    journal: Path       # ~/SecondBrain/journal — daily notes
    learning: Path      # ~/SecondBrain/learning
    studies: Path       # ~/Projects/Studies
    apps: Path          # ~/Projects/Programming/Apps

    @property
    def index(self) -> Path:
        return self.brain / "index" / "brain.db"

    def exists(self) -> bool:
        return self.brain.is_dir()

    def relative(self, path: Path | str) -> str:
        """Home-relative with forward slashes — how the Order names a file anywhere."""
        p = Path(path)
        try:
            return p.resolve().relative_to(self.home.resolve()).as_posix()
        except (ValueError, OSError):
            return p.as_posix()


def places() -> Places:
    """The layout on this machine.

    `ORDER_HOME` moves the whole tree (tests, a portable drive); `ANAM_HOME`
    moves only the brain, which is what Anamnesis itself already honours.
    """
    home = Path(os.environ.get("ORDER_HOME") or Path.home())
    brain = Path(os.environ.get("ANAM_HOME") or home / "SecondBrain")
    projects = home / "Projects"
    return Places(
        home=home, projects=projects, brain=brain,
        vault=brain / "vault" / "nodes", inbox=brain / "inbox",
        journal=brain / "journal", learning=brain / "learning",
        studies=projects / "Studies",
        apps=projects / "Programming" / "Apps")


def cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    folder = base / "order"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def note_roots(where: Places | None = None) -> list[Path]:
    """Where notes are looked for: the brain, and the projects that are prose."""
    p = where or places()
    return [p.vault, p.journal, p.inbox, p.learning, p.studies,
            p.projects / "Documents"]


# ------------------------------------------------------------- names -----


def key(text: str) -> str:
    """How a link target is compared: no accents, no case, no extension, one space.

    `[[Fémek]]`, `[[femek]]` and `[[Fémek.md]]` all reach `Fémek.md`, because a
    link typed in a hurry should not miss on a diacritic.
    """
    text = text.strip()
    if text.lower().endswith((".md", ".markdown")):
        text = text.rsplit(".", 1)[0]
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(folded.casefold().split())


_WIKILINK = re.compile(r"(!?)\[\[([^\[\]\n|#^]+)(#[^\[\]\n|]*)?(?:\|([^\[\]\n]*))?\]\]")


@dataclass(frozen=True)
class WikiLink:
    target: str             # what is linked to, as written
    heading: str | None     # `#Heading` after it, without the hash
    label: str | None       # `|label`, as written
    embed: bool             # `![[…]]`
    start: int              # offsets in the text it was found in
    end: int

    @property
    def shown(self) -> str:
        return self.label or self.target + (f" › {self.heading}" if self.heading else "")


def wikilinks(text: str) -> list[WikiLink]:
    """Every `[[link]]` in a text, outside code."""
    masked = _mask_code(text)
    found = []
    for m in _WIKILINK.finditer(masked):
        raw = text[m.start():m.end()]
        inner = _WIKILINK.match(raw)
        if not inner:
            continue
        found.append(WikiLink(
            target=inner.group(2).strip(),
            heading=(inner.group(3) or "")[1:].strip() or None,
            label=(inner.group(4) or "").strip() or None,
            embed=bool(inner.group(1)), start=m.start(), end=m.end()))
    return found


def _mask_code(text: str) -> str:
    out = list(text)
    for m in re.finditer(r"(?ms)^(\s{0,3})(`{3,}|~{3,}).*?^\s{0,3}\2", text):
        for i in range(m.start(), m.end()):
            if out[i] != "\n":
                out[i] = "\0"
    for m in re.finditer(r"(`+)[^\n]+?\1", text):
        for i in range(m.start(), m.end()):
            out[i] = "\0"
    return "".join(out)


def _front_matter(text: str) -> dict:
    return split_front_matter(text)[0]


def title_of(text: str, path: Path) -> str:
    """A note's title: its front matter's, else its first heading, else its name."""
    meta = _front_matter(text)
    if meta.get("title"):
        return str(meta["title"]).strip()
    for line in text.splitlines()[:60]:
        m = re.match(r"^#\s+(.+?)\s*#*\s*$", line)
        if m:
            return m.group(1).strip()
    stem = path.stem
    # Anamnesis names node files `slug--id8`; the slug alone reads better.
    return re.sub(r"--[0-9a-f]{8}$", "", stem)


# ------------------------------------------------------------- notes -----

NOTE_SUFFIXES = (".md", ".markdown")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".cache",
             "dist", "build", "reviews", ".order"}
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class Note:
    path: Path
    title: str
    rel: str            # home-relative
    node_id: str | None = None
    kind: str | None = None


@dataclass(frozen=True)
class Backlink:
    path: Path
    title: str
    line: int
    context: str        # the line the link is on, trimmed


class Notes:
    """Every note the Order knows, kept in a small index that refreshes itself.

    One instance per process is enough (`Notes.shared()`); it is safe to call
    from a worker thread, and every app's index is the same file, so whichever
    app refreshed last has done the work for the others.
    """

    _shared: "Notes | None" = None
    _lock = threading.Lock()

    def __init__(self, where: Places | None = None, db_path: Path | None = None,
                 roots: list[Path] | None = None):
        self.places = where or places()
        self.roots = roots if roots is not None else note_roots(self.places)
        self.db_path = db_path or cache_dir() / "notes.db"
        self._local = threading.local()

    @classmethod
    def shared(cls) -> "Notes":
        with cls._lock:
            if cls._shared is None:
                cls._shared = cls()
            return cls._shared

    # -- storage ------------------------------------------------------------
    def _db(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(self.db_path, timeout=10)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=10000")
            if con.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                con.executescript("""
                    DROP TABLE IF EXISTS notes; DROP TABLE IF EXISTS names;
                    DROP TABLE IF EXISTS links;
                    CREATE TABLE notes (rel TEXT PRIMARY KEY, mtime REAL, size INTEGER,
                                        title TEXT, node_id TEXT, kind TEXT);
                    CREATE TABLE names (key TEXT, rel TEXT, rank INTEGER);
                    CREATE INDEX names_key ON names(key);
                    CREATE INDEX names_rel ON names(rel);
                    CREATE TABLE links (src TEXT, key TEXT, line INTEGER, context TEXT);
                    CREATE INDEX links_key ON links(key);
                    CREATE INDEX links_src ON links(src);
                """)
                con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                con.commit()
            self._local.con = con
        return con

    # -- refreshing ---------------------------------------------------------
    def _walk(self):
        for root in self.roots:
            if not root.is_dir():
                continue
            for folder, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
                for name in files:
                    if name.lower().endswith(NOTE_SUFFIXES):
                        yield Path(folder) / name

    def refresh(self) -> int:
        """Bring the index up to date with the disk. Returns how many notes changed."""
        con = self._db()
        known = {rel: (mtime, size) for rel, mtime, size
                 in con.execute("SELECT rel, mtime, size FROM notes")}
        present = set()
        changed = 0
        for file in self._walk():
            rel = self.places.relative(file)
            present.add(rel)
            try:
                st = file.stat()
            except OSError:
                continue
            if known.get(rel) == (st.st_mtime, st.st_size):
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            self._store(con, rel, file, text, st.st_mtime, st.st_size)
            changed += 1
        gone = set(known) - present
        for rel in gone:
            for table, column in (("notes", "rel"), ("names", "rel"), ("links", "src")):
                con.execute(f"DELETE FROM {table} WHERE {column}=?", (rel,))
        con.commit()
        return changed + len(gone)

    def update(self, file: Path, text: str | None = None) -> None:
        """Re-read one note now — after a save, without waiting for a refresh."""
        file = Path(file)
        try:
            st = file.stat()
            text = text if text is not None else file.read_text(encoding="utf-8",
                                                                  errors="replace")
        except OSError:
            return
        con = self._db()
        self._store(con, self.places.relative(file), file, text, st.st_mtime, st.st_size)
        con.commit()

    def _store(self, con, rel, file, text, mtime, size):
        meta = _front_matter(text)
        title = title_of(text, file)
        node_id = str(meta.get("id")) if meta.get("id") else None
        kind = str(meta.get("kind")) if meta.get("kind") else None
        con.execute("DELETE FROM names WHERE rel=?", (rel,))
        con.execute("DELETE FROM links WHERE src=?", (rel,))
        con.execute("INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?)",
                    (rel, mtime, size, title, node_id, kind))
        names = [(key(title), 0)]
        aliases = meta.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        names += [(key(str(a)), 1) for a in aliases if str(a).strip()]
        stem = re.sub(r"--[0-9a-f]{8}$", "", file.stem)
        names += [(key(file.stem), 2), (key(stem), 2)]
        if node_id:
            names += [(node_id.lower(), 3), (node_id[:8].lower(), 3)]
        best: dict[str, int] = {}
        for name, rank in names:
            if name and rank < best.get(name, 99):
                best[name] = rank
        con.executemany("INSERT INTO names VALUES (?,?,?)",
                        [(name, rel, rank) for name, rank in best.items()])
        lines = text.split("\n")
        starts = [0]
        for line in lines:
            starts.append(starts[-1] + len(line) + 1)
        rows = []
        for link in wikilinks(text):
            line_no = _line_at(starts, link.start)
            target = link.target.replace("\\", "/").rsplit("/", 1)[-1]
            rows.append((rel, key(target), line_no + 1,
                         lines[line_no].strip()[:240]))
        con.executemany("INSERT INTO links VALUES (?,?,?,?)", rows)

    # -- asking -------------------------------------------------------------
    def all(self) -> list[Note]:
        con = self._db()
        return [Note(self.places.home / rel, title, rel, node_id, kind)
                for rel, title, node_id, kind
                in con.execute("SELECT rel, title, node_id, kind FROM notes ORDER BY title")]

    def find(self, text: str, limit: int = 50) -> list[Note]:
        """Notes whose title or name contains every word typed — for a switcher."""
        words = key(text).split()
        con = self._db()
        if not words:
            return []
        clauses = " AND ".join("names.key LIKE ?" for _ in words)
        rows = con.execute(
            f"SELECT DISTINCT notes.rel, notes.title, notes.node_id, notes.kind, "
            f"MIN(names.rank), length(notes.title) FROM names JOIN notes "
            f"ON notes.rel = names.rel WHERE {clauses} GROUP BY notes.rel "
            f"ORDER BY MIN(names.rank), length(notes.title) LIMIT ?",
            [f"%{w}%" for w in words] + [limit]).fetchall()
        return [Note(self.places.home / r[0], r[1], r[0], r[2], r[3]) for r in rows]

    def resolve(self, target: str, near: Path | str | None = None) -> Path | None:
        """The file a `[[link]]` means, or `None` if there is no such note yet.

        A path-like target is tried relative to the linking note first. A name
        is matched on title, then alias, then file name, then node id; among
        equals the one closest to the linking note wins, then the shortest path.
        """
        target = target.strip().replace("\\", "/")
        if not target:
            return None
        if near is not None and ("/" in target or target.lower().endswith(NOTE_SUFFIXES)):
            base = Path(near).parent
            for candidate in (base / target, base / (target + ".md")):
                if candidate.is_file():
                    return candidate
        name = target.rsplit("/", 1)[-1]
        rows = self._db().execute(
            "SELECT rel, rank FROM names WHERE key=? ORDER BY rank",
            (key(name),)).fetchall()
        if not rows:
            return None
        best_rank = rows[0][1]
        candidates = [rel for rel, rank in rows if rank == best_rank]
        near_rel = self.places.relative(near) if near else ""
        near_dir = near_rel.rsplit("/", 1)[0] if "/" in near_rel else ""

        def closeness(rel):
            common = os.path.commonprefix([rel.split("/"), near_dir.split("/")])
            return (-len(common), len(rel), rel)

        return self.places.home / sorted(candidates, key=closeness)[0]

    def backlinks(self, file: Path | str) -> list[Backlink]:
        """Every note that links to this one, with the line it does it on."""
        con = self._db()
        rel = self.places.relative(file)
        keys = [k for (k,) in con.execute("SELECT key FROM names WHERE rel=?", (rel,))]
        if not keys:
            path = Path(file)
            keys = [key(path.stem)]
        marks = ",".join("?" * len(keys))
        rows = con.execute(
            f"SELECT DISTINCT links.src, notes.title, links.line, links.context "
            f"FROM links LEFT JOIN notes ON notes.rel = links.src "
            f"WHERE links.key IN ({marks}) AND links.src != ? "
            f"ORDER BY notes.title, links.line", keys + [rel]).fetchall()
        return [Backlink(self.places.home / src, title or src, line, context)
                for src, title, line, context in rows]

    def unresolved(self) -> list[tuple[str, int]]:
        """Link targets no note answers to, with how often each is linked."""
        con = self._db()
        return con.execute(
            "SELECT links.key, COUNT(*) FROM links LEFT JOIN names ON names.key = links.key "
            "WHERE names.key IS NULL GROUP BY links.key ORDER BY COUNT(*) DESC").fetchall()


def _line_at(starts: list[int], offset: int) -> int:
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo


# ------------------------------------------------------------- search ----


@dataclass(frozen=True)
class Hit:
    title: str
    path: Path              # the file to open: the original if there is one
    kind: str               # a node kind, or "note"
    snippet: str = ""
    node_id: str | None = None
    tier: str | None = None


def search(text: str, limit: int = 40, where: Places | None = None) -> list[Hit]:
    """Search the brain. Anamnesis's index when it exists, note titles when not.

    Results from the index are ranked by match and by weight together, so an
    evening's writing outranks a page once visited — and a hit that came from
    a file opens that file, not the node about it.
    """
    p = where or places()
    words = [w for w in re.findall(r"\w+", text, re.UNICODE) if w]
    if not words:
        return []
    hits: list[Hit] = []
    if p.index.is_file():
        try:
            con = sqlite3.connect(f"file:{p.index.as_posix()}?mode=ro", uri=True,
                                  timeout=5)
            query = " ".join('"' + w.replace('"', "") + '"*' for w in words)
            rows = con.execute(
                "SELECT n.id, n.kind, n.title, n.path, n.tier, "
                "snippet(nodes_fts, 1, '«', '»', '…', 12), "
                "(SELECT s.origin || char(31) || s.path FROM sources s "
                " WHERE s.node_id = n.id LIMIT 1) "
                "FROM nodes_fts JOIN nodes n ON n.rowid = nodes_fts.rowid "
                "WHERE nodes_fts MATCH ? "
                "ORDER BY bm25(nodes_fts) - n.importance / 50.0 LIMIT ?",
                (query, limit)).fetchall()
            con.close()
        except sqlite3.Error:
            rows = []
        for node_id, kind, title, rel, tier, snippet, source in rows:
            path = p.brain / "vault" / rel if not Path(rel).is_absolute() else Path(rel)
            if source:
                origin, _, src = source.partition("\x1f")
                original = p.home / src
                if origin == "fs" and original.is_file():
                    path = original
            hits.append(Hit(title=title, path=path, kind=kind,
                            snippet=(snippet or "").replace("\n", " "),
                            node_id=node_id, tier=tier))
    if len(hits) < limit:
        seen = {h.path for h in hits}
        try:
            notes = Notes.shared() if where is None else Notes(where)
            for note in notes.find(text, limit=limit - len(hits)):
                if note.path not in seen:
                    hits.append(Hit(title=note.title, path=note.path,
                                    kind=note.kind or "note", node_id=note.node_id))
        except sqlite3.Error:
            pass
    return hits


# -------------------------------------------------------------- inbox ----

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(text: str, limit: int = 80) -> str:
    """A file name that is legal on Windows and Linux alike, and still readable."""
    name = " ".join(_UNSAFE.sub(" ", text).split())[:limit].strip(" .")
    return name or "Untitled"


def _stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%d %H%M")


def capture_text(text: str, title: str | None = None, *, origin: str = "manual",
                 app: str | None = None, where: Places | None = None,
                 when: datetime | None = None) -> Path:
    """Put a thought in the inbox as a note of its own. Returns its path."""
    p = where or places()
    when = when or datetime.now(timezone.utc).astimezone()
    first = next((line.strip("# ").strip() for line in text.splitlines()
                  if line.strip()), "")
    title = (title or first or "Note")[:80]
    p.inbox.mkdir(parents=True, exist_ok=True)
    path = _unique(p.inbox / f"{_stamp(when)} {safe_name(title, 60)}.md")
    from_line = f"origin: {origin}\n" + (f"app: {app}\n" if app else "")
    machine = os.environ.get("ORDER_MACHINE") or _hostname()
    body = (f"---\ntitle: {_yaml_str(title)}\ncreated: {when.isoformat(timespec='seconds')}\n"
            f"{from_line}machine: {machine}\n---\n\n{text.rstrip()}\n")
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def capture_file(source: Path | str, *, move: bool = True, origin: str = "manual",
                 where: Places | None = None) -> Path:
    """Put a file in the inbox, keeping its name. Moves it unless told to copy."""
    p = where or places()
    source = Path(source)
    p.inbox.mkdir(parents=True, exist_ok=True)
    dest = _unique(p.inbox / source.name)
    if move:
        shutil.move(str(source), str(dest))
    elif source.is_dir():
        shutil.copytree(source, dest)
    else:
        shutil.copy2(source, dest)
    return dest


def inbox_items(where: Places | None = None) -> list[Path]:
    """What is waiting in the inbox, oldest first."""
    p = where or places()
    if not p.inbox.is_dir():
        return []
    items = [c for c in p.inbox.iterdir() if not c.name.startswith(".")]
    return sorted(items, key=lambda c: (c.stat().st_mtime, c.name))


def daily_note(day: date | None = None, where: Places | None = None,
               create: bool = True) -> Path:
    """`journal/YYYY/YYYY-MM-DD.md`, made with a heading if it does not exist yet."""
    p = where or places()
    day = day or date.today()
    path = p.journal / f"{day:%Y}" / f"{day:%Y-%m-%d}.md"
    if create and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {day:%Y-%m-%d}, {day:%A}\n\n", encoding="utf-8",
                        newline="\n")
    return path


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while (path.with_name(f"{stem} ({n}){suffix}")).exists():
        n += 1
    return path.with_name(f"{stem} ({n}){suffix}")


def _yaml_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _hostname() -> str:
    import socket                                           # noqa: PLC0415
    name = socket.gethostname() or "unknown"
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "unknown"
