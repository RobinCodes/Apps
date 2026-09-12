"""Turning numbers into something readable.

Kept apart from the widgets so the tests can check it without a display, and
because getting these wrong is the quickest way to make a disk tool look like
it is lying: a figure that says 1.0 GB next to a figure that says 1073741824
is two answers to one question.

Sizes are powers of 1024 by default and spelled KB/MB/GB, which is what every
disk tool the user has met does — `lsblk` calls this machine's SSD 119.2G and
means 2^37 bytes. The SI spelling is there for anyone who would rather the
labels meant what the SI says they mean.
"""

from __future__ import annotations

import time

BINARY_UNITS = ("bytes", "KB", "MB", "GB", "TB", "PB")
SI_UNITS = ("bytes", "kB", "MB", "GB", "TB", "PB")


def human_size(count: int | float, si: bool = False) -> str:
    """A byte count, at three significant figures or thereabouts.

    The width of the answer is deliberately even — 9.99 GB, 99.9 GB, 999 GB —
    so a column of these lines up on the decimal point without a monospace
    font doing the work.
    """
    step = 1000.0 if si else 1024.0
    units = SI_UNITS if si else BINARY_UNITS
    value = float(count)
    negative = value < 0
    value = abs(value)

    index = 0
    while value >= step and index < len(units) - 1:
        value /= step
        index += 1

    if index == 0:
        text = "1 byte" if value == 1 else f"{int(value)} {units[0]}"
    elif value < 10:
        text = f"{value:.2f} {units[index]}"
    elif value < 100:
        text = f"{value:.1f} {units[index]}"
    else:
        text = f"{value:.0f} {units[index]}"
    return "-" + text if negative else text


def human_count(count: int) -> str:
    """A plain integer with thousands separators."""
    return f"{count:,}"


def human_percent(part: int | float, whole: int | float) -> str:
    """A share of a total. Never 0.0% for something that is actually there."""
    if not whole:
        return "—"
    share = 100.0 * part / whole
    if share >= 99.95:
        return "100%"
    if share >= 10:
        return f"{share:.1f}%"
    if share >= 0.05:
        return f"{share:.2f}%"
    return "<0.05%" if part else "0%"


def human_time(stamp: float) -> str:
    """A modification time, as short as it can be and still unambiguous.

    Today's files get a clock, this year's get a day and month, older ones get
    the year — the same thinning-out `ls -l` does, for the same reason: the
    detail you want about a file from 2019 is not what minute it was written.
    """
    if not stamp:
        return ""
    try:
        when = time.localtime(stamp)
        now = time.localtime()
    except (OSError, OverflowError, ValueError):
        return ""
    if when.tm_year != now.tm_year:
        return time.strftime("%d %b %Y", when)
    if when.tm_yday == now.tm_yday:
        return time.strftime("%H:%M", when)
    return time.strftime("%d %b %H:%M", when)


def human_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes} min {rest} s"


def human_rate(count: int, seconds: float) -> str:
    if seconds <= 0:
        return ""
    rate = count / seconds
    if rate >= 1_000_000:
        return f"{rate / 1_000_000:.1f}M files/s"
    if rate >= 1000:
        return f"{rate / 1000:.0f}k files/s"
    return f"{rate:.0f} files/s"
