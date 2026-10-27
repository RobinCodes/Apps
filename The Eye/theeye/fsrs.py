"""FSRS, the scheduler: when to see a card again so that it is just about to be forgotten.

This is the Free Spaced Repetition Scheduler, version 5, as published by
Jarrett Ye and the open-spaced-repetition project — the model Anki has shipped
since 23.10. It replaces SM-2's fixed multipliers with a model of memory fitted
to hundreds of millions of real reviews, and for the same retention it asks for
noticeably fewer of them.

A memory is two numbers:

* **stability** `S` — how many days until recall falls to 90%;
* **difficulty** `D` — 1 to 10, how hard this card is *for you*.

and one derived one, **retrievability** `R` — the chance of recalling it right
now, which decays from 1 along a power curve as days pass. A review moves `S`
and `D` according to what you answered and how far `R` had fallen: a card
remembered when it was nearly lost gains far more stability than one reviewed
while it was still fresh, which is the whole reason spacing works.

Nothing here is stored. The state of a card is what you get by replaying its
reviews in order (`replay`), so the schedule is a pure function of the review
log — which is what lets every machine's log be merged without conflicts and
still give one answer.

Pure Python, no imports beyond the standard library; vendored into each app
that schedules, the way `theme.py` is.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

# The FSRS-5 defaults: fitted on the open dataset, good for anyone until there
# is enough of their own history to fit personal ones.
DEFAULT_W: tuple[float, ...] = (
    0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046,
    1.54575, 0.1192, 1.01925, 1.9395, 0.11, 0.29605, 2.2698, 0.2315,
    2.9898, 0.51655, 0.6621,
)

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
RATINGS = (AGAIN, HARD, GOOD, EASY)
RATING_NAMES = {AGAIN: "Again", HARD: "Hard", GOOD: "Good", EASY: "Easy"}

DECAY = -0.5
FACTOR = 0.9 ** (1 / DECAY) - 1          # 19/81: makes R(S, S) exactly 0.9

MAX_INTERVAL = 36500                     # a hundred years is "learned"
MIN_STABILITY = 0.01


@dataclass(frozen=True)
class Memory:
    """What FSRS knows about one card after its last review."""

    stability: float
    difficulty: float
    last: datetime                       # when it was last reviewed
    due: datetime                        # when it should be seen next
    reps: int = 0
    lapses: int = 0                      # times it was forgotten after being learned

    def retrievability(self, now: datetime) -> float:
        return retrievability(elapsed_days(self.last, now), self.stability)

    @property
    def interval(self) -> float:
        return (self.due - self.last).total_seconds() / 86400


def elapsed_days(since: datetime, now: datetime) -> float:
    return max(0.0, (now - since).total_seconds() / 86400)


def retrievability(days: float, stability: float) -> float:
    """The chance of recall `days` after a review, for a memory of this stability."""
    return (1 + FACTOR * days / max(stability, MIN_STABILITY)) ** DECAY


def interval_for(stability: float, retention: float = 0.9) -> float:
    """Days until retrievability falls to `retention` — the next review, unrounded."""
    return stability / FACTOR * (retention ** (1 / DECAY) - 1)


def _clamp_d(d: float) -> float:
    return min(10.0, max(1.0, d))


def initial_stability(rating: int, w=DEFAULT_W) -> float:
    return max(MIN_STABILITY, w[rating - 1])


def initial_difficulty(rating: int, w=DEFAULT_W) -> float:
    return _clamp_d(w[4] - math.exp(w[5] * (rating - 1)) + 1)


def next_difficulty(d: float, rating: int, w=DEFAULT_W) -> float:
    # The change is damped as D approaches 10 (FSRS-5's linear damping), then
    # pulled a little back towards the difficulty of a card first rated Easy,
    # so that one bad day does not mark a card hard for ever.
    delta = -w[6] * (rating - 3)
    damped = d + delta * (10 - d) / 9
    return _clamp_d(w[7] * initial_difficulty(EASY, w) + (1 - w[7]) * damped)


def stability_after_recall(d: float, s: float, r: float, rating: int,
                           w=DEFAULT_W) -> float:
    hard = w[15] if rating == HARD else 1.0
    easy = w[16] if rating == EASY else 1.0
    growth = (math.exp(w[8]) * (11 - d) * s ** -w[9]
              * (math.exp(w[10] * (1 - r)) - 1) * hard * easy)
    return max(MIN_STABILITY, s * (growth + 1))


def stability_after_lapse(d: float, s: float, r: float, w=DEFAULT_W) -> float:
    new = (w[11] * d ** -w[12] * ((s + 1) ** w[13] - 1)
           * math.exp(w[14] * (1 - r)))
    # Forgetting can never leave a memory *more* stable than it was.
    return max(MIN_STABILITY, min(new, s))


def stability_same_day(s: float, rating: int, w=DEFAULT_W) -> float:
    """A second look on the same day: short-term memory, not spacing."""
    return max(MIN_STABILITY, s * math.exp(w[17] * (rating - 3 + w[18])))


def _fuzzed_days(days: float, seed: str) -> int:
    """Round an interval, and spread it a little so a batch does not stay a batch.

    Twenty cards learned together would otherwise come due together for ever.
    The spread is deterministic — derived from the card and the review — so
    replaying the same log on another machine gives the same day.
    """
    if days < 2.5:
        return max(1, round(days))
    spread = max(1.0, days * 0.05)
    unit = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return max(1, min(MAX_INTERVAL, round(days - spread + 2 * spread * unit)))


def review(memory: Memory | None, rating: int, when: datetime, *,
           retention: float = 0.9, w=DEFAULT_W, seed: str = "") -> Memory:
    """The memory after answering `rating` at `when`. `None` is a new card."""
    if rating not in RATINGS:
        raise ValueError(f"rating must be 1-4, not {rating!r}")
    if memory is None:
        s = initial_stability(rating, w)
        d = initial_difficulty(rating, w)
        reps, lapses = 1, 0
    else:
        days = elapsed_days(memory.last, when)
        d = next_difficulty(memory.difficulty, rating, w)
        if days < 1:
            s = stability_same_day(memory.stability, rating, w)
        else:
            r = retrievability(days, memory.stability)
            if rating == AGAIN:
                s = stability_after_lapse(memory.difficulty, memory.stability, r, w)
            else:
                s = stability_after_recall(memory.difficulty, memory.stability,
                                           r, rating, w)
        reps = memory.reps + 1
        lapses = memory.lapses + (1 if rating == AGAIN and memory.reps > 0 else 0)
    if rating == AGAIN:
        # Forgotten cards come back tomorrow, whatever the model says: the
        # session itself shows them again before it ends.
        days_next = 1
    else:
        days_next = _fuzzed_days(interval_for(s, retention),
                                 f"{seed}|{when.isoformat()}|{rating}")
    return Memory(stability=s, difficulty=d, last=when,
                  due=_start_of_day(when) + timedelta(days=days_next),
                  reps=reps, lapses=lapses)


def _start_of_day(when: datetime) -> datetime:
    # Due dates are days, not instants: a card due "in 3 days" is due all of
    # that day, from the small hours, so a morning sheet includes it.
    return when.replace(hour=4, minute=0, second=0, microsecond=0) \
        if when.hour >= 4 else (when - timedelta(days=1)).replace(
            hour=4, minute=0, second=0, microsecond=0)


def replay(reviews, *, retention: float = 0.9, w=DEFAULT_W,
           seed: str = "") -> Memory | None:
    """Fold a card's reviews, in time order, into its present memory.

    `reviews` is any iterable of `(when, rating)`. Out-of-order input is
    sorted first, so two machines' logs can be concatenated in any order.
    """
    memory = None
    for when, rating in sorted(reviews, key=lambda item: item[0]):
        memory = review(memory, rating, when, retention=retention, w=w, seed=seed)
    return memory


def preview(memory: Memory | None, now: datetime, *, retention: float = 0.9,
            w=DEFAULT_W, seed: str = "") -> dict[int, float]:
    """For each rating, the days until the card would come back — for the buttons."""
    out = {}
    for rating in RATINGS:
        after = review(memory, rating, now, retention=retention, w=w, seed=seed)
        out[rating] = (after.due - now).total_seconds() / 86400
    return out


def describe_days(days: float) -> str:
    """`0.4` → "today", `1` → "1d", `45` → "1.5mo", `800` → "2.2y"."""
    if days < 0.5:
        return "today"
    if days < 30:
        return f"{round(days)}d"
    if days < 365:
        return f"{days / 30:.1f}mo".replace(".0mo", "mo")
    return f"{days / 365:.1f}y".replace(".0y", "y")
