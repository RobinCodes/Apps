#!/usr/bin/env python3
"""The brain's own suite: the layout, the links, the cards, and the scheduler.

`brain.py`, `cards.py` and `fsrs.py` are vendored into every app that joins
the second brain, so a mistake here is a mistake in four apps at once. Every
test builds its own small home in a scratch folder — a brain, a few notes, a
review log — and points the Order at it with `ORDER_HOME`, so nothing on the
machine running the suite is read or written.

No GTK is needed: these modules are standard library only, and this suite runs
anywhere Python does.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from house import brain, cards, fsrs  # noqa: E402

SCRATCH = Path(tempfile.mkdtemp(prefix="house-brain-"))
TZ = timezone(timedelta(hours=1))


def at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 10, 1, hour, tzinfo=TZ) + timedelta(days=day)


def make_home(name: str) -> brain.Places:
    home = SCRATCH / name
    os.environ["ORDER_HOME"] = str(home)
    os.environ.pop("ANAM_HOME", None)
    where = brain.places()
    for folder in (where.vault / "idea", where.inbox, where.learning / "reviews",
                   where.studies / "Biology", where.studies / "Mathematics" / "Mazsó"):
        folder.mkdir(parents=True, exist_ok=True)
    return where


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------------------------ fsrs ---

def test_retrievability_is_ninety_percent_at_the_stability():
    for s in (0.5, 1, 7, 100):
        assert abs(fsrs.retrievability(s, s) - 0.9) < 1e-9
    assert abs(fsrs.interval_for(10, 0.9) - 10) < 1e-9
    assert fsrs.interval_for(10, 0.8) > fsrs.interval_for(10, 0.95)


def test_a_new_card_starts_from_the_published_defaults():
    m = fsrs.review(None, fsrs.GOOD, at(0))
    assert abs(m.stability - fsrs.DEFAULT_W[2]) < 1e-9
    assert 1 <= m.difficulty <= 10
    easy = fsrs.review(None, fsrs.EASY, at(0))
    again = fsrs.review(None, fsrs.AGAIN, at(0))
    assert easy.difficulty < m.difficulty < again.difficulty
    assert again.due == fsrs._start_of_day(at(0)) + timedelta(days=1)


def test_remembering_late_is_worth_more_than_remembering_early():
    first = fsrs.review(None, fsrs.GOOD, at(0))
    on_time = fsrs.review(first, fsrs.GOOD, at(3))
    late = fsrs.review(first, fsrs.GOOD, at(20))
    assert late.stability > on_time.stability > first.stability


def test_forgetting_never_makes_a_memory_stronger():
    m = fsrs.replay([(at(0), 3), (at(3), 3), (at(12), 3)])
    lapsed = fsrs.review(m, fsrs.AGAIN, at(60))
    assert lapsed.stability <= m.stability
    assert lapsed.lapses == 1


def test_intervals_grow_and_replay_is_order_free():
    log = [(at(0), 3), (at(3), 3), (at(11), 3), (at(40), 3)]
    a = fsrs.replay(log, seed="x")
    b = fsrs.replay(list(reversed(log)), seed="x")
    assert a == b
    gaps = []
    m = None
    for when, r in log:
        m = fsrs.review(m, r, when, seed="x")
        gaps.append(m.interval)
    assert gaps == sorted(gaps), gaps


def test_the_buttons_preview_is_ordered():
    m = fsrs.replay([(at(0), 3), (at(3), 3)])
    p = fsrs.preview(m, at(10))
    assert p[1] <= p[2] <= p[3] <= p[4], p
    assert fsrs.describe_days(0.2) == "today"
    assert fsrs.describe_days(400).endswith("y")


# ----------------------------------------------------------------- cards ---

NOTE = """---
title: A gyökér
---

# Biology

## Gyökér

- Wilson-tétel :: (p−1)! ≡ −1 (mod p)
- il gesso ::: kréta
Code `std::vector :: nope` stays prose.
Maths $a :: b$ stays prose too.

Q: Miért kevés a gyökérnyomás
önmagában?
A: Mert csak néhány méterre
emeli a vizet.

A {{mitokondrium}} termeli az {{ATP|energia}}-t.

```
not :: a card
{{not a cloze}}
```

$$
x :: y
$$

| Szerv | Feladat |
|---|---|
| gyökér | {{felszívás}} |
"""


def test_every_form_is_found_and_nothing_else():
    found = cards.parse_text(NOTE, "Projects/Studies/Biology/2026-10-22.md")
    forms = [c.form for c in found]
    assert forms.count("line") == 1, forms
    assert forms.count("both") == 1 and forms.count("both-r") == 1, forms
    assert forms.count("qa") == 1, forms
    assert forms.count("cloze") == 3, forms
    assert len(found) == 7, [(c.form, c.front) for c in found]
    line = next(c for c in found if c.form == "line")
    assert line.front == "Wilson-tétel" and line.back.startswith("(p−1)!")
    assert line.context == ("Biology", "Gyökér")
    assert line.deck == "Studies/Biology"
    rev = next(c for c in found if c.form == "both-r")
    assert (rev.front, rev.back) == ("kréta", "il gesso")
    qa = next(c for c in found if c.form == "qa")
    assert "önmagában" in qa.front and "emeli" in qa.back
    clozes = [c for c in found if c.form == "cloze"]
    assert clozes[0].front == "A […] termeli az ATP-t.", clozes[0].front
    assert clozes[1].front == "A mitokondrium termeli az [energia]-t.", clozes[1].front
    assert "**mitokondrium**" in clozes[0].back
    assert "[…]" in clozes[2].front and "gyökér" in clozes[2].front


def test_line_numbers_count_the_front_matter():
    found = cards.parse_text(NOTE, "x.md")
    line = next(c for c in found if c.form == "line")
    assert NOTE.split("\n")[line.line - 1].startswith("- Wilson"), line.line


def test_ids_are_stable_and_distinct():
    one = cards.parse_text(NOTE, "a/b.md")
    two = cards.parse_text(NOTE.replace("emeli", "emeli fel"), "a/b.md")
    assert [c.id for c in one if c.form != "qa"] == [c.id for c in two if c.form != "qa"]
    assert len({c.id for c in one}) == len(one)
    twice = cards.parse_text("x :: 1\nx :: 2\n", "t.md")
    assert twice[0].id != twice[1].id


def test_a_note_can_opt_out_and_name_its_deck():
    assert cards.parse_text("---\ncards: false\n---\na :: b\n", "n.md") == []
    named = cards.parse_text("---\ndeck: Italian\n---\na :: b\n", "Projects/x/n.md")
    assert named[0].deck == "Italian"
    assert cards.parse_text("a :: b", "SecondBrain/learning/decks/Major system.md")[0].deck \
        == "Major system"


def test_a_palace_walks_in_order():
    text = ("---\nkind: palace\ntitle: The way to school\n---\n"
            "1. Front door :: Wilson\n2. Lift mirror :: Fermat\n"
            "- Bus stop :: Euler\n")
    found = cards.parse_text(text, "SecondBrain/learning/palaces/school.md")
    assert [c.order for c in found] == [1, 2, 3], [c.order for c in found]
    assert {c.palace for c in found} == {"The way to school"}


def test_latex_theorems_become_cards():
    tex = r"""
\newtheorem{definition}{Definíció}[section]
\newtheorem*{remark}{Megjegyzés}
\begin{document}
\section{Gráfok}
\begin{definition}[Gráf]
A gráf csúcsokból és élekből áll. % comment :: not a card
\end{definition}
\begin{remark} Nem kártya. \end{remark}
\begin{theorem}
Minden fában $n-1$ él van.
\end{theorem}
\begin{example}[Út]
Az $a-b-c$ út egy fa.
\end{example}
\begin{lemma}
\end{lemma}
"""
    found = cards.parse_text(tex, "Projects/Programming/Websites/Digitalization/DataHU/G.tex")
    assert len(found) == 3, [(c.front, c.back) for c in found]
    assert found[2].front == "Example: Út", "an example is a card; an empty lemma is not"
    assert found[0].front == "Definíció: Gráf"
    assert "comment" not in found[0].back
    assert found[0].context == ("Gráfok",)
    assert found[1].front.startswith("Theorem — Minden fában")


# ------------------------------------------------------------- the deck ----

def test_the_deck_schedules_logs_and_survives_an_edit():
    where = make_home("deck")
    note = write(where.studies / "Biology" / "2026-10-22.md", "a :: b\nc :: d\n")
    log = cards.ReviewLog(where.learning / "reviews", machine="test-a")
    deck = cards.Deck(where.home, ["Projects/Studies"], log, new_per_day=10)
    deck.refresh()
    assert deck.counts(at(0))["new"] == 2
    queue = deck.queue(at(0))
    assert len(queue) == 2 and all(i.new for i in queue)
    for item in queue:
        deck.answer(item, fsrs.GOOD, at=at(0), ms=1200)
    assert deck.queue(at(0)) == []
    assert len(deck.queue(at(5))) == 2

    record = json.loads(log.own.read_text(encoding="utf-8").splitlines()[0])
    assert record["r"] == 3 and record["src"] == "Projects/Studies/Biology/2026-10-22.md"

    # A second machine answers too; its file is merged by being there.
    other = cards.ReviewLog(where.learning / "reviews", machine="test-b")
    first = queue[0].card
    other.append(cards.Answer(when=at(5), card=first.id, rating=4))
    deck.refresh()
    assert deck.items[first.id].memory.reps == 2

    # Editing the front keeps the history.
    write(note, "a, edited :: b\nc :: d\n")
    deck.refresh()
    edited = next(i for i in deck.items.values() if i.card.front == "a, edited")
    assert edited.memory is not None and edited.memory.reps == 2


def test_a_broken_log_line_costs_one_answer_not_the_schedule():
    where = make_home("broken")
    folder = where.learning / "reviews"
    write(folder / "m.jsonl",
          '{"t":"2026-10-01T09:00:00+01:00","card":"abc","r":3}\n'
          '<<<<<<< HEAD\n{"t": broken\n'
          '{"t":"2026-10-02T09:00:00+01:00","card":"abc","r":9}\n'
          '{"t":"2026-10-03T09:00:00+01:00","card":"abc","r":4}\n')
    answers = cards.ReviewLog(folder).read()
    assert [a.rating for a in answers] == [3, 4]


def test_new_cards_are_rationed_per_day():
    where = make_home("ration")
    write(where.studies / "Biology" / "many.md",
          "\n".join(f"q{i} :: a{i}" for i in range(30)) + "\nx ::: y\n")
    deck = cards.Deck(where.home, ["Projects/Studies"],
                      cards.ReviewLog(where.learning / "reviews", "r"), new_per_day=5)
    deck.refresh()
    q = deck.queue(at(0))
    assert len(q) == 5
    for item in q:
        deck.answer(item, fsrs.GOOD, at=at(0))
    assert deck.queue(at(0)) == []
    deck.new_per_day = 40
    later = deck.queue(at(0))
    spots = [(i.card.path, i.card.line) for i in later]
    assert len(spots) == len(set(spots)), "both directions introduced together"


# ------------------------------------------------------------- the notes ---

def test_links_resolve_by_title_alias_name_and_id():
    where = make_home("links")
    write(where.vault / "idea" / "lie-borns-lie--9f2c1a7b.md",
          "---\nid: 9f2c1a7b00000000\nkind: idea\ntitle: Lie borns lie\n"
          "aliases: [Hazugság]\n---\n\nbody\n")
    fem = write(where.studies / "Chemistry" / "Fémek.md", "# Fémek\n\nSee [[Lie borns lie]].\n")
    write(where.studies / "Biology" / "2026-10-22.md",
          "# Gyökér\n\nLinks: [[femek]], [[Hazugság|a lie]], [[9f2c1a7b]], "
          "[[Nowhere yet]], `[[not a link]]`.\n")
    notes = brain.Notes(where, db_path=SCRATCH / "links.db")
    assert notes.refresh() == 3
    assert notes.refresh() == 0, "a second refresh should read nothing"
    near = where.studies / "Biology" / "2026-10-22.md"
    assert notes.resolve("femek", near) == fem
    assert notes.resolve("Fémek.md", near) == fem
    idea = where.vault / "idea" / "lie-borns-lie--9f2c1a7b.md"
    assert notes.resolve("Hazugság", near) == idea
    assert notes.resolve("9f2c1a7b", near) == idea
    assert notes.resolve("Nowhere yet", near) is None
    back = notes.backlinks(idea)
    assert {b.path.name for b in back} == {"Fémek.md", "2026-10-22.md"}, back
    assert dict(notes.unresolved()).get("nowhere yet") == 1
    assert "not a link" not in dict(notes.unresolved())
    assert [n.title for n in notes.find("lie bor")] == ["Lie borns lie"]


def test_the_nearest_of_two_namesakes_wins():
    where = make_home("namesakes")
    a = write(where.studies / "Biology" / "Összefoglaló.md", "x")
    b = write(where.studies / "Mathematics" / "Összefoglaló.md", "y")
    notes = brain.Notes(where, db_path=SCRATCH / "namesakes.db")
    notes.refresh()
    assert notes.resolve("Összefoglaló", where.studies / "Biology" / "n.md") == a
    assert notes.resolve("Összefoglaló", where.studies / "Mathematics" / "n.md") == b


def test_a_deleted_note_leaves_the_index():
    where = make_home("deleted")
    gone = write(where.studies / "Biology" / "gone.md", "[[x]]")
    notes = brain.Notes(where, db_path=SCRATCH / "deleted.db")
    notes.refresh()
    gone.unlink()
    assert notes.refresh() == 1
    assert notes.find("gone") == [] and notes.unresolved() == []


def test_wikilink_parts():
    [link] = brain.wikilinks("see ![[Gráf#Fák|the trees]] here")
    assert (link.target, link.heading, link.label, link.embed) == ("Gráf", "Fák", "the trees", True)
    assert brain.wikilinks("```\n[[in code]]\n```") == []


# ------------------------------------------------------------- the inbox ---

def test_capture_puts_things_in_the_inbox():
    where = make_home("inbox")
    when = datetime(2026, 10, 27, 7, 30, tzinfo=TZ)
    note = brain.capture_text("# A thought: about cats?\n\nThey sit.", origin="notedown",
                              where=where, when=when)
    assert note.parent == where.inbox
    assert note.name == "2026-10-27 0730 A thought about cats.md", note.name
    text = note.read_text(encoding="utf-8")
    assert "origin: notedown" in text and "They sit." in text
    again = brain.capture_text("# A thought: about cats?", where=where, when=when)
    assert again != note and again.name.endswith("(2).md")
    loose = write(SCRATCH / "scan.jpg", "jpeg")
    moved = brain.capture_file(loose, where=where)
    assert moved.parent == where.inbox and not loose.exists()
    assert [p.name for p in brain.inbox_items(where)][-1] in {"scan.jpg", again.name, note.name}
    day = brain.daily_note(datetime(2026, 10, 27).date(), where)
    assert day.as_posix().endswith("journal/2026/2026-10-27.md") and day.read_text(encoding="utf-8").startswith("# 2026-10-27")


def test_a_capture_keeps_its_provenance():
    where = make_home("provenance")
    when = datetime(2026, 10, 27, 19, 5, tzinfo=TZ)
    note = brain.capture_text("![](2026-10-27-1.jpg)", "Notebook page, 2026-10-27",
                              origin="the-eye", app="The Eye", where=where, when=when,
                              meta={"source": 'Pictures/IMG "1".jpg', "page": "Matematika II · 14",
                                    "pages": ["a.jpg", "b.jpg"], "empty": "", "bad key!": "x",
                                    "multi": "two\nlines"})
    meta, body, _ = brain.split_front_matter(note.read_text(encoding="utf-8"))
    assert meta["origin"] == "the-eye" and meta["app"] == "The Eye", meta
    assert meta["source"] == 'Pictures/IMG "1".jpg', meta.get("source")
    assert meta["page"] == "Matematika II · 14" and meta["pages"] == ["a.jpg", "b.jpg"], meta
    assert "empty" not in meta and meta["bad_key"] == "x" and meta["multi"] == "two lines", meta
    assert body.strip() == "![](2026-10-27-1.jpg)", body
    plain = brain.capture_text("no meta", where=where, when=when)
    assert set(brain.split_front_matter(plain.read_text(encoding="utf-8"))[0]) == \
        {"title", "created", "origin", "machine"}, "a capture without meta changed shape"


def test_search_uses_the_brain_index_and_opens_the_original():
    where = make_home("search")
    original = write(where.studies / "Chemistry" / "Fémek.md", "# Fémek\n")
    where.index.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(where.index)
    con.executescript("""
        CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT, title TEXT, path TEXT,
            tier TEXT, importance REAL DEFAULT 0, body TEXT, tags TEXT);
        CREATE TABLE sources (node_id TEXT, origin TEXT, path TEXT);
        CREATE VIRTUAL TABLE nodes_fts USING fts5(title, body, tags, content='nodes',
            content_rowid='rowid', tokenize="unicode61 remove_diacritics 2");
    """)
    con.execute("INSERT INTO nodes VALUES ('n1','source','Fémek','nodes/source/femek--n1.md',"
                "'writing',50,'A fémek vezetik az áramot.','[]')")
    con.execute("INSERT INTO sources VALUES ('n1','fs','Projects/Studies/Chemistry/Fémek.md')")
    con.execute("INSERT INTO nodes_fts(rowid, title, body, tags) SELECT rowid, title, body, tags FROM nodes")
    con.commit()
    con.close()
    hits = brain.search("femek aram", where=where)
    assert hits and hits[0].path == original, hits
    assert "«" in hits[0].snippet


def test_claude_streams_an_answer_back():
    import threading
    from house import claude

    where = make_home("claude")
    fake = write(SCRATCH / "fake_claude.py", (
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "assert args[0] == '-p' and '--append-system-prompt' in args\n"
        "print('not json')\n"
        "print(json.dumps({'type': 'system', 'subtype': 'init', 'session_id': 's-1'}))\n"
        "print(json.dumps({'type': 'assistant', 'message': {'content': "
        "[{'type': 'text', 'text': 'First.'}]}}))\n"
        "print(json.dumps({'type': 'assistant', 'message': {'content': "
        "[{'type': 'tool_use', 'name': 'Read'}, {'type': 'text', 'text': 'Second.'}]}}))\n"
        "print(json.dumps({'type': 'result', 'result': 'First.\\n\\nSecond.', "
        "'is_error': False, 'session_id': 's-1'}))\n"))
    chunks, done = [], threading.Event()
    outcome = {}

    def finished(ok, result, session):
        outcome.update(ok=ok, result=result, session=session)
        done.set()

    ask = claude.Ask("Cards from this", files=[where.studies / "x.md"],
                     command=[sys.executable, str(fake)], cwd=where.home,
                     on_text=chunks.append, on_done=finished)
    assert "x.md" in ask.argv()[3] and "SYSTEM.md" in claude.briefing()
    ask.start()
    assert done.wait(30), "the stand-in never finished"
    assert outcome == {"ok": True, "result": "First.\n\nSecond.", "session": "s-1"}, outcome
    assert chunks == ["First.", "\n\nSecond."]

    failing = write(SCRATCH / "fail_claude.py", "import sys; sys.stderr.write('no auth'); sys.exit(1)\n")
    done.clear()
    claude.Ask("x", command=[sys.executable, str(failing)], cwd=where.home,
               on_done=finished).start()
    assert done.wait(30)
    assert outcome["ok"] is False and "no auth" in outcome["result"]


def test_names_are_safe_everywhere():
    assert brain.safe_name('a/b\\c:d*e?"f<g>h|i') == "a b c d e f g h i"
    assert brain.safe_name("   ") == "Untitled"
    assert brain.key(" Fémek.MD ") == "femek"


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
