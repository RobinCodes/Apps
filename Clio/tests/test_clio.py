"""Clio's suite: what is checked without a screen, and what needs one.

    python3 tests/test_clio.py

The first half needs nothing but Python: the axis from centuries to days, where
marks and braces and names go, what the date fields accept, what is read out of
Claude's answer, the library and the command line. The second half builds the real
view — in its own window and in a stand-in for Mind Palace — against a scratch
Order, and drives it: adding, editing and taking away, grouping into a span,
filters and the list, a file changed elsewhere, Claude played by a script.

Nothing here touches the real brain or reaches a model: `ORDER_HOME` points at the
scratch folder, and `claude` is a stand-in that answers from a file.
"""

import ast
import os
import shutil
import sys
import tempfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

scratch = tempfile.mkdtemp(prefix="clio-tests-")
for name, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                     ("XDG_CACHE_HOME", "cache"), ("LOCALAPPDATA", "local")):
    os.environ[name] = os.path.join(scratch, folder)
os.environ["APPS_NO_UPDATE"] = "1"
HOME = os.path.join(scratch, "home")
os.environ["ORDER_HOME"] = HOME
os.environ.pop("ANAM_HOME", None)
os.environ["ORDER_CLAUDE"] = sys.executable          # "installed" — never asked
BRAIN = os.path.join(HOME, "SecondBrain")
BOARDS = os.path.join(BRAIN, "timelines")
os.makedirs(BOARDS, exist_ok=True)

from clio import asking, cli, editing, layout, listmode  # noqa: E402
from clio.axis import MAX_PPD, MIN_PPD, YEAR, Axis, age_at  # noqa: E402
from clio.library import Library  # noqa: E402
from clio.timelines import Board, Item, When, days_from_civil, parse_lines  # noqa: E402

failures = []

LIFE = """---
kind: timeline
title: My life
born: 2011-10-10        # the axis also shows age
colours:
  family: "#c0623b"
  school: "#4f7cac"
---

Text before the first section describes the board.

## Events
- 2011-10-10 · Born {family} !
- ~2017-09 · First day at school {school}
  Indented lines are its notes; see [[Primary school]].
- 2021-03 · First message on Discord {projects}
- 2023 · The year of the bicycle

## Spans
- 2017-09 .. 2025-06 · Primary school {school}
- 2019-09 .. 2021-06 · Lower years {school}
- 2025-09 .. · Szent István Gimnázium {school}

## Something Clio does not know about
Kept as it is.
"""

ARPAD = """---
kind: timeline
title: The Árpád house
---

## Events
- 0895 · The conquest
- 1000-12-25 · Stephen crowned {kings} !
- 1241-04-11 · Battle of Mohi {war}
- -0044-03-15 · Caesar, for scale

## Spans
- 0895 .. 1301 · The house of Árpád {kings}
- 1235 .. 1270 · Béla IV {kings}
"""


def check(name, fn):
    try:
        fn()
    except BaseException as exc:  # noqa: BLE001 - reported at the end
        import traceback

        failures.append((name, exc))
        print(f"  FAIL  {name}: {exc!r}")
        for line in traceback.format_exc().strip().splitlines()[-4:]:
            print(f"        {line}")
        return
    print(f"  ok  {name}")


def write(name, text):
    path = os.path.join(BOARDS, name)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def clear_brain():
    for name in os.listdir(BOARDS):
        os.remove(os.path.join(BOARDS, name))


def widths(text, bold):
    return len(text) * (8.5 if bold else 7.5)


# ------------------------------------------------------- without a display --

def test_a_date_field_takes_the_format_and_forgives_a_little():
    assert str(editing.read_date("2017-09")) == "2017-09"
    assert str(editing.read_date("~1848-03-15")) == "~1848-03-15"
    assert str(editing.read_date("-0044-03-15")) == "-0044-03-15"
    # What nobody would mean as anything else; the file gets the strict form.
    assert str(editing.read_date("896")) == "0896"
    assert str(editing.read_date("-44")) == "-0044"
    assert str(editing.read_date("44 BC")) == "-0044"
    assert str(editing.read_date("~44 BC")) == "~-0044"
    for wrong in ("", "soon", "2017-13", "2017-02-30", "20170", "0"):
        assert editing.read_date(wrong) is None, wrong


def test_a_range_says_what_is_wrong_with_it():
    assert editing.read_range("2017", "", False) == (When(2017), None, "")
    assert editing.read_range("2017", "", True) == (When(2017), None, "")
    start, end, wrong = editing.read_range("2017-09", "2025-06", True)
    assert not wrong and str(end) == "2025-06"
    assert editing.read_range("", "", False)[2] == "It needs a date"
    assert "YYYY" in editing.read_range("soon", "", False)[2]
    assert editing.read_range("2020", "2010", True)[2] == "It ends before it starts"
    # A span may end in the very year it starts.
    assert not editing.read_range("2020-05", "2020", True)[2]


def test_categories_are_read_without_braces_or_repeats():
    assert editing.read_categories("school, {family},, school ,#abc") == ["school", "family"]


def test_the_axis_keeps_the_day_under_the_pointer_when_it_zooms():
    axis = Axis(centre=0.0, ppd=1.0, width=800.0)
    day = axis.day(200.0)
    axis.zoom(3.0, 200.0)
    assert abs(axis.x(day) - 200.0) < 1e-6
    axis.zoom(1e12)
    assert axis.ppd == MAX_PPD
    axis.zoom(1e-12)
    assert axis.ppd == MIN_PPD


def test_the_axis_marks_centuries_years_months_and_days():
    axis = Axis(width=800.0)
    axis.show(days_from_civil(895, 1, 1), days_from_civil(1301, 1, 1))
    labels = [t.label for t in axis.ticks() if t.major]
    assert "1000" in labels and "1200" in labels, labels

    axis.show(days_from_civil(2011, 1, 1), days_from_civil(2026, 1, 1))
    labels = [t.label for t in axis.ticks() if t.major]
    assert "2020" in labels and all(len(x) == 4 for x in labels), labels

    axis.show(days_from_civil(2025, 1, 1), days_from_civil(2026, 1, 1))
    labels = [t.label for t in axis.ticks() if t.major]
    assert any(x.startswith(("Apr", "Mar", "Jul")) for x in labels), labels

    axis.show(days_from_civil(2025, 3, 1), days_from_civil(2025, 3, 15))
    labels = [t.label for t in axis.ticks() if t.major]
    assert "1 Mar" in labels or "3 Mar" in labels, labels


def test_labelled_marks_stand_apart_at_every_zoom():
    axis = Axis(centre=days_from_civil(2000, 1, 1), ppd=MAX_PPD, width=800.0)
    while axis.ppd > MIN_PPD:
        days = [t.day for t in axis.ticks() if t.major]
        gaps = [(b - a) * axis.ppd for a, b in zip(days, days[1:])]
        # A month is shorter than an average one, so a little under the mark is fair.
        assert not gaps or min(gaps) >= 60, (axis.ppd, min(gaps))
        assert len(axis.ticks()) < 400, axis.ppd
        axis.zoom(0.7)


def test_the_years_before_the_common_era_run_on_to_ad_1():
    axis = Axis(width=2000.0)
    axis.show(days_from_civil(-15, 1, 1), days_from_civil(15, 1, 1))
    labels = [t.label for t in axis.ticks() if t.major]
    assert any(label.endswith(" BC") for label in labels), labels
    assert "0" not in labels and labels.count("1") <= 1, labels

    axis = Axis(width=800.0)
    axis.show(days_from_civil(-3, 1, 1), days_from_civil(4, 1, 1))
    labels = [t.label for t in axis.ticks() if t.major]
    assert labels.count("1") == 1 and "0" not in labels, labels


def test_a_jump_shows_a_date_as_precisely_as_it_was_written():
    axis = Axis(width=800.0)
    for text, wide in (("1241", 3 * YEAR), ("1241-04", 180), ("1241-04-11", 30)):
        when = When.parse(text)
        axis.jump(when)
        assert axis.day(0) < when.first_day() and when.end_day() < axis.day(800)
        assert axis.day(800) - axis.day(0) >= wide


def test_the_view_cannot_run_away_past_the_format():
    axis = Axis(ppd=MAX_PPD, width=800.0)
    axis.pan(-1e15)
    assert axis.centre <= 4_000_000
    axis.pan(1e15)
    assert axis.centre >= -4_000_000
    axis.zoom(1e-12)
    assert axis.ticks() is not None


def test_ages_are_marked_from_the_birthday_on():
    born = When.parse("2011-10-10")
    axis = Axis(width=800.0)
    axis.show(days_from_civil(2009, 1, 1), days_from_civil(2027, 1, 1))
    ages = axis.ages(born)
    assert ages and all(age >= 0 for _d, age in ages)
    assert (days_from_civil(2011, 10, 10), 0) in ages
    assert all(day >= days_from_civil(2011, 10, 10) for day, _a in ages)
    assert age_at(born, days_from_civil(2026, 10, 9)) == 14
    assert age_at(born, days_from_civil(2026, 10, 10)) == 15
    # Zoomed right in between two birthdays, there is no mark to put.
    axis.show(days_from_civil(2026, 3, 1), days_from_civil(2026, 3, 8))
    assert axis.ages(born) == []


def test_a_leap_day_birthday_is_kept_in_the_years_without_one():
    born = When.parse("2012-02-29")
    assert age_at(born, days_from_civil(2013, 2, 28)) == 0
    assert age_at(born, days_from_civil(2013, 3, 1)) == 1
    assert age_at(born, days_from_civil(2016, 2, 29)) == 4


def _scene(text=LIFE, first=(2009, 1, 1), last=(2027, 1, 1), width=900.0, height=560.0,
           **keywords):
    board = Board.parse(text, "x.md")
    axis = Axis(width=width)
    axis.show(days_from_civil(*first), days_from_civil(*last))
    keywords.setdefault("today", date(2026, 11, 3))
    return board, axis, layout.arrange(board, axis, height, widths, **keywords)


def _by_title(scene, title):
    for mark in scene.marks:
        if mark.item.title == title:
            return mark
    for brace in scene.braces:
        if brace.item.title == title:
            return brace
    raise AssertionError(title)


def test_an_event_is_drawn_as_precisely_as_its_date_says():
    _b, axis, scene = _scene(first=(2022, 6, 1), last=(2024, 6, 1))
    year = _by_title(scene, "The year of the bicycle")
    assert year.band, "a year is a mark across the year, not a point on 1 January"
    assert abs(year.x1 - axis.x(days_from_civil(2023, 1, 1))) < 1
    assert abs(year.x2 - axis.x(days_from_civil(2024, 1, 1))) < 1
    _b, _a, scene = _scene(first=(1990, 1, 1), last=(2030, 1, 1))
    born = _by_title(scene, "Born")
    assert not born.band and born.radius > _by_title(scene, "First message on Discord").radius


def test_names_never_lie_across_each_other():
    many = "\n".join(f"- 2020-{m:02d}-{d:02d} · Event number {m}.{d} {{a}}"
                     for m in range(1, 13) for d in (3, 12, 24))
    text = f"---\nkind: timeline\ntitle: Crowded\n---\n\n## Events\n{many}\n"
    _b, _a, scene = _scene(text, first=(2019, 6, 1), last=(2021, 6, 1), width=455.0, height=400.0)
    named = [m for m in scene.marks if m.row >= 0]
    assert named and len(named) < len(scene.marks), "some must give way at this width"
    for i, a in enumerate(named):
        for b in named[i + 1:]:
            if a.row == b.row:
                assert (a.label_x + a.label_w <= b.label_x
                        or b.label_x + b.label_w <= a.label_x), (a.item, b.item)
        assert a.label_y >= 0 and a.label_x >= 0 and a.label_x + a.label_w <= 455


def test_a_milestone_is_the_last_to_lose_its_name():
    many = "\n".join(f"- 2020-06-{d:02d} · A very ordinary event {d}" for d in range(1, 29))
    text = (f"---\nkind: timeline\n---\n\n## Events\n{many}\n"
            f"- 2020-06-15 · The turning point !\n")
    _b, _a, scene = _scene(text, first=(2015, 1, 1), last=(2025, 1, 1), width=455.0, height=300.0)
    assert _by_title(scene, "The turning point").row == 0
    assert any(m.row < 0 for m in scene.marks)


def test_spans_inside_spans_stack_outwards_brace_beside_brace():
    _b, _a, scene = _scene()
    inner = _by_title(scene, "Lower years")
    outer = _by_title(scene, "Primary school")
    assert inner.lane < outer.lane and inner.y < outer.y
    # The next school does not overlap either: it can sit nearest the events.
    assert _by_title(scene, "Szent István Gimnázium").lane == 0
    assert inner.y > scene.axis_y + layout.TICKS
    assert outer.x1 < inner.x1 < inner.x2 < outer.x2


def test_a_brace_keeps_its_lane_as_the_view_moves():
    first = _by_title(_scene()[2], "Szent István Gimnázium")
    moved = _by_title(_scene(first=(2024, 1, 1), last=(2027, 1, 1))[2], "Szent István Gimnázium")
    assert first.lane == moved.lane and first.y == moved.y


def test_the_name_of_a_long_span_stays_on_screen():
    _b, axis, scene = _scene(ARPAD, first=(1000, 1, 1), last=(1100, 1, 1), width=455.0)
    house = _by_title(scene, "The house of Árpád")
    assert house.x1 < 0 and house.x2 > 455
    assert 0 <= house.label_x and house.label_x + house.label_w <= 455
    assert abs(house.point - 455 / 2) < 1


def test_the_names_of_one_lane_keep_off_each_other():
    text = ("---\nkind: timeline\n---\n\n## Spans\n"
            "- 2020-01 .. 2020-02 · A span with quite a long name indeed\n"
            "- 2020-03 .. 2020-04 · Another span with quite a long name\n")
    _b, _a, scene = _scene(text, first=(2015, 1, 1), last=(2025, 1, 1), width=455.0)
    a, b = scene.braces
    assert a.lane == b.lane
    assert a.label_x + a.label_w <= b.label_x or not b.label_w


def test_a_filter_hides_only_what_has_nothing_else_showing():
    text = ("---\nkind: timeline\n---\n\n## Events\n- 2020 · School {school}\n"
            "- 2021 · Both {school, family}\n- 2022 · Neither\n")
    _b, _a, scene = _scene(text, first=(2015, 1, 1), last=(2025, 1, 1),
                           hidden=frozenset({"school"}))
    assert {m.item.title for m in scene.marks} == {"Both", "Neither"}


def test_what_hangs_off_the_bottom_can_be_reached():
    spans = "\n".join(f"- {1900 + i} .. {2100 - i} · Layer {i}" for i in range(9))
    text = f"---\nkind: timeline\n---\n\n## Spans\n{spans}\n"
    _b, _a, scene = _scene(text, first=(1890, 1, 1), last=(2110, 1, 1), height=320.0)
    assert scene.overflow > 0
    deepest = max(b.y for b in scene.braces)
    assert deepest + layout.LANE <= 320 + scene.overflow + layout.BOTTOM


def test_the_axis_holds_still_as_you_zoom():
    _b, _a, wide = _scene(ARPAD, first=(800, 1, 1), last=(1400, 1, 1))
    _b, _a, close = _scene(ARPAD, first=(1230, 1, 1), last=(1250, 1, 1))
    assert wide.axis_y == close.axis_y
    # A sparse board is not pushed to the bottom of the window.
    assert wide.axis_y < 560 - layout.TICKS - 5 * layout.LANE


def test_things_can_be_found_where_they_are_drawn():
    _b, _a, scene = _scene()
    born = _by_title(scene, "Born")
    assert scene.mark_at(born.centre, scene.axis_y) is born
    assert scene.mark_at(born.label_x + 3, born.label_y + 5) is born
    assert scene.mark_at(born.centre, scene.axis_y + 60) is None
    school = _by_title(scene, "Primary school")
    assert scene.brace_at((school.x1 + school.x2) / 2, school.y + 3) is school
    assert scene.brace_at(school.label_x + 3, school.label_y + 4) is school


def test_a_span_around_a_selection_covers_it_as_precisely_as_it_was_written():
    items = parse_lines("- 2017-09 · A {school, family}\n- ~2019 · B {school}\n"
                        "- 2018-03-04 · C {school}\n")
    span = editing.span_around(items)
    assert str(span.start) == "2017-09" and str(span.end) == "~2019" and span.span
    assert span.categories == ["school"] and not span.title
    with_open = items + parse_lines("- 2019 .. · Still going\n")
    assert editing.span_around(with_open).open_end


def test_an_edit_keeps_the_section_in_date_order_and_touches_nothing_else():
    board = Board.parse(LIFE, "x.md")
    item = next(i for i in board.events if i.title == "First message on Discord")
    editing.write_item(board, item, start=When(2012), end=None, span=False,
                       title="First message on Discord", categories=["projects"],
                       milestone=False, notes=[])
    text = board.dump()
    assert text.index("- 2012 · First message") < text.index("- ~2017-09 · First day")
    assert "  Indented lines are its notes; see [[Primary school]].\n" in text
    assert "# the axis also shows age" in text and "Kept as it is." in text


def test_an_event_made_a_span_moves_to_the_spans():
    board = Board.parse(LIFE, "x.md")
    item = next(i for i in board.events if i.title == "First message on Discord")
    editing.write_item(board, item, start=When(2021, 3), end=When(2022), span=True,
                       title="Discord", categories=[], milestone=False, notes=[])
    text = board.dump()
    assert text.index("## Spans") < text.index("- 2021-03 .. 2022 · Discord")


def test_a_renamed_category_follows_onto_every_item_and_its_colour():
    board = Board.parse(LIFE, "x.md")
    editing.rename_category(board, "school", "lessons")
    text = board.dump()
    assert "{school}" not in text and "- 2017-09 .. 2025-06 · Primary school {lessons}" in text
    assert board.colours["lessons"] == "#4f7cac" and "school" not in board.colours


def test_a_board_does_not_grow_colours_for_being_looked_at():
    board = Board.parse(ARPAD, "x.md")
    editing.write_categories(board, editing.category_rows(board))
    assert not board.changed and board.dump() == ARPAD
    editing.write_categories(board, [("kings", "#112233"), ("war", board.category_colour("war"))])
    assert board.colours == {"kings": "#112233"}
    # A category nothing wears yet has to be written, or it would not exist.
    editing.write_categories(board, [("kings", "#112233"), ("war", board.category_colour("war")),
                                     ("plague", "#445566")])
    assert board.colours == {"kings": "#112233", "plague": "#445566"}
    assert "plague" in board.categories()


def test_claudes_answer_becomes_ghosts_and_only_new_ones():
    board = Board.parse(ARPAD, "x.md")
    prompt = asking.prompt(board, "add the Árpád kings")
    assert "add the Árpád kings" in prompt and "kings" in prompt and "do not edit" in prompt
    found = asking.read("Here you are:\n```\n"
                        "- 1000-12-25 · Stephen crowned {kings} !\n"
                        "* 1077 .. 1095 · Ladislaus I {kings}\n"
                        "- 1205 · Andrew II {kings}\n"
                        "- 1205 · Andrew II {kings}\n"
                        "- nonsense\n```\nHope it helps.", board)
    assert [(str(i.start), i.title, i.span) for i in found] == [
        ("1077", "Ladislaus I", True), ("1205", "Andrew II", False)]
    assert asking.read("I would rather not.", board) == []


def test_the_library_knows_what_it_wrote_and_what_it_did_not():
    clear_brain()
    path = write("My life.md", LIFE)
    library = Library()
    library.refresh()
    board = library.find(path)
    assert board is not None and not library.changed_elsewhere(board)
    assert library.on_disk() == library._stamps
    board.add(Item(When(2024), "Written here"))
    library.save(board)
    assert not library.changed_elsewhere(board) and library.on_disk() == library._stamps
    assert "- 2024 · Written here" in read(path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\nWritten elsewhere.\n")
    assert library.changed_elsewhere(board) and library.on_disk() != library._stamps
    made = library.new_board("Árpád: a house", When(895))
    assert made.path.exists() and library.find(made.path) is made and made.born == When(895)
    assert "title: " in read(made.path) and "## Events" in read(made.path)


def test_an_untouched_board_is_written_back_exactly():
    clear_brain()
    with_crlf = LIFE.replace("\n", "\r\n")
    path = write("Windows.md", with_crlf)
    library = Library()
    library.refresh()
    library.save(library.find(path))
    assert read(path) == with_crlf


def test_the_list_is_in_order_and_says_when_and_how_old():
    board = Board.parse(LIFE, "x.md")
    titles = [i.title for i in listmode.in_order(board)]
    assert titles[0] == "Born"
    assert titles.index("Primary school") < titles.index("First day at school")
    lower = next(i for i in board.spans if i.title == "Lower years")
    detail = listmode.details(board, lower)
    assert "September 2019 – June 2021" in detail and "age 7" in detail and "school" in detail
    still = next(i for i in board.spans if i.open_end)
    assert "– now" in listmode.details(board, still)
    hidden = [i.title for i in listmode.in_order(board, frozenset({"school"}))]
    assert "Primary school" not in hidden and "Born" in hidden


def test_the_command_line_answers_without_a_window():
    assert cli.main(["--version"]) == 0
    assert cli.main(["--help"]) == 0
    assert cli.main([]) is None and cli.main(["board.md"]) is None


def test_the_room_says_what_it_is_in_plain_literals():
    # The palace reads these with `ast`, without importing anything (ROOMS.md).
    with open(os.path.join(ROOT, "clio", "room.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
             if isinstance(node, ast.Assign) and node.targets[0].id.isupper()}
    assert names["TITLE"] == "Clio" and names["ICON"] == "clio" and names["ORDER"] == 30
    assert names["KINDS"] == ("timeline",)


# ---------------------------------------------------------- with a display --

STANDIN = """import os, sys
for _ in sys.stdin:
    break
reply = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "reply.txt"),
             encoding="utf-8").read()
import json
print(json.dumps({"type": "result", "subtype": "success", "result": reply,
                  "session_id": "s"}))
"""


def open_board(view, name="My life.md", text=LIFE):
    clear_brain()
    path = write(name, text)
    view.current = None
    view._views.clear()
    view.reload()
    view.open_board(view.library.find(path))
    pump(120)
    return path, view.current


def dialog_of(view):
    pump(60)
    dialog = view.get_root().get_visible_dialog()
    assert dialog is not None, "no dialog came up"
    return dialog


def closed(dialog):
    if dialog.get_root() is not None:
        dialog.force_close()
    pump(40)


def test_a_board_opens_with_all_of_it_in_view(view):
    _path, board = open_board(view)
    assert view.pages.get_visible_child_name() == "board"
    assert view.title_label.get_label() == "My life"
    axis = view.canvas.axis
    first, last = axis.span()
    assert first <= board.items[0].first_day() and today_after(last)
    assert view.canvas.scene.marks and view.canvas.scene.braces


def today_after(last):
    from clio.timelines import today_days

    return today_days() <= last


def test_a_new_event_is_written_at_once_in_the_files_own_format(view):
    path, _board = open_board(view)
    view.new_event(When(2024, 5))
    dialog = dialog_of(view)
    assert dialog.start_entry.get_text() == "2024-05"
    assert not dialog.save_button.get_sensitive(), "it needs a title"
    dialog.title_entry.set_text("  The move ")
    dialog.categories_entry.set_text("family, {new}")
    dialog.milestone.set_active(True)
    buffer = dialog.notes.get_buffer()
    buffer.set_text("It rained.\n\nA lot.\n")
    assert dialog.save_button.get_sensitive()
    dialog.save()
    pump(60)
    text = read(path)
    assert "- 2024-05 · The move {family, new} !\n  It rained.\n  A lot.\n" in text, text
    assert text.index("- 2023 · The year") < text.index("- 2024-05 · The move")
    assert text.index("- 2024-05 · The move") < text.index("## Spans")
    assert "# the axis also shows age" in text and "Kept as it is." in text


def test_a_date_that_is_not_one_cannot_be_saved(view):
    path, _board = open_board(view)
    before = read(path)
    view.new_event(When(2024))
    dialog = dialog_of(view)
    dialog.title_entry.set_text("Something")
    dialog.start_entry.set_text("sometime in spring")
    assert not dialog.save_button.get_sensitive()
    assert "YYYY" in dialog.echo.get_label()
    dialog.save()
    assert read(path) == before
    dialog.start_entry.set_text("-44")
    assert dialog.save_button.get_sensitive() and "44 BC" in dialog.echo.get_label()
    closed(dialog)


def test_a_span_with_no_end_runs_to_today(view):
    path, board = open_board(view)
    view.new_span()
    dialog = dialog_of(view)
    assert dialog.span_button.get_active() and dialog.end_entry.get_visible()
    dialog.start_entry.set_text("2026-09")
    dialog.title_entry.set_text("This school year")
    assert "– now" in dialog.echo.get_label()
    dialog.save()
    pump(60)
    assert "- 2026-09 .. · This school year\n" in read(path)
    assert any(i.open_end and i.title == "This school year" for i in board.spans)


def test_editing_changes_only_that_line(view):
    path, board = open_board(view)
    item = next(i for i in board.events if i.title == "First message on Discord")
    view.edit_item(item)
    dialog = dialog_of(view)
    assert settle(lambda: not dialog.title_entry.get_selection_bounds(), 1000), \
        "the next key would replace the title"
    dialog.title_entry.set_text("First words on Discord")
    dialog.save()
    pump(60)
    text = read(path)
    expected = LIFE.replace("First message on Discord", "First words on Discord")
    assert text == expected, text


def test_something_taken_away_comes_back_with_undo(view):
    path, board = open_board(view)
    item = next(i for i in board.events if i.title == "First day at school")
    view.delete_items([item])
    assert "First day at school" not in read(path)
    assert item not in board.items
    assert view.last_undo is not None
    view.last_undo()
    assert read(path) == LIFE, "and exactly where it was"
    assert any(i is item for i in board.items)


def test_selecting_several_and_grouping_makes_a_span(view):
    path, board = open_board(view)
    canvas = view.canvas
    canvas.scene = canvas.arranged()
    first = next(i for i in board.events if i.title == "First day at school")
    second = next(i for i in board.events if i.title == "First message on Discord")
    canvas.select([first])
    assert view.action_bar.get_reveal_child() and not view.group_button.get_visible()
    canvas.select([first, second])
    assert view.group_button.get_visible() and "2 selected" in view.selected_label.get_label()
    view.group_selected()
    dialog = dialog_of(view)
    assert dialog.span_button.get_active()
    assert dialog.start_entry.get_text() == "~2017-09"
    assert dialog.end_entry.get_text() == "2021-03"
    assert not dialog.save_button.get_sensitive()
    dialog.title_entry.set_text("The first years")
    dialog.save()
    pump(60)
    assert "- ~2017-09 .. 2021-03 · The first years\n" in read(path)
    canvas.select([])
    assert not view.action_bar.get_reveal_child()


def test_group_with_too_little_selected_only_says_so(view):
    path, _board = open_board(view)
    before = read(path)
    view.canvas.select([])
    view.group_selected()
    pump(30)
    assert view.get_root().get_visible_dialog() is None and read(path) == before


def test_the_canvas_finds_what_is_under_the_pointer(view):
    _path, board = open_board(view)
    canvas = view.canvas
    canvas.scene = canvas.arranged()
    mark = next(m for m in canvas.scene.marks if m.item.title == "Born")
    assert canvas.item_at(mark.centre, canvas.scene.axis_y) is mark.item
    brace = canvas.scene.braces[0]
    assert canvas.item_at((max(brace.x1, 0) + min(brace.x2, canvas.axis.width)) / 2,
                          brace.y + 3) is brace.item
    assert canvas.item_at(canvas.axis.width - 2, 2) is None
    # A sweep across the axis picks up the events on it.
    canvas.select([])
    canvas.sweep(0, canvas.axis.width)
    assert {i.title for i in canvas.selection} == {i.title for i in board.events}
    canvas.select([])


def test_a_double_click_date_is_as_precise_as_the_zoom_can_tell(view):
    open_board(view)
    axis = view.canvas.axis
    axis.ppd = 0.0005
    assert axis.precision() == "year"
    axis.ppd = 0.5
    assert axis.precision() == "month"
    axis.ppd = 8.0
    assert axis.precision() == "day"


def test_zooming_and_jumping_and_coming_back(view):
    # A glide moves one frame at a time, and on a busy machine frames come late. The
    # waits are long because `settle` returns the moment the glide arrives: they only
    # cost time when something is really wrong.
    open_board(view)
    canvas = view.canvas
    canvas.fit(glide=False)
    fitted = canvas.axis.ppd
    view.actions.activate_action("zoom-in", None)
    assert settle(lambda: canvas.axis.ppd > fitted * 1.4, 10000), canvas.axis.ppd
    assert view.jump("2019-03") and canvas.pin == When(2019, 3)
    target = When(2019, 3).middle()
    assert settle(lambda: abs(canvas.axis.centre - target) < 1, 10000), canvas.axis.centre
    assert not view.jump("whenever")
    assert view.jump("today")
    view.actions.activate_action("zoom-fit", None)
    assert settle(lambda: abs(canvas.axis.ppd - fitted) / fitted < 0.01, 10000), canvas.axis.ppd


def test_a_board_is_where_it_was_left(view):
    _path, board = open_board(view)
    other = write("Other.md", ARPAD)
    view.canvas.fly(days_from_civil(2020, 1, 1), 0.5, glide=False)
    view.reload()
    view.current = board if board in view.library.boards else view.library.find(board.path)
    view.open_board(view.library.find(other))
    pump(40)
    assert view.title_label.get_label() == "The Árpád house"
    assert view.canvas.axis.ppd != 0.5
    view.open_board(view.library.find(board.path))
    pump(40)
    assert view.canvas.axis.ppd == 0.5


def test_a_filter_takes_a_category_off_the_axis_and_out_of_the_list(view):
    open_board(view)
    view.set_category_shown("school", False)
    pump(40)
    assert "1 off" in view.filter_button.get_label()
    assert not any("school" in b.item.categories for b in view.canvas.arranged().braces)
    view.mode_button.set_active(True)
    pump(40)
    titles = [row.item.title for row in view.list_mode.rows]
    assert "Primary school" not in titles and "Born" in titles
    view.set_category_shown("school", True)
    pump(40)
    assert "Primary school" in [row.item.title for row in view.list_mode.rows]
    view.fill_filter()
    assert view.filter_popover.get_child() is not None
    view.mode_button.set_active(False)
    pump(30)


def test_the_list_edits_what_is_activated(view):
    path, _board = open_board(view)
    view.mode_button.set_active(True)
    pump(40)
    assert view.modes.get_visible_child_name() == "list"
    row = next(r for r in view.list_mode.rows if r.item.title == "Born")
    view.list_mode.box.emit("row-activated", row)
    dialog = dialog_of(view)
    assert dialog.title_entry.get_text() == "Born" and dialog.milestone.get_active()
    dialog.title_entry.set_text("Arrived")
    dialog.save()
    pump(60)
    assert "- 2011-10-10 · Arrived {family} !" in read(path)
    assert "Arrived" in [r.item.title for r in view.list_mode.rows]
    view.mode_button.set_active(False)
    pump(30)


def test_categories_are_named_and_coloured_in_one_place(view):
    from gi.repository import Gdk

    from clio.dialogs import BoardDialog

    path, board = open_board(view)
    view.board_settings()
    dialog = dialog_of(view)
    assert isinstance(dialog, BoardDialog)
    assert dialog.title_entry.get_text() == "My life"
    assert dialog.born_entry.get_text() == "2011-10-10"
    rows_ = list(dialog._category_rows())
    assert [r.original for r in rows_] == ["family", "school", "projects"]
    colour = Gdk.RGBA()
    colour.parse("#102030")
    rows_[0].colour.set_rgba(colour)
    rows_[1].name.set_text("lessons")
    dialog.new_name.set_text("plague")
    dialog.add_category()
    dialog.title_entry.set_text("A life")
    dialog.born_entry.set_text("bad")
    assert not dialog.save_button.get_sensitive()
    dialog.born_entry.set_text("2011-10-11")
    dialog.save()
    pump(60)
    text = read(path)
    assert "title: A life" in text and "born: 2011-10-11" in text
    assert "family: " in text and "#102030" in text and "lessons: " in text
    assert "plague: " in text and "{lessons}" in text and "{school}" not in text
    assert "projects:" not in text, "its colour is the palette's: no line for it"
    assert view.title_label.get_label() == "A life" and board.born == When(2011, 10, 11)


def test_a_new_board_is_a_file_and_on_show(view):
    clear_brain()
    view.current = None
    view.reload()
    view.new_board()
    dialog = dialog_of(view)
    assert not dialog.make_button.get_sensitive()
    dialog.title_entry.set_text("A project")
    dialog.born_entry.set_text("not a date")
    assert not dialog.make_button.get_sensitive()
    dialog.born_entry.set_text("")
    dialog.make()
    pump(60)
    path = os.path.join(BOARDS, "A project.md")
    assert os.path.exists(path) and view.current.title == "A project"
    assert "kind: timeline" in read(path) and "born" not in read(path)


def test_an_empty_board_can_be_drawn(view):
    clear_brain()
    path = write("Empty.md", "---\nkind: timeline\nborn: 2011-10-10\n---\n")
    view.current = None
    view.reload()
    view.open_board(view.library.find(path))
    pump(100)
    assert view.canvas.scene.marks == [] and view.canvas.scene.braces == []
    view.mode_button.set_active(True)
    pump(40)
    assert view.list_mode.rows == []
    view.mode_button.set_active(False)
    pump(30)


def test_a_folder_that_cannot_be_read_just_now_is_read_later(view):
    open_board(view)

    def refused():
        raise PermissionError(5, "Access is denied")

    view.library.on_disk = refused
    try:
        view.check_disk()            # no exception: it is a moment, not a failure
    finally:
        del view.library.on_disk
    assert view.current is not None
    view.check_disk()


def test_a_file_changed_elsewhere_is_read_again(view):
    path, board = open_board(view)
    view.canvas.fly(days_from_civil(2020, 1, 1), 0.5, glide=False)
    before = view.canvas.view_state()
    write("My life.md", LIFE.replace("The year of the bicycle", "The year of the tandem"))
    os.utime(path, (2_000_000_000, 2_000_000_000))
    view.check_disk()
    pump(40)
    assert view.current is not board
    assert any(i.title == "The year of the tandem" for i in view.current.items)
    assert view.canvas.view_state() == before, "and it stays where it was on the axis"


def test_an_edit_begun_before_the_file_changed_is_not_lost(view):
    path, board = open_board(view)
    item = next(i for i in board.events if i.title == "The year of the bicycle")
    view.edit_item(item)
    dialog = dialog_of(view)
    write("My life.md", LIFE.replace("- 2021-03 ·", "- 2021-04 ·"))
    os.utime(path, (2_000_000_000, 2_000_000_000))
    view.check_disk()
    pump(30)
    dialog.title_entry.set_text("The year of the bike")
    dialog.save()
    pump(60)
    text = read(path)
    assert "- 2023 · The year of the bike\n" in text and "- 2021-04 ·" in text, text


def test_a_board_deleted_elsewhere_closes(view):
    path, _board = open_board(view)
    os.remove(path)
    view.check_disk()
    pump(30)
    assert view.current is None and view.pages.get_visible_child_name() == "empty"


def test_opening_a_file_takes_only_boards(view):
    clear_brain()
    path = write("My life.md", LIFE)
    view.current = None
    view.reload()
    assert view.open(path) and view.current.title == "My life"
    note = os.path.join(scratch, "note.md")
    with open(note, "w", encoding="utf-8") as fh:
        fh.write("---\nkind: note\n---\nA note.\n")
    assert not view.open(note) and view.current.title == "My life"
    elsewhere = os.path.join(scratch, "Board elsewhere.md")
    with open(elsewhere, "w", encoding="utf-8") as fh:
        fh.write(ARPAD)
    assert view.open(elsewhere) and view.current.title == "The Árpád house"


def standin(reply):
    folder = os.path.join(scratch, "claude")
    os.makedirs(folder, exist_ok=True)
    script = os.path.join(folder, "standin.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(STANDIN)
    with open(os.path.join(folder, "reply.txt"), "w", encoding="utf-8") as fh:
        fh.write(reply)
    return [sys.executable, script]


def test_claude_proposes_ghosts_and_nothing_is_written_until_kept(view):
    path, board = open_board(view, "Árpád.md", ARPAD)
    before = read(path)
    view.command = standin("- 1077 .. 1095 · Ladislaus I {kings}\n- 1205 · Andrew II {kings}\n"
                           "- 1000-12-25 · Stephen crowned {kings} !\n")
    view.ask("add the Árpád kings")
    assert settle(lambda: view.proposal or not view.stop_button.get_visible(), 15000)
    assert [i.title for i in view.proposal] == ["Ladislaus I", "Andrew II"], \
        view.ghost_label.get_text()
    assert read(path) == before
    assert [g.title for g in view.canvas.ghosts] == ["Ladislaus I", "Andrew II"]
    scene = view.canvas.arranged()
    assert any(m.ghost for m in scene.marks) and any(b.ghost for b in scene.braces)
    assert view.accept_button.get_visible()

    # One left out by hand, then the rest kept.
    ghost = next(g for g in view.canvas.ghosts if g.title == "Andrew II")
    view.delete_items([ghost])
    assert [g.title for g in view.canvas.ghosts] == ["Ladislaus I"] and read(path) == before
    view.accept()
    text = read(path)
    assert "- 1077 .. 1095 · Ladislaus I {kings}\n" in text and "Andrew II" not in text
    assert not view.canvas.ghosts and not view.proposal
    assert text.index("- 0895 .. 1301") < text.index("- 1077 ..") < text.index("- 1235 ..")

    view.ask("again")
    assert settle(lambda: view.proposal or not view.stop_button.get_visible(), 15000)
    assert [i.title for i in view.proposal] == ["Andrew II"]
    view.discard()
    assert not view.canvas.ghosts and "Andrew II" not in read(path)
    view.command = None


def test_ghosts_do_not_follow_you_to_another_board(view):
    path, _board = open_board(view, "Árpád.md", ARPAD)
    view.command = standin("- 1205 · Andrew II {kings}\n")
    view.ask("add a king")
    assert settle(lambda: view.proposal, 15000)
    other = write("Life.md", LIFE)
    view.reload()
    view.current = view.library.find(path) or view.current
    view.open_board(view.library.find(other))
    pump(30)
    assert not view.canvas.ghosts and not view.proposal
    view.command = None


def test_it_can_be_painted(view):
    from gi.repository import Graphene, Gsk  # noqa: F401

    for name, text in (("My life.md", LIFE), ("Árpád.md", ARPAD)):
        open_board(view, name, text)
        for ppd in (MIN_PPD, 0.001, 0.05, 0.5, 8.0, MAX_PPD):
            view.canvas.axis.ppd = ppd
            view.canvas.axis.centre = days_from_civil(1241, 4, 11)
            view.canvas.queue_draw()
            pump(30)
        view.canvas.fit(glide=False)
        view.canvas.select(view.current.items[:2])
        pump(40)
        view.canvas.select([])


def test_the_room_works_hosted_in_another_window(view_app):
    from gi.repository import Adw

    from clio import room

    class Host:
        def __init__(self):
            self.app = view_app
            self.shown = []
            self.opened = []

        def set_subtitle(self, text=""):
            self.shown.append(text)

        def open(self, path):
            self.opened.append(path)
            return True

    clear_brain()
    host = Host()
    built = room.build(host)
    window = Adw.ApplicationWindow(application=view_app)
    window.set_content(built.widget)
    built.widget.insert_action_group("room", built.actions)
    window.present()
    pump(200)
    path = write("My life.md", LIFE)
    built.view.reload()
    assert built.open(path) and host.shown[-1] == "My life"
    # There is no header bar here to bring the list of boards back from.
    built.view.show_list(False)
    assert built.view.list_button.get_visible()
    built.view.show_list(True)
    note = os.path.join(BRAIN, "A note.md")
    with open(note, "w", encoding="utf-8") as fh:
        fh.write("# A note\n")
    assert not built.open(note)
    assert {a for a, _k in built.accels} >= {"room.new-event", "room.group", "room.jump"}
    # The palace's own keys are not asked for (ROOMS.md).
    taken = {key for _a, keys in built.accels for key in keys}
    assert not taken & {"<Control><Shift>a", "F9", "<Control>question", "<Control>k",
                        "<Control>comma"}, taken
    assert built.context() == [path]
    built.shown()
    built.widget.activate_action("room.toggle-mode", None)
    assert built.view.modes.get_visible_child_name() == "list"
    built.widget.activate_action("room.toggle-mode", None)
    # A link to a note goes to the palace.
    built.view.library.resolve = lambda target, near=None: note
    built.view.follow_link("A note")
    assert host.opened == [note]
    assert built.can_close()
    built.save_state()
    built.shutdown()
    window.close()
    pump(30)


def test_the_window_fits_in_a_tiling_column(window):
    open_board(window.view)
    assert not window.view.list_button.get_visible(), "the header bar has that"
    minimum = window.get_content().measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    assert minimum <= 455, f"the content cannot be made narrower than {minimum}px"


def test_nothing_is_cut_off_at_any_width(view_app):
    # What matters is what is inside: a breakpoint bin reports the size it was asked
    # for whatever its child needs, and clips the child to it. Hosted, because the
    # room has the most in its bar.
    from gi.repository import Adw

    from clio.view import ClioView

    class Host:
        app = view_app

        def set_subtitle(self, text=""):
            pass

    open_at = write("My life.md", LIFE)
    for width in (360, 455, 683, 821, 1100):
        view = ClioView(host=Host())
        shell = Adw.ApplicationWindow(application=view_app, default_width=width,
                                      default_height=520)
        shell.set_content(view)
        shell.present()
        pump(250)
        view.open(open_at)
        view.show_list(False)
        pump(150)
        assert view.get_width() >= width - 2, (width, view.get_width())
        needed = view.toasts.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        assert needed <= width, f"{needed}px of content in a {width}px window"
        assert view.split.get_collapsed() == (width <= 820), width
        if width <= 820:
            assert not view.ask_button.get_visible()
        view.cancel_work()
        shell.destroy()
        pump(40)


def test_no_key_does_two_things(window):
    from clio.view import ACCELERATORS, SHORTCUTS

    app = window.get_application()
    seen = {}
    for action, _keys in ACCELERATORS:
        for accel in app.get_accels_for_action(action):
            _ok, key, mods = Gtk.accelerator_parse(accel)
            pressed = (Gdk.keyval_to_lower(key), int(mods))
            assert pressed not in seen, f"{accel} is both {seen[pressed]} and {action}"
            seen[pressed] = action
        assert window.view.actions.lookup_action(action.split(".", 1)[1]) is not None, action
    sheet = " ".join(keys for _g, rows_ in SHORTCUTS for keys, _w in rows_)
    for keys in ("Ctrl+N", "Ctrl+B", "Ctrl+G", "Ctrl+J", "Ctrl+L", "Ctrl+E", "Ctrl+Shift+A", "F9"):
        assert keys in sheet, keys


# ------------------------------------------------------------------ helpers --

def pump(milliseconds):
    deadline = GLib.get_monotonic_time() + milliseconds * 1000
    context = GLib.MainContext.default()
    while GLib.get_monotonic_time() < deadline:
        while context.pending():
            context.iteration(False)
        GLib.usleep(2000)


def settle(predicate, milliseconds=3000):
    deadline = GLib.get_monotonic_time() + milliseconds * 1000
    context = GLib.MainContext.default()
    while GLib.get_monotonic_time() < deadline:
        if predicate():
            return True
        while context.pending():
            context.iteration(False)
        GLib.usleep(2000)
    return predicate()


def main():
    print("Clio tests")
    print("\nwithout a display")
    for name, fn in sorted((n[5:].replace("_", " "), f) for n, f in globals().items()
                           if n.startswith("test_") and f.__code__.co_argcount == 0):
        check(name, fn)

    print("\nwith a display")
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk as _Gdk, Gio, GLib as _GLib, Gtk as _Gtk

    globals().update(Gdk=_Gdk, GLib=_GLib, Gtk=_Gtk)
    from clio import widgets
    from clio.window import MainWindow

    app = Adw.Application(application_id="com.robin.ClioTests",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

    def run():
        widgets.install_css()
        window = MainWindow(app)
        window.present()
        pump(400)
        for name, fn in sorted(globals().items()):
            if not name.startswith("test_") or fn.__code__.co_argcount != 1:
                continue
            argument = fn.__code__.co_varnames[0]
            subject = {"view": window.view, "window": window, "view_app": app}[argument]
            check(name[5:].replace("_", " "), lambda fn=fn, s=subject: fn(s))
        app.release()
        app.quit()
        return False

    app.connect("activate", lambda a: (a.hold(), _GLib.timeout_add(200, run)))
    app.run([])

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
        os.environ.pop("ORDER_HOME", None)
        shutil.rmtree(scratch, ignore_errors=True)
