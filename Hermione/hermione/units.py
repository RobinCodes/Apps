"""Turning numbers into something readable.

Kept apart from the widgets so the tests can check it without a display. A
file manager shows more numbers than anything else in this repository — a
size, a count and a date on every row — so they are formatted in one place
and the columns are built to line up: even widths, tabular figures, and the
same thinning-out of detail `ls -l` does with old dates.
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


def human_items(files: int, folders: int) -> str:
    """What a folder holds, said the way a person would say it."""
    parts = []
    if folders:
        parts.append(f"{folders:,} folder" + ("s" if folders != 1 else ""))
    if files:
        parts.append(f"{files:,} file" + ("s" if files != 1 else ""))
    return ", ".join(parts) if parts else "empty"


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


def exact_time(stamp: float) -> str:
    """The whole timestamp, for the places that have room for it."""
    if not stamp:
        return "—"
    try:
        return time.strftime("%A %d %B %Y, %H:%M:%S", time.localtime(stamp))
    except (OSError, OverflowError, ValueError):
        return "—"


def human_duration(seconds: float) -> str:
    if seconds < 1:
        return "less than a second"
    if seconds < 60:
        return f"{seconds:.0f} seconds"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes} min {rest} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


def human_rate(byte_count: int, seconds: float, si: bool = False) -> str:
    """Throughput, for the progress line of a long copy."""
    if seconds <= 0:
        return ""
    return human_size(byte_count / seconds, si) + "/s"


def human_mode(mode: int) -> str:
    """Permission bits as `rwxr-xr-x`, the spelling everyone already reads."""
    bits = "rwxrwxrwx"
    out = []
    for index in range(9):
        out.append(bits[index] if mode & (0o400 >> index) else "-")
    # The three special bits ride on the execute positions, as in `ls -l`.
    if mode & 0o4000:
        out[2] = "s" if out[2] == "x" else "S"
    if mode & 0o2000:
        out[5] = "s" if out[5] == "x" else "S"
    if mode & 0o1000:
        out[8] = "t" if out[8] == "x" else "T"
    return "".join(out)
