"""The trainers' logic: what to ask, what counts as right, and the log of scores.

The Mentalist curriculum (SYSTEM.md §9) ends every module in cards and
drills. The cards are scheduled like any others; the drills are timed and
live here. Six kinds, named the way `drills.yaml` names them:

| trainer | asks | answered by |
|---|---|---|
| `observation` | a photograph, then the same one with one region changed | clicking where |
| `deck` | a position in the memorised deck, or a card | the card, or the position |
| `doomsday` | a date | its weekday, 0–6 from Sunday, Conway's numbering |
| `major` | a number, or a word from the Major system deck | the word, or the number |
| `calc` | a sum, a square, a cube root | the number |
| `cards` | a deck's due cards | the ordinary review |

The curriculum says *which* drills, with what settings, in
`learning/curricula/*/drills.yaml`; when there is no such file the built-in
set below stands in. Every run ends in one line of
`learning/drills/<machine>.jsonl` — `{t, drill, score, ms, n}`, score a
fraction right and ms the median answer time — next to the review log and
merged the same way.

No GTK in this module: the window draws the questions, this decides them.
"""

from __future__ import annotations

import json
import random
import re
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import brain, cards, dates, yamlite

TRAINERS = ("observation", "deck", "doomsday", "major", "calc", "cards")

TRAINER_NAMES = {
    "observation": "What changed?", "deck": "Memorised deck", "doomsday": "Doomsday",
    "major": "Major system", "calc": "Mental arithmetic", "cards": "Card review",
}

# --------------------------------------------------------------- drills ----


@dataclass
class Drill:
    id: str
    title: str
    trainer: str
    module: str = ""
    minutes: int = 0
    params: dict = field(default_factory=dict)
    source: str = ""                  # the curriculum it came from, or "" built in

    def param(self, key, default=None):
        value = self.params.get(key, default)
        return default if value is None else value

    def int_param(self, key, default: int) -> int:
        try:
            return int(self.param(key, default))
        except (TypeError, ValueError):
            return default


BUILTIN = [
    Drill("obs-10s", "What changed? Ten seconds", "observation",
          minutes=5, params={"seconds": 10, "rounds": 8}),
    Drill("deck-full", "The memorised deck, 1–52", "deck",
          minutes=5, params={"from": 1, "to": 52, "direction": "both", "rounds": 20}),
    Drill("doom-year", "Doomsday: this year", "doomsday",
          minutes=5, params={"range": "year", "rounds": 10}),
    Drill("doom-full", "Doomsday: 1700–2099", "doomsday",
          minutes=5, params={"range": "full", "rounds": 10}),
    Drill("major-full", "Major system, 00–99", "major",
          minutes=5, params={"from": 0, "to": 99, "direction": "both", "rounds": 20}),
    Drill("calc-multiply", "Two digits times two digits", "calc",
          minutes=5, params={"kind": "multiply", "digits": 2, "rounds": 10}),
]


def curricula_files(where: brain.Places) -> list[Path]:
    folder = where.learning / "curricula"
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*/drills.yaml"))


def load_drills(where: brain.Places | None = None) -> list[Drill]:
    """Every drill the curricula define, or the built-in set if they define none."""
    where = where or brain.places()
    found: list[Drill] = []
    seen: set[str] = set()
    for file in curricula_files(where):
        try:
            data = yamlite.load(file.read_text(encoding="utf-8"))
        except OSError:
            continue
        if isinstance(data, dict):
            data = data.get("drills")
        if not isinstance(data, list):
            continue
        for entry in data:
            drill = _drill(entry, file.parent.name)
            if drill and drill.id not in seen:
                seen.add(drill.id)
                found.append(drill)
    return found or list(BUILTIN)


def _drill(entry, source: str) -> Drill | None:
    if not isinstance(entry, dict):
        return None
    trainer = str(entry.get("trainer") or "").strip().lower()
    ident = str(entry.get("id") or "").strip()
    if trainer not in TRAINERS or not ident:
        return None
    params = entry.get("params")
    try:
        minutes = int(entry.get("minutes") or 0)
    except (TypeError, ValueError):
        minutes = 0
    return Drill(id=ident, title=str(entry.get("title") or ident), trainer=trainer,
                 module=str(entry.get("module") or ""), minutes=minutes,
                 params=params if isinstance(params, dict) else {}, source=source)


# ---------------------------------------------------------------- the log --


@dataclass
class Result:
    when: datetime
    drill: str
    score: float
    ms: int
    n: int
    machine: str = ""
    extra: dict = field(default_factory=dict)


class DrillLog:
    """`learning/drills/*.jsonl`: every drill run, one line each, one file per machine."""

    def __init__(self, folder: Path, machine: str | None = None):
        self.folder = Path(folder)
        self.machine = machine or cards.machine_name()

    @property
    def own(self) -> Path:
        return self.folder / f"{self.machine}.jsonl"

    def append(self, drill: str, score: float, ms: int, n: int,
               at: datetime | None = None, **extra) -> Result:
        at = at or cards.now()
        record = {"t": at.isoformat(timespec="seconds"), "drill": drill,
                  "score": round(float(score), 3), "ms": int(ms), "n": int(n), **extra}
        self.folder.mkdir(parents=True, exist_ok=True)
        with self.own.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return Result(at, drill, float(score), int(ms), int(n), self.machine, extra)

    def read(self) -> list[Result]:
        out: list[Result] = []
        if not self.folder.is_dir():
            return out
        for file in sorted(self.folder.glob("*.jsonl")):
            try:
                lines = file.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    rec = json.loads(line)
                    when = cards.parse_time(rec["t"])
                    result = Result(when, str(rec["drill"]), float(rec.get("score", 0)),
                                    int(rec.get("ms", 0) or 0), int(rec.get("n", 0) or 0),
                                    file.stem,
                                    {k: v for k, v in rec.items()
                                     if k not in ("t", "drill", "score", "ms", "n")})
                except (ValueError, KeyError, TypeError):
                    continue
                if when is not None:
                    out.append(result)
        out.sort(key=lambda r: r.when)
        return out

    def since(self, start: datetime) -> list[Result]:
        return [r for r in self.read() if r.when >= start]


class Run:
    """One go at a drill: the marks so far, and the score they make."""

    def __init__(self, drill: Drill, rounds: int):
        self.drill = drill
        self.rounds = max(1, rounds)
        self.marks: list[tuple[bool, int]] = []

    def mark(self, correct: bool, ms: int) -> None:
        self.marks.append((bool(correct), int(ms)))

    @property
    def done(self) -> bool:
        return len(self.marks) >= self.rounds

    @property
    def right(self) -> int:
        return sum(1 for ok, _ in self.marks if ok)

    @property
    def score(self) -> float:
        return self.right / len(self.marks) if self.marks else 0.0

    @property
    def median_ms(self) -> int:
        return int(statistics.median(ms for _, ms in self.marks)) if self.marks else 0

    def save(self, log: DrillLog, **extra) -> Result | None:
        if not self.marks:
            return None
        return log.append(self.drill.id, self.score, self.median_ms, len(self.marks), **extra)


# ------------------------------------------------------------- doomsday ----

# Conway's numbering, which the method itself counts in: Sunday is 0
# ("Noneday"), Monday 1 ("Oneday") and so on round to Saturday 6.
WEEKDAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")

# The day in each month that falls on the year's doomsday. Four of them are
# easy to remember in pairs (4/4, 6/6, 8/8, 10/10, 12/12) and four as a rhyme
# ("I work 9 to 5 at the 7-Eleven": 5/9, 9/5, 7/11, 11/7).
MONTH_DOOMSDAY = {1: 3, 2: 28, 3: 14, 4: 4, 5: 9, 6: 6, 7: 11, 8: 8, 9: 5,
                  10: 10, 11: 7, 12: 12}
MONTHS = dates.MONTHS


def is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def weekday(day: date) -> int:
    """0 Sunday … 6 Saturday."""
    return (day.weekday() + 1) % 7


def century_anchor(year: int) -> int:
    """The anchor day of the century: 1700s Sunday, 1800s Friday, 1900s Wednesday, 2000s Tuesday."""
    return (5 * ((year // 100) % 4) + 2) % 7


def odd_plus_eleven(year: int) -> tuple[int, list[str]]:
    """How far the year's doomsday sits past the century's anchor, and the steps."""
    t = year % 100
    steps = [f"{t:02d}"]
    if t % 2:
        t += 11
        steps.append(f"odd, +11 → {t}")
    t //= 2
    steps.append(f"÷2 → {t}")
    if t % 2:
        t += 11
        steps.append(f"odd, +11 → {t}")
    rest = t % 7
    offset = (7 - rest) % 7
    steps.append(f"mod 7 → {rest}, 7 − {rest} → {offset}")
    return offset, steps


def year_doomsday(year: int) -> int:
    return (century_anchor(year) + odd_plus_eleven(year)[0]) % 7


def month_doomsday(year: int, month: int) -> int:
    if is_leap(year) and month in (1, 2):
        return MONTH_DOOMSDAY[month] + 1
    return MONTH_DOOMSDAY[month]


def explain(day: date) -> list[str]:
    """The method, worked through for one date — shown when an answer was wrong."""
    anchor = century_anchor(day.year)
    offset, steps = odd_plus_eleven(day.year)
    dd = (anchor + offset) % 7
    marker = month_doomsday(day.year, day.month)
    diff = day.day - marker
    lines = [
        f"Century: the {day.year // 100}00s start from {WEEKDAYS[anchor]} ({anchor}).",
        "Year: " + " → ".join(steps) + ".",
        f"Doomsday {day.year}: {anchor} + {offset} = {WEEKDAYS[dd]} ({dd}).",
        f"{MONTHS[day.month - 1]}: the {marker}{_suffix(marker)} is a doomsday"
        + (" (leap year)" if is_leap(day.year) and day.month in (1, 2) else "") + ".",
        f"{day.day} is {diff:+d} from it: {dd} {'+' if diff >= 0 else '−'} {abs(diff) % 7}"
        f" → {WEEKDAYS[weekday(day)]} ({weekday(day)}).",
    ]
    return lines


def _suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def doomsday_years(drill: Drill, today: date) -> tuple[int, int] | None:
    """The years a doomsday drill draws from; None means "this month"."""
    years = drill.param("years")
    if isinstance(years, list) and len(years) == 2:
        try:
            low, high = sorted((int(years[0]), int(years[1])))
            return max(1583, low), min(9999, high)
        except (TypeError, ValueError):
            pass
    span = str(drill.param("range", "year")).lower()
    if span == "month":
        return None
    return {"year": (today.year, today.year), "2000": (2000, 2099),
            "1900": (1900, 1999), "full": (1700, 2099)}.get(span, (today.year, today.year))


def random_date(drill: Drill, rng: random.Random, today: date) -> date:
    years = doomsday_years(drill, today)
    if years is None:
        first = today.replace(day=1)
        following = (first + timedelta(days=32)).replace(day=1)
        return first + timedelta(days=rng.randrange((following - first).days))
    start = date(years[0], 1, 1)
    end = date(years[1], 12, 31)
    return start + timedelta(days=rng.randrange((end - start).days + 1))


# --------------------------------------------------------- playing cards ---

SUITS = {"♠": "♠", "s": "♠", "spade": "♠", "spades": "♠", "pikk": "♠",
         "♥": "♥", "h": "♥", "heart": "♥", "hearts": "♥", "kor": "♥", "kőr": "♥",
         "♦": "♦", "d": "♦", "diamond": "♦", "diamonds": "♦", "karo": "♦", "káró": "♦",
         "♣": "♣", "c": "♣", "club": "♣", "clubs": "♣", "treff": "♣",
         "♤": "♠", "♡": "♥", "♢": "♦", "♧": "♣"}
RANKS = {"a": "A", "ace": "A", "1": "A", "asz": "A", "ász": "A",
         "k": "K", "king": "K", "kiraly": "K", "király": "K",
         "q": "Q", "queen": "Q", "dama": "Q", "dáma": "Q",
         "j": "J", "jack": "J", "bubi": "J",
         "t": "10", "10": "10", **{str(n): str(n) for n in range(2, 10)}}
RANK_ORDER = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUIT_COLOUR = {"♠": "black", "♣": "black", "♥": "red", "♦": "red"}


def parse_playing_card(text: str) -> str | None:
    """`QH`, `q♥`, `10d`, `Td`, `4 of clubs`, `pikk ász` → `Q♥`, `10♦`, `4♣`, `A♠`."""
    words = re.findall(r"[^\W\d_]+|\d+|[♠♥♦♣♤♡♢♧]", text.casefold().replace(" of ", " "))
    if len(words) == 1:
        m = re.fullmatch(r"(10|[2-9]|[atjqk1])([shdc])", words[0])
        if m:
            words = [m.group(1), m.group(2)]
    if len(words) != 2:
        return None
    for rank_word, suit_word in (words, words[::-1]):
        rank, suit = RANKS.get(rank_word), SUITS.get(suit_word)
        if rank and suit:
            return f"{rank}{suit}"
    return None


NEW_DECK_ORDER = ([f"{r}♠" for r in RANK_ORDER] + [f"{r}♦" for r in RANK_ORDER]
                  + [f"{r}♣" for r in reversed(RANK_ORDER)]
                  + [f"{r}♥" for r in reversed(RANK_ORDER)])


def deck_files(where: brain.Places) -> list[Path]:
    folder = where.learning / "decks"
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in (".md", ".markdown"))


def _pairs(file: Path, where: brain.Places):
    """`(front, back)` of every one-way card in a deck file; `:::` read once."""
    for card in cards.parse_file(file, where.home):
        if card.form in ("line", "both", "qa"):
            yield card.front.strip(), card.back.strip()


def load_stack(where: brain.Places | None = None, name: str | None = None) \
        -> tuple[str, dict[int, str]]:
    """The memorised deck: position → card, from a deck file of `N :: card` lines.

    `name` picks a file by its stem or deck; otherwise one with Mnemonica in
    its name wins, then any file that has the most positions. With none, new
    deck order — a stack to practise the trainer on, not one worth learning.
    """
    where = where or brain.places()
    best: tuple[str, dict[int, str]] | None = None
    for file in deck_files(where):
        if name and brain.key(name) not in (brain.key(file.stem), brain.key(file.name)):
            continue
        stack: dict[int, str] = {}
        # Only the cards that ask a position: the deck also asks the other way
        # (`4♣ in Mnemonica :: 1`), and those say nothing the forward ones do not.
        for front, back in _pairs(file, where):
            if re.fullmatch(r"\d{1,2}", front) and 1 <= int(front) <= 52:
                parsed = parse_playing_card(back)
                if parsed:
                    stack.setdefault(int(front), parsed)
        if len(stack) < 13:
            continue
        preferred = "mnemonica" in brain.key(file.stem)
        if best is None or preferred or len(stack) > len(best[1]):
            best = (file.stem, stack)
            if preferred:
                break
    if best:
        return best
    return "New deck order", {i + 1: card for i, card in enumerate(NEW_DECK_ORDER)}


# --------------------------------------------------------- major system ---


def load_major(where: brain.Places | None = None, name: str | None = None) \
        -> tuple[str, dict[str, list[str]]]:
    """The Major system deck: number (as written, `07` ≠ `7`) → its words.

    The deck switched on is the one used — a file with `cards: false` reads as
    empty, which is how the curriculum keeps the language not being learned
    out of the way. Empty when there is none; the trainer says so.
    """
    where = where or brain.places()
    for file in deck_files(where):
        stem = brain.key(file.stem)
        if name and brain.key(name) not in (stem, brain.key(file.name)):
            continue
        if not name and "major" not in stem:
            continue
        table: dict[str, list[str]] = {}
        for front, back in _pairs(file, where):
            for number, words in ((front, back), (back, front)):
                if re.fullmatch(r"\d{1,3}", number.strip()):
                    found = [w.strip() for w in re.split(r"[,/;|]| or | vagy ", words)
                             if w.strip() and not re.fullmatch(r"\d+", w.strip())]
                    if found:
                        bucket = table.setdefault(number.strip(), [])
                        bucket += [w for w in found if w not in bucket]
                        break
        if table:
            return file.stem, table
    return "", {}


def answer_key(text: str) -> str:
    """How a typed answer is compared: no case, no accents, no punctuation."""
    folded = cards.strip_accents(text.casefold())
    return " ".join(re.sub(r"[^\w\s]", " ", folded).split())


def major_numbers(table: dict[str, list[str]], low: int, high: int) -> list[str]:
    """The numbers in range, as the deck writes them (`00`–`09` count as 0–9 too)."""
    return [n for n in table if low <= int(n) <= high]


# ------------------------------------------------------------ arithmetic ---

CALC_KINDS = ("multiply", "square-5", "times-11", "cube-root", "add", "subtract", "square")


def calc_problem(kind: str, digits: int, rng: random.Random) -> tuple[str, int]:
    """One question of a kind, as `(prompt, answer)`."""
    digits = max(1, min(6, int(digits or 2)))
    low, high = 10 ** (digits - 1), 10 ** digits - 1
    if digits == 1:
        low = 2
    if kind == "square-5":
        tens = rng.randint(max(1, low // 10), max(1, high // 10))
        n = tens * 10 + 5
        return f"{n}²", n * n
    if kind == "times-11":
        n = rng.randint(low, high)
        return f"{n} × 11", n * 11
    if kind == "cube-root":
        n = rng.randint(2 if digits <= 1 else 10, 9 if digits <= 1 else 99)
        return f"∛{n ** 3}", n
    if kind == "square":
        n = rng.randint(low, high)
        return f"{n}²", n * n
    if kind == "add":
        a, b = rng.randint(low, high), rng.randint(low, high)
        return f"{a} + {b}", a + b
    if kind == "subtract":
        a, b = sorted((rng.randint(low, high), rng.randint(low, high)), reverse=True)
        return f"{a} − {b}", a - b
    a, b = rng.randint(low, high), rng.randint(low, high)
    return f"{a} × {b}", a * b


# ------------------------------------------------------------ observation --

CHANGES = ("mirror", "recolour", "clone")


@dataclass(frozen=True)
class Change:
    kind: str
    x: int
    y: int
    w: int
    h: int
    sx: int = 0                       # for a clone: where the patch came from
    sy: int = 0

    def contains(self, x: float, y: float, slack: float = 0.35) -> bool:
        """Whether a click lands on the change, give or take a fraction of its size."""
        mx, my = self.w * slack, self.h * slack
        return (self.x - mx <= x <= self.x + self.w + mx
                and self.y - my <= y <= self.y + self.h + my)


def plan_change(width: int, height: int, rng: random.Random, kinds=CHANGES,
                interest=None, tries: int = 8) -> Change:
    """Where to change a photograph, and how.

    The region is an eighth to a fifth of the shorter side, kept off the very
    edges. `interest(x, y, w, h)` scores a candidate — how much detail it has
    — because mirroring a patch of clear sky changes nothing anyone could
    see; the best of a few random candidates is taken.
    """
    kinds = [k for k in kinds if k in CHANGES] or list(CHANGES)
    side = max(8, int(min(width, height) * rng.uniform(0.12, 0.2)))
    w, h = side, max(8, int(side * rng.uniform(0.8, 1.25)))
    w, h = min(w, width // 2), min(h, height // 2)
    margin_x, margin_y = max(1, width // 20), max(1, height // 20)

    def candidate():
        return (rng.randint(margin_x, max(margin_x, width - w - margin_x)),
                rng.randint(margin_y, max(margin_y, height - h - margin_y)))

    spots = [candidate() for _ in range(max(1, tries))]
    if interest is not None:
        spots.sort(key=lambda spot: interest(spot[0], spot[1], w, h), reverse=True)
    x, y = spots[0]
    kind = rng.choice(kinds)
    sx = sy = 0
    if kind == "clone":
        # A patch from somewhere that does not overlap the target.
        for _ in range(20):
            sx, sy = candidate()
            if abs(sx - x) >= w or abs(sy - y) >= h:
                break
        else:
            kind = "mirror"
    return Change(kind, x, y, w, h, sx, sy)
