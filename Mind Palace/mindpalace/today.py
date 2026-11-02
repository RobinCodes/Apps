"""What Home shows: today, read off the brain. No GTK, so it is tested as arithmetic.

Everything here is something another part of the Order already keeps, read
the way that part keeps it — nothing is stored by the palace:

* **cards** — `cards.Deck` over the roots in `config/learning.yaml`, with
  the decks switched off there left out and the fitted weights used when
  there are any, so the numbers are the ones Mnemosyne would give;
* **the inbox** — `brain.inbox_items()`, oldest first;
* **recent notes** — `brain.Notes.recent()`, the index every app shares;
* **sync** — the state `order sync` writes beside that index;
* **the day's drill** — one entry of The Mentalist's `drills.yaml`, the
  same one all day;
* **questions to revisit** and **on this day** — from `threads.py` and
  `timelines.py`, the brain modules for SYSTEM.md §10 and §11, when they have
  been vendored in beside this one. Until then those two cards are not shown:
  the palace reads the brain through the house's modules, not round them.

Each part fails on its own. A broken learning.yaml costs the cards card, not
Home.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import brain, cards

try:                                    # SYSTEM.md §10, once it is in the house
    from . import threads               # type: ignore[attr-defined]
except ImportError:                     # pragma: no cover - depends on the checkout
    threads = None
try:                                    # SYSTEM.md §11
    from . import timelines             # type: ignore[attr-defined]
except ImportError:                     # pragma: no cover
    timelines = None

DRILLS = Path("curricula") / "The Mentalist" / "drills.yaml"


@dataclass
class Drill:
    id: str
    title: str
    module: str = ""
    trainer: str = ""
    minutes: int = 0


@dataclass
class Sync:
    when: datetime | None = None        # the last `order sync` on this machine
    failures: int = 0


@dataclass
class Today:
    day: date
    due: int | None = None              # None: the cards could not be read
    new: int | None = None
    cards_problem: str = ""
    inbox: list[Path] = field(default_factory=list)
    recent: list = field(default_factory=list)          # brain.Note
    sync: Sync = field(default_factory=Sync)
    drill: Drill | None = None
    questions: list | None = None       # None: no threads module to ask
    on_this_day: list | None = None     # None: no timelines module to ask


# --------------------------------------------------------------- YAML ----

def read_yaml(text: str):
    """PyYAML when it is installed, and the house's small reader when it is not.

    A quoted `"off":` key is unquoted for the small reader, which only knows
    bare keys; PyYAML reads both, and reads a bare `off:` as False, which is
    why Mnemosyne writes it quoted.
    """
    try:
        import yaml                                     # noqa: PLC0415

        data = yaml.safe_load(text)
        if isinstance(data, dict) and False in data:
            data["off"] = data.pop(False)
        return data
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - malformed: fall through to the lenient reader
        pass
    plain = re.sub(r'(?m)^(\s*)["\']([\w-]+)["\']\s*:', r"\1\2:", text)
    return cards._simple_yaml(plain)


# --------------------------------------------------------------- cards ---

def _learning(places: brain.Places) -> dict:
    path = places.brain / "config" / "learning.yaml"
    try:
        data = read_yaml(path.read_text(encoding="utf-8"))
    except OSError:
        data = None
    return data if isinstance(data, dict) else {}


def switched_off(card: cards.Card, entries) -> bool:
    """Mnemosyne's rule: a deck, a deck above it, or whole segments of the card's path."""
    for entry in entries:
        entry = str(entry).strip().strip("/")
        if not entry:
            continue
        if card.deck == entry or card.deck.startswith(entry + "/"):
            return True
        if f"/{entry}/" in f"/{card.path}/":
            return True
    return False


def card_counts(places: brain.Places, at: datetime | None = None) -> tuple[int, int]:
    """(due now, new still to meet today), as Mnemosyne counts them."""
    learning = _learning(places)
    roots = learning.get("roots") or ["SecondBrain/learning", "Projects/Studies"]
    if isinstance(roots, str):
        roots = [roots]

    def number(key, default, kind):
        try:
            return kind(learning.get(key, default))
        except (TypeError, ValueError):
            return default

    weights = learning.get("weights")
    try:
        w = tuple(float(x) for x in weights) if isinstance(weights, list) else None
    except (TypeError, ValueError):
        w = None
    extensions = learning.get("extensions") or ["md", "tex"]
    deck = cards.Deck(places.home, [str(r) for r in roots],
                      cards.ReviewLog(places.learning / "reviews"),
                      retention=number("desired_retention", 0.9, float),
                      new_per_day=number("new_per_day", 20, int),
                      extensions=tuple(str(e) for e in extensions),
                      **({"w": w} if w and len(w) == 19 else {}))
    deck.refresh()
    off = learning.get("off") or []
    if isinstance(off, str):
        off = [off]
    if off:
        deck.items = {cid: item for cid, item in deck.items.items()
                      if not switched_off(item.card, off)}
    queue = deck.queue(at)
    new = sum(1 for item in queue if item.new)
    return len(queue) - new, new


# --------------------------------------------------------------- drills --

def read_drills(text: str) -> list[Drill]:
    """The entries of a drills.yaml: a list, or a mapping with a `drills:` list."""
    data = None
    try:
        import yaml                                     # noqa: PLC0415

        data = yaml.safe_load(text)
    except ImportError:
        data = _drill_items(text)
    except Exception:  # noqa: BLE001 - malformed YAML: try the line reader
        data = _drill_items(text)
    if isinstance(data, dict):
        data = data.get("drills")
    out = []
    for item in data or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        try:
            minutes = int(item.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        out.append(Drill(id=str(item["id"]), title=str(item.get("title") or item["id"]),
                         module=str(item.get("module") or ""),
                         trainer=str(item.get("trainer") or ""), minutes=minutes))
    return out


def _drill_items(text: str) -> list[dict]:
    """Enough of a list of mappings for a drills file, without PyYAML: scalars only."""
    items: list[dict] = []
    current, column = None, -1
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        start = re.match(r"^(\s*-\s+)([\w-]+)\s*:\s*(.*)$", line)
        if start:
            # The item's own keys line up with the first one, after the dash;
            # anything indented further belongs to a nested mapping (params).
            current, column = {}, len(start.group(1))
            items.append(current)
            current[start.group(2)] = _scalar(start.group(3))
            continue
        more = re.match(r"^(\s*)([\w-]+)\s*:\s*(.*)$", line)
        if more and current is not None and len(more.group(1)) == column:
            current.setdefault(more.group(2), _scalar(more.group(3)))
    return items


def _scalar(value: str):
    value = value.split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def drill_of_the_day(drills: list[Drill], day: date) -> Drill | None:
    """One trainer drill per day, the same one all day, every drill in turn.

    Card reviews are left out: those are the daily queue's, which Home counts
    already.
    """
    trainers = [d for d in drills if d.trainer and d.trainer != "cards"] or drills
    if not trainers:
        return None
    return trainers[day.toordinal() % len(trainers)]


# ----------------------------------------------------------------- sync --

def sync_state(cache: Path | None = None) -> Sync:
    """What `order sync` last wrote: when, and how many repositories failed."""
    path = (cache or brain.cache_dir()) / "state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Sync()
    when = None
    raw = data.get("last_sync") if isinstance(data, dict) else None
    if isinstance(raw, str):
        try:
            when = datetime.fromisoformat(raw)
        except ValueError:
            when = None
    failures = data.get("last_sync_failures", 0) if isinstance(data, dict) else 0
    return Sync(when=when, failures=failures if isinstance(failures, int) else 0)


# ---------------------------------------------------------------- ages ---

def age(then: datetime | float, now: datetime | None = None) -> str:
    """'just now', '12 minutes', '3 hours', '9 days' — how long ago, in words."""
    now = now or datetime.now()
    if isinstance(then, (int, float)):
        then = datetime.fromtimestamp(then)
    if then.tzinfo is not None and now.tzinfo is None:
        then = then.astimezone().replace(tzinfo=None)
    seconds = max(0, int((now - then).total_seconds()))
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'' if n == 1 else 's'}"
    return "just now"


# ------------------------------------------------------------- gathering -

def gather(places: brain.Places | None = None, day: date | None = None,
           notes: brain.Notes | None = None, cache: Path | None = None) -> Today:
    """Everything Home shows, as it stands now. Run it on a worker: the cards are a scan.

    The recent notes are what the shared index says. Bringing that index up to
    date is `Notes.refresh`, which on a brain of tens of thousands of notes can
    take minutes, so it is never part of this: Home asks for it separately, and
    draws again if anything changed.
    """
    places = places or brain.places()
    day = day or date.today()
    today = Today(day=day)

    try:
        today.due, today.new = card_counts(places)
    except Exception as exc:  # noqa: BLE001 - one card fails, Home does not
        today.cards_problem = str(exc) or exc.__class__.__name__

    try:
        today.inbox = brain.inbox_items(places)
    except OSError:
        today.inbox = []

    try:
        notes = notes or brain.Notes(places)
        inbox = places.inbox.resolve()
        recent = []
        for note in notes.recent(12):
            try:
                if note.path.resolve().is_relative_to(inbox):
                    continue
            except (OSError, ValueError):
                pass
            recent.append(note)
        today.recent = recent[:6]
    except Exception:  # noqa: BLE001 - an unreadable index costs the recent notes only
        today.recent = []

    today.sync = sync_state(cache)

    try:
        text = (places.learning / DRILLS).read_text(encoding="utf-8")
        today.drill = drill_of_the_day(read_drills(text), day)
    except OSError:
        today.drill = None

    due_questions = getattr(threads, "questions_due", None)
    if callable(due_questions):
        try:
            today.questions = list(due_questions(day, places))
        except Exception:  # noqa: BLE001
            today.questions = None
    remembered = getattr(timelines, "on_this_day", None)
    if callable(remembered):
        try:
            today.on_this_day = list(remembered(day, places))
        except Exception:  # noqa: BLE001
            today.on_this_day = None
    return today
