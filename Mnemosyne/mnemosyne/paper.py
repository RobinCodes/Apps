"""Paper: the review sheet, the ticks typed back, and cut-out flashcards.

The method says learning happens on paper (SYSTEM.md §6), so a printed sheet
is not an export — it is the main way a day's reviews get done, and the
software's job is to make the round trip cost nothing:

    print ──► fold in half ──► answer the left from memory ──► unfold, check,
    tick 1-4 ──► type the ticks back, a digit a row ──► the log, via "paper"

**What is on each sheet** is recorded in the brain, under
`learning/sheets/<id>.json`, the moment it is printed. The sheet can then be
typed back on any machine — printed at home on the big one, answered on the
tram, entered on the school laptop — and the id in its corner is what says
which sheet it is. Ids are the date and a letter of the machine, `261027-a1`,
so two machines printing on the same day before they sync cannot collide.

**The sheet** is A4, folded down the middle: questions on the left half, each
with its row number, its context and four tick boxes against the fold;
answers on the right half with the same numbers. Folded, only the questions
show. The boxes carry their digits faintly inside, so typing back is reading
along a column.

**Flashcards** are a deck drawn ten to a page, fronts on one page and backs on
the next with the columns swapped, so a printer that turns pages on the long
edge puts each back behind its own front.

Drawn with cairo and Pango — the same Pango markup as the screen, so a
formula looks the same on paper as in the window — and nothing else.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import cards, dates

# --------------------------------------------------------------- sheets ----


@dataclass
class Row:
    card: str
    front: str
    back: str
    context: str = ""
    src: str = ""
    deck: str = ""
    new: bool = False
    leech: bool = False


@dataclass
class Sheet:
    id: str
    day: date
    made: datetime
    machine: str
    rows: list[Row] = field(default_factory=list)
    entered: datetime | None = None
    ratings: list[int | None] | None = None

    def to_json(self) -> str:
        record = {
            "id": self.id, "day": self.day.isoformat(),
            "made": self.made.isoformat(timespec="seconds"),
            "machine": self.machine,
            "rows": [{k: v for k, v in row.__dict__.items() if v not in ("", False)}
                     for row in self.rows],
        }
        if self.entered:
            record["entered"] = self.entered.isoformat(timespec="seconds")
            record["ratings"] = self.ratings or []
        return json.dumps(record, ensure_ascii=False, indent=1)

    @classmethod
    def from_json(cls, text: str) -> "Sheet | None":
        try:
            data = json.loads(text)
            rows = [Row(card=str(r["card"]), front=str(r.get("front", "")),
                        back=str(r.get("back", "")), context=str(r.get("context", "")),
                        src=str(r.get("src", "")), deck=str(r.get("deck", "")),
                        new=bool(r.get("new", False)), leech=bool(r.get("leech", False)))
                    for r in data.get("rows", [])]
            made = cards.parse_time(data["made"])
            if made is None:
                return None
            return cls(id=str(data["id"]), day=date.fromisoformat(data["day"]),
                       made=made, machine=str(data.get("machine", "")), rows=rows,
                       entered=cards.parse_time(data["entered"]) if data.get("entered") else None,
                       ratings=data.get("ratings"))
        except (ValueError, KeyError, TypeError):
            return None

    @property
    def title(self) -> str:
        return dates.spoken(self.day)


class Sheets:
    """`learning/sheets/*.json`: one small file per printed sheet, in the brain."""

    def __init__(self, folder: Path, machine: str):
        self.folder = Path(folder)
        self.machine = machine

    def all(self) -> list[Sheet]:
        found = []
        if not self.folder.is_dir():
            return found
        for file in self.folder.glob("*.json"):
            try:
                sheet = Sheet.from_json(file.read_text(encoding="utf-8"))
            except OSError:
                continue
            if sheet:
                found.append(sheet)
        found.sort(key=lambda s: s.made, reverse=True)
        return found

    def open(self) -> list[Sheet]:
        return [s for s in self.all() if s.entered is None]

    def get(self, sheet_id: str) -> Sheet | None:
        try:
            return Sheet.from_json((self.folder / f"{sheet_id}.json").read_text(encoding="utf-8"))
        except OSError:
            return None

    def new_id(self, day: date) -> str:
        letter = (re.sub(r"[^a-z]", "", self.machine.lower()) or "x")[0]
        stem = f"{day:%y%m%d}-{letter}"
        taken = {p.stem for p in self.folder.glob(f"{stem}*.json")} if self.folder.is_dir() else set()
        n = 1
        while f"{stem}{n}" in taken:
            n += 1
        return f"{stem}{n}"

    def make(self, items, at: datetime, day: date, leeches=frozenset()) -> Sheet:
        sheet = Sheet(id=self.new_id(day), day=day, made=at, machine=self.machine)
        for item in items:
            card = item.card
            sheet.rows.append(Row(card=card.id, front=card.front, back=card.back,
                                  context=" › ".join(card.context), src=card.path,
                                  deck=card.deck, new=item.new,
                                  leech=card.id in leeches))
        self.save(sheet)
        return sheet

    def save(self, sheet: Sheet) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{sheet.id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(sheet.to_json() + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)


# ------------------------------------------------------------ the ticks ----

# What a row's tick is typed as. A digit is the rating; anything that reads as
# "nothing here" leaves the row unanswered.
BLANKS = set("0 -._·x")


def parse_ticks(text: str, rows: int) -> list[int | None]:
    """The digits typed for a sheet, one per row, as ratings or None.

    `3341` → Good, Good, Hard, Again. `0`, `-`, `.`, `x` or a space mark a
    row that was not answered, and anything else typed is ignored, so a stray
    letter costs nothing.
    """
    out: list[int | None] = []
    for char in text:
        if len(out) >= rows:
            break
        if char in "1234":
            out.append(int(char))
        elif char in BLANKS:
            out.append(None)
    return out


def tally(ratings) -> dict:
    counts = {1: 0, 2: 0, 3: 0, 4: 0, None: 0}
    for rating in ratings:
        counts[rating if rating in (1, 2, 3, 4) else None] += 1
    return counts


# -------------------------------------------------------------- drawing ----

# A4 in points.
PAGE_W, PAGE_H = 595.28, 841.89
MARGIN = 30.0
FOLD = PAGE_W / 2
GUTTER = 12.0                 # either side of the fold
NUMBER_W = 17.0
BOX = 10.0
BOX_GAP = 3.5
BOXES_W = 4 * BOX + 3 * BOX_GAP
ROW_PAD = 4.0                 # above and below the text of a row
INK = (0.12, 0.12, 0.14)
GREY = (0.48, 0.48, 0.52)
FAINT = (0.78, 0.78, 0.81)


def _surface(path: Path):
    """An A4 PDF surface. PDF 1.4, because a school's printer driver may be old."""
    import cairo                                            # noqa: PLC0415

    surface = cairo.PDFSurface(str(path), PAGE_W, PAGE_H)
    surface.restrict_to_version(cairo.PDF_VERSION_1_4)
    return surface


def _pango():
    import gi                                               # noqa: PLC0415

    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo             # noqa: PLC0415

    return Pango, PangoCairo


def _layout(ctx, markup: str, width: float, size: float, *, family="Sans",
            lines: int = 0, align="left"):
    Pango, PangoCairo = _pango()
    layout = PangoCairo.create_layout(ctx)
    desc = Pango.FontDescription.from_string(family)
    desc.set_size(int(size * Pango.SCALE))
    layout.set_font_description(desc)
    layout.set_width(int(width * Pango.SCALE))
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    if lines:
        layout.set_height(-lines)
        layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_alignment({"left": Pango.Alignment.LEFT, "center": Pango.Alignment.CENTER,
                          "right": Pango.Alignment.RIGHT}[align])
    layout.set_markup(markup, -1)
    return layout


def _height(layout) -> float:
    _, logical = layout.get_pixel_extents()
    return float(logical.height)


def _show(ctx, layout, x, y, colour=INK):
    _, PangoCairo = _pango()
    ctx.set_source_rgb(*colour)
    ctx.move_to(x, y)
    PangoCairo.show_layout(ctx, layout)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_sheet(sheet: Sheet, path: str | Path, font_size: float = 9.5,
                 home: Path | None = None) -> Path:
    """Draw a review sheet to a PDF at `path`. Returns the path.

    With `home`, a picture in a card (`![](…)`) is found beside its note and
    drawn under the text, scaled to fit the column and no taller than a
    thumbnail: on a sheet a picture is a reminder of the one in the note,
    not a replacement for it.
    """
    import cairo                                            # noqa: PLC0415

    from .cardtext import to_markup                         # noqa: PLC0415

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    surface = _surface(path)
    surface.set_metadata(cairo.PDF_METADATA_TITLE, f"Mnemosyne sheet {sheet.id}")
    surface.set_metadata(cairo.PDF_METADATA_CREATOR, "Mnemosyne")
    ctx = cairo.Context(surface)

    left_x = MARGIN + NUMBER_W
    left_w = FOLD - GUTTER - BOXES_W - 8 - left_x
    right_x = FOLD + GUTTER + NUMBER_W
    right_w = PAGE_W - MARGIN - right_x
    small = font_size * 0.78

    # Measure every row once, then lay the rows out into pages, so that the
    # header can say "page 1 of 3" before page 1 is drawn.
    measured = []
    for index, row in enumerate(sheet.rows, start=1):
        context = " · ".join(part for part in (
            row.context, "new" if row.new else "",
            "leech: rewrite it" if row.leech else "") if part)
        ctx_layout = (_layout(ctx, _esc(context), left_w, small, lines=1)
                      if context else None)
        question = _layout(ctx, to_markup(row.front), left_w, font_size, lines=9)
        answer = _layout(ctx, to_markup(row.back), right_w, font_size, lines=14)
        top = (_height(ctx_layout) + 1.5) if ctx_layout else 0.0
        q_pics = _pictures(row.front, row.src, home, left_w)
        a_pics = _pictures(row.back, row.src, home, right_w)
        q_h = _height(question) + sum(h + 3 for _s, _w, h in q_pics)
        a_h = _height(answer) + sum(h + 3 for _s, _w, h in a_pics)
        height = top + max(q_h, a_h, BOX) + 2 * ROW_PAD
        measured.append((index, row, ctx_layout, (question, q_pics), (answer, a_pics),
                         top, height))

    header_h = _sheet_header(ctx, sheet, font_size, draw=False)
    footer_h = 22.0
    usable = PAGE_H - MARGIN - header_h - footer_h - MARGIN
    pages: list[list] = [[]]
    used = 0.0
    for entry in measured:
        if pages[-1] and used + entry[-1] > usable:
            pages.append([])
            used = 0.0
        pages[-1].append(entry)
        used += entry[-1]

    for number, page in enumerate(pages, start=1):
        _sheet_header(ctx, sheet, font_size)
        y = MARGIN + header_h
        top_of_rows = y
        for index, row, ctx_layout, question, answer, top, height in page:
            pad = ROW_PAD
            line = y + pad + top            # where the question and the answer start
            # Row numbers on both halves, so a folded sheet still lines up.
            for x in (MARGIN - 3, FOLD + GUTTER - 3):
                num = _layout(ctx, f"<b>{index}</b>", NUMBER_W - 3, font_size * 0.9,
                              align="right")
                _show(ctx, num, x, line, GREY)
            if ctx_layout:
                _show(ctx, ctx_layout, left_x, y + pad, GREY)
            _show_with_pictures(ctx, *question, left_x, line)
            _show_with_pictures(ctx, *answer, right_x, line)
            _tick_boxes(ctx, FOLD - GUTTER - BOXES_W, line + 0.5)
            y += height
            ctx.set_source_rgb(*FAINT)
            ctx.set_line_width(0.5)
            ctx.move_to(MARGIN, y)
            ctx.line_to(PAGE_W - MARGIN, y)
            ctx.stroke()
        # The fold.
        ctx.set_source_rgb(*GREY)
        ctx.set_line_width(0.6)
        ctx.set_dash([3.0, 3.0])
        ctx.move_to(FOLD, top_of_rows - 6)
        ctx.line_to(FOLD, PAGE_H - MARGIN - footer_h + 4)
        ctx.stroke()
        ctx.set_dash([])
        _sheet_footer(ctx, sheet, number, len(pages), font_size)
        ctx.show_page()
    surface.finish()
    return path


def _pictures(text: str, src: str, home: Path | None, width: float,
              max_h: float = 64.0) -> list:
    """The pictures in a card's text as `(cairo surface, width, height)` in points."""
    if home is None or "![" not in text:
        return []
    from .cardtext import image_paths                       # noqa: PLC0415

    out = []
    for file in image_paths(text, home / src, home / "SecondBrain", home)[:3]:
        surface = _png_surface(file)
        if surface is None:
            continue
        w, h = surface.get_width(), surface.get_height()
        scale = min(width / w, max_h / h)
        out.append((surface, w * scale, h * scale))
    return out


def _png_surface(file: Path):
    """A picture as a cairo surface, decoded small enough to print sharply at
    thumbnail size and no larger, whatever format it was in."""
    import io                                               # noqa: PLC0415

    import cairo                                            # noqa: PLC0415
    import gi                                               # noqa: PLC0415

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib               # noqa: PLC0415

    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(file), 600, 600, True)
        pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
        ok, data = pixbuf.save_to_bufferv("png", [], [])
        return cairo.ImageSurface.create_from_png(io.BytesIO(data)) if ok else None
    except (GLib.Error, cairo.Error, OSError):
        return None


def _show_with_pictures(ctx, layout, pictures, x, y):
    _show(ctx, layout, x, y)
    y += _height(layout) + 3
    for surface, w, h in pictures:
        ctx.save()
        ctx.translate(x, y)
        ctx.scale(w / surface.get_width(), h / surface.get_height())
        ctx.set_source_surface(surface, 0, 0)
        ctx.paint()
        ctx.restore()
        y += h + 3


def _sheet_header(ctx, sheet, font_size, draw=True) -> float:
    """The title, the date, how to use it, and the digits over the boxes.

    Returns its height, so that the rows can be paginated before any page is
    drawn; called with `draw=False` it only measures.
    """
    when = dates.spoken(sheet.day, year=True)
    title = _layout(ctx, f"<b>Mnemosyne</b>  ·  sheet <b>{sheet.id}</b>",
                    FOLD - MARGIN, font_size * 1.25, lines=1)
    right = _layout(ctx, _esc(f"{when}  ·  {len(sheet.rows)} cards"),
                    PAGE_W - MARGIN - FOLD, font_size, lines=1, align="right")
    legend = _layout(
        ctx, _esc("Fold on the dashed line. Answer from memory, unfold, check, tick one:"
                  "   1 Again  ·  2 Hard  ·  3 Good  ·  4 Easy"),
        PAGE_W - 2 * MARGIN, font_size * 0.8, lines=2)
    y_legend = MARGIN + _height(title) + 3
    y_digits = y_legend + _height(legend) + 5
    digit_h = font_size * 0.75 * 1.5
    if draw:
        _show(ctx, title, MARGIN, MARGIN)
        _show(ctx, right, FOLD, MARGIN + (_height(title) - _height(right)) / 2)
        _show(ctx, legend, MARGIN, y_legend, GREY)
        x0 = FOLD - GUTTER - BOXES_W
        for i in range(4):
            digit = _layout(ctx, f"<b>{i + 1}</b>", BOX, font_size * 0.75, align="center")
            _show(ctx, digit, x0 + i * (BOX + BOX_GAP), y_digits, GREY)
    return y_digits + digit_h + 4 - MARGIN


def _sheet_footer(ctx, sheet, page, pages, font_size):
    text = (f"{sheet.id}  ·  type the ticks back in Mnemosyne → Paper → Enter results"
            + (f"  ·  {page}/{pages}" if pages > 1 else ""))
    footer = _layout(ctx, _esc(text), PAGE_W - 2 * MARGIN, font_size * 0.75, align="center")
    _show(ctx, footer, MARGIN, PAGE_H - MARGIN - 10, GREY)


def _tick_boxes(ctx, x, y):
    for i in range(4):
        bx = x + i * (BOX + BOX_GAP)
        ctx.set_source_rgb(*GREY)
        ctx.set_line_width(0.7)
        ctx.rectangle(bx, y, BOX, BOX)
        ctx.stroke()
        digit = _layout(ctx, str(i + 1), BOX, 5.2, align="center")
        _show(ctx, digit, bx, y + (BOX - _height(digit)) / 2, FAINT)


# ----------------------------------------------------------- flashcards ----

FLASH_COLS, FLASH_ROWS = 2, 5


def render_flashcards(title: str, card_list, path: str | Path) -> Path:
    """A deck as cut-out cards: fronts, then backs mirrored for long-edge duplex."""
    import cairo                                            # noqa: PLC0415

    from .cardtext import to_markup                         # noqa: PLC0415

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    surface = _surface(path)
    surface.set_metadata(cairo.PDF_METADATA_TITLE, f"Mnemosyne flashcards: {title}")
    surface.set_metadata(cairo.PDF_METADATA_CREATOR, "Mnemosyne")
    ctx = cairo.Context(surface)
    cell_w = (PAGE_W - 2 * MARGIN) / FLASH_COLS
    cell_h = (PAGE_H - 2 * MARGIN) / FLASH_ROWS
    per_page = FLASH_COLS * FLASH_ROWS
    card_list = list(card_list)
    for start in range(0, max(1, len(card_list)), per_page):
        batch = card_list[start:start + per_page]
        for side in ("front", "back"):
            _cut_marks(ctx, cell_w, cell_h)
            for index, card in enumerate(batch):
                row, col = divmod(index, FLASH_COLS)
                if side == "back":
                    col = FLASH_COLS - 1 - col
                x = MARGIN + col * cell_w
                y = MARGIN + row * cell_h
                text = card.front if side == "front" else card.back
                _flash_cell(ctx, x, y, cell_w, cell_h, to_markup(text),
                            " › ".join(card.context) if side == "front" else "",
                            card.deck if side == "front" else f"{start + index + 1}")
            ctx.show_page()
    surface.finish()
    return path


def _cut_marks(ctx, cell_w, cell_h):
    ctx.set_source_rgb(*FAINT)
    ctx.set_line_width(0.4)
    ctx.set_dash([2.0, 3.0])
    for col in range(FLASH_COLS + 1):
        x = MARGIN + col * cell_w
        ctx.move_to(x, MARGIN - 8)
        ctx.line_to(x, PAGE_H - MARGIN + 8)
    for row in range(FLASH_ROWS + 1):
        y = MARGIN + row * cell_h
        ctx.move_to(MARGIN - 8, y)
        ctx.line_to(PAGE_W - MARGIN + 8, y)
    ctx.stroke()
    ctx.set_dash([])


def _flash_cell(ctx, x, y, w, h, markup, context, corner):
    pad = 12.0
    inner_w = w - 2 * pad
    room = h - 2 * pad - 14
    # The largest size the text fits at, down to a floor that is still legible.
    size = 15.0
    layout = _layout(ctx, markup, inner_w, size, align="center")
    while _height(layout) > room and size > 6.5:
        size -= 0.75
        layout = _layout(ctx, markup, inner_w, size, align="center")
    if _height(layout) > room:
        layout = _layout(ctx, markup, inner_w, size, align="center",
                         lines=max(1, int(room / (size * 1.3))))
    _show(ctx, layout, x + pad, y + pad + 7 + (room - _height(layout)) / 2)
    if context:
        top = _layout(ctx, _esc(context), inner_w, 6.5, lines=1, align="center")
        _show(ctx, top, x + pad, y + 6, GREY)
    if corner:
        bottom = _layout(ctx, _esc(corner), inner_w, 6.0, lines=1, align="right")
        _show(ctx, bottom, x + pad, y + h - 14, FAINT)


# --------------------------------------------------------------- the week --


def render_week(summary: dict, path: str | Path, font_size: float = 10.0) -> Path:
    """The weekly review's page: the figures, the tests, the leeches to rewrite.

    `summary` is `stats.for_paper()`: plain values, so drawing it needs nothing
    from the library. Leeches are listed with their note and line, because the
    point of the list is to go and rewrite them with the sheet beside you.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    surface = _surface(path)
    import cairo                                            # noqa: PLC0415

    surface.set_metadata(cairo.PDF_METADATA_TITLE, f"Mnemosyne week to {summary['day']}")
    surface.set_metadata(cairo.PDF_METADATA_CREATOR, "Mnemosyne")
    ctx = cairo.Context(surface)
    width = PAGE_W - 2 * MARGIN
    y = MARGIN

    def line(markup, size=font_size, colour=INK, gap=4.0, lines=0):
        nonlocal y
        if y > PAGE_H - MARGIN - 30:
            ctx.show_page()
            y = MARGIN
        layout = _layout(ctx, markup, width, size, lines=lines)
        _show(ctx, layout, MARGIN, y, colour)
        y += _height(layout) + gap

    line(f"<b>Mnemosyne</b>  ·  the week to {_esc(summary['day'])}", font_size * 1.4, gap=10)
    for title, value in summary["figures"]:
        line(f"<b>{_esc(title)}</b>   {_esc(value)}")
    y += 8
    if summary["tests"]:
        line("<b>Tests</b>", font_size * 1.15, gap=6)
        for text in summary["tests"]:
            line(_esc(text), colour=INK)
        y += 8
    line(f"<b>Leeches to rewrite</b> ({len(summary['leeches'])})", font_size * 1.15, gap=6)
    if not summary["leeches"]:
        line("None this week.", colour=GREY)
    for front, back, where, count in summary["leeches"]:
        line(f"☐  <b>{_esc(front)}</b> → {_esc(back)}", lines=3, gap=1)
        line(f"      {_esc(where)} · forgotten {count} times", font_size * 0.8, GREY, gap=6)
    y += 8
    if summary["slipping"]:
        line("<b>Slipping this week</b>", font_size * 1.15, gap=6)
        for front, where in summary["slipping"]:
            line(f"·  {_esc(front)}   <span foreground='#77777f'>{_esc(where)}</span>",
                 lines=2)
    ctx.show_page()
    surface.finish()
    return path
