"""The week, from the log: what was done, what held, and what is slipping.

SYSTEM.md's weekly review (§4.3) asks Mnemosyne three questions on a Sunday —
what was learned, what is slipping, which decks are due — and this answers
them from the same log the schedule is replayed from. Nothing is counted
anywhere else, so the numbers cannot disagree with the schedule.

**Retention** is the honest one: of the reviews this week of cards that had
already been seen on an earlier day, the share not answered Again. A first
meeting with a new card, or a second look the same afternoon, says nothing
about memory over time and is left out, which is why this number is usually
lower than a session's own and why it is the one to watch against the
retention FSRS aims for.

**Slipping** is the cards that were known and then were not: answered Again
this week after having been remembered before, most lapses first. Those are
the cards worth rewriting — a card that keeps lapsing is usually asking two
things at once, or asking something that was never understood.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import cards, dates, fsrs
from .library import Library, study_day


@dataclass
class Day:
    day: date
    reviews: int = 0
    again: int = 0
    paper: int = 0
    ms: int = 0


@dataclass
class DeckWeek:
    deck: str
    reviews: int = 0
    held: int = 0
    tested: int = 0
    due: int = 0
    cards: int = 0

    @property
    def retention(self) -> float | None:
        return self.held / self.tested if self.tested else None


@dataclass
class Week:
    days: list[Day] = field(default_factory=list)
    reviews: int = 0
    held: int = 0
    tested: int = 0
    minutes: float = 0.0
    streak: int = 0
    new_cards: int = 0
    slipping: list[tuple[cards.Item, int]] = field(default_factory=list)
    decks: list[DeckWeek] = field(default_factory=list)

    @property
    def retention(self) -> float | None:
        return self.held / self.tested if self.tested else None


def week(library: Library, at: datetime | None = None, days: int = 7) -> Week:
    at = at or cards.now()
    today = study_day(at)
    first = today - timedelta(days=days - 1)
    out = Week(days=[Day(first + timedelta(days=i)) for i in range(days)])
    by_day = {d.day: d for d in out.days}
    per_deck: dict[str, DeckWeek] = {}
    active_days: set[date] = set()

    for item in library.deck.items.values():
        deck = per_deck.setdefault(item.card.deck, DeckWeek(item.card.deck))
        deck.cards += 1
        if item.due(at):
            deck.due += 1
        lapses = 0
        previous_day: date | None = None
        remembered = False
        for index, answer in enumerate(item.answers):
            day = study_day(answer.when)
            active_days.add(day)
            in_window = first <= day <= today
            if in_window:
                bucket = by_day[day]
                bucket.reviews += 1
                bucket.ms += answer.ms
                if answer.rating == fsrs.AGAIN:
                    bucket.again += 1
                if answer.via == "paper":
                    bucket.paper += 1
                out.reviews += 1
                deck.reviews += 1
                if index == 0:
                    out.new_cards += 1
                if previous_day is not None and previous_day < day:
                    held = answer.rating != fsrs.AGAIN
                    out.tested += 1
                    out.held += held
                    deck.tested += 1
                    deck.held += held
                    if not held and remembered:
                        lapses += 1
            if answer.rating != fsrs.AGAIN and previous_day is not None and previous_day < day:
                remembered = True
            previous_day = day
        if lapses:
            out.slipping.append((item, lapses))

    out.minutes = sum(d.ms for d in out.days) / 60000
    out.slipping.sort(key=lambda pair: (-pair[1], pair[0].card.deck, pair[0].card.front))
    out.decks = sorted((d for d in per_deck.values() if d.reviews or d.due),
                       key=lambda d: (-d.reviews, -d.due, d.deck))
    # The streak: consecutive study days up to today (or yesterday, if today
    # has not started yet) with at least one answer.
    cursor = today if today in active_days else today - timedelta(days=1)
    while cursor in active_days:
        out.streak += 1
        cursor -= timedelta(days=1)
    return out


def measured(library: Library, at: datetime | None = None, days: int = 30) -> tuple[int, int]:
    """`(held, tested)` over the last `days`: recall on spaced reviews, the calibration.

    The same rule as the week's retention — only reviews a day or more after
    the card's previous one — over a longer window, because a week of a few
    dozen reviews is too few to tell 88% from 92%.
    """
    at = at or cards.now()
    first = study_day(at) - timedelta(days=days - 1)
    held = tested = 0
    for item in library.deck.items.values():
        previous = None
        for answer in item.answers:
            day = study_day(answer.when)
            if previous is not None and previous < day and day >= first:
                tested += 1
                held += answer.rating != fsrs.AGAIN
            previous = day
    return held, tested


def waiting(library: Library, deck: str | None = None) -> int:
    """New cards not met yet."""
    return sum(1 for item in library.deck.items.values()
               if item.new and (deck is None or item.card.deck == deck
                                or item.card.deck.startswith(deck + "/")))


def due_ahead(library: Library, at: datetime | None = None, days: int = 7) -> list[int]:
    """How many cards fall due on each of the next `days` study days — the week's load."""
    at = at or cards.now()
    today = study_day(at)
    counts = [0] * days
    for item in library.deck.items.values():
        if item.memory is None:
            continue
        offset = (study_day(item.memory.due) - today).days
        if offset <= 0:
            counts[0] += 1
        elif offset < days:
            counts[offset] += 1
    return counts


def for_paper(library: Library, at: datetime | None = None) -> dict:
    """The week as plain values, for `paper.render_week` to draw."""
    at = at or cards.now()
    today = study_day(at)
    current = week(library, at)
    aim = float(library.learning["desired_retention"])
    held, tested = measured(library, at, 30)
    figures = [
        ("Reviews", f"{current.reviews}" + (f", {current.minutes:.0f} min on screen"
                                            if current.minutes >= 1 else "")),
        ("Retention this week", (f"{current.retention:.0%}" if current.retention is not None
                                 else "—") + f" (the aim is {aim:.0%})"),
        ("Retention, 30 days", f"{held / tested:.0%} of {tested}" if tested else "—"),
        ("New cards met", str(current.new_cards)),
        ("New cards waiting", str(waiting(library))),
        ("Streak", f"{current.streak} days"),
    ]
    tests = []
    for deck, day in library.tests(at).items():
        left = (day - today).days
        tests.append(f"{deck}: {dates.spoken(day)}, in {left} days — "
                     f"{waiting(library, deck)} new waiting")
    leeches = [(item.card.front[:140], item.card.back[:140],
                f"{item.card.path}:{item.card.line}", count)
               for item, count in library.leeches()[:25]]
    slipping = [(item.card.front[:120], f"{item.card.path}:{item.card.line}")
                for item, _count in current.slipping[:15]]
    return {"day": dates.spoken(today, year=True), "figures": figures,
            "tests": tests, "leeches": leeches, "slipping": slipping}
