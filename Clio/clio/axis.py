"""The time axis: where a day is on the screen, and which marks it carries at this zoom.

One continuous scale from a few pixels per millennium to a couple of hundred
pixels per day, so a board can be looked at as the Árpád house and as one
week of it without changing anything but the zoom. Days are the unit (see
`timelines.days_from_civil`), so the years before the common era are on the
same line as the rest, with no year nought between 1 BC and AD 1.

The marks are chosen for the zoom: the smallest step whose labels stand at
least `LABEL_PX` apart is the one labelled, and the next smaller one is drawn
as unlabelled ticks if those would still be distinguishable.

No GTK here; the suite checks it without a display.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .timelines import When, astronomical, civil_from_days, days_from_civil, written

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
YEAR = 365.2425
LABEL_PX = 84
TICK_PX = 9
MIN_PPD = 0.00004           # about ten thousand years across a narrow window
MAX_PPD = 240.0             # a day as wide as a sentence
LIMIT = 4_000_000.0         # about eleven thousand years either way: as far as a view runs

# Steps, smallest first: (kind, size). Years are counted as written.
STEPS = (("day", 1), ("day", 7), ("month", 1), ("month", 3), ("month", 6),
         ("year", 1), ("year", 2), ("year", 5), ("year", 10), ("year", 20), ("year", 50),
         ("year", 100), ("year", 200), ("year", 500), ("year", 1000), ("year", 2000), ("year", 5000), ("year", 10000))


def step_days(step) -> float:
    kind, size = step
    return size * (1 if kind == "day" else YEAR / 12 if kind == "month" else YEAR)


def year_label(year_written: int) -> str:
    return f"{-year_written} BC" if year_written < 0 else str(year_written)


@dataclass
class Tick:
    day: float
    label: str
    major: bool


@dataclass
class Axis:
    centre: float = 0.0        # the day in the middle of the view
    ppd: float = 1.0           # pixels per day
    width: float = 800.0

    def x(self, day: float) -> float:
        return self.width / 2 + (day - self.centre) * self.ppd

    def day(self, x: float) -> float:
        return self.centre + (x - self.width / 2) / self.ppd

    def span(self) -> tuple[float, float]:
        return self.day(0), self.day(self.width)

    def zoom(self, factor: float, around_x: float | None = None) -> None:
        around_x = self.width / 2 if around_x is None else around_x
        anchor = self.day(around_x)
        self.ppd = max(MIN_PPD, min(MAX_PPD, self.ppd * factor))
        self.centre = _within(anchor - (around_x - self.width / 2) / self.ppd)

    def pan(self, pixels: float) -> None:
        """Move what is on screen by a number of pixels."""
        self.centre = _within(self.centre - pixels / self.ppd)

    def show(self, first: float, last: float, margin: float = 0.06) -> None:
        """Fit the days `first` to `last` in the width, with a margin each side."""
        length = max(last - first, 1.0)
        self.ppd = max(MIN_PPD, min(MAX_PPD, self.width / (length * (1 + 2 * margin))))
        self.centre = _within((first + last) / 2)

    def jump(self, when: When) -> None:
        """Centre a date, at a zoom that shows it as precisely as it was written."""
        around = {"year": 4 * YEAR, "month": 8 * 31, "day": 6 * 7}[when.precision]
        self.ppd = max(MIN_PPD, min(MAX_PPD, self.width / around))
        self.centre = _within(when.middle())

    def precision(self) -> str:
        """How precise a date clicked on the axis can sensibly be at this zoom."""
        return "day" if self.ppd >= 3 else "month" if self.ppd >= 0.12 else "year"

    def steps(self):
        """(labelled step, unlabelled step or None) for this zoom."""
        major = next((s for s in STEPS if step_days(s) * self.ppd >= LABEL_PX), STEPS[-1])
        index = STEPS.index(major)
        minor = None
        for candidate in reversed(STEPS[:index]):
            if step_days(candidate) * self.ppd >= TICK_PX and _divides(candidate, major):
                minor = candidate
                break
        return major, minor

    def ticks(self) -> list[Tick]:
        first, last = self.span()
        major, minor = self.steps()
        out = [Tick(day, label, True) for day, label in _marks(major, first, last)]
        if minor is not None:
            taken = {round(t.day) for t in out}
            out += [Tick(day, "", False) for day, _ in _marks(minor, first, last)
                    if round(day) not in taken]
        return sorted(out, key=lambda t: t.day)

    def ages(self, born: When) -> list[tuple[float, int]]:
        """(day, age) at the birthdays worth marking at this zoom, from `born` on.

        The same rhythm as the labelled years where those are in years — every
        fifth birthday beside every fifth year — and every birthday once the
        axis is showing months or days.
        """
        major, _ = self.steps()
        every = major[1] if major[0] == "year" else 1
        first, last = self.span()
        year, month, day = civil_from_days(born.first_day())
        age = max(0, math.floor((first - born.first_day()) / YEAR) - 1)
        age -= age % every
        out = []
        while age <= 400:
            when = _birthday(year + age, month, day)
            if when > last:
                break
            if when >= first:
                out.append((when, age))
            age += every
        return out


def age_at(born: When, day: float) -> int:
    """How old someone born then is on a day: birthdays passed, never less than nought."""
    year, month, date = civil_from_days(born.first_day())
    age = max(0, math.floor((day - born.first_day()) / YEAR) - 1)
    while _birthday(year + age + 1, month, date) <= day:
        age += 1
    return age


def _within(day: float) -> float:
    return max(-LIMIT, min(LIMIT, day))


def _divides(small, big) -> bool:
    if small[0] == big[0]:
        return big[1] % small[1] == 0
    if small[0] == "month" and big[0] == "year":
        return 12 % small[1] == 0
    return small[0] == "day" and small[1] == 1


def _birthday(year: int, month: int, day: int) -> int:
    # 29 February is a birthday on the 1st of March in the years without one.
    if month == 2 and day == 29 and days_from_civil(year, 3, 1) - days_from_civil(year, 2, 1) == 28:
        return days_from_civil(year, 3, 1)
    return days_from_civil(year, month, day)


def _marks(step, first: float, last: float):
    """(day, label) at every step between two days."""
    kind, size = step
    if kind == "year":
        start = written(civil_from_days(int(math.floor(first)))[0])
        number = math.floor(start / size) * size
        count = 0
        while count < 2000:
            # There is no year nought; every 100 years is counted to AD 1 in its
            # place, except every single year, where AD 1 comes round by itself.
            if number == 0 and size == 1:
                number += size
                continue
            year = number if number != 0 else 1
            day = days_from_civil(astronomical(year), 1, 1)
            if day > last:
                break
            if day >= first:
                yield day, year_label(year)
            number += size
            count += 1
        return
    if kind == "month":
        year, month, _ = civil_from_days(int(math.floor(first)))
        month = ((month - 1) // size) * size + 1
        while True:
            day = days_from_civil(year, month, 1)
            if day > last:
                break
            if day >= first:
                label = MONTHS[month - 1]
                if month == 1 or size >= 6:
                    label += f" {year_label(written(year))}"
                yield day, label
            month += size
            if month > 12:
                year, month = year + 1, month - 12
        return
    day = math.floor(first)
    if size == 7:
        day -= (day + 3) % 7                  # Mondays: 1970-01-01 was a Thursday
    while day <= last:
        if day >= first:
            _y, m, d = civil_from_days(day)
            yield day, (f"{d} {MONTHS[m - 1]}" if d == 1 or size == 7 else str(d))
        day += size
