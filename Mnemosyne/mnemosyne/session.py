"""One sitting at the screen: the cards in order, and what became of them.

A session is the queue it was started with, worked through one card at a
time. Three rules, each the answer to something that goes wrong otherwise:

* **A card answered Again comes back before the session ends**, a few cards
  later — far enough that the answer is not still on the screen in your head,
  near enough that it is fixed while the failure is fresh. FSRS schedules it
  for tomorrow as well; the same-day look is extra, and FSRS counts it as a
  short-term review rather than as spacing.
* **The time on a card is measured from the question to the rating**, and
  capped: a card left on the screen while you made tea did not take twenty
  minutes to recall.
* **Skipping is allowed once per card.** It goes to the back of the queue; a
  second skip is an answer you are avoiding, and the button says so.

Nothing here touches GTK. The window draws whatever `current` is.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from . import cards, fsrs

AGAIN_GAP = 4                 # cards between a lapse and its second look
MAX_MS = 120_000              # longer than this on one card was not recall


@dataclass
class Summary:
    answers: int = 0
    cards: int = 0
    new: int = 0
    ratings: dict = field(default_factory=lambda: {1: 0, 2: 0, 3: 0, 4: 0})
    seconds: float = 0.0
    first_again: int = 0

    @property
    def recalled(self) -> float:
        """The share of first answers that were not Again — the session's retention."""
        return (self.cards - self.first_again) / self.cards if self.cards else 0.0


class Session:
    def __init__(self, library, items, *, title: str = "Today",
                 again_gap: int = AGAIN_GAP, clock=time.monotonic):
        self.library = library
        self.title = title
        self.pending: list[cards.Item] = list(items)
        self.total = len(self.pending)
        self.again_gap = again_gap
        self.clock = clock
        self.started = clock()
        self.shown_at: float | None = None
        self.revealed = False
        self.log: list[tuple[cards.Item, int, int]] = []
        self._skipped: set[str] = set()
        self._first: dict[str, int] = {}
        self._was_new = {item.card.id for item in self.pending if item.new}

    # -- where we are -----------------------------------------------------
    @property
    def current(self) -> cards.Item | None:
        return self.pending[0] if self.pending else None

    @property
    def finished(self) -> bool:
        return not self.pending

    @property
    def remaining(self) -> int:
        return len(self.pending)

    @property
    def done(self) -> int:
        return len(self.log)

    def show(self) -> None:
        """The current card has just been put on the screen."""
        self.shown_at = self.clock()
        self.revealed = False

    def reveal(self) -> None:
        self.revealed = True

    def can_skip(self) -> bool:
        item = self.current
        return item is not None and item.card.id not in self._skipped and len(self.pending) > 1

    # -- what the buttons say ---------------------------------------------
    def preview(self, at: datetime | None = None) -> dict[int, float]:
        item = self.current
        if item is None:
            return {}
        deck = self.library.deck
        return fsrs.preview(item.memory, at or cards.now(), retention=deck.retention,
                            w=deck.w, seed=item.card.id)

    # -- moving on --------------------------------------------------------
    def answer(self, rating: int, at: datetime | None = None) -> cards.Item:
        item = self.pending.pop(0)
        ms = 0
        if self.shown_at is not None:
            ms = int(min(MAX_MS, (self.clock() - self.shown_at) * 1000))
        self.library.answer(item, rating, ms=ms, at=at)
        self.log.append((item, rating, ms))
        self._first.setdefault(item.card.id, rating)
        if rating == fsrs.AGAIN:
            self.pending.insert(min(self.again_gap, len(self.pending)), item)
        self.shown_at = None
        self.revealed = False
        return item

    def skip(self) -> bool:
        if not self.can_skip():
            return False
        item = self.pending.pop(0)
        self._skipped.add(item.card.id)
        self.pending.append(item)
        self.shown_at = None
        self.revealed = False
        return True

    def summary(self) -> Summary:
        out = Summary()
        out.answers = len(self.log)
        out.cards = len(self._first)
        out.new = len(self._was_new & set(self._first))
        for _item, rating, _ms in self.log:
            out.ratings[rating] += 1
        out.first_again = sum(1 for r in self._first.values() if r == fsrs.AGAIN)
        out.seconds = sum(ms for _i, _r, ms in self.log) / 1000
        return out
