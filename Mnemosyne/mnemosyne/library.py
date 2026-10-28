"""The brain's cards, scheduled — and the rules that make a day's queue a good one.

`cards.Deck` is the scheduler every app shares: the cards under the learning
roots, the review log, and FSRS replayed over it. This is what Mnemosyne adds
on top, all of it about *which* cards make up a day, and in what order:

* **A card on a printed sheet is being reviewed.** It was handed out this
  morning and is in a schoolbag being answered in pencil; if the screen asked
  it again this afternoon the paper would be answering a card that had moved
  on. So the screen leaves out whatever is on a sheet printed today and not
  typed back yet, and the new cards on it count against the day's ration.
* **Siblings wait for tomorrow.** The two directions of a `:::` card, and the
  clozes of one sentence, give each other away: asked on the same day, the
  second is read off the first. Only one of a family is in a day's queue, and
  none once one has been answered today (HANDBOOK.md §5.2).
* **A test date pulls a deck forward.** Until the date, that deck's new cards
  come before any others, and the day's ration rises as far as it must to meet
  them all three days before the test, so the last days are reviews, not
  first meetings.
* **The day is interleaved.** Due cards and new cards are each dealt across
  their decks in turn, so the queue and the sheet do not run Biology, then all
  of Italian, then all of Mathematics — mixing is what makes the question
  "which of these is it?" part of the recall (HANDBOOK.md §6.3).
* **Decks can be switched off** in learning.yaml, which `.tex` files need,
  having no front matter to say `cards: false` in.

Nothing here touches GTK, so the command line (`mnemosyne --due`) and the
tests use it exactly as the window does.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path

from . import brain, cards, fit, fsrs
from .config import Learning
from .paper import Sheet, Sheets

# A test's new cards are all met this many days before it.
TEST_MARGIN = 3
# A card forgotten this many times after it had been learned is a leech.
LEECH_LAPSES = 6


def study_day(at: datetime) -> date:
    """The day a moment belongs to for studying: days begin at 4 in the morning.

    The same rule FSRS uses for due dates, so a card answered at one in the
    morning counts towards the evening before, and a sheet printed then is
    still that evening's sheet.
    """
    return fsrs._start_of_day(at).date()


def family(card: cards.Card) -> tuple[str, int] | None:
    """What a card shares with its siblings, or None for a card that has none.

    Both directions of a `:::` line, and every cloze of one paragraph, are
    written on the same line of the same note.
    """
    if card.form in ("both", "both-r", "cloze"):
        return (card.path, card.line)
    return None


def switched_off(card: cards.Card, entries) -> bool:
    """Whether an `off:` entry covers a card: its deck, a deck above it, or a folder on its path."""
    deck = "/" + card.deck.strip("/") + "/"
    path = "/" + card.path
    for entry in entries:
        entry = entry.strip("/")
        if not entry:
            continue
        if deck.startswith("/" + entry + "/") or ("/" + entry + "/") in path \
                or ("/" + entry + "/") in deck:
            return True
    return False


def lapses(item: cards.Item) -> int:
    """Times a card was forgotten after it had been remembered on an earlier day."""
    count = 0
    remembered = False
    last_day = None
    for answer in item.answers:
        day = study_day(answer.when)
        later = last_day is not None and day > last_day
        if answer.rating == fsrs.AGAIN and remembered and later:
            count += 1
        elif answer.rating != fsrs.AGAIN and later:
            remembered = True
        last_day = day
    return count


def interleave(items: list[cards.Item]) -> list[cards.Item]:
    """Deal items across their decks in turn, keeping each deck's own order.

    The deck that comes first is the one whose first card came first, so the
    most urgent card of the day is still the first one asked.
    """
    by_deck: dict[str, list[cards.Item]] = {}
    for item in items:
        by_deck.setdefault(item.card.deck, []).append(item)
    lanes = list(by_deck.values())
    out: list[cards.Item] = []
    index = 0
    while lanes:
        lane = lanes[index % len(lanes)]
        out.append(lane.pop(0))
        if not lane:
            lanes.remove(lane)
            if not lanes:
                break
            index %= len(lanes)
        else:
            index = (index + 1) % len(lanes)
    return out


class Library:
    def __init__(self, where: brain.Places | None = None,
                 learning: Learning | None = None, machine: str | None = None):
        self.places = where or brain.places()
        self.learning = learning or Learning(self.places)
        self.machine = machine or cards.machine_name()
        self.log = cards.ReviewLog(self.places.learning / "reviews", self.machine)
        self.sheets = Sheets(self.places.learning / "sheets", self.machine)
        self.deck = self._new_deck()
        self.off: dict[str, int] = {}          # deck → cards switched off in it
        self.loaded = False

    # -- building ---------------------------------------------------------
    def _new_deck(self) -> cards.Deck:
        weights = self.learning["weights"]
        return cards.Deck(
            self.places.home, list(self.learning["roots"]), self.log,
            retention=float(self.learning["desired_retention"]),
            new_per_day=int(self.learning["new_per_day"]),
            extensions=tuple(self.learning["extensions"]),
            w=tuple(weights) if weights else fsrs.DEFAULT_W)

    def exists(self) -> bool:
        """Whether there is a brain on this machine to learn from and write to."""
        return self.places.exists()

    def refresh(self) -> "Library":
        """Rescan the notes and replay the log. Safe on a worker thread.

        A new `Deck` is built and swapped in at the end, so a window reading
        the old one while this runs never sees half a scan. Cards in decks
        switched off are taken out of it, and so is any card with nothing on
        one side — an environment written but not filled in yet is a promise,
        not a question.
        """
        self.learning.load()
        deck = self._new_deck()
        deck.refresh()
        entries = list(self.learning["off"])
        off: dict[str, int] = {}
        kept = {}
        for cid, item in deck.items.items():
            card = item.card
            if not card.front.strip() or not card.back.strip():
                continue
            if entries and switched_off(card, entries):
                off[card.deck] = off.get(card.deck, 0) + 1
                continue
            kept[cid] = item
        deck.items = kept
        self.off = off
        self.deck = deck
        self.loaded = True
        return self

    def all_decks(self) -> list[str]:
        """Every deck there is, switched on or off."""
        return sorted(set(self.deck.decks()) | set(self.off))

    def deck_is_off(self, name: str) -> bool:
        probe = cards.Card(id="", form="line", front="", back="", path="", line=0,
                           deck=name)
        return switched_off(probe, self.learning["off"])

    def set_deck(self, name: str, on: bool) -> bool:
        """Switch a deck on or off, in learning.yaml.

        Off adds the deck. On takes away the deck, anything under it, and any
        entry above it — and puts back, one by one, the other decks that entry
        was switching off, so turning one subject back on does not turn on
        its neighbours.
        """
        entries = [e.strip("/") for e in self.learning["off"]]
        inside = [e for e in entries if e == name or e.startswith(name + "/")]
        above = [e for e in entries if name.startswith(e + "/")]
        keep = [e for e in entries if e not in inside and e not in above]
        if not on:
            keep.append(name)
        else:
            for parent in above:
                for other in self.all_decks():
                    if (other.startswith(parent + "/") and other != name
                            and not other.startswith(name + "/")
                            and not name.startswith(other + "/")):
                        keep.append(other)
        return self.learning.set("off", keep)

    # -- test dates -------------------------------------------------------
    def tests(self, at: datetime | None = None) -> dict[str, date]:
        """The test dates still ahead, deck → day, soonest first."""
        today = study_day(at or cards.now())
        ahead = {deck: day for deck, day in self.learning["tests"].items() if day >= today}
        return dict(sorted(ahead.items(), key=lambda pair: pair[1]))

    def set_test(self, deck: str, day: date | None) -> bool:
        tests = dict(self.learning["tests"])
        if day is None:
            tests.pop(deck, None)
        else:
            tests[deck] = day
        return self.learning.set("tests", tests)

    def _in(self, item: cards.Item, deck: str | None) -> bool:
        name = item.card.deck
        return deck is None or name == deck or name.startswith(deck + "/")

    def _test_ration(self, at: datetime) -> dict[str, int]:
        """For each deck with a test ahead, how many of its new cards to meet today."""
        today = study_day(at)
        start = fsrs._start_of_day(at)
        ration = {}
        for deck, day in self.tests(at).items():
            waiting = sum(1 for i in self.deck.items.values() if self._in(i, deck) and i.new)
            met_today = sum(1 for i in self.deck.items.values()
                            if self._in(i, deck) and i.answers and i.answers[0].when >= start)
            days = max(1, (day - today).days - TEST_MARGIN)
            ration[deck] = max(0, math.ceil((waiting + met_today) / days) - met_today)
        return ration

    # -- what is on paper -------------------------------------------------
    def open_sheets(self, at: datetime | None = None) -> list[Sheet]:
        """Sheets printed on the study day of `at` and not typed back yet."""
        day = study_day(at or cards.now())
        return [s for s in self.sheets.open() if s.day == day]

    def on_paper(self, at: datetime | None = None) -> set[str]:
        return {row.card for sheet in self.open_sheets(at) for row in sheet.rows}

    # -- what to study ----------------------------------------------------
    def today(self, at: datetime | None = None, deck: str | None = None,
              new_limit: int | None = None, paper: set[str] | None = None) -> list[cards.Item]:
        """The day's queue: due cards first, then new ones; mixed, with no siblings.

        `paper` is `on_paper(at)` when the caller already has it — the Decks page
        asks this once per deck, and reading the sheets once is enough.
        """
        at = at or cards.now()
        paper = self.on_paper(at) if paper is None else paper
        start = fsrs._start_of_day(at)
        items = self.deck.items

        # Families already met today — on screen, or on a sheet still out.
        taken = set()
        for item in items.values():
            if item.answers and item.answers[-1].when >= start:
                key = family(item.card)
                if key:
                    taken.add(key)
        for cid in paper:
            if cid in items:
                key = family(items[cid].card)
                if key:
                    taken.add(key)

        def admit(item) -> bool:
            if item.card.id in paper:
                return False
            key = family(item.card)
            if key is None:
                return True
            if key in taken:
                return False
            taken.add(key)
            return True

        due = [i for i in self.deck.queue(at, deck=deck, new_limit=0) if admit(i)]

        # The ration of new cards, raised by any test that needs more.
        tests = self._test_ration(at)
        allowance = self.deck.new_per_day if new_limit is None else new_limit
        allowance = max(allowance, sum(tests.values()))
        new_on_paper = sum(1 for cid in paper if cid in items and items[cid].new)
        remaining = max(0, allowance - self.deck.introduced_today(at) - new_on_paper)

        waiting = sorted((i for i in items.values() if i.new and self._in(i, deck)),
                         key=lambda i: (i.card.path, i.card.line))
        fresh: list[cards.Item] = []
        for test_deck, count in tests.items():
            for item in waiting:
                if count <= 0 or len(fresh) >= remaining:
                    break
                if self._in(item, test_deck) and item not in fresh and admit(item):
                    fresh.append(item)
                    count -= 1
        rest = []
        for item in waiting:
            if len(fresh) + len(rest) >= remaining:
                break
            if item not in fresh and admit(item):
                rest.append(item)
        return interleave(due) + interleave(fresh) + interleave(rest)

    def counts(self, at: datetime | None = None, deck: str | None = None,
               paper: set[str] | None = None) -> dict:
        """`due`, `new` (today's ration of them) and `paper`, for a badge or a page."""
        at = at or cards.now()
        paper = self.on_paper(at) if paper is None else paper
        queue = self.today(at, deck, paper=paper)
        return {"due": sum(1 for i in queue if not i.new),
                "new": sum(1 for i in queue if i.new),
                "paper": len(paper)}

    # -- leeches and the fitted schedule ----------------------------------
    def leeches(self) -> list[tuple[cards.Item, int]]:
        """Cards forgotten again and again: worst first. They want rewriting."""
        found = [(item, lapses(item)) for item in self.deck.items.values()]
        found = [(item, n) for item, n in found if n >= LEECH_LAPSES]
        return sorted(found, key=lambda pair: (-pair[1], pair[0].card.deck))

    def histories(self) -> list[list[tuple[datetime, int]]]:
        return [[(a.when, a.rating) for a in item.answers]
                for item in self.deck.items.values() if len(item.answers) > 1]

    def fit_if_due(self, force: bool = False) -> "fit.Fit | None":
        """Fit FSRS to the log when it has grown enough, and keep the result.

        Run on a worker. Returns the fit when one was made and written to
        learning.yaml, None when it was not due (or could not be written).
        """
        histories = self.histories()
        spaced = fit.spaced_reviews(histories)
        if not force and not fit.due_for_a_fit(spaced, int(self.learning["weights_reviews"])):
            return None
        result = fit.fit(histories)
        if not result.reviews:
            return None
        if not (self.learning.set("weights", list(result.weights))
                and self.learning.set("weights_reviews", result.reviews)):
            return None
        return result

    def for_note(self, path: Path | str, everything: bool = False) -> list[cards.Item]:
        """A single note's cards: the due ones, then every new one, in its order.

        No ration of new cards: asking to study one note right after writing
        it is asking to meet all of it. `everything` adds the cards that are
        not due yet as well, for going over a note before a test.

        A note outside the learning roots still works — it is read on the spot
        and its history taken from the log — because Notedown can open a file
        anywhere and "study this note" should not depend on where it is.
        """
        file = Path(path)
        rel = self.places.relative(file)
        found = [item for item in self.deck.items.values() if item.card.path == rel]
        if not found and file.is_file():
            history: dict[str, list[cards.Answer]] = {}
            parsed = cards.parse_file(file, self.places.home)
            wanted = {c.id for c in parsed}
            for answer in self.log.read():
                if answer.card in wanted:
                    history.setdefault(answer.card, []).append(answer)
            for card in parsed:
                answers = history.get(card.id, [])
                memory = fsrs.replay(((a.when, a.rating) for a in answers),
                                     retention=self.deck.retention, w=self.deck.w,
                                     seed=card.id)
                found.append(cards.Item(card, memory, answers))
        at = cards.now()
        found.sort(key=lambda item: item.card.line)
        due = sorted((i for i in found if i.due(at)),
                     key=lambda i: i.memory.retrievability(at))
        fresh = [i for i in found if i.new]
        rest = [i for i in found if not i.new and not i.due(at)] if everything else []
        return due + fresh + rest

    def note_size(self, path: Path | str) -> int:
        rel = self.places.relative(Path(path))
        return sum(1 for item in self.deck.items.values() if item.card.path == rel)

    def item(self, card_id: str) -> cards.Item | None:
        return self.deck.items.get(card_id)

    # -- answering --------------------------------------------------------
    def answer(self, item: cards.Item, rating: int, *, ms: int = 0,
               via: str = "screen", at: datetime | None = None) -> cards.Item:
        return self.deck.answer(item, rating, ms=ms, via=via, at=at)

    def print_today(self, at: datetime | None = None, include_new: bool = True,
                    deck: str | None = None) -> Sheet | None:
        """Put today's queue on a new sheet and record it. None when there is nothing."""
        at = at or cards.now()
        items = self.today(at, deck)
        if not include_new:
            items = [i for i in items if not i.new]
        if not items:
            return None
        leeches = {item.card.id for item, _count in self.leeches()}
        return self.sheets.make(items, at, study_day(at), leeches)

    def enter_sheet(self, sheet: Sheet, ratings: list[int | None],
                    at: datetime | None = None) -> int:
        """Write a sheet's ticks into the log as paper answers. Returns how many.

        A row left blank was not answered and is not written. The answers are
        dated when the sheet was answered: now, if it is typed back the same
        day; that evening, if it is typed back later — a sheet answered on
        Monday and entered on Wednesday was still a Monday review, and dating
        it Wednesday would tell FSRS the cards had held two days longer than
        they had.
        """
        now = at or cards.now()
        when = answered_at(sheet, now)
        written = 0
        for row, rating in zip(sheet.rows, ratings):
            if rating not in fsrs.RATINGS:
                continue
            item = self.deck.items.get(row.card)
            if item is not None:
                self.deck.answer(item, rating, via="paper", at=when)
            else:
                # Edited or gone since it was printed. The answer is kept with
                # what the card said, so the log can re-attach it to the card
                # it became — the same thing an edit on screen relies on.
                self.log.append(cards.Answer(
                    when=when, card=row.card, rating=rating, src=row.src,
                    front=row.front, back=cards.back_hash(row.back), via="paper",
                    machine=self.log.machine))
            written += 1
        sheet.entered = now
        sheet.ratings = list(ratings[:len(sheet.rows)])
        self.sheets.save(sheet)
        return written


def answered_at(sheet: Sheet, now: datetime) -> datetime:
    """When a sheet's answers count as given: see `Library.enter_sheet`."""
    if sheet.day >= study_day(now):
        return now
    evening = now.replace(year=sheet.day.year, month=sheet.day.month,
                          day=sheet.day.day, hour=20, minute=0, second=0,
                          microsecond=0)
    return min(evening, now)
