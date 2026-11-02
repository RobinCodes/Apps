#!/usr/bin/env python3
"""Questions and logical threads (SYSTEM.md §10), and the front matter under both.

`threads.py` and `frontmatter.py` are vendored into every app that joins the
brain, and the files they read are edited by Ariadne, by the Anamnesis viewer
and by hand. So most of what is checked here is the promise that makes that
safe: what nobody changed comes back byte for byte, and what somebody did
change changes only its own lines.

Every test that touches the disk builds its own home in a scratch folder and
points the Order at it with `ORDER_HOME`. No GTK is needed.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from house import brain, frontmatter, threads  # noqa: E402
from house.threads import Edge, Question, Thread  # noqa: E402

SCRATCH = Path(tempfile.mkdtemp(prefix="house-threads-"))
TZ = timezone(timedelta(hours=1))

THREAD = """---
kind: thread
title: Compatibilism holds
answers: ["Is free will compatible with a deterministic universe?"]
created: 2026-10-27T13:20:00+01:00
layout: {a: [80, 60], b: [80, 260], c: [420, 160]}
---

## a · Every choice has causes
What I want is itself the product of things I did not choose.
-> c : therefore

## b · Free means unforced, not uncaused
-> c

## c · Freedom is compatible with determinism
"""

QUESTION = """---
kind: question
title: Is free will compatible with a deterministic universe?
asked: 2026-10-27T13:05:00+01:00
status: open            # open · living · answered · let-go
revisit: 2027-01-27     # optional: when it should be asked again
tags: [philosophy]
---

The question in full, and what prompted it — the moment matters as much as the words.

## Revisits

- 2026-12-01 — still open; leaning towards [[Compatibilism holds]]
"""


def make_home(name: str) -> brain.Places:
    os.environ["ORDER_HOME"] = str(SCRATCH / name)
    os.environ.pop("ANAM_HOME", None)
    return brain.places()


# ------------------------------------------------------------ front matter --

def test_front_matter_keeps_every_line_nobody_set():
    front, body = frontmatter.split(QUESTION)
    assert body.startswith("\nThe question")
    before = list(front.lines)
    front.set("status", "living")
    assert front.lines[3] == "status: living"
    assert [a for a, b in zip(front.lines, before) if a != b] == ["status: living"]
    assert "revisit: 2027-01-27     # optional: when it should be asked again" in front.lines


def test_front_matter_reads_what_the_order_writes():
    front, _ = frontmatter.split("---\n"
                                 "a: plain text  # a comment\n"
                                 "b: \"quoted, with # inside\"\n"
                                 "c: [\"one, still one\", two, 'th''ree']\n"
                                 "d: {x: [1, 2], y: [3.5, -4]}\n"
                                 "e:\n  - p\n  - \"q\"\n"
                                 "colours:\n  family: \"#c0623b\"\n  school: '#4f7cac'\n"
                                 "---\n")
    assert front.get("a") == "plain text"
    assert front.get("b") == "quoted, with # inside"
    assert front.get("c") == ["one, still one", "two", "th'ree"]
    assert front.get("d") == {"x": ["1", "2"], "y": ["3.5", "-4"]}
    assert front.get("e") == ["p", "q"]
    assert front.get("colours") == {"family": "#c0623b", "school": "#4f7cac"}
    assert front.get("missing", "x") == "x"


def test_setting_a_key_to_what_it_says_changes_nothing():
    front, _ = frontmatter.split(QUESTION)
    front.set("status", "open")
    front.set("tags", ["philosophy"])
    assert not front.dirty


def test_a_block_is_written_one_line_to_a_key_and_removed_when_empty():
    front = frontmatter.FrontMatter(["title: x", "colours:", "  a: \"#111111\"", "z: 1"])
    front.set("colours", frontmatter.Block({"a": "#111111", "b": "#222222"}))
    assert front.lines == ["title: x", "colours:", '  a: "#111111"', '  b: "#222222"', "z: 1"]
    front.set("colours", None)
    assert front.lines == ["title: x", "z: 1"]


def test_a_scalar_is_quoted_only_when_it_has_to_be():
    assert frontmatter.render("title", "Plain words") == ["title: Plain words"]
    assert frontmatter.render("title", "Is it so? Mostly.") == ["title: Is it so? Mostly."]
    assert frontmatter.render("title", "Is it: really?") == ['title: "Is it: really?"']
    assert frontmatter.render("title", "#hashtag") == ['title: "#hashtag"']
    assert frontmatter.render("title", "true") == ['title: "true"']
    assert frontmatter.render("layout", {"a": [80.0, 60]}) == ["layout: {a: [80, 60]}"]


# ----------------------------------------------------------------- threads --

def test_a_thread_reads_as_the_contract_says():
    thread = Thread.parse(THREAD)
    assert thread.title == "Compatibilism holds"
    assert thread.answers == ["Is free will compatible with a deterministic universe?"]
    assert thread.layout == {"a": (80.0, 60.0), "b": (80.0, 260.0), "c": (420.0, 160.0)}
    assert [(n.id, n.title) for n in thread.nodes] == [
        ("a", "Every choice has causes"), ("b", "Free means unforced, not uncaused"),
        ("c", "Freedom is compatible with determinism")]
    assert thread.node("a").text == "What I want is itself the product of things I did not choose."
    assert thread.node("a").edges == [Edge("c", "therefore")]
    assert thread.node("b").edges == [Edge("c", "")] and thread.node("b").text == ""


def test_a_thread_nobody_changed_is_written_back_byte_for_byte():
    for text in (THREAD, THREAD.replace("\n", "\r\n"), THREAD.rstrip("\n"),
                 THREAD.replace(" · ", " - ").replace("->", "→")):
        assert Thread.parse(text).dump() == text, repr(text[:60])


def test_the_fallback_separator_and_the_other_arrow_are_read():
    thread = Thread.parse("## my-id - A - title\n→ other : but\n-> x:y\n")
    node = thread.node("my-id")
    assert node.title == "A - title"
    assert node.edges == [Edge("other", "but"), Edge("x", "y")]
    assert Thread.parse("## a · Á thought · with dots\n").node("a").title == \
        "Á thought · with dots"
    # An id is letters, digits and -; anything else is a heading, not a thought.
    assert Thread.parse("## ékezet · no\n## under_score · no\n### a · no\n").nodes == []


def test_what_is_not_a_thought_is_kept_untouched():
    text = ("---\nkind: thread\ntitle: T\n---\n\nSome words before.\n## Not a node\n\n"
            "## a · First\nText.\n### A sub-heading\nMore.\n-> b\n\n## b · Second\n")
    thread = Thread.parse(text)
    assert thread.preamble[:3] == ["", "Some words before.", "## Not a node"]
    assert "### A sub-heading" in thread.node("a").text
    thread.node("b").title = "Second, renamed"
    out = thread.dump()
    assert out == text.replace("## b · Second", "## b · Second, renamed")


def test_an_edit_to_one_thought_leaves_the_others_alone():
    thread = Thread.parse(THREAD)
    thread.node("a").text = "Rewritten."
    out = thread.dump()
    assert "## a · Every choice has causes\nRewritten.\n-> c : therefore\n\n## b" in out, out
    assert out.split("## b")[1] == THREAD.split("## b")[1]


def test_a_changed_arrow_changes_its_own_line_and_no_other():
    text = THREAD.replace("-> c : therefore\n", "Before.\n-> c : therefore\nAfter.\n")
    thread = Thread.parse(text)
    thread.node("a").edges[0].label = "so"
    thread.node("a").edges.append(Edge("b", "and"))
    out = thread.dump()
    assert "Before.\n-> c : so\n-> b : and\nAfter.\n" in out, out
    thread = Thread.parse(text)
    thread.node("a").edges.clear()
    assert "Before.\nAfter.\n\n## b" in thread.dump()


def test_an_arrow_to_nowhere_is_kept_and_reported():
    thread = Thread.parse(THREAD + "-> ghost : unless\n")
    assert [(n.id, e.target) for n, e in thread.dangling()] == [("c", "ghost")]
    thread.node("a").title = "Changed"
    assert "-> ghost : unless" in thread.dump()


def test_an_id_is_short_and_never_handed_out_twice():
    thread = Thread.parse(THREAD)
    first = thread.add("Fourth")
    assert re.fullmatch(r"t[0-9a-z]+", first.id) and len(first.id) <= 8, first.id
    assert threads.NODE.match(f"## {first.id} · x")
    thread.remove(first.id)
    second = thread.add("Fifth")
    assert second.id != first.id, "a taken-away id was handed out again"
    # Not one that something still points at, either.
    thread.node("a").edges.append(Edge(thread.new_id()))
    assert thread.new_id() not in {e.target for _, e in thread.edges()}


def test_a_thought_taken_away_takes_its_arrows_and_comes_back_with_them():
    thread = Thread.parse(THREAD)
    index = [n.id for n in thread.nodes].index("c")
    node, incoming = thread.remove("c")
    assert thread.node("a").edges == [] and thread.node("b").edges == []
    assert "c" not in thread.layout
    thread.restore(node, incoming, index, (420, 160))
    assert thread.node("a").edges == [Edge("c", "therefore")]
    assert thread.layout["c"] == (420.0, 160.0)
    assert thread.dump().count("## c ·") == 1


def test_a_new_thread_and_its_first_thoughts_are_well_formed():
    thread = Thread.new("Is it so?", answers=["A question"],
                        when=datetime(2026, 10, 28, 9, 0, tzinfo=TZ))
    a = thread.add("First", "Its **text**.", id="a")
    b = thread.add("Second", id="b")
    thread.connect(a.id, b.id, "therefore")
    thread.layout = {"a": (10.4, 20.6), "b": (300, 20)}
    out = thread.dump()
    assert out == ("---\nkind: thread\ntitle: Is it so?\nanswers: [\"A question\"]\n"
                   "created: 2026-10-28T09:00:00+01:00\nlayout: {a: [10, 21], b: [300, 20]}\n"
                   "---\n\n## a · First\nIts **text**.\n-> b : therefore\n\n## b · Second\n"), out
    assert Thread.parse(out).dump() == out


def test_a_thought_added_to_a_file_read_has_a_blank_line_above_it():
    thread = Thread.parse(THREAD)
    thread.add("Fourth", "Words.", id="d")
    assert thread.dump().endswith("## c · Freedom is compatible with determinism\n\n"
                                  "## d · Fourth\nWords.\n"), thread.dump()
    tight = Thread.parse("## a · One\ntext")
    tight.add("Two", id="b")
    assert tight.dump() == "## a · One\ntext\n\n## b · Two\n", tight.dump()


def test_a_thread_without_layout_loses_nothing_and_gains_only_what_is_set():
    text = THREAD.replace("layout: {a: [80, 60], b: [80, 260], c: [420, 160]}\n", "")
    thread = Thread.parse(text)
    assert thread.layout == {}
    thread.layout = {"a": (1, 2)}
    assert thread.dump() == text.replace("created: 2026-10-27T13:20:00+01:00\n",
                                         "created: 2026-10-27T13:20:00+01:00\nlayout: {a: [1, 2]}\n")


def test_connecting_twice_or_to_itself_makes_one_arrow_or_none():
    thread = Thread.parse(THREAD)
    assert thread.connect("a", "c") is thread.node("a").edges[0]
    assert thread.connect("a", "a") is None
    assert len(thread.node("a").edges) == 1


# --------------------------------------------------------------- questions --

def test_a_question_reads_as_the_contract_says():
    question = Question.parse(QUESTION)
    assert question.title.startswith("Is free will")
    assert question.status == "open" and question.tags == ["philosophy"]
    assert question.revisit == date(2027, 1, 27)
    assert question.asked_date == date(2026, 10, 27)
    assert question.text.startswith("The question in full")
    assert question.revisits == [("2026-12-01",
                                  "still open; leaning towards [[Compatibilism holds]]")]
    assert question.links() == ["Compatibilism holds"]


def test_a_question_nobody_changed_is_written_back_byte_for_byte():
    for text in (QUESTION, QUESTION.replace("\n", "\r\n")):
        assert Question.parse(text).dump() == text


def test_a_question_is_due_when_its_revisit_comes_and_it_is_still_open():
    question = Question.parse(QUESTION)
    assert not question.due(date(2027, 1, 26))
    assert question.due(date(2027, 1, 27))
    question.status = "answered"
    assert not question.due(date(2028, 1, 1))
    question.status = "living"
    assert question.due(date(2028, 1, 1))
    try:
        question.status = "maybe"
    except ValueError:
        pass
    else:
        raise AssertionError("a status outside the four was taken")


def test_a_revisit_is_a_line_under_revisits_and_nothing_else_moves():
    question = Question.parse(QUESTION)
    question.add_revisit("closer   now", date(2027, 1, 30))
    assert question.dump() == QUESTION + "- 2027-01-30 — closer now\n"
    fresh = Question.parse(QUESTION.split("\n## Revisits")[0] + "\n")
    fresh.add_revisit("first look", date(2026, 11, 1))
    assert fresh.dump().endswith("words.\n\n## Revisits\n\n- 2026-11-01 — first look\n"), fresh.dump()
    assert fresh.revisits == [("2026-11-01", "first look")]


def test_changing_the_text_keeps_the_revisits():
    question = Question.parse(QUESTION)
    question.text = "A shorter question."
    out = question.dump()
    assert "---\n\nA shorter question.\n\n## Revisits\n\n- 2026-12-01" in out, out
    question.text = "A shorter question."
    assert question.dump() == out


def test_the_questions_due_come_first_then_the_newest():
    def make(title, asked, status="open", revisit=None):
        question = Question.new(title, when=datetime.fromisoformat(asked))
        question.status = status
        question.revisit = revisit
        return question

    found = threads.order_questions([
        make("old open", "2026-01-01T00:00:00+01:00"),
        make("answered", "2026-12-01T00:00:00+01:00", "answered"),
        make("new open", "2026-06-01T00:00:00+01:00"),
        make("due", "2025-01-01T00:00:00+01:00", revisit=date(2026, 1, 1)),
        make("let go", "2026-11-01T00:00:00+01:00", "let-go"),
    ], today=date(2026, 10, 28))
    assert [q.title for q in found] == ["due", "new open", "old open", "answered", "let go"]


def test_a_question_and_a_thread_are_files_in_their_folders():
    where = make_home("files")
    when = datetime(2026, 10, 28, 7, 41, tzinfo=TZ)
    question = threads.new_question("Why: is anything?", "Asked on the bus.", when=when)
    assert question.path == where.brain / "questions" / "Why is anything.md"
    assert "asked: 2026-10-28T07:41:00+01:00" in question.dump()
    again = threads.new_question("Why: is anything?", when=when)
    assert again.path.name == "Why is anything (2).md"
    thread = threads.new_thread("Because it can", answers=[question.title], when=when)
    assert thread.path == where.brain / "threads" / "Because it can.md"
    other = threads.new_thread("Unrelated", when=when)
    loaded = threads.load_threads()
    assert [t.title for t in loaded] == ["Because it can", "Unrelated"]
    assert [t.title for t in threads.threads_for(question, loaded)] == ["Because it can"]
    assert isinstance(threads.load(question.path), Question)
    assert isinstance(threads.load(other.path), Thread)
    assert [q.title for q in threads.load_questions()] == ["Why: is anything?"] * 2
    # Written whole or not at all: no half-file is ever left beside it.
    assert not [p for p in (where.brain / "threads").iterdir() if p.name.endswith(".writing")]


def test_a_question_finds_a_thread_it_links_to_as_well():
    question = Question.parse(QUESTION.replace("[[Compatibilism holds]]", "[[compatibilism HOLDS]]"))
    thread = Thread.parse(THREAD.replace('answers: ["Is free will compatible with a '
                                         'deterministic universe?"]\n', ""))
    assert threads.threads_for(question, [thread]) == [thread]
    resolved = Thread.parse(THREAD.replace("title: Compatibilism holds", "title: Other"),
                            path=SCRATCH / "x.md")
    assert threads.threads_for(Question.parse(QUESTION.replace("Compatibilism holds", "x")),
                               [resolved], resolve=lambda t: SCRATCH / "x.md")


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
