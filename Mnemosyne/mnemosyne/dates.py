"""Dates in words, the same words whatever the locale says.

`strftime("%A")` asks the C library, and GTK sets the C library's locale from
the desktop's at startup — so on this machine the same sheet printed from the
window says *kedd, 27 október* and printed from the command line, where no
toolkit has touched the locale, says *Tuesday 27 October*. The rest of the
app is in English, so the dates are too, from these tables.
"""

from __future__ import annotations

from datetime import date

# Python's order: Monday is 0.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")


def weekday(day: date) -> str:
    return WEEKDAYS[day.weekday()]


def short_weekday(day: date) -> str:
    return WEEKDAYS[day.weekday()][:3]


def spoken(day: date, weekday_too: bool = True, year: bool = False) -> str:
    """`Tuesday 27 October`, `Tuesday 27 October 2026`, `27 October`."""
    text = f"{day.day} {MONTHS[day.month - 1]}"
    if year:
        text += f" {day.year}"
    return f"{weekday(day)} {text}" if weekday_too else text
