#!/usr/bin/env python3
"""Timeline boards (SYSTEM.md §11): dates as precise as they are written, and files kept.

`timelines.py` is vendored into every app that joins the brain, and a board
is edited by Clio, by the Anamnesis viewer and by hand. Two promises are
checked here. Dates mean what they say — a year is the whole year, `-0044` is
44 BC, February has a 29th only when it does. And a board comes back as it
was read, changed only where it was changed.

No GTK is needed; the disk is a scratch folder named by `ORDER_HOME`.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from house import brain, timelines  # noqa: E402
from house.timelines import Board, Item, When  # noqa: E402

SCRATCH = Path(tempfile.mkdtemp(prefix="house-timelines-"))

BOARD = """---
kind: timeline
title: My life
born: 2011-10-10        # optional: the axis also shows age
colours:                # categories → colours (any CSS colour)
  family: "#c0623b"
  school: "#4f7cac"
  projects: "#6b8f4e"
  inner: "#8a6bb0"
---

Text before the first section describes the board.

## Events
- 2011-10-10 · Born {family} !
- ~2017-09 · First day at school {school}
  Indented lines under an event are its notes; [[links]] work in them.
- 2021-03 · First message on Discord {projects}

## Spans
- 2017-09 .. 2025-06 · Primary school {school}
- 2025-09 .. · Szent István Gimnázium {school}
"""


def days(y, m=1, d=1):
    return timelines.days_from_civil(y, m, d)


# ------------------------------------------------------------------ dates --

def test_a_date_is_as_precise_as_it_is_written():
    assert When.parse("2017") == When(2017)
    assert When.parse("2017-09") == When(2017, 9)
    assert When.parse("2011-10-10") == When(2011, 10, 10)
    assert When.parse("~2017-09") == When(2017, 9, approx=True)
    assert When.parse("-0044-03-15") == When(-44, 3, 15)
    assert [When.parse(t).precision for t in ("2017", "2017-09", "2017-09-01")] == \
        ["year", "month", "day"]
    for bad in ("2017-13", "2017-02-30", "2023-02-29", "17th May", "", "2017-9-", "2017-9",
                "17", "12345"):
        assert When.parse(bad) is None, bad
    assert When.parse("2024-02-29") is not None and When.parse("2000-02-29") is not None
    assert When.parse("1900-02-29") is None


def test_a_year_covers_the_year_and_a_month_its_month():
    assert When(2017).first_day() == days(2017) and When(2017).end_day() == days(2018)
    assert When(2024, 2).end_day() - When(2024, 2).first_day() == 29
    assert When(2023, 2).end_day() - When(2023, 2).first_day() == 28
    assert When(2011, 10, 10).end_day() == When(2011, 10, 10).first_day() + 1
    assert When(2011, 10, 10).first_day() == (date(2011, 10, 10) - date(1970, 1, 1)).days


def test_before_the_common_era_counts_down_and_has_no_year_nought():
    caesar = When(-44, 3, 15)
    assert When(-45).first_day() < When(-44).first_day() < caesar.first_day() < When(1).first_day()
    # 1 BC is followed directly by AD 1.
    assert When(-1).end_day() == When(1).first_day()
    assert str(caesar) == "-0044-03-15" and caesar.label() == "15 March 44 BC"
    assert When.from_days(caesar.first_day()) == caesar


def test_day_numbers_go_and_come_back_over_millennia():
    for number in range(-1_200_000, 200_000, 997):
        assert timelines.days_from_civil(*timelines.civil_from_days(number)) == number
    start = date(1, 1, 1)
    for step in range(0, 800_000, 7919):
        day = start + timedelta(days=step)
        assert timelines.civil_from_days(days(day.year, day.month, day.day)) == \
            (day.year, day.month, day.day)


def test_a_date_reads_aloud():
    assert When(2017, 9, approx=True).label() == "c. September 2017"
    assert When(2011, 10, 10).label() == "10 October 2011"
    assert str(When(7)) == "0007" and str(When(2017, 9, approx=True)) == "~2017-09"


# ------------------------------------------------------------------ items --

def test_an_event_and_a_span_read_as_the_contract_says():
    born = timelines.parse_item("- 2011-10-10 · Born {family} !")
    assert (str(born.start), born.title, born.categories, born.milestone) == \
        ("2011-10-10", "Born", ["family"], True)
    both = timelines.parse_item("- 2021 - Two things {a, b}")
    assert both.title == "Two things" and both.categories == ["a", "b"] and not both.milestone
    hexed = timelines.parse_item("- -0044-03-15 · The Ides {#a33} !")
    assert hexed.colour == "#a33" and hexed.categories == [] and hexed.start.year == -44
    span = timelines.parse_item("- 2017-09 .. 2025-06 · Primary school {school}")
    assert span.span and str(span.end) == "2025-06" and span.title == "Primary school"
    open_span = timelines.parse_item("- 2025-09 .. · Szent István Gimnázium {school}")
    assert open_span.open_end and open_span.title == "Szent István Gimnázium"
    assert timelines.parse_item("- Wow! no date") is None
    assert timelines.parse_item("- 2017-13 · not a month") is None
    assert timelines.parse_item("* 2017 · not this bullet") is None
    loud = timelines.parse_item("- 2020 · Wow! {x}")
    assert loud.title == "Wow!" and not loud.milestone
    braces = timelines.parse_item("- 2020 · Use {braces} here {code}")
    assert braces.title == "Use {braces} here" and braces.categories == ["code"]


def test_an_open_span_runs_to_today():
    span = timelines.parse_item("- 2025-09 .. · Now")
    today = date(2026, 10, 28)
    assert span.end_day(today) == days(2026, 10, 29)
    year_span = timelines.parse_item("- 2017 .. 2018 · Two whole years")
    assert year_span.end_day() == days(2019)


# ------------------------------------------------------------------ board --

def test_a_board_reads_as_the_contract_says():
    board = Board.parse(BOARD)
    assert board.title == "My life" and board.born == When(2011, 10, 10)
    assert board.colours == {"family": "#c0623b", "school": "#4f7cac",
                             "projects": "#6b8f4e", "inner": "#8a6bb0"}
    assert [e.title for e in board.events] == ["Born", "First day at school",
                                               "First message on Discord"]
    assert board.events[1].notes == [
        "Indented lines under an event are its notes; [[links]] work in them."]
    assert [s.title for s in board.spans] == ["Primary school", "Szent István Gimnázium"]


def test_a_board_nobody_changed_is_written_back_byte_for_byte():
    for text in (BOARD, BOARD.replace("\n", "\r\n"), BOARD.rstrip("\n"),
                 BOARD.replace(" · ", " - ")):
        assert Board.parse(text).dump() == text


def test_an_edit_changes_its_own_line_and_nothing_else():
    board = Board.parse(BOARD)
    board.events[2].title = "First message on Discord, ever"
    out = board.dump()
    assert out == BOARD.replace("First message on Discord {projects}",
                                "First message on Discord, ever {projects}")
    board = Board.parse(BOARD)
    board.events[1].milestone = True
    assert "- ~2017-09 · First day at school {school} !\n  Indented lines" in board.dump()


def test_unknown_sections_and_text_are_carried_through():
    text = BOARD + "\n## Sources\n- 2020 · this is not an event here {x}\nProse.\n"
    board = Board.parse(text)
    assert len(board.events) == 3
    board.events[0].title = "Born, in Budapest"
    out = board.dump()
    assert out.endswith("\n## Sources\n- 2020 · this is not an event here {x}\nProse.\n")


def test_a_new_item_goes_in_date_order_into_its_section():
    board = Board.parse(BOARD)
    board.add(Item(When(2019), "Something", categories=["school"]))
    board.add(Item(When(2010), "Before it all"))
    board.add(Item(When(2026, 2), "Digitalization", span=True, categories=["projects"]))
    out = board.dump()
    events = out.split("## Events\n")[1].split("\n\n")[0].split("\n")
    assert [line.split(" · ")[1].split(" {")[0] for line in events if line.startswith("- ")] == \
        ["Before it all", "Born", "First day at school", "Something", "First message on Discord"]
    assert out.rstrip().endswith("- 2026-02 .. · Digitalization {projects}")


def test_a_new_board_grows_its_sections_in_the_right_place():
    board = Board.new("A course")
    board.add(Item(When(1000, 12, 25), "Stephen crowned", categories=["kings"], milestone=True))
    board.add(Item(When(1241), "Tatars", end=When(1242)))
    out = board.dump()
    assert out == ("---\nkind: timeline\ntitle: A course\n---\n\n## Events\n"
                   "- 1000-12-25 · Stephen crowned {kings} !\n\n## Spans\n"
                   "- 1241 .. 1242 · Tatars\n"), out
    assert Board.parse(out).dump() == out
    lone = Board.parse("---\ntitle: X\n---\n\nJust words.\n")
    lone.add(Item(When(2020), "First"))
    assert lone.dump().endswith("Just words.\n\n## Events\n- 2020 · First\n"), lone.dump()


def test_a_removed_item_comes_back_where_it_was():
    board = Board.parse(BOARD)
    item = board.events[1]
    index = board.remove(item)
    assert "First day" not in board.dump() and "Indented lines" not in board.dump()
    board.restore(item, index)
    assert board.dump() == BOARD


def test_categories_have_their_colours_or_one_of_the_house_palette():
    board = Board.parse(BOARD)
    assert board.category_colour("school") == "#4f7cac"
    assert board.category_colour("unnamed") == timelines.palette_colour("unnamed")
    assert timelines.palette_colour("Unnamed ") == timelines.palette_colour("unnamed")
    assert board.colour_of(timelines.parse_item("- 2020 · x {#123456}")) == "#123456"
    board.colours = {**board.colours, "maths": "#b8862f"}
    out = board.dump()
    assert '  inner: "#8a6bb0"\n  maths: "#b8862f"\n---' in out, out
    assert "born: 2011-10-10        # optional: the axis also shows age" in out
    assert board.categories()[:5] == ["family", "school", "projects", "inner", "maths"]


def test_spans_inside_spans_nest_outwards_and_overlaps_never_share_a_brace():
    board = Board.parse("## Spans\n"
                        "- 2000 .. 2020 · Outer\n"
                        "- 2005 .. 2010 · Inner\n"
                        "- 2006 .. 2007 · Innermost\n"
                        "- 2015 .. 2025 · Overlapping\n"
                        "- 2030 .. 2031 · Apart\n")
    lanes = board.lanes(today=date(2026, 1, 1))
    by = {s.title: lanes[id(s)] for s in board.spans}
    assert by["Innermost"] == 0 and by["Inner"] == 1 and by["Outer"] == 2, by
    assert by["Overlapping"] != by["Outer"] and by["Apart"] == 0, by


def test_a_board_is_a_file_in_the_timelines_folder():
    os.environ["ORDER_HOME"] = str(SCRATCH / "home")
    where = brain.places()
    board = timelines.new_board("History: the Árpáds", born=None)
    assert board.path == where.brain / "timelines" / "History the Árpáds.md"
    board.add(Item(When(1000, 12, 25), "Stephen crowned", categories=["kings"], milestone=True))
    board.save()
    again = timelines.load_boards()
    assert [b.title for b in again] == ["History: the Árpáds"]
    assert again[0].events[0].start == When(1000, 12, 25) and again[0].events[0].milestone
    assert again[0].dump() == board.dump()


def test_what_claude_proposes_is_read_line_by_line():
    found = timelines.parse_lines("Here you are:\n\n"
                                  "- 1000-12-25 · Stephen I crowned {kings} !\n"
                                  "  * 1241 .. 1242 · The Tatar invasion {war}\n"
                                  "Not a line.\n- 1301 · End of the house")
    assert [(i.title, i.span) for i in found] == [
        ("Stephen I crowned", False), ("The Tatar invasion", True), ("End of the house", False)]


TESTS = [(name[5:].replace("_", " "), fn) for name, fn in sorted(globals().items())
         if name.startswith("test_") and callable(fn)]


def main() -> int:
    failures = []
    try:
        for name, test in TESTS:
            try:
                test()
            except Exception as exc:                    # noqa: BLE001
                failures.append((name, exc))
                import traceback
                print(f"  FAIL  {name}: {exc!r}")
                for line in traceback.format_exc().strip().splitlines()[-4:]:
                    print(f"        {line}")
            else:
                print(f"  ok  {name}")
    finally:
        os.environ.pop("ORDER_HOME", None)
        shutil.rmtree(SCRATCH, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} failed")
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
