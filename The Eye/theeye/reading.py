"""Reading a page: the words on it, by Claude or by Tesseract.

A straightened page is a picture of handwriting, and the second brain wants
text — to search, to link, to rewrite into a note with the cards in it. There
are two readers, and they are not equals.

*Claude* reads handwriting, in Hungarian, with the mathematics in it, the way
a person would, and writes it out as Markdown with the formulas as `$…$`. It
is the reader to use whenever Claude Code is installed: `claude.Ask` hands it
the page image, and the answer comes back on a worker thread a few seconds to
a minute later.

*Tesseract* reads print well and handwriting poorly, but it is local, quick
and free, so it is the reader when Claude is not there or not wanted. It runs
as `tesseract page.png stdout -l hun+eng`, with `TESSDATA_PREFIX` pointed at
the brain's own `config/tessdata` when that exists — which is how Hungarian
reaches a machine whose package manager only installed English — and with
whichever of the two languages this machine actually has. What it returns is
kept as rough text, not as Markdown (see `filing.py`).

Either way, a page's first line is its header — `2026-10-27 · Dinamika · 14`
in the notebook's own convention (HANDBOOK §1.1) — and `header()` reads the
date, the topic and the page number back out of it, so the page can be filed
under the day of the lesson rather than the day it was photographed.
"""

from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import brain, claude

WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if WINDOWS else 0

LANGUAGES = ("hun", "eng")

# A page takes Tesseract a second or two; a stuck one should not hold the
# queue of pages behind it for ever.
TIMEOUT = 180

READERS = ("claude", "tesseract", "none")
NAMES = {"claude": "Claude", "tesseract": "Tesseract", "none": "nobody"}


class Unreadable(Exception):
    """The page could not be read, and the message says why in words."""


# ------------------------------------------------------------- Tesseract ----

def tesseract() -> str | None:
    """The tesseract command, or None.

    PATH first. Then beside the Python that is running, because on Windows
    MSYS2 puts tesseract.exe in the same bin folder as python.exe, and an
    app started from a shortcut has no MSYS2 on its PATH.
    """
    found = shutil.which("tesseract")
    if found:
        return found
    beside = Path(sys.executable).with_name("tesseract.exe" if WINDOWS else "tesseract")
    return str(beside) if beside.is_file() else None


def tessdata() -> Path | None:
    """The brain's own language data, when it has some."""
    folder = brain.places().brain / "config" / "tessdata"
    return folder if folder.is_dir() and any(folder.glob("*.traineddata")) else None


def environment() -> dict:
    env = dict(os.environ)
    data = tessdata()
    if data is not None:
        env["TESSDATA_PREFIX"] = str(data)
    return env


def languages(command=None) -> list[str]:
    """Which of hun and eng this Tesseract can read here, in that order."""
    argv = list(command) if command else [tesseract() or "tesseract"]
    try:
        done = subprocess.run(argv + ["--list-langs"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30,
                              env=environment(), creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    have = {line.strip() for line in (done.stdout + done.stderr).splitlines()}
    return [lang for lang in LANGUAGES if lang in have]


def ocr(image: str | Path, command=None, timeout: int = TIMEOUT) -> str:
    """The text Tesseract finds on a page image. Blocking.

    `command` replaces the tesseract binary, which is how the suite checks the
    call on a machine that has none. Raises `Unreadable` with something worth
    putting on screen when there is no Tesseract, no language for it, or it
    fails.
    """
    if command is None:
        found = tesseract()
        if found is None:
            raise Unreadable("Tesseract is not installed, so the page was filed "
                             "without its text — pacman -S tesseract "
                             "tesseract-data-hun, or mingw-w64-ucrt-x86_64-tesseract-ocr "
                             "on Windows")
        command = [found]
    langs = languages(command) or ["eng"]
    argv = list(command) + [str(image), "stdout", "-l", "+".join(langs)]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=environment(),
                              creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise Unreadable("Tesseract took too long over that page") from None
    except OSError as exc:
        raise Unreadable(f"Tesseract could not be started: {exc}") from None
    if done.returncode != 0:
        why = (done.stderr or "").strip().splitlines()
        raise Unreadable("Tesseract could not read the page"
                         + (f": {why[-1]}" if why else ""))
    return tidy_ocr(done.stdout)


def tidy_ocr(text: str) -> str:
    """Tesseract's output without its form feeds and its runs of blank lines."""
    text = text.replace("\f", "\n")
    lines = [line.rstrip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    return "\n".join(out).strip()


# ---------------------------------------------------------------- Claude ----

PROMPT = """Transcribe this photographed notebook page into Markdown.

It is a page of class notes, handwritten, usually in Hungarian, sometimes in English. Write out what is on the page — not a summary, not a correction, not a translation. Keep the language it is written in.

- Mathematics as LaTeX: $…$ inline, $$…$$ for displayed formulas.
- Headings only from ### downwards; lists as lists; a table as a Markdown table.
- The page's first line is usually a header: the date, the topic and the page number, like `2026-10-27 · Dinamika · 14`. Keep it as the first line, as written.
- A mark in the left margin (?, ★, → 14) goes at the start of the line it stands beside.
- A drawing becomes one line in italics saying what it shows, like *[ábra: erők egy lejtőn]*.
- A word you cannot read becomes [?].

Reply with the transcription and nothing else: no preamble, no code fence around it."""


def transcribe(image: str | Path, on_done, command=None) -> "claude.Ask":
    """Ask Claude to read a page; `on_done(ok, text_or_why)` from a worker thread.

    Read-only: the default permission mode, in a scratch folder of its own
    beside the image, so the only thing Claude is asked to touch is the page.
    """
    image = Path(image)

    def finished(ok, result, _session):
        if ok:
            on_done(True, tidy_markdown(result or ""))
        else:
            on_done(False, f"Claude could not read the page: {result}"
                    if result and result != "cancelled" else "cancelled")

    return claude.Ask(PROMPT, files=[image], cwd=image.parent, on_done=finished,
                      command=command).start()


def tidy_markdown(text: str) -> str:
    """Claude's transcription, ready to sit under a note's `## Pages`.

    A fence around the whole answer is taken off, and every heading is pushed
    down to at least `###`, so that nothing in a page can end the section the
    pages are kept in.
    """
    text = text.strip()
    fenced = re.match(r"^```[\w-]*\n(.*)\n```$", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    out = []
    inside = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            inside = not inside
        heading = None if inside else re.match(r"^(#{1,6})(\s+.*)$", line)
        if heading and len(heading.group(1)) < 3:
            line = "###" + heading.group(2)
        out.append(line.rstrip())
    return "\n".join(out).strip()


# ---------------------------------------------------------------- header ----

@dataclass(frozen=True)
class Header:
    day: datetime.date | None = None
    topic: str = ""
    page: str = ""

    def label(self) -> str:
        return " · ".join(bit for bit in (self.topic, self.page and f"p. {self.page}") if bit)


_DATE = re.compile(r"(\d{4})\s*[-.]\s*(\d{1,2})\s*[-.]\s*(\d{1,2})\.?")


def parse_day(text: str) -> datetime.date | None:
    """A date as the notebooks and the Order write one: `2026-10-27`, or the
    Hungarian `2026.10.27.` and `2026. 10. 27.`, which every tool here accepts."""
    m = _DATE.search(text or "")
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def header(text: str) -> Header:
    """The date, the topic and the page number from a page's header line.

    The first of the first three non-empty lines with a date in it. What
    follows the date, split on `·`, `|`, `/` or a run of spaces, is the topic
    and then the page number, if the last piece is a number.
    """
    lines = [line.strip().strip("#*_ ").strip() for line in (text or "").splitlines()]
    for line in [line for line in lines if line][:3]:
        m = _DATE.search(line)
        if not m:
            continue
        try:
            day = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        rest = (line[:m.start()] + " " + line[m.end():]).strip(" ·|-–—/,")
        pieces = [p.strip() for p in re.split(r"\s*[·|/•]\s*|\s{2,}", rest) if p.strip()]
        page = ""
        # A reader that lost the dots leaves "Dinamika 14": the number at the
        # end is still the page.
        if len(pieces) == 1:
            tail = re.match(r"^(.*?\S)\s+(\d{1,4})\.?$", pieces[0])
            if tail:
                pieces = [tail.group(1), tail.group(2)]
        if pieces:
            last = re.fullmatch(r"(?:p\.?|o\.?|old\.?|page|oldal)?\s*(\d{1,4})\.?",
                                pieces[-1], re.I)
            if last:
                page = last.group(1)
                pieces = pieces[:-1]
        return Header(day, " · ".join(pieces).strip(), page)
    return Header()


def default_reader() -> str:
    """Claude when Claude Code is here, Tesseract when it is, else nobody."""
    if claude.available():
        return "claude"
    return "tesseract" if tesseract() else "none"


def available(reader: str) -> bool:
    if reader == "claude":
        return claude.available()
    if reader == "tesseract":
        return tesseract() is not None
    return reader == "none"
