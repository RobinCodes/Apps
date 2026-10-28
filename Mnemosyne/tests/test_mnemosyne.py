"""What can be checked without a screen, and what needs one.

Most of Mnemosyne is arithmetic on a log — which cards are due, what a sheet
said, what a week added up to — so most of the suite is that, against a small
home built in a scratch folder: a brain with notes, decks, a palace, a review
log and a curriculum's drills.yaml, all pointed at with `ORDER_HOME` so that
nothing on the machine running the suite is read or written.

    python3 tests/test_mnemosyne.py

The second half needs a display. It opens the real window on the same scratch
home and drives it: a review by its keys, a sheet printed and typed back, a
palace walked, a drill run, the content hosted in somebody else's window the
way Mind Palace does it. Nothing is launched: a printed PDF would open a
viewer, so the window's launcher is replaced with one that only records.
"""

import io
import json
import os
import random
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

scratch = tempfile.mkdtemp(prefix="mnemosyne-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(scratch, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(scratch, "cache")
os.environ["ORDER_HOME"] = os.path.join(scratch, "home")
os.environ["ORDER_MACHINE"] = "arch-school"
os.environ.pop("ANAM_HOME", None)
# An app built by a test must not go to the network for an update, nor find a
# real Claude Code to talk to.
os.environ["APPS_NO_UPDATE"] = "1"
os.environ["ORDER_CLAUDE"] = os.path.join(scratch, "no-claude-here")
os.environ["PATH"] = os.pathsep.join(
    p for p in os.environ.get("PATH", "").split(os.pathsep) if "claude" not in p.lower())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from mnemosyne import (app as app_module, brain, cardtext, cards, cli, config,  # noqa: E402
                       fsrs, paper, stats, trainers, yamlite)
from mnemosyne.library import Library, answered_at, study_day  # noqa: E402
from mnemosyne.session import Session  # noqa: E402

failures = []
TZ = timezone(timedelta(hours=1))
HOME = Path(os.environ["ORDER_HOME"])


def check(name, fn):
    try:
        fn()
    except BaseException as exc:  # noqa: BLE001 - re-raised after the run
        failures.append((name, exc))
        print(f"  FAIL  {name}: {exc}")
        return
    print(f"  ok  {name}")


def at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 10, 1, hour, tzinfo=TZ) + timedelta(days=day)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


BIOLOGY = """# Biology

## Gyökér

- A gyökér zónái? :: süvegzóna, osztódási, megnyúlási, felszívási
- Wilson-tétel :: $(p-1)! \\equiv -1$ mod $p$
A {{mitokondrium}} termeli az ATP-t.

Q: Miért kevés a gyökérnyomás?
A: Mert csak néhány méterre emel.
"""

PALACE = """---
kind: palace
title: The way to school
---
1. Front door :: Wilson's theorem
   ![](door.png)
2. The lift mirror :: Fermat's little theorem
3. The bus stop bench :: Euler's theorem
"""

DRILLS = """# The Mentalist's drills
drills:
  - id: doom-year
    title: Doomsday, this year
    module: "11"
    trainer: doomsday
    minutes: 5
    params: {range: year, rounds: 3}
  - id: obs-2s
    title: What changed? Two seconds
    module: "02"
    trainer: observation
    params:
      seconds: 1
      rounds: 2
      folder: Pictures/Drill
  - id: major-0-49
    trainer: major
    params: {from: 0, to: 49, direction: both}
  - id: not-a-drill
    trainer: juggling
"""


def make_home(name: str) -> brain.Places:
    """A small, complete home: notes, decks, a palace, drills — and no reviews."""
    home = Path(scratch) / name
    os.environ["ORDER_HOME"] = str(home)
    where = brain.places()
    (where.learning / "reviews").mkdir(parents=True, exist_ok=True)
    write(where.studies / "Biology" / "2026-10-22.md", BIOLOGY)
    write(where.learning / "decks" / "Major system.md",
          "00 :: szósz\n01 :: szita\n7 :: kő\n37 :: mák, makk\n42 :: rény\n")
    write(where.learning / "decks" / "Major system (English).md",
          "---\ncards: false\n---\n00 :: sauce\n37 :: mug\n")
    stack = ["4♣", "2♥", "7♦", "3♣", "4♥", "6♦", "A♠", "5♥", "9♠", "2♠", "Q♥", "3♦",
             "Q♣", "8♥"]
    write(where.learning / "decks" / "Memorised deck - Mnemonica.md",
          "\n".join(f"{i} :: {c}" for i, c in enumerate(stack, start=1)) + "\n")
    write(where.learning / "palaces" / "School.md", PALACE)
    write(where.learning / "curricula" / "The Mentalist" / "drills.yaml", DRILLS)
    return where


def use_home(where: brain.Places) -> None:
    os.environ["ORDER_HOME"] = str(where.home)


def library_at(where: brain.Places) -> Library:
    use_home(where)
    return Library(where).refresh()


# ---------------------------------------------------------------- yamlite --

def test_the_yaml_reader_reads_what_drills_yaml_holds():
    data = yamlite.parse(DRILLS)
    drills = data["drills"]
    assert drills[0] == {"id": "doom-year", "title": "Doomsday, this year", "module": "11",
                         "trainer": "doomsday", "minutes": 5,
                         "params": {"range": "year", "rounds": 3}}, drills[0]
    assert drills[1]["params"] == {"seconds": 1, "rounds": 2, "folder": "Pictures/Drill"}
    assert drills[2]["params"]["direction"] == "both"


def test_the_yaml_reader_takes_lists_quotes_and_comments():
    data = yamlite.parse("roots:\n- 'a: b'   # quoted\n- \"c\\\"d\"\nn: 07\nx: [1, two, 3.5]\n"
                         "ok: yes\nnothing:\n")
    assert data == {"roots": ["a: b", 'c"d'], "n": "07", "x": [1, "two", 3.5],
                    "ok": True, "nothing": None}, data


def test_what_the_yaml_writer_writes_the_reader_reads_back():
    value = {"roots": ["SecondBrain/learning", "Projects/Studies"], "new_per_day": 20,
             "desired_retention": 0.9, "odd": "a: b # c", "empty": [],
             "nested": {"list": [{"id": "x", "p": {"k": 1}}]}}
    text = yamlite.dump(value, "a header")
    assert text.startswith("# a header\n"), text
    assert yamlite.parse(text) == value, text


# ----------------------------------------------------------------- config --

def test_learning_settings_default_to_system_md_without_a_file():
    where = make_home("learning-defaults")
    learning = config.Learning(where)
    assert learning["new_per_day"] == 20 and learning["desired_retention"] == 0.9
    assert learning["roots"][0] == "SecondBrain/learning"
    assert learning["extensions"] == ["md", "tex"]


def test_learning_settings_are_read_clamped_and_written_back_with_other_keys():
    where = make_home("learning-file")
    write(where.brain / "config" / "learning.yaml",
          "roots: [SecondBrain/learning]\nnew_per_day: 900\ndesired_retention: 85\n"
          "anki_import: true\n")
    learning = config.Learning(where)
    assert learning["new_per_day"] == config.NEW_RANGE[1]
    assert learning["desired_retention"] == 0.85
    assert learning.set("new_per_day", 12)
    again = yamlite.parse((where.brain / "config" / "learning.yaml").read_text("utf-8"))
    assert again["new_per_day"] == 12 and again["anki_import"] is True, again
    assert again["desired_retention"] == 0.85


def test_learning_settings_are_not_written_where_there_is_no_brain():
    home = Path(scratch) / "no-brain"
    os.environ["ORDER_HOME"] = str(home)
    learning = config.Learning(brain.places())
    assert learning.set("new_per_day", 5) is False
    assert not (home / "SecondBrain").exists()


def test_the_window_settings_ignore_what_they_do_not_know():
    os.makedirs(config.DIR, exist_ok=True)
    with open(config.PATH, "w", encoding="utf-8") as fh:
        json.dump({"card_font_size": 22, "bogus": 1, "page": 3}, fh)
    loaded = config.Config()
    assert loaded["card_font_size"] == 22 and "bogus" not in loaded
    assert loaded["page"] == config.DEFAULTS["page"]
    os.remove(config.PATH)


# ---------------------------------------------------------------- library --

def test_today_is_the_due_cards_then_a_ration_of_new_ones():
    where = make_home("today")
    library = library_at(where)
    library.deck.new_per_day = 3
    first = library.today(at(0))
    assert len(first) == 3 and all(i.new for i in first)
    for item in first:
        library.answer(item, fsrs.GOOD, at=at(0))
    # The same day: the ration is spent.
    assert library.today(at(0, 15)) == []
    # Later the reviewed ones are due, and come before the next new ones.
    later = library.today(at(30))
    assert [i.new for i in later[:3]] == [False] * 3 and later[3].new, later


def test_cards_on_a_sheet_printed_today_are_left_off_the_screen():
    where = make_home("paper-queue")
    library = library_at(where)
    library.deck.new_per_day = 4
    sheet = library.print_today(at(0))
    assert sheet is not None and len(sheet.rows) == 4
    on_paper = {row.card for row in sheet.rows}
    assert library.on_paper(at(0, 12)) == on_paper
    # The new ones on paper count against the day's ration.
    assert library.today(at(0, 12)) == []
    assert library.counts(at(0, 12)) == {"due": 0, "new": 0, "paper": 4}
    # Tomorrow the sheet is no longer today's.
    assert library.on_paper(at(1)) == set()


def test_a_sheet_typed_back_writes_paper_answers_and_skips_blank_rows():
    where = make_home("enter")
    library = library_at(where)
    library.deck.new_per_day = 3
    sheet = library.print_today(at(0))
    written = library.enter_sheet(sheet, [3, None, 1], at(0, 18))
    assert written == 2
    answers = library.log.read()
    assert [a.via for a in answers] == ["paper", "paper"]
    assert [a.rating for a in answers] == [3, 1]
    assert answers[0].when == at(0, 18)
    stored = library.sheets.get(sheet.id)
    assert stored.entered is not None and stored.ratings == [3, None, 1]
    assert library.sheets.open() == []


def test_a_sheet_typed_back_days_later_is_dated_the_evening_it_was_answered():
    printed = paper.Sheet(id="x", day=date(2026, 10, 1), made=at(0), machine="m")
    assert answered_at(printed, at(0, 15)) == at(0, 15)
    late = answered_at(printed, at(2, 10))
    assert late.date() == date(2026, 10, 1) and late.hour == 20, late


def test_a_card_edited_after_printing_keeps_its_paper_answer():
    where = make_home("edited")
    library = library_at(where)
    sheet = library.print_today(at(0))
    first = sheet.rows[0]
    note = where.studies / "Biology" / "2026-10-22.md"
    note.write_text(BIOLOGY.replace("A gyökér zónái?", "A gyökér négy zónája?"), "utf-8")
    library.refresh()
    assert first.card not in library.deck.items
    library.enter_sheet(sheet, [4], at(0, 18))
    library.refresh()
    # Re-attached to the edited card by its unchanged back.
    edited = next(i for i in library.deck.items.values()
                  if i.card.front == "A gyökér négy zónája?")
    assert len(edited.answers) == 1 and edited.answers[0].via == "paper"


def test_studying_one_note_takes_all_its_new_cards():
    where = make_home("one-note")
    library = library_at(where)
    library.deck.new_per_day = 1
    items = library.for_note(where.studies / "Biology" / "2026-10-22.md")
    assert len(items) == 4 and all(i.new for i in items)
    assert [i.card.line for i in items] == sorted(i.card.line for i in items)


def test_a_note_outside_the_roots_can_still_be_studied():
    where = make_home("outside")
    loose = write(where.home / "Desktop" / "loose.md", "Kína fővárosa :: Peking\n")
    library = library_at(where)
    items = library.for_note(loose)
    assert [i.card.front for i in items] == ["Kína fővárosa"]
    library.answer(items[0], fsrs.GOOD, at=at(0))
    again = library.for_note(loose, everything=True)
    assert again[0].memory is not None, "its history comes from the log"


# ---------------------------------------------------------------- session --

class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_a_card_answered_again_comes_back_before_the_session_ends():
    where = make_home("again")
    library = library_at(where)
    items = library.today(at(0))[:5]
    clock = FakeClock()
    session = Session(library, items, again_gap=2, clock=clock)
    first = session.current
    session.show()
    clock.now += 3
    session.answer(fsrs.AGAIN, at=at(0))
    assert session.pending.index(first) == 2
    seen = [first.card.id]
    while not session.finished:
        session.show()
        seen.append(session.current.card.id)
        session.answer(fsrs.GOOD, at=at(0))
    assert seen.count(first.card.id) == 2
    summary = session.summary()
    assert summary.cards == 5 and summary.answers == 6 and summary.first_again == 1
    assert summary.ratings[fsrs.AGAIN] == 1 and abs(summary.seconds - 3) < 0.01
    assert abs(summary.recalled - 0.8) < 1e-9


def test_time_on_a_card_is_capped_and_a_skip_is_allowed_once():
    where = make_home("skip")
    library = library_at(where)
    items = library.today(at(0))[:3]
    clock = FakeClock()
    session = Session(library, items, clock=clock)
    first = session.current
    assert session.skip() and session.pending[-1] is first
    while session.current is not first:
        session.show()
        session.answer(fsrs.GOOD, at=at(0))
    assert not session.can_skip()
    session.show()
    clock.now += 3600
    session.answer(fsrs.GOOD, at=at(0))
    assert library.log.read()[-1].ms == 120_000


def test_the_buttons_preview_the_intervals_fsrs_would_give():
    where = make_home("preview")
    library = library_at(where)
    session = Session(library, library.today(at(0)))
    days = session.preview(at(0))
    assert set(days) == {1, 2, 3, 4} and days[1] <= days[2] <= days[3] <= days[4]


# ------------------------------------------------------------------ paper --

def test_ticks_are_a_digit_a_row_and_anything_blank_is_skipped():
    assert paper.parse_ticks("3341", 4) == [3, 3, 4, 1]
    assert paper.parse_ticks("3-0 x.2", 10) == [3, None, None, None, None, None, 2]
    assert paper.parse_ticks("12345", 3) == [1, 2, 3]
    assert paper.parse_ticks("a1b", 3) == [1]
    assert paper.tally([1, 3, 3, None]) == {1: 1, 2: 0, 3: 2, 4: 0, None: 1}


def test_sheet_ids_carry_the_date_and_the_machine_and_never_collide():
    folder = Path(scratch) / "sheets"
    sheets = paper.Sheets(folder, "arch-school")
    day = date(2026, 10, 27)
    first = sheets.make([], at(0), day)
    second = sheets.make([], at(0), day)
    assert first.id == "261027-a1" and second.id == "261027-a2"
    assert paper.Sheets(folder, "main-pc").new_id(day) == "261027-m1"
    back = paper.Sheet.from_json(first.to_json())
    assert back.id == first.id and back.day == day and back.made == first.made


def _pdf_pages(path) -> int:
    data = Path(path).read_bytes()
    assert data.startswith(b"%PDF"), "not a PDF"
    import re

    return len(re.findall(rb"/Type\s*/Page[^s]", data))


def test_a_sheet_prints_as_a_pdf_with_as_many_pages_as_it_needs():
    where = make_home("print")
    library = library_at(where)
    sheet = library.print_today(at(0))
    path = paper.render_sheet(sheet, Path(scratch) / "out" / "sheet.pdf")
    assert _pdf_pages(path) == 2, "twenty cards are one sheet of paper, both sides"
    long = paper.Sheet(id="long", day=date(2026, 10, 1), made=at(0), machine="m",
                       rows=[paper.Row(card=str(i), front=f"Question {i} " * 6,
                                       back=f"Answer {i} " * 8) for i in range(80)])
    assert _pdf_pages(paper.render_sheet(long, Path(scratch) / "out" / "long.pdf")) >= 3


def test_flashcards_print_fronts_then_backs_ten_to_a_page():
    where = make_home("flash")
    library = library_at(where)
    chosen = [i.card for i in library.deck.items.values()][:12]
    path = paper.render_flashcards("Mixed", chosen, Path(scratch) / "out" / "flash.pdf")
    assert _pdf_pages(path) == 4


# --------------------------------------------------------------- trainers --

def test_doomsday_agrees_with_the_calendar_for_four_centuries():
    rng = random.Random(7)
    for _ in range(2000):
        day = date(1700, 1, 1) + timedelta(days=rng.randrange(146000))
        dd = trainers.year_doomsday(day.year)
        marker = date(day.year, day.month, trainers.month_doomsday(day.year, day.month))
        assert trainers.weekday(marker) == dd, (day, dd)
        # 25 October 2026 was a Sunday.
        assert trainers.weekday(day) == (day - date(2026, 10, 25)).days % 7, day


def test_the_method_is_worked_through_to_the_right_day():
    lines = trainers.explain(date(2026, 10, 27))
    assert "Tuesday (2)" in lines[0] and lines[-1].endswith("Tuesday (2).")
    assert "Saturday (6)" in lines[2], lines


def test_playing_cards_are_read_however_they_are_typed():
    for typed, card in (("QH", "Q♥"), ("q♥", "Q♥"), ("10d", "10♦"), ("Td", "10♦"),
                        ("4 of clubs", "4♣"), ("ace of spades", "A♠"), ("pikk ász", "A♠"),
                        ("kőr király", "K♥"), ("x", None), ("11h", None)):
        assert trainers.parse_playing_card(typed) == card, (typed, card)


def test_the_stack_and_the_major_system_come_from_the_decks():
    where = make_home("decks")
    name, stack = trainers.load_stack(where)
    assert "Mnemonica" in name and stack[1] == "4♣" and stack[14] == "8♥"
    name, table = trainers.load_major(where)
    assert name == "Major system", "the deck switched off is not the one used"
    assert table["37"] == ["mák", "makk"] and table["00"] == ["szósz"] and "7" in table
    assert trainers.major_numbers(table, 0, 9) == ["00", "01", "7"]
    empty = make_home("no-decks")
    shutil.rmtree(empty.learning / "decks")
    assert trainers.load_stack(empty)[0] == "New deck order"
    assert trainers.load_major(empty) == ("", {})


def test_answers_are_compared_without_case_or_accents():
    assert trainers.answer_key("  Mák! ") == trainers.answer_key("mak")


def test_every_kind_of_sum_has_the_right_answer():
    rng = random.Random(3)
    for kind in trainers.CALC_KINDS:
        for _ in range(50):
            prompt, answer = trainers.calc_problem(kind, 2, rng)
            expression = (prompt.replace("×", "*").replace("−", "-").replace("²", "**2")
                          .replace("∛", ""))
            if kind == "cube-root":
                assert answer ** 3 == int(expression), prompt
            else:
                assert eval(expression) == answer, prompt          # noqa: S307
            if kind == "square-5":
                assert prompt.split("²")[0].endswith("5")


def test_a_change_is_inside_the_photograph_and_a_clone_comes_from_elsewhere():
    rng = random.Random(1)
    for _ in range(200):
        change = trainers.plan_change(640, 480, rng)
        assert 0 <= change.x and change.x + change.w <= 640
        assert 0 <= change.y and change.y + change.h <= 480
        if change.kind == "clone":
            assert abs(change.sx - change.x) >= change.w or abs(change.sy - change.y) >= change.h
        assert change.contains(change.x + change.w / 2, change.y + change.h / 2)
        assert not change.contains(change.x - change.w, change.y - change.h)


def test_the_most_detailed_candidate_is_the_one_changed():
    change = trainers.plan_change(1000, 1000, random.Random(5),
                                  interest=lambda x, y, w, h: -abs(x - 500) - abs(y - 500))
    assert abs(change.x - 500) < 250 and abs(change.y - 500) < 250


def test_drills_come_from_the_curriculum_and_nonsense_is_left_out():
    where = make_home("drills")
    drills = trainers.load_drills(where)
    assert [d.id for d in drills] == ["doom-year", "obs-2s", "major-0-49"]
    assert drills[0].source == "The Mentalist" and drills[0].int_param("rounds", 10) == 3
    shutil.rmtree(where.learning / "curricula")
    assert trainers.load_drills(where) == trainers.BUILTIN


def test_a_top_level_list_of_drills_is_read_as_well():
    where = make_home("drills-list")
    write(where.learning / "curricula" / "Other" / "drills.yaml",
          "- id: calc-11\n  trainer: calc\n  params: {kind: times-11}\n")
    ids = [d.id for d in trainers.load_drills(where)]
    assert "calc-11" in ids and "doom-year" in ids


def test_a_run_scores_and_the_log_reads_back_from_every_machine():
    folder = Path(scratch) / "drill-log"
    run = trainers.Run(trainers.BUILTIN[0], 4)
    for correct, ms in ((True, 1000), (False, 3000), (True, 2000), (True, 9000)):
        run.mark(correct, ms)
    assert run.done and run.score == 0.75 and run.median_ms == 2500
    run.save(trainers.DrillLog(folder, "arch-school"), title="x")
    trainers.DrillLog(folder, "main-pc").append("doom-year", 1.0, 900, 10)
    results = trainers.DrillLog(folder, "arch-school").read()
    assert {r.machine for r in results} == {"arch-school", "main-pc"}
    line = json.loads((folder / "arch-school.jsonl").read_text("utf-8"))
    assert set(line) == {"t", "drill", "score", "ms", "n", "title"}, line


# ------------------------------------------------------------------ stats --

def test_the_week_counts_reviews_retention_and_what_slipped():
    where = make_home("week")
    library = library_at(where)
    items = list(library.deck.items.values())[:3]
    for item in items:
        library.answer(item, fsrs.GOOD, at=at(0))           # first meeting
    library.answer(items[0], fsrs.GOOD, at=at(2))           # held
    library.answer(items[1], fsrs.GOOD, at=at(2))           # held
    library.answer(items[0], fsrs.AGAIN, at=at(5))          # slipped
    library.answer(items[0], fsrs.GOOD, at=at(5, 10))       # same day: not a test
    week = stats.week(library, at(6))
    assert week.reviews == 7 and week.new_cards == 3
    assert week.tested == 3 and week.held == 2, (week.tested, week.held)
    assert [item.card.id for item, _ in week.slipping] == [items[0].card.id]
    assert [d.reviews for d in week.days] == [3, 0, 2, 0, 0, 2, 0]
    assert week.streak == 1, "yesterday counts until today is over"
    assert stats.week(library, at(5, 20)).streak == 1
    assert stats.week(library, at(8)).streak == 0


def test_the_days_ahead_count_what_falls_due():
    where = make_home("ahead")
    library = library_at(where)
    item = next(iter(library.deck.items.values()))
    library.answer(item, fsrs.GOOD, at=at(0))
    ahead = stats.due_ahead(library, at(0, 12), 7)
    assert sum(ahead) == 1 and ahead[0] == 0


# --------------------------------------------------------------- cardtext --

def test_card_text_becomes_pango_markup():
    out = cardtext.to_markup("A **bold** and *it* & <x> `a::b` ~~no~~")
    assert out == "A <b>bold</b> and <i>it</i> &amp; &lt;x&gt; <tt>a::b</tt> <s>no</s>", out
    assert cardtext.to_markup("- one\n- two") == "• one\n• two"
    assert cardtext.to_markup("[[Fémek|metals]] and [x](http://y)") == \
        "<u>metals</u> and <u>x</u>"
    assert cardtext.to_markup("snake_case_name costs $5 and $6") == \
        "snake_case_name costs $5 and $6"


def test_maths_in_a_card_is_typeset_rather_than_shown_as_tex():
    out = cardtext.to_markup(r"$x^2 \leq \alpha$")
    assert "<sup>2</sup>" in out and "≤" in out and "α" in out and "\\" not in out, out
    assert cardtext.plain("**a** $\\to$ b") == "a → b"


def test_pictures_in_a_card_are_found_beside_its_note():
    where = make_home("pictures")
    note = write(where.studies / "Biology" / "cell.md", "x")
    photo = write(where.studies / "Biology" / "img" / "cell.png", "not really a png")
    text = "A sejt ![a cell](img/cell.png) és ![[missing.png]] ![](https://example.org/x.png)"
    assert cardtext.images(text) == ["img/cell.png", "missing.png"]
    assert cardtext.image_paths(text, note, where.brain) == [photo]
    assert cardtext.to_markup(text).strip() == "A sejt  és", cardtext.to_markup(text)


# ------------------------------------------------- the handbook's rules --

SIBLINGS = """# Olasz

il gesso ::: kréta
la gomma ::: radír
A {{mitokondrium}} termeli az {{ATP}}-t.
Pisa :: Toszkána
"""


def sibling_home(name):
    where = make_home(name)
    for deck in ("decks", "palaces", "curricula"):
        shutil.rmtree(where.learning / deck)
    (where.studies / "Biology" / "2026-10-22.md").unlink()
    write(where.studies / "Italian" / "lesson.md", SIBLINGS)
    write(where.studies / "History" / "kings.md",
          "\n".join(f"King {n} :: {1000 + n}" for n in range(6)) + "\n")
    return where


def test_siblings_never_share_a_day():
    where = sibling_home("siblings")
    library = library_at(where)
    queue = library.today(at(0))
    families = [cards_family(i) for i in queue if cards_family(i)]
    assert len(families) == len(set(families)), "two of one family in a day"
    italian = [i for i in queue if i.card.deck == "Studies/Italian"]
    assert len(italian) == 4, [i.card.front for i in italian]   # 2 + 1 cloze + Pisa
    # Once one side is answered, the other waits for tomorrow — and comes then.
    gesso = next(i for i in italian if i.card.front == "il gesso")
    library.answer(gesso, fsrs.GOOD, at=at(0))
    later = library.today(at(0, 15))
    assert not any(i.card.front == "kréta" for i in later)
    assert any(i.card.front == "kréta" for i in library.today(at(1)))


def cards_family(item):
    from mnemosyne.library import family

    return family(item.card)


def test_a_day_is_dealt_across_its_decks():
    where = sibling_home("mixing")
    library = library_at(where)
    decks = [i.card.deck for i in library.today(at(0))]
    assert decks[:4] == ["Studies/History", "Studies/Italian"] * 2, decks


def test_a_test_date_brings_a_deck_forward_and_raises_the_ration():
    where = sibling_home("test-date")
    library = library_at(where)
    library.learning.values["new_per_day"] = 1
    library.refresh()
    assert len(library.today(at(0))) == 1
    library.learning.values["tests"] = {"Studies/History": (at(0) + timedelta(days=5)).date()}
    queue = library.today(at(0))
    # Six new cards, met by two days ahead of the test: three a day.
    assert len(queue) == 3 and all(i.card.deck == "Studies/History" for i in queue), \
        [i.card.deck for i in queue]
    assert library.tests(at(0)) == {"Studies/History": date(2026, 10, 6)}
    assert library.tests(at(9)) == {}, "a test that has been is forgotten"


def test_a_deck_switched_off_is_out_of_every_queue():
    where = sibling_home("off")
    write(where.projects / "Programming" / "Websites" / "Digitalization" / "DataHU" /
          "Humán tárgyak" / "Nyelvtan" / "szofajok.tex",
          "\\begin{definition}[Főnév]A dolgok neve.\\end{definition}\n")
    library = library_at(where)
    assert any(i.card.form == "tex" for i in library.deck.items.values())
    assert library.set_deck("Studies/Italian", False)
    assert library.learning.set("off", library.learning["off"]
                                + ["Digitalization/DataHU/Humán tárgyak"])
    library.refresh()
    decks = {i.card.deck for i in library.deck.items.values()}
    assert decks == {"Studies/History"}, decks
    assert "Studies/Italian" in library.all_decks() and library.deck_is_off("Studies/Italian")
    saved = yamlite.parse((where.brain / "config" / "learning.yaml").read_text("utf-8"))
    assert saved["off"] == ["Studies/Italian", "Digitalization/DataHU/Humán tárgyak"], saved
    assert '"off":' in (where.brain / "config" / "learning.yaml").read_text("utf-8"),         "quoted, or YAML 1.1 reads the key as False"


def test_switching_one_deck_back_on_leaves_its_neighbours_off():
    where = sibling_home("off-again")
    library = library_at(where)
    assert library.set_deck("Studies", False)
    library.refresh()
    assert not library.deck.items
    assert library.set_deck("Studies/Italian", True)
    library.refresh()
    assert {i.card.deck for i in library.deck.items.values()} == {"Studies/Italian"}
    assert library.learning["off"] == ["Studies/History"]


def test_a_card_with_an_empty_side_is_never_shown():
    where = sibling_home("empty")
    write(where.studies / "Maths" / "x.tex",
          "\\begin{example}[Üres]\n\\end{example}\n\\begin{definition}[Gráf]G=(V,E)\\end{definition}\n")
    library = library_at(where)
    maths = [i.card.front for i in library.deck.items.values() if i.card.deck == "Studies/Maths"]
    assert maths == ["Definition: Gráf"], maths


def test_a_card_forgotten_six_times_is_a_leech():
    where = sibling_home("leech")
    library = library_at(where)
    item = next(i for i in library.deck.items.values() if i.card.front == "Pisa")
    library.answer(item, fsrs.GOOD, at=at(0))
    for day in range(1, 13, 2):
        library.answer(item, fsrs.GOOD, at=at(day))
        library.answer(item, fsrs.AGAIN, at=at(day + 1))
        library.answer(item, fsrs.AGAIN, at=at(day + 1, 15))   # same day: not another lapse
    from mnemosyne.library import lapses

    assert lapses(item) == 6
    assert [i.card.front for i, _ in library.leeches()] == ["Pisa"]
    item.memory = fsrs.replay([(a.when, a.rating) for a in item.answers])
    sheet = library.sheets.make([item], at(14), date(2026, 10, 15),
                                {i.card.id for i, _ in library.leeches()})
    assert sheet.rows[0].leech and paper.Sheet.from_json(sheet.to_json()).rows[0].leech


def _simulated(weights, cards_n=150, days=120, seed=11):
    """Reviews a memory with `weights` would have produced, reviewing on schedule."""
    rng = random.Random(seed)
    histories = []
    for c in range(cards_n):
        when = at(0)
        history = [(when, fsrs.GOOD)]
        memory = fsrs.review(None, fsrs.GOOD, when, w=weights)
        while True:
            when = memory.due + timedelta(hours=rng.randint(0, 30))
            if when > at(days):
                break
            r = fsrs.retrievability(fsrs.elapsed_days(memory.last, when), memory.stability)
            rating = fsrs.GOOD if rng.random() < r else fsrs.AGAIN
            history.append((when, rating))
            memory = fsrs.review(memory, rating, when, w=weights)
        histories.append(history)
    return histories


def test_fitting_moves_the_weights_towards_the_memory_that_made_the_log():
    from mnemosyne import fit

    forgetful = list(fsrs.DEFAULT_W)
    for i in range(4):
        forgetful[i] *= 0.3                       # first meetings fade fast
    histories = _simulated(forgetful)
    result = fit.fit(histories, rounds=3)
    assert result.reviews > 100 and result.loss_after < result.loss_before, result
    assert result.weights[2] < fsrs.DEFAULT_W[2], "Good's first stability came down"
    for got, default in zip(result.weights, fsrs.DEFAULT_W):
        assert default / fit.BOUND - 1e-9 <= got <= default * fit.BOUND + 1e-9
    assert not fit.due_for_a_fit(399, 0) and fit.due_for_a_fit(400, 0)
    assert not fit.due_for_a_fit(450, 400) and fit.due_for_a_fit(500, 400)


def test_a_fitted_schedule_is_kept_in_the_brain_and_used_everywhere():
    where = sibling_home("fitted")
    library = library_at(where)
    assert library.fit_if_due() is None, "too few reviews"
    item = next(iter(library.deck.items.values()))
    for day in range(0, 40, 3):
        library.answer(item, fsrs.GOOD if day % 2 else fsrs.AGAIN, at=at(day))
    result = library.fit_if_due(force=True)
    assert result is not None and result.reviews == 13
    saved = yamlite.parse((where.brain / "config" / "learning.yaml").read_text("utf-8"))
    assert len(saved["weights"]) == 19 and saved["weights_reviews"] == 13
    again = Library(where).refresh()
    assert again.deck.w == tuple(saved["weights"])


def test_learning_settings_read_test_dates_and_refuse_broken_weights():
    where = make_home("learning-more")
    write(where.brain / "config" / "learning.yaml",
          "tests:\n  Studies/Biology: 2026-11-12\n  Nonsense: someday\n"
          "weights: [1, 2, 3]\noff: Studies/Italian\n")
    learning = config.Learning(where)
    assert learning["tests"] == {"Studies/Biology": date(2026, 11, 12)}
    assert learning["weights"] is None and learning["off"] == ["Studies/Italian"]
    learning.set("off", [])
    text = (where.brain / "config" / "learning.yaml").read_text("utf-8")
    assert "off" not in text and "weights" not in text and "2026-11-12" in text, text


def test_the_week_prints_on_one_page_with_its_leeches():
    where = sibling_home("week-paper")
    library = library_at(where)
    summary = stats.for_paper(library, at(3))
    assert summary["leeches"] == [] and summary["figures"][0][0] == "Reviews"
    summary["leeches"] = [("Pisa", "Toszkána", "Projects/Studies/Italian/lesson.md:6", 7)]
    path = paper.render_week(summary, Path(scratch) / "out" / "week.pdf")
    assert _pdf_pages(path) == 1


def test_a_sheet_draws_the_pictures_in_its_cards():
    where = sibling_home("sheet-pictures")
    pixbuf_module = __import__("gi.repository.GdkPixbuf", fromlist=["GdkPixbuf"])
    pixbuf = pixbuf_module.Pixbuf.new(pixbuf_module.Colorspace.RGB, False, 8, 40, 30)
    pixbuf.fill(0x8899aaff)
    pixbuf.savev(str(where.studies / "Italian" / "gesso.png"), "png", [], [])
    sheet = paper.Sheet(id="pics", day=date(2026, 10, 1), made=at(0), machine="m",
                        rows=[paper.Row(card="x", front="Mi ez? ![](gesso.png)",
                                        back="il gesso", src="Projects/Studies/Italian/lesson.md")])
    plain_pdf = paper.render_sheet(sheet, Path(scratch) / "out" / "nopics.pdf")
    with_pics = paper.render_sheet(sheet, Path(scratch) / "out" / "pics.pdf", home=where.home)
    assert b"/Subtype /Image" in with_pics.read_bytes()
    assert b"/Subtype /Image" not in plain_pdf.read_bytes()


def test_an_indented_picture_belongs_to_the_locus_above_it():
    from mnemosyne.palaces import locus_photos

    where = make_home("loci")
    note = write(where.learning / "palaces" / "Flat.md",
                 "---\nkind: palace\n---\n1. Door :: talc\n   ![](door.jpg)\n"
                 "2. Shoes :: gypsum\n3. Mirror :: calcite\n\n   ![](mirror.jpg)\n")
    for name in ("door.jpg", "mirror.jpg"):
        write(note.parent / name, "x")
    found = locus_photos(note, [4, 6, 7])
    assert found == {4: note.parent / "door.jpg", 7: note.parent / "mirror.jpg"}, found


# -------------------------------------------------------------- the CLI --

def test_due_prints_two_numbers():
    where = make_home("cli-due")
    use_home(where)
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(["--due"]) == 0
    due, new = out.getvalue().split()
    assert due == "0" and new == "20", out.getvalue()


def test_due_says_nothing_is_due_where_there_is_no_brain():
    os.environ["ORDER_HOME"] = os.path.join(scratch, "nowhere")
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(["--due"]) == 0
    assert out.getvalue().strip() == "0 0"


def test_print_writes_a_sheet_and_says_where():
    where = make_home("cli-print")
    use_home(where)
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(["--print", "--no-open"]) == 0
    path = Path(out.getvalue().strip())
    assert path.is_file() and path.suffix == ".pdf", out.getvalue()
    assert len(list((where.learning / "sheets").glob("*.json"))) == 1


def test_the_window_is_left_to_gtk_for_everything_else():
    assert cli.main([]) is None
    assert cli.main(["--study", "x.md"]) is None
    assert app_module.parse_args(["-n", "--study", "a.md", "b"]) == (True, "a.md", ["b"])
    assert app_module.parse_args(["--study=c.md"]) == (False, "c.md", [])


# ---------------------------------------------------- claude, unplugged --

def test_proposed_cards_are_only_the_lines_that_read_as_cards():
    from mnemosyne.assistant import proposals

    reply = ("Sure! Here they are.\nBEGIN CARDS\nFront :: Back\n\nNot a card at all\n\n"
             "Q: Why?\nA: Because.\n\nThe {{sun}} is a star.\nEND CARDS\nHope that helps.")
    assert proposals(reply, "Projects/Studies/x.md") == [
        "Front :: Back", "Q: Why?\nA: Because.", "The {{sun}} is a star."]


def test_approved_cards_go_to_the_end_of_the_note_under_cards():
    from mnemosyne.assistant import append_cards

    note = Path(scratch) / "append" / "note.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_bytes(b"# Topic\r\n\r\nText.\r\n")
    append_cards(note, ["A :: B", "The {{sun}} is a star."])
    raw = note.read_bytes().decode("utf-8")
    assert raw == ("# Topic\r\n\r\nText.\r\n\r\n## Cards\r\n\r\nA :: B\r\n\r\n"
                   "The {{sun}} is a star.\r\n"), repr(raw)
    append_cards(note, ["C :: D"])
    assert note.read_text("utf-8").count("## Cards") == 1


# ---------------------------------------------------------------- the rest --

def test_mnemosyne_is_vendored_and_installed_like_the_others():
    sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "house"))
    import sync                                               # noqa: PLC0415

    assert sync.PACKAGES.get("Mnemosyne") == "mnemosyne"
    assert "Mnemosyne" in sync.BRAIN_APPS
    install = Path(os.path.dirname(ROOT), "windows", "install.ps1").read_text("utf-8")
    assert "Entry = 'mnemosyne.py'" in install


# ============================================================ with a display =

def pump(milliseconds):
    """Let the main loop run — for the idle callbacks the app leans on."""
    deadline = GLib.get_monotonic_time() + milliseconds * 1000
    context = GLib.MainContext.default()
    while GLib.get_monotonic_time() < deadline:
        while context.pending():
            context.iteration(False)
        GLib.usleep(2000)


def settle(predicate, milliseconds=5000):
    deadline = GLib.get_monotonic_time() + milliseconds * 1000
    context = GLib.MainContext.default()
    while GLib.get_monotonic_time() < deadline:
        if predicate():
            return True
        while context.pending():
            context.iteration(False)
        GLib.usleep(2000)
    return predicate()


def press(window, keyval):
    assert window.activity is not None, "nothing is being done"
    return window.activity.handle_key(keyval, Gdk.ModifierType(0))


def fresh_window(app, name):
    """A window on a home of its own, refreshed, with nothing launched for real."""
    where = make_home(name)
    use_home(where)
    library = Library(where)
    window = MainWindow(app, library)
    window.launched = []
    window.launch_file = lambda path: window.launched.append(str(path))
    window.opened = []
    window.open_note = lambda path, line=None: window.opened.append((str(path), line))
    window.present()
    assert settle(lambda: library.loaded and not window._refreshing), "never refreshed"
    pump(100)
    return window, where


def test_the_window_opens_on_today(app):
    window, where = fresh_window(app, "w-today")
    today = window.pages["today"]
    assert today.new.value.get_text() == "20" and today.due.value.get_text() == "0"
    assert today.start.get_sensitive()
    window.close()


def test_the_window_fits_in_a_tiling_column(app):
    """455px is the narrowest preset column on this machine's screen."""
    window, _ = fresh_window(app, "w-narrow")
    window.set_default_size(455, 700)
    pump(400)
    minimum = window.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    assert minimum <= 360, f"the window cannot be made narrower than {minimum}px"
    window.close()


def test_a_review_is_driven_by_its_keys(app):
    window, where = fresh_window(app, "w-review")
    window.study_today()
    pump(100)
    view = window.activity
    assert view is not None and view.session.total == 20
    first = view.session.current
    press(window, Gdk.KEY_space)
    assert view.session.revealed and view.back_revealer.get_reveal_child()
    assert view.rating_buttons[3][1].get_text(), "the buttons say when"
    press(window, Gdk.KEY_1)
    assert first in view.session.pending, "Again comes back"
    while not view.session.finished:
        press(window, Gdk.KEY_space)
        press(window, Gdk.KEY_space)
    assert view.summary_shown
    assert len(window.library.log.read()) == 21
    press(window, Gdk.KEY_Return)
    assert window.activity is None
    assert window.pages["today"].new.value.get_text() == "0"
    window.close()


def test_studying_a_note_asks_only_that_note(app):
    window, where = fresh_window(app, "w-note")
    window.study_note(str(where.studies / "Biology" / "2026-10-22.md"))
    pump(100)
    assert window.activity is not None and window.activity.title == "2026-10-22"
    assert window.activity.session.total == 4
    window.activity.handle_key(Gdk.KEY_e, Gdk.ModifierType(0))
    assert window.opened and window.opened[0][0].endswith("2026-10-22.md")
    window.close_activity()
    window.close()


def test_a_sheet_is_printed_and_its_ticks_typed_back(app):
    window, where = fresh_window(app, "w-paper")
    window.pages["paper"].print_today()
    assert settle(lambda: window.launched), "the sheet was never handed to a viewer"
    assert window.launched[0].endswith(".pdf") and os.path.isfile(window.launched[0])
    assert window.pages["today"].paper.value.get_text() == "20"
    window.enter_results()
    view = window.activity
    view.entry.set_text("3 41x")
    assert view.entry.get_text() == "3-41-", view.entry.get_text()
    assert view.rows[0][1].get_text() == "3" and view.rows[1][1].get_text() == "–"
    view.save()
    answers = window.library.log.read()
    assert [a.rating for a in answers] == [3, 4, 1] and {a.via for a in answers} == {"paper"}
    assert window.activity is None and not window.library.sheets.open()
    window.close()


def test_a_palace_is_walked_with_its_photographs(app):
    window, where = fresh_window(app, "w-palace")
    photo = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 64, 48)
    photo.fill(0x336699ff)
    photo.savev(str(where.learning / "palaces" / "door.png"), "png", [], [])
    window.pages["palaces"].walk("The way to school")
    view = window.activity
    assert view.picture.get_visible(), "the front door's photograph is shown"
    press(window, Gdk.KEY_space)
    press(window, Gdk.KEY_space)                 # got it
    assert not view.picture.get_visible(), "the lift mirror has no photograph"
    press(window, Gdk.KEY_space)
    press(window, Gdk.KEY_1)                     # missed
    press(window, Gdk.KEY_space)
    press(window, Gdk.KEY_space)
    assert view.stack.get_visible_child_name() == "summary"
    line = json.loads((where.learning / "drills" / "arch-school.jsonl")
                      .read_text("utf-8").splitlines()[-1])
    assert line["drill"] == "palace" and line["title"] == "The way to school"
    assert abs(line["score"] - 2 / 3) < 0.01
    window.close_activity()
    window.close()


def test_a_doomsday_drill_is_scored_and_logged(app):
    window, where = fresh_window(app, "w-doom")
    drill = trainers.load_drills(where)[0]
    window.pages["trainers"].start(drill)
    view = window.activity
    assert settle(lambda: view.round_label.get_text() == "1 of 3")
    for _ in range(3):
        truth = trainers.weekday(view.day)
        view.handle_key(getattr(Gdk, f"KEY_{(truth + 1) % 7}"), Gdk.ModifierType(0))
        assert view.method.get_visible(), "a miss shows the method"
        view.handle_key(Gdk.KEY_space, Gdk.ModifierType(0))
    assert view.stack.get_visible_child_name() == "summary"
    result = trainers.DrillLog(where.learning / "drills").read()[-1]
    assert result.drill == "doom-year" and result.score == 0 and result.n == 3
    window.close_activity()
    window.pages["trainers"].reload()
    window.close()


def test_the_observation_drill_finds_a_change_where_it_was_made(app):
    window, where = fresh_window(app, "w-observe")
    folder = where.home / "Pictures" / "Drill"
    folder.mkdir(parents=True)
    for n in range(2):
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 200, 150)
        pixbuf.fill(0x204060ff + n * 0x10000000)
        GdkPixbuf.Pixbuf.new_subpixbuf(pixbuf, 20, 20, 60, 40).fill(0xe0c040ff)
        pixbuf.savev(str(folder / f"p{n}.png"), "png", [], [])
    drill = next(d for d in trainers.load_drills(where) if d.trainer == "observation")
    window.pages["trainers"].start(drill)
    view = window.activity
    assert settle(lambda: view.phase == "study"), view.feedback.get_text()
    assert settle(lambda: view.phase == "find", 3000), "the look never ended"
    pump(100)
    change = view.change
    width, height = view.picture.get_width(), view.picture.get_height()
    scale = min(width / view.size[0], height / view.size[1])
    ox, oy = (width - view.size[0] * scale) / 2, (height - view.size[1] * scale) / 2
    view._clicked(None, 1, ox + (change.x + change.w / 2) * scale,
                  oy + (change.y + change.h / 2) * scale)
    assert view.run.marks[0][0], "a click on the change is a hit"
    window.close_activity()
    window.close()


def test_a_changed_photograph_differs_only_where_it_was_changed(app):
    from mnemosyne import observe

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 120, 90)
    pixbuf.fill(0x406080ff)
    # Off-centre, so that mirroring the region moves it.
    GdkPixbuf.Pixbuf.new_subpixbuf(pixbuf, 10, 10, 15, 30).fill(0xff0000ff)
    for kind in trainers.CHANGES:
        change = trainers.Change(kind, 5, 5, 40, 40, 60, 40)
        changed = observe.apply(pixbuf, change)
        before = pixbuf.read_pixel_bytes().get_data()
        after = changed.read_pixel_bytes().get_data()
        stride = pixbuf.get_rowstride()
        differs = {(i % stride) // 3 for i in range(len(before)) if before[i] != after[i]}
        rows = {i // stride for i in range(len(before)) if before[i] != after[i]}
        assert differs and max(differs) < 45 and max(rows) < 45, (kind, max(differs))


def test_the_other_pages_draw_without_complaint(app):
    window, where = fresh_window(app, "w-pages")
    for name in ("decks", "paper", "palaces", "trainers", "week"):
        window.show_page(name)
        pump(60)
    decks = window.pages["decks"]
    titles = []
    row = decks.list.get_first_child()
    while row is not None:
        titles.append(row.get_title())
        row = row.get_next_sibling()
    assert "Biology" in titles and "Major system" in titles, titles
    decks.search.set_text("major")
    assert settle(lambda: decks.list.get_first_child() is not None
                  and decks.list.get_first_child().get_next_sibling() is None, 2000)
    window.pages["week"].reload()
    window.show_preferences()
    pump(100)
    window.close()


def test_a_review_shows_a_cards_pictures_and_marks_a_leech(app):
    window, where = fresh_window(app, "w-pictures")
    photo = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 80, 60)
    photo.fill(0x55aa77ff)
    photo.savev(str(where.studies / "Biology" / "root.png"), "png", [], [])
    write(where.studies / "Biology" / "pics.md", "Mi ez? ![](root.png) :: gyökér\n")
    settle(lambda: not window._refreshing)
    window.library.refresh()
    window.study_note(str(where.studies / "Biology" / "pics.md"))
    pump(100)
    view = window.activity
    assert view is not None, "the note was not studied"
    assert view.front_pics.get_first_child() is not None, "no picture"
    assert view.front_pics.get_visible(), "the picture is hidden"
    assert not view.leech_tag.get_visible()
    window.close_activity()
    window.close()


def test_a_test_date_shows_on_its_deck_and_in_the_week(app):
    window, where = fresh_window(app, "w-tests")
    soon = cards.now().date() + timedelta(days=9)
    assert window.library.set_test("Studies/Biology", soon)
    settle(lambda: not window._refreshing)
    window.library.refresh()
    window.reload_pages()
    assert window.pages["decks"].tests
    row = window.pages["decks"].list.get_first_child()
    subtitles = []
    while row is not None:
        subtitles.append(row.get_subtitle())
        row = row.get_next_sibling()
    assert any("test in 9 days" in s for s in subtitles), subtitles
    window.pages["week"].reload()
    assert window.pages["week"].tests.get_visible()
    window.pages["week"].print_week()
    assert settle(lambda: window.launched), "the week was never printed"
    assert window.launched[-1].endswith(".pdf")
    window.close()


def test_a_deck_switched_off_in_preferences_leaves_the_queue(app):
    window, where = fresh_window(app, "w-off")
    before = window.library.counts()["new"]
    assert window.library.set_deck("Major system", False)
    settle(lambda: not window._refreshing)
    window.library.refresh()
    window.reload_pages()
    assert "Major system" in window.library.off
    assert "Major system" not in window.library.deck.decks()
    assert window.library.counts()["new"] <= before
    from mnemosyne.prefs import preferences

    dialog = preferences(window)
    assert dialog is not None
    window.close()


class FakePalace:
    """The host side of Mind Palace's room contract, as little of it as a room uses."""

    def __init__(self, app):
        self.app = app
        self.parent = None
        self.toasts, self.opened, self.asked, self.shown = [], [], [], []

    def toast(self, text, undo=None):
        self.toasts.append(text)

    def open(self, path):
        self.opened.append(str(path))
        return True

    def show(self, title):
        self.shown.append(title)

    def ask_claude(self, prompt, files):
        self.asked.append((prompt, list(files)))


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()


def test_mnemosyne_is_a_room_of_the_palace(app):
    from mnemosyne import room

    where = make_home("w-room")
    use_home(where)
    host = FakePalace(app)
    assert room.TITLE == "Mnemosyne" and isinstance(room.ORDER, int)
    assert os.path.isfile(os.path.join(ROOT, f"{room.ICON}.svg"))
    opened = room.build(host)
    widget = opened.widget
    assert not any(isinstance(w, Adw.HeaderBar) for w in _descendants(widget)), \
        "a room has no header bar of its own"
    assert opened.window not in app.get_windows(), "a room must not keep the app alive"
    assert all(action.startswith("room.") for action, _keys in opened.accels)

    palace = Adw.ApplicationWindow(application=app)
    palace.set_default_size(455, 720)
    palace.set_content(widget)
    widget.insert_action_group("room", opened.actions)
    palace.present()
    assert settle(lambda: opened.window.library.loaded and not opened.window._refreshing)
    pump(100)
    assert palace.measure(Gtk.Orientation.HORIZONTAL, -1)[0] <= 455

    widget.activate_action("room.page-week", None)
    assert opened.window.views.get_visible_child_name() == "week"
    note = where.studies / "Biology" / "2026-10-22.md"
    assert not opened.open(str(note)), "a plain note is Notedown's"
    assert opened.open(f"study:{note}")
    view = opened.window.activity
    assert view is not None and view.session.total == 4
    view.handle_key(Gdk.KEY_e, Gdk.ModifierType(0))
    assert host.opened == [str(where.home / view.session.current.card.path)]
    opened.window.assistant.available = True
    opened.window.assistant.explain(view.session.current.card)
    assert host.asked and host.asked[0][1][0].endswith("2026-10-22.md")
    opened.window.close_activity()
    opened.save_state()
    assert opened.can_close()
    palace.close()


def test_the_content_works_hosted_in_another_window(app):
    """What Mind Palace does: take the content, host it, act through "win"."""
    window, where = fresh_window(app, "w-hosted")
    content = window.get_content()
    window.set_content(None)
    host = Adw.ApplicationWindow(application=app)
    host.set_content(content)
    content.insert_action_group("win", window)
    host.present()
    pump(100)
    content.activate_action("win.page-week", None)
    assert window.views.get_visible_child_name() == "week"
    controllers = [window.toasts.observe_controllers().get_item(i)
                   for i in range(window.toasts.observe_controllers().get_n_items())]
    assert any(isinstance(c, Gtk.ShortcutController) for c in controllers)
    content.activate_action("win.study-today", None)
    pump(60)
    assert window.activity is not None
    window.close_activity()
    host.close()
    window.close()


def main():
    print("Mnemosyne tests")
    print("\nwithout a display")
    for name, fn in sorted(
            (n[5:].replace("_", " "), f) for n, f in globals().items()
            if n.startswith("test_") and f.__code__.co_argcount == 0):
        check(name, fn)

    print("\nwith a display")
    from gi.repository import Adw as _Adw, Gdk as _Gdk, GdkPixbuf as _GdkPixbuf  # noqa: F811
    from gi.repository import Gio, GLib as _GLib, Gtk as _Gtk  # noqa: F811

    globals().update(Adw=_Adw, Gdk=_Gdk, GdkPixbuf=_GdkPixbuf, GLib=_GLib, Gtk=_Gtk)
    from mnemosyne import widgets
    from mnemosyne.window import MainWindow as _MainWindow

    globals()["MainWindow"] = _MainWindow
    application = _Adw.Application(application_id="com.robin.MnemosyneTests",
                                   flags=Gio.ApplicationFlags.NON_UNIQUE)

    def run_gtk_tests():
        widgets.install_css()
        for name, fn in sorted(
                (n[5:].replace("_", " "), f) for n, f in globals().items()
                if n.startswith("test_") and f.__code__.co_argcount == 1):
            check(name, lambda fn=fn: fn(application))
        application.release()
        application.quit()
        return _GLib.SOURCE_REMOVE

    def activate(instance):
        instance.hold()
        _GLib.timeout_add(150, run_gtk_tests)

    application.connect("activate", activate)
    application.run([])

    if failures:
        print(f"\n{len(failures)} failed:")
        for name, exc in failures:
            print(f"  {name}: {exc}")
        raise SystemExit(1)
    print("\nall good")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
