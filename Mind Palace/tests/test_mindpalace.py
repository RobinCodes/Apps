"""The palace: what it finds, where a file goes, what Home says, and the contract.

    python3 tests/test_mindpalace.py

The first half needs no display: discovery against a small checkout built for
the purpose (and against the real one), routing, and Home's numbers against a
small home pointed at with ORDER_HOME, so nothing real is read or written. The
second half opens the real window and drives it. A fake room, written to disk
the way an app would write one, walks the whole of ROOMS.md; then the real rooms
on main are built in it. Nothing is launched: `winenv.spawn` is replaced by one
that only records, and Claude is a stand-in script.
"""

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

scratch = Path(tempfile.mkdtemp(prefix="mindpalace-tests-"))
os.environ["XDG_CONFIG_HOME"] = str(scratch / "config")
os.environ["XDG_DATA_HOME"] = str(scratch / "data")
os.environ["XDG_CACHE_HOME"] = str(scratch / "cache")
os.environ["LOCALAPPDATA"] = str(scratch / "local")
os.environ["ORDER_HOME"] = str(scratch / "home")
# An app built by a test must not go to the network for an update.
os.environ["APPS_NO_UPDATE"] = "1"

from mindpalace import brain, cards, rooms, today  # noqa: E402

HOME = scratch / "home"
failures = []


def check(name, fn, *args):
    try:
        fn(*args)
    except BaseException as exc:  # noqa: BLE001 - counted, and raised after the rest
        import traceback

        failures.append(name)
        print(f"  FAIL  {name}: {exc!r}")
        for line in traceback.format_exc().strip().splitlines()[-5:]:
            print(f"        {line}")
    else:
        print(f"  ok  {name}")


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ------------------------------------------------- a checkout of our own ----

ROOM_THAT_EXPLODES = '''
TITLE = "Tower"
ICON = "tower"
ORDER = 15
OPENS = (".tower",)
KINDS = ("turret",)
raise RuntimeError("reading the sidebar must not run this")
'''


def make_checkout(where):
    where = Path(where)
    write(where / "Plain" / "plain.desktop",
          "[Desktop Entry]\nName=Plain\nGenericName=A thing\nComment=Does a thing\n"
          "Exec=/home/robin/.local/bin/plain %f\nIcon=plain\n\n"
          "[Desktop Action new-window]\nName=New Window\n")
    write(where / "Plain" / "plain.py", "")
    write(where / "Plain" / "plain.svg", "<svg/>")
    write(where / "Tall" / "tall.desktop",
          "[Desktop Entry]\nName=Tall\nComment=Has a room\nExec=tall\nIcon=tall\n")
    write(where / "Tall" / "tall.py", "")
    write(where / "Tall" / "tower.svg", "<svg/>")
    write(where / "Tall" / "tallpkg" / "__init__.py", "")
    write(where / "Tall" / "tallpkg" / "room.py", ROOM_THAT_EXPLODES)
    # As Lyndon has it: the .desktop under data/, and dots in the icon's name.
    write(where / "Web" / "data" / "org.web.Browser.desktop",
          "[Desktop Entry]\nName=Web\nExec=webby %u\nIcon=org.web.Browser\n")
    write(where / "Web" / "webby.py", "")
    write(where / "Web" / "data" / "icons" / "org.web.Browser.svg", "<svg/>")
    # As Prism has it: it does not exist on Windows.
    write(where / "Terminal" / "prism.desktop", "[Desktop Entry]\nName=Prism\nExec=prism\n")
    write(where / "Terminal" / "prism.py", "")
    # Not apps: a library, a folder with no launcher, and the palace itself.
    write(where / "house" / "brain.py", "")
    write(where / "Notes" / "readme.desktop", "[Desktop Entry]\nName=Readme\nExec=gone\n")
    write(where / "Mind Palace" / "mind-palace.desktop", "[Desktop Entry]\nName=Mind Palace\nExec=x\n")
    write(where / "Mind Palace" / "mind-palace.py", "")
    return where


def test_the_sidebar_reads_a_room_without_running_it():
    meta = rooms.room_meta(make_checkout(scratch / "c1") / "Tall" / "tallpkg" / "room.py")
    assert meta == {"TITLE": "Tower", "ICON": "tower", "ORDER": 15,
                    "OPENS": (".tower",), "KINDS": ("turret",)}, meta


def test_a_broken_room_file_is_not_a_crash():
    assert rooms.room_meta(write(scratch / "bad.py", "TITLE = (")) == {}
    assert rooms.room_meta(scratch / "not-there.py") == {}
    assert rooms.room_meta(write(scratch / "odd.py", "TITLE = make_title()\nORDER = 3\n")) == {"ORDER": 3}


def test_a_desktop_file_gives_its_own_name_not_an_actions():
    entry = rooms.read_desktop(make_checkout(scratch / "c2") / "Plain" / "plain.desktop")
    assert entry["Name"] == "Plain" and entry["Comment"] == "Does a thing"


def test_apps_become_rooms_and_doors_in_order():
    found = rooms.discover(make_checkout(scratch / "c3"), platform="posix")
    titles = [s.title for s in found]
    assert titles[0] == "Home" and found[0].kind == "native"
    assert "Tower" in titles and "Plain" in titles and "Web" in titles and "Prism" in titles
    assert "Readme" not in titles, "a .desktop with no launcher is not an app"
    assert "Mind Palace" not in titles, "the palace is not a room of itself"
    tower = next(s for s in found if s.title == "Tower")
    assert tower.kind == "room" and tower.package == "tallpkg" and tower.order == 15
    assert tower.icon.name == "tower.svg", tower.icon
    assert tower.opens == (".tower",) and tower.kinds == ("turret",)
    plain = next(s for s in found if s.title == "Plain")
    assert plain.kind == "door" and plain.package is None and plain.launcher.name == "plain.py"
    assert plain.comment == "Does a thing" and plain.icon.name == "plain.svg"
    assert plain.order == rooms.UNKNOWN_ORDER
    web = next(s for s in found if s.title == "Web")
    assert web.launcher.name == "webby.py" and web.icon.name == "org.web.Browser.svg", web
    orders = [s.order for s in found]
    assert orders == sorted(orders)


def test_what_cannot_run_here_says_so():
    where = make_checkout(scratch / "c4")
    on_windows = next(s for s in rooms.discover(where, "nt") if s.title == "Prism")
    assert "Windows" in on_windows.unavailable
    on_linux = next(s for s in rooms.discover(where, "posix") if s.title == "Prism")
    assert on_linux.unavailable == ""


def test_a_title_means_one_room():
    where = make_checkout(scratch / "c5")
    write(where / "Other" / "other.desktop", "[Desktop Entry]\nName=Plain\nExec=other\n")
    write(where / "Other" / "other.py", "")
    assert sum(s.title == "Plain" for s in rooms.discover(where, "posix")) == 1


def test_the_real_checkout_is_all_there():
    found = {s.title: s for s in rooms.discover()}
    for title in ("Home", "Notedown", "Mnemosyne", "Hermione", "The Eye", "Fathom",
                  "LaRenderer", "Git Manager", "Claude Desk", "Prism", "Lyndon"):
        assert title in found, sorted(found)
    assert "Mind Palace" not in found and "Order" not in found
    assert found["Mnemosyne"].kind == "room" and found["The Eye"].kind == "room"
    for spec in found.values():
        if spec.kind != "native":
            assert spec.launcher and spec.launcher.is_file(), spec.title
            assert spec.icon and spec.icon.is_file(), f"{spec.title} has no icon"
            assert spec.comment, f"{spec.title} says nothing about itself"
    assert found["Mnemosyne"].order == 40 and found["The Eye"].order == 60


# ------------------------------------------------------------ routing ----

def specs_for_routing():
    return [
        rooms.Spec("Notedown", 10, "door", opens=rooms.MARKDOWN),
        rooms.Spec("Clio", 30, "room", kinds=("timeline",)),
        rooms.Spec("Mnemosyne", 40, "room", opens=("study:",), argv={"study:": ["--study"]}),
        rooms.Spec("Hermione", 50, "door", opens=(rooms.FOLDER,)),
        rooms.Spec("The Eye", 60, "room", opens=rooms.PICTURES),
        rooms.Spec("Lyndon", 120, "door", opens=(*rooms.WEB, rooms.URL)),
    ]


def titles(found):
    return [s.title for s in found]


def test_a_file_goes_by_its_extension():
    specs = specs_for_routing()
    assert titles(rooms.route(str(scratch / "a.md"), specs)) == ["Notedown"]
    assert titles(rooms.route(str(scratch / "a.JPG"), specs)) == ["The Eye"]
    assert titles(rooms.route(str(scratch / "a.xyz"), specs)) == []
    assert titles(rooms.route(str(scratch / "no-extension"), specs)) == []


def test_a_note_goes_first_to_whoever_keeps_its_kind():
    specs = specs_for_routing()
    board = write(scratch / "board.md", "---\nkind: timeline   # a board\ntitle: t\n---\nbody\n")
    assert rooms.front_matter_kind(str(board)) == "timeline"
    assert titles(rooms.route(str(board), specs, "timeline")) == ["Clio", "Notedown"]
    plain = write(scratch / "plain.md", "# no front matter\nkind: timeline\n")
    assert rooms.front_matter_kind(str(plain)) is None
    assert rooms.front_matter_kind(str(scratch / "a.png")) is None


def test_a_folder_goes_to_whoever_opens_folders():
    assert titles(rooms.route(str(scratch), specs_for_routing())) == ["Hermione"]


def test_a_scheme_goes_to_whoever_declared_it():
    specs = specs_for_routing()
    assert rooms.scheme_of("study:C:/notes/a.md") == "study:"
    assert rooms.scheme_of("study:/home/robin/a.md") == "study:"
    assert rooms.scheme_of("C:/notes/a.md") is None, "a drive letter is not a scheme"
    assert rooms.scheme_of("https://example.org") is None
    assert titles(rooms.route("study:C:/notes/a.md", specs)) == ["Mnemosyne"]
    assert titles(rooms.route("nobody:thing", specs)) == []


def test_a_link_goes_to_the_browser():
    assert titles(rooms.route("https://example.org/page", specs_for_routing())) == ["Lyndon"]


def test_a_door_is_handed_what_its_command_line_understands():
    mnemosyne = specs_for_routing()[2]
    assert rooms.door_argv(mnemosyne) == []
    assert rooms.door_argv(mnemosyne, "C:/a.md") == ["C:/a.md"]
    assert rooms.door_argv(mnemosyne, "study:C:/a.md") == ["--study", "C:/a.md"]
    assert rooms.door_argv(specs_for_routing()[0], "study:C:/a.md") == []


def test_a_real_palace_routes_study_to_mnemosyne():
    found = rooms.discover()
    assert titles(rooms.route("study:C:/a.md", found)) == ["Mnemosyne"]
    assert titles(rooms.route(str(scratch / "a.png"), found)) == ["The Eye"]
    assert titles(rooms.route(str(scratch / "a.md"), found))[0] == "Notedown"
    assert titles(rooms.route(str(scratch), found)) == ["Hermione"]


# -------------------------------------------------------------- today ----

def make_home():
    where = brain.places()
    write(where.brain / "config" / "learning.yaml",
          'roots:\n  - SecondBrain/learning\n  - Projects/Studies\n'
          'new_per_day: 2\ndesired_retention: 0.9\n"off":\n  - Off deck\n')
    write(where.learning / "decks" / "One.md", "---\ndeck: On\n---\n\n- a :: 1\n- b :: 2\n- c :: 3\n")
    write(where.learning / "decks" / "Two.md", "---\ndeck: Off deck\n---\n\n- x :: 9\n- y :: 8\n")
    write(where.studies / "Biology" / "2026-10-22.md", "# Gyökér\n\nA gyökér zónái? :: négy\n")
    write(where.learning / "curricula" / "The Mentalist" / "drills.yaml",
          "# a comment\ndrills:\n"
          '  - id: obs-10s\n    title: "What changed? 10 s"\n    module: "02"\n'
          "    trainer: observation\n    minutes: 5\n    params: {seconds: 10, rounds: 10}\n"
          "\n"
          '  - id: doom-2000\n    title: "Doomsday: 2000-2099"\n    module: "11"\n'
          "    trainer: doomsday\n    minutes: 6\n    params:\n      years: [2000, 2099]\n"
          "      rounds: 10\n"
          "\n"
          '  - id: cards-pao\n    title: "PAO"\n    module: "03"\n'
          "    trainer: cards\n    minutes: 6\n    params: {deck: \"PAO\", limit: 26}\n")
    write(where.inbox / "2026-10-20 0800 Old thought.md", "old")
    newer = write(where.inbox / "2026-10-27 0800 New thought.md", "new")
    os.utime(where.inbox / "2026-10-20 0800 Old thought.md", (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_800_000_000, 1_800_000_000))
    write(where.journal / "2026" / "2026-10-27.md", "# A day\n")
    return where


def test_the_counts_leave_out_switched_off_decks_and_ration_new_cards():
    where = make_home()
    due, new = today.card_counts(where)
    # Four cards on, two off; a ration of two new ones a day.
    assert (due, new) == (0, 2), (due, new)
    log = cards.ReviewLog(where.learning / "reviews")
    deck = cards.Deck(where.home, ["SecondBrain/learning"], log, new_per_day=50)
    deck.refresh()
    first = next(item for item in deck.items.values() if item.card.front == "a")
    deck.answer(first, 3, at=datetime.now(timezone.utc) - timedelta(days=60))
    due, new = today.card_counts(where)
    assert due == 1, (due, new)
    log.own.unlink()


def test_switched_off_is_mnemosynes_rule():
    card = cards.Card(id="x", form="line", front="f", back="b", deck="School/Biology",
                      path="Projects/Digitalization/DataHU/Humán tárgyak/a.tex", line=1)
    assert today.switched_off(card, ["School"])
    assert today.switched_off(card, ["School/Biology"])
    assert today.switched_off(card, ["Digitalization/DataHU/Humán tárgyak"])
    assert not today.switched_off(card, ["Bio"]), "not part of a name"
    assert not today.switched_off(card, ["Hum"]), "not part of a path segment"
    assert not today.switched_off(card, [])


def test_yaml_reads_with_or_without_pyyaml():
    text = 'roots:\n  - a\n  - b\nnew_per_day: 2\n"off":\n  - x\n'
    assert today.read_yaml(text)["off"] == ["x"]
    saved = sys.modules.get("yaml")
    sys.modules["yaml"] = None          # `import yaml` now raises ImportError
    try:
        data = today.read_yaml(text)
    finally:
        if saved is None:
            del sys.modules["yaml"]
        else:
            sys.modules["yaml"] = saved
    assert data["roots"] == ["a", "b"] and data["off"] == ["x"], data


def test_drills_read_with_or_without_pyyaml():
    text = (brain.places().learning / today.DRILLS).read_text(encoding="utf-8")
    with_yaml = today.read_drills(text)
    assert [d.id for d in with_yaml] == ["obs-10s", "doom-2000", "cards-pao"], with_yaml
    assert with_yaml[0].title == "What changed? 10 s" and with_yaml[0].minutes == 5
    saved = sys.modules.get("yaml")
    sys.modules["yaml"] = None
    try:
        without = today.read_drills(text)
    finally:
        if saved is None:
            del sys.modules["yaml"]
        else:
            sys.modules["yaml"] = saved
    assert without == with_yaml, without


def test_the_day_has_one_drill_and_it_is_never_card_review():
    drills = today.read_drills((brain.places().learning / today.DRILLS).read_text(encoding="utf-8"))
    day = date(2026, 11, 2)
    assert today.drill_of_the_day(drills, day) == today.drill_of_the_day(drills, day)
    seen = {today.drill_of_the_day(drills, day + timedelta(days=n)).id for n in range(10)}
    assert seen == {"obs-10s", "doom-2000"}, seen
    assert today.drill_of_the_day([], day) is None


def test_the_real_drills_file_is_understood():
    real = Path.home() / "SecondBrain" / "learning" / today.DRILLS
    if not real.is_file():
        print("        (no real drills.yaml on this machine)")
        return
    drills = today.read_drills(real.read_text(encoding="utf-8"))
    assert len(drills) > 10 and all(d.id and d.title and d.trainer for d in drills)


def test_sync_is_what_order_wrote():
    cache = scratch / "sync"
    assert today.sync_state(cache).when is None
    write(cache / "state.json", json.dumps({"last_sync": "2026-11-02T08:00:00",
                                            "last_sync_failures": 2}))
    state = today.sync_state(cache)
    assert state.when == datetime(2026, 11, 2, 8) and state.failures == 2
    write(cache / "state.json", "not json")
    assert today.sync_state(cache).when is None


def test_an_age_reads_the_way_a_person_says_it():
    now = datetime(2026, 11, 2, 12)
    assert today.age(now - timedelta(seconds=20), now) == "just now"
    assert today.age(now - timedelta(minutes=1), now) == "1 minute"
    assert today.age(now - timedelta(minutes=12), now) == "12 minutes"
    assert today.age(now - timedelta(hours=3), now) == "3 hours"
    assert today.age(now - timedelta(days=9), now) == "9 days"


def test_gather_reads_all_of_it():
    where = make_home()
    ingested = write(where.vault / "idea" / "ingested.md", "---\ntitle: Ingested\n---\nx\n")
    os.utime(ingested, (1_900_000_000, 1_900_000_000))
    notes = brain.Notes(where, db_path=scratch / "gather.db")
    notes.refresh()
    found = today.gather(where, date(2026, 11, 2), notes=notes, cache=scratch / "no-sync")
    assert (found.due, found.new, found.cards_problem) == (0, 2, ""), found
    assert [p.name for p in found.inbox][0].startswith("2026-10-20"), "oldest first"
    assert found.drill is not None and found.drill.trainer != "cards"
    assert found.recent, "the recent notes are empty"
    assert all("thought" not in n.title.lower() for n in found.recent), "the inbox has its own card"
    assert all("Ingested" != n.title for n in found.recent), "the vault is not what anybody wrote"
    assert found.sync.when is None


def test_gathering_never_waits_for_the_note_index():
    where = make_home()
    notes = brain.Notes(where, db_path=scratch / "no-wait.db")

    def refresh():
        raise AssertionError("gather brought the index up to date itself")

    notes.refresh = refresh
    found = today.gather(where, date(2026, 11, 2), notes=notes, cache=scratch / "no-sync")
    assert found.recent == [], "an index nothing has filled yet has nothing recent"
    assert found.new == 2, "and everything else is there anyway"


def test_one_broken_part_does_not_cost_the_rest():
    where = make_home()
    original = today.card_counts

    def broken(_places, _at=None):
        raise ValueError("the deck is on fire")

    today.card_counts = broken
    try:
        found = today.gather(where, date(2026, 11, 2),
                             notes=brain.Notes(where, db_path=scratch / "broken.db"),
                             cache=scratch / "no-sync")
    finally:
        today.card_counts = original
    assert found.due is None and "fire" in found.cards_problem
    assert found.inbox and found.drill is not None


def test_a_question_is_due_when_its_day_has_come_and_it_is_still_open():
    where = make_home()
    folder = where.brain / "questions"
    write(folder / "due.md", "---\nkind: question\ntitle: Due\nstatus: open\nrevisit: 2026-11-01\n---\n\nq\n")
    write(folder / "older.md", "---\nkind: question\ntitle: Older\nstatus: living\nrevisit: 2026-01-01\n---\n\nq\n")
    write(folder / "later.md", "---\nkind: question\ntitle: Later\nstatus: open\nrevisit: 2027-01-01\n---\n\nq\n")
    write(folder / "done.md", "---\nkind: question\ntitle: Done\nstatus: answered\nrevisit: 2026-01-01\n---\n\nq\n")
    write(folder / "never.md", "---\nkind: question\ntitle: Never\nstatus: open\n---\n\nq\n")
    due = today.questions_due(where, date(2026, 11, 2))
    assert [q.title for q in due] == ["Older", "Due"], [q.title for q in due]
    assert due[0].path.name == "older.md"
    shutil.rmtree(folder)


def test_on_this_day_is_only_what_happened_on_this_day():
    where = make_home()
    board = write(where.brain / "timelines" / "Life.md",
                  "---\nkind: timeline\ntitle: Life\n---\n\n## Events\n"
                  "- 2011-11-02 · Exact {family}\n"
                  "- 2020-11-02 · More recent\n"
                  "- ~2015-11-02 · Only roughly\n"
                  "- 2016-11 · No day\n"
                  "- 2017 · No month\n"
                  "- 2018-11-03 · Another day\n"
                  "- 2026-11-02 · Today itself\n"
                  "- 2027-11-02 · Not yet\n"
                  "- -0044-11-02 · Long ago\n\n"
                  "## Spans\n- 2019-11-02 .. · A beginning\n")
    found = today.on_this_day(where, date(2026, 11, 2))
    words = [r.title for r in found]
    assert words == ["6 years ago: More recent", "7 years ago: A beginning",
                     "15 years ago: Exact", "2069 years ago: Long ago"], words
    assert found[0].path == board
    assert today.on_this_day(where, date(2026, 3, 1)) == []
    shutil.rmtree(where.brain / "timelines")


def test_a_year_ago_is_one_year():
    where = make_home()
    write(where.brain / "timelines" / "Life.md",
          "---\nkind: timeline\ntitle: Life\n---\n\n## Events\n- 2025-11-02 · Last year\n")
    assert [r.title for r in today.on_this_day(where, date(2026, 11, 2))] == ["1 year ago: Last year"]
    shutil.rmtree(where.brain / "timelines")


def test_gather_brings_questions_and_days_with_it():
    where = make_home()
    write(where.brain / "questions" / "q.md",
          "---\nkind: question\ntitle: Is it?\nstatus: open\nrevisit: 2026-01-01\n---\n\nq\n")
    write(where.brain / "timelines" / "Life.md",
          "---\nkind: timeline\ntitle: Life\n---\n\n## Events\n- 2020-11-02 · A thing\n")
    notes = brain.Notes(where, db_path=scratch / "both.db")
    found = today.gather(where, date(2026, 11, 2), notes=notes, cache=scratch / "no")
    assert [q.title for q in found.questions] == ["Is it?"]
    assert [r.title for r in found.on_this_day] == ["6 years ago: A thing"]
    shutil.rmtree(where.brain / "questions")
    shutil.rmtree(where.brain / "timelines")


# ------------------------------------------------------------ the window ---

FAKE_ROOM = '''
"""A room as an app would write one, walking the whole of ROOMS.md."""
from gi.repository import Gio, Gtk

TITLE = "Fake"
ICON = "fake"
ORDER = 25
OPENS = (".fake",)
KINDS = ("fake-kind",)

built = []


class Room:
    def __init__(self, host):
        self.host = host
        self.widget = Gtk.Label(label="a fake room", focusable=True)
        self.actions = Gio.SimpleActionGroup()
        self.fired = []
        action = Gio.SimpleAction.new("poke", None)
        action.connect("activate", lambda *_: self.fired.append("poke"))
        self.actions.add_action(action)
        self.accels = [("room.poke", ["<Control>p"])]
        self.opened = []
        self.saved = 0
        self.closable = True
        self.ended = 0
        self.seen = 0
        self.gone = 0
        self.files = []
        self.restyled = 0

    def open(self, path):
        if str(path).endswith(".declined.fake"):
            return False
        self.opened.append(str(path))
        return True

    def save_state(self):
        self.saved += 1

    def can_close(self):
        return self.closable

    def shutdown(self):
        self.ended += 1

    def shown(self):
        self.seen += 1

    def hidden(self):
        self.gone += 1

    def context(self):
        return self.files

    def restyle(self):
        self.restyled += 1


def build(host):
    room = Room(host)
    built.append(room)
    return room
'''

BROKEN_ROOM = '''
TITLE = "Fallen"
ORDER = 26
def build(host):
    raise RuntimeError("the roof came in")
'''

MINIMAL_ROOM = '''
"""As little as the contract allows: a title, an order, and a widget."""
from gi.repository import Gtk

TITLE = "Bare"
ICON = "bare"
ORDER = 27


class Room:
    def __init__(self):
        self.widget = Gtk.Label(label="bare")


def build(host):
    return Room()
'''


def fake_checkout():
    where = scratch / "rooms"
    for name, source in (("Fake", FAKE_ROOM), ("Fallen", BROKEN_ROOM), ("Bare", MINIMAL_ROOM)):
        low = name.lower()
        write(where / name / f"{low}.desktop", f"[Desktop Entry]\nName={name}\nComment=A {low} app\nExec={low}\n")
        write(where / name / f"{low}.py", "")
        write(where / name / f"{low}pkg" / "__init__.py", "")
        write(where / name / f"{low}pkg" / "room.py", source)
    write(where / "Doorway" / "doorway.desktop",
          "[Desktop Entry]\nName=Doorway\nComment=Only a door\nExec=doorway\n")
    write(where / "Doorway" / "doorway.py", "")
    write(where / "Terminal" / "prism.desktop",
          "[Desktop Entry]\nName=Prism\nComment=A terminal\nExec=prism\n")
    write(where / "Terminal" / "prism.py", "")
    return where


STAND_IN = '''
import json, sys
print(json.dumps({"type": "assistant", "session_id": "s-1",
                  "message": {"content": [{"type": "text", "text": "Forty-two."}]}}), flush=True)
print(json.dumps({"type": "result", "session_id": "s-1", "result": "Forty-two."}), flush=True)
'''


def pump(ms):
    from gi.repository import GLib

    context = GLib.MainContext.default()
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        context.iteration(False)
        time.sleep(0.002)


def pump_until(condition, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(20)
        if condition():
            return True
    return False


def fresh(app, specs=None, start_at_home=True, **kwargs):
    from mindpalace.window import PalaceWindow

    window = PalaceWindow(app, specs=specs if specs is not None else
                          rooms.discover(fake_checkout(), platform="posix"), **kwargs)
    window.present()
    assert pump_until(lambda: window.current is not None), "the window never chose a room"
    pump(50)
    # Every test starts from Home unless it is the one about coming back.
    if start_at_home:
        window.show_room("Home")
    return window


def close(window):
    window.close()
    pump(50)


def test_it_opens_on_home(app):
    window = fresh(app)
    assert window.current is not None and window.current.spec.title == "Home"
    assert window.title_widget.get_title() == "Home"
    assert isinstance(window.stack.get_visible_child(), type(window.current.room.widget))
    close(window)


def test_rooms_are_built_only_when_visited(app):
    window = fresh(app)
    built = [s.spec.title for s in window.slots.values() if s.room is not None]
    assert built == ["Home"], built
    assert window.show_room("fake"), "a title is matched without regard to case"
    assert window.current.spec.title == "Fake"
    assert window.slots["Fake"].room is not None and window.slots["Doorway"].room is None
    assert not window.show_room("Nowhere")
    assert window.current.spec.title == "Fake", "asking for no room leaves you where you are"
    close(window)


def test_a_room_keeps_its_state_between_visits(app):
    window = fresh(app)
    window.show_room("Fake")
    first = window.slots["Fake"].room
    seen, gone = first.seen, first.gone
    window.show_room("Home")
    window.show_room("Fake")
    assert window.slots["Fake"].room is first
    assert (first.seen, first.gone) == (seen + 1, gone + 1)
    close(window)


def test_a_rooms_keys_come_and_go_with_it(app):
    window = fresh(app)
    window.show_room("Fake")
    room = window.slots["Fake"].room
    assert app.get_accels_for_action("room.poke") == ["<Control>p"]
    # Where the application's shortcuts look: the window, not the widget.
    window.activate_action("room.poke", None)
    assert room.fired == ["poke"], "the action does not resolve from the window"
    # And from inside the room, as a button or a menu there would.
    room.widget.activate_action("room.poke", None)
    assert room.fired == ["poke", "poke"]
    window.show_room("Home")
    assert app.get_accels_for_action("room.poke") == [], "the keys outlived the room"
    assert not window.activate_action("room.poke", None), "the action outlived the room"
    assert room.fired == ["poke", "poke"]
    close(window)


def test_a_room_with_only_a_widget_is_a_room(app):
    window = fresh(app)
    assert window.show_room("Bare")
    assert window.title_widget.get_title() == "Bare"
    window.show_room("Home")
    close(window)


def test_a_room_that_raises_costs_that_room_and_not_the_palace(app):
    from mindpalace.door import BrokenRoom

    window = fresh(app)
    assert window.show_room("Fallen")
    room = window.slots["Fallen"].room
    assert isinstance(room, BrokenRoom) and "the roof came in" in room.problem
    assert window.show_room("Home")
    close(window)


def _no_spawning(window):
    from mindpalace import winenv

    calls = []
    kept = winenv.spawn

    def spawn(argv, cwd=None):
        calls.append((argv, cwd))
        return True

    winenv.spawn = spawn
    return calls, lambda: setattr(winenv, "spawn", kept)


def test_a_door_opens_the_app_in_its_own_window(app):
    from mindpalace import winenv
    from mindpalace.door import DoorRoom

    window = fresh(app)
    calls, restore = _no_spawning(window)
    try:
        window.show_room("Doorway")
        room = window.slots["Doorway"].room
        assert isinstance(room, DoorRoom)
        assert window.title_widget.get_title() == "Doorway"
        room.button.emit("clicked")
        assert len(calls) == 1, calls
        argv, cwd = calls[0]
        assert argv[0] == winenv.gui_python() and argv[1].endswith("doorway.py") and len(argv) == 2
        assert cwd.endswith("Doorway")
        if winenv.WINDOWS:
            assert argv[0].lower().endswith("pythonw.exe"), "a console would flash"
    finally:
        restore()
    close(window)


def test_on_windows_every_child_is_told_not_to_make_a_console():
    from mindpalace import winenv

    if winenv.WINDOWS:
        assert winenv.NO_WINDOW == 0x08000000
    else:
        assert winenv.NO_WINDOW == 0


def test_a_door_that_cannot_run_here_will_not_try(app):
    window = fresh(app, specs=rooms.discover(fake_checkout(), platform="nt"))
    calls, restore = _no_spawning(window)
    try:
        window.show_room("Prism")
        room = window.slots["Prism"].room
        assert not room.button.get_sensitive()
        assert room.open("a-file") is False
        assert calls == []
    finally:
        restore()
    close(window)


def test_host_open_routes_to_a_room_and_shows_it(app):
    window = fresh(app)
    path = write(scratch / "something.fake", "x")
    assert window.open_target(str(path))
    assert window.current.spec.title == "Fake"
    assert window.slots["Fake"].room.opened == [str(path)]
    close(window)


def test_a_rooms_kind_outranks_an_extension(app):
    window = fresh(app)
    calls, restore = _no_spawning(window)
    try:
        note = write(scratch / "kept.md", "---\nkind: fake-kind\n---\nbody\n")
        assert window.open_target(str(note))
        assert window.slots["Fake"].room.opened == [str(note)]
    finally:
        restore()
    close(window)


def test_a_declined_file_goes_on_to_the_desktop(app):
    from mindpalace.window import PalaceWindow

    window = fresh(app)
    asked = []
    kept = PalaceWindow._open_on_the_desktop
    PalaceWindow._open_on_the_desktop = lambda self, target: asked.append(target) or True
    try:
        path = write(scratch / "x.declined.fake", "x")
        assert window.open_target(str(path))
        assert asked == [str(path)]
        assert window.current.spec.title != "Fake", "a room that declined must not be shown"
    finally:
        PalaceWindow._open_on_the_desktop = kept
    close(window)


def test_a_room_passing_something_on_is_not_handed_it_back(app):
    from mindpalace.host import Host
    from mindpalace.window import PalaceWindow

    window = fresh(app)
    asked = []
    kept = PalaceWindow._open_on_the_desktop
    PalaceWindow._open_on_the_desktop = lambda self, target: asked.append(target) or True
    try:
        window.show_room("Fake")
        path = write(scratch / "again.fake", "x")
        assert Host(window, "Fake").open(str(path))
        assert window.slots["Fake"].room.opened == [], "the room was handed its own request"
        assert asked == [str(path)]
    finally:
        PalaceWindow._open_on_the_desktop = kept
    close(window)


def test_a_scheme_nobody_declared_says_so(app):
    window = fresh(app)
    assert window.open_target("nobody:thing") is False
    close(window)


def test_study_reaches_a_door_as_its_command_line(app):
    specs = [rooms.HOME, rooms.Spec("Mnemosyne", 40, "door", folder=scratch,
                                    launcher=scratch / "mnemosyne.py", opens=("study:",),
                                    argv={"study:": ["--study"]})]
    window = fresh(app, specs=specs)
    calls, restore = _no_spawning(window)
    try:
        assert window.open_target("study:C:/notes/a.md")
        assert calls[0][0][1:] == [str(scratch / "mnemosyne.py"), "--study", "C:/notes/a.md"]
    finally:
        restore()
    close(window)


def test_the_host_has_everything_the_contract_says(app):
    from mindpalace.host import Host

    window = fresh(app)
    host = Host(window, "Fake")
    assert host.app is app and host.parent is window
    undone = []
    host.toast("A message")
    host.toast("Deleted", undo=lambda: undone.append(True))
    assert host.show("Bare") and window.current.spec.title == "Bare"
    assert not host.show("Nowhere")
    window.show_room("Fake")
    host.set_subtitle("a thing")
    assert window.title_widget.get_subtitle() == "a thing"
    window.show_room("Bare")
    assert window.title_widget.get_subtitle() == "", "the subtitle belongs to one room"
    window.show_room("Fake")
    assert window.title_widget.get_subtitle() == "a thing"
    host.set_subtitle("")
    assert window.title_widget.get_subtitle() == ""
    close(window)


def test_saying_what_is_showing_never_moves_anyone(app):
    from mindpalace.host import Host

    window = fresh(app)
    window.show_room("Bare")
    Host(window, "Fake").set_subtitle("Home")
    assert window.current.spec.title == "Bare", "a room out of sight moved the palace"
    assert window.title_widget.get_subtitle() == "", "and wrote on someone else's header"
    close(window)


def test_the_palace_saves_and_ends_every_room_built(app):
    window = fresh(app)
    window.show_room("Fake")
    room = window.slots["Fake"].room
    window.show_room("Bare")
    close(window)
    assert room.saved == 1 and room.ended == 1


def test_a_busy_room_stops_the_window_closing(app):
    window = fresh(app)
    window.show_room("Fake")
    room = window.slots["Fake"].room
    room.closable = False
    window.show_room("Home")
    window.close()
    pump(50)
    assert window.get_visible(), "the window closed under a busy room"
    assert window.current.spec.title == "Fake", "the busy room should be brought forward"
    assert room.saved == 0, "nothing saved by a close that did not happen"
    room.closable = True
    close(window)
    assert room.saved == 1


def test_the_last_room_is_remembered(app):
    from mindpalace.config import Config

    window = fresh(app)
    window.show_room("Bare")
    close(window)
    assert Config()["last_room"] == "Bare"
    again = fresh(app, start_at_home=False)
    assert again.current.spec.title == "Bare", again.current.spec.title
    again.show_room("Home")
    close(again)


def test_a_room_that_has_gone_is_no_trouble_to_come_back_for(app):
    from mindpalace.config import Config

    config = Config()
    config["last_room"] = "Nowhere any more"
    config.save()
    window = fresh(app, start_at_home=False)
    assert window.current.spec.title == "Home"
    close(window)


def test_ctrl_and_a_number_goes_to_a_room(app):
    window = fresh(app)
    window.activate_action("palace.room-2", None)
    assert window.current.spec.title == window.specs[1].title
    window.activate_action("palace.room-1", None)
    assert window.current.spec.title == "Home"
    assert not window.show_room_number(9)
    close(window)


def test_the_palaces_keys_are_caught_before_a_rooms(app):
    from gi.repository import Gtk

    window = fresh(app)
    controllers = window.observe_controllers()
    found = [controllers.get_item(i) for i in range(controllers.get_n_items())]
    mine = [c for c in found if isinstance(c, Gtk.ShortcutController)
            and c.get_propagation_phase() == Gtk.PropagationPhase.CAPTURE]
    assert mine, "no capture-phase shortcuts on the window"
    triggers = []
    for controller in mine:
        for i in range(controller.get_n_items()):
            triggers.append(controller.get_item(i).get_trigger().to_string())
    for wanted in ("<Control>k", "<Control><Shift>a", "<Control>1", "<Control>9"):
        parsed = Gtk.ShortcutTrigger.parse_string(wanted).to_string()
        assert parsed in triggers, (wanted, triggers)
    close(window)


def test_it_fits_in_a_tiling_column(app):
    """455px is the narrowest preset column on this machine's screen."""
    from gi.repository import Gtk

    window = fresh(app)
    window.set_default_size(455, 700)
    pump(400)
    minimum = window.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    assert minimum <= 455, f"the window cannot be made narrower than {minimum}px"
    for title in ("Fake", "Doorway", "Fallen", "Home"):
        window.show_room(title)
        pump(120)
        minimum = window.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        assert minimum <= 455, f"{title} makes the window {minimum}px at the least"
    close(window)


def test_the_sidebar_folds_away_in_a_narrow_window(app):
    window = fresh(app)
    window.set_default_size(455, 700)
    window.unmaximize()
    pump(500)
    if window.get_width() > 720:
        print(f"        (the window stayed {window.get_width()}px wide here)")
        close(window)
        return
    assert window.split.get_collapsed()
    window.split.set_show_sidebar(True)
    window.show_room("Bare")
    assert not window.split.get_show_sidebar(), "picking a room should fold the sidebar"
    close(window)


def fake_today():
    return today.Today(
        day=date(2026, 11, 2), due=12, new=8,
        inbox=[write(scratch / "inboxed" / "Old thought.md", "x"),
               write(scratch / "inboxed" / "New thought.md", "x")],
        recent=[brain.Note(scratch / "n.md", "A recent note", "SecondBrain/n.md")],
        sync=today.Sync(when=datetime.now() - timedelta(minutes=12)),
        drill=today.Drill("doom-2000", "Doomsday: 2000-2099", "11", "doomsday", 6),
        questions=[("Is free will compatible?", scratch / "q.md")],
        on_this_day=[today.Remembered("9 years ago: First day at school")])


def _labels(widget):
    from gi.repository import Gtk

    found = []
    if isinstance(widget, Gtk.Label):
        found.append(widget.get_text())
    if isinstance(widget, Gtk.Button) and widget.get_label():
        found.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        found.extend(_labels(child))
        child = child.get_next_sibling()
    return found


def _button(widget, text):
    from gi.repository import Gtk

    if isinstance(widget, Gtk.Button) and (
            widget.get_label() == text or text in _labels(widget)):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _button(child, text)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def test_home_says_what_is_waiting(app):
    from mindpalace.home import HomeRoom
    from mindpalace.host import Host

    window = fresh(app)
    calls, restore = _no_spawning(window)
    try:
        room = HomeRoom(Host(window, "Home"), gather=fake_today, refresh_notes=lambda: 0)
        room.refresh()
        assert pump_until(lambda: room.today is not None), "Home never drew"
        text = " | ".join(_labels(room.widget))
        for wanted in ("Monday, 2 November", "12", "due", "8", "new today", "2 waiting",
                       "Old thought", "Doomsday: 2000-2099", "doomsday · 6 min · module 11",
                       "Is free will compatible?", "9 years ago: First day at school",
                       "A recent note", "Synced 12 minutes ago"):
            assert wanted in text, f"{wanted!r} not in {text}"
        opened = []
        window.open_target, kept = (lambda path, source=None: opened.append(path) or True), window.open_target
        _button(room.widget, "A recent note").emit("clicked")
        assert opened == [str(scratch / "n.md")], opened
        window.open_target = kept
        _button(room.widget, "Review").emit("clicked")
        assert window.current is not None
    finally:
        restore()
    close(window)


def test_home_leaves_out_what_has_nothing_to_say(app):
    from mindpalace.home import HomeRoom
    from mindpalace.host import Host

    window = fresh(app)
    empty = today.Today(day=date(2026, 11, 2), due=0, new=0)
    room = HomeRoom(Host(window, "Home"), gather=lambda: empty, refresh_notes=lambda: 0)
    room.refresh()
    assert pump_until(lambda: room.today is not None)
    text = " | ".join(_labels(room.widget))
    assert "Nothing to review today." in text and "Inbox" not in text
    assert "Questions" not in text and "On this day" not in text
    assert "Not synced on this machine yet" in text
    close(window)


def test_a_cards_problem_is_said_on_the_card(app):
    from mindpalace.home import HomeRoom
    from mindpalace.host import Host

    window = fresh(app)
    sad = today.Today(day=date(2026, 11, 2), cards_problem="no learning.yaml")
    room = HomeRoom(Host(window, "Home"), gather=lambda: sad, refresh_notes=lambda: 0)
    room.refresh()
    assert pump_until(lambda: room.today is not None)
    assert any("no learning.yaml" in t for t in _labels(room.widget))
    close(window)


def test_home_draws_first_and_reads_again_if_the_index_changed(app):
    from mindpalace.home import HomeRoom
    from mindpalace.host import Host

    window = fresh(app)
    gathered, asked = [], []

    def gather():
        gathered.append(True)
        return today.Today(day=date(2026, 11, 2), due=1, new=0)

    def refresh_notes():
        asked.append(len(gathered))
        return 3

    room = HomeRoom(Host(window, "Home"), gather=gather, refresh_notes=refresh_notes)
    room.refresh()
    assert pump_until(lambda: len(gathered) == 2), f"read {len(gathered)} times"
    assert asked == [1], "the index is asked for once, after the first drawing"
    pump(100)
    assert asked == [1] and len(gathered) == 2
    close(window)


def test_search_finds_rooms_at_once_and_the_brain_after(app):
    from mindpalace.search import SearchDialog

    window = fresh(app)
    hits = [brain.Hit(title="A note about fakes", path=scratch / "hit.md", kind="note",
                      snippet="about «fakes» & co")]
    dialog = SearchDialog(window, search=lambda text: hits)
    dialog.present(window)
    pump(60)
    assert _labels(dialog.list), "an empty search should offer the rooms"
    dialog.entry.set_text("fake")
    assert pump_until(lambda: "A note about fakes" in _labels(dialog.list)), _labels(dialog.list)
    text = _labels(dialog.list)
    assert text.index("Fake") < text.index("A note about fakes"), "rooms come first"
    assert dialog.list.get_row_at_index(0) in dialog.actions
    dialog._pick_first()
    pump(50)
    assert window.current.spec.title == "Fake"
    close(window)


def test_a_search_hit_goes_through_the_routing(app):
    from mindpalace.search import SearchDialog

    window = fresh(app)
    target = write(scratch / "hit2.fake", "x")
    hits = [brain.Hit(title="zzz", path=target, kind="note")]
    dialog = SearchDialog(window, search=lambda text: hits)
    dialog.present(window)
    dialog.entry.set_text("zzz")
    assert pump_until(lambda: "zzz" in _labels(dialog.list)), _labels(dialog.list)
    dialog._pick_first()
    pump(50)
    assert window.slots["Fake"].room.opened == [str(target)]
    close(window)


def test_only_the_newest_search_is_shown(app):
    from mindpalace.search import SearchDialog

    window = fresh(app)

    def slow_then_fast(text):
        if text == "old":
            time.sleep(0.2)
        return [brain.Hit(title=f"hit for {text}", path=scratch / "h.md", kind="note")]

    dialog = SearchDialog(window, search=slow_then_fast)
    dialog.present(window)
    dialog.entry.set_text("old")
    pump(120)
    dialog.entry.set_text("new")
    assert pump_until(lambda: "hit for new" in _labels(dialog.list))
    pump(350)
    assert "hit for old" not in _labels(dialog.list)
    dialog.close()
    close(window)


def test_a_snippets_markers_are_bold_and_the_rest_is_escaped():
    from mindpalace.search import snippet_markup

    assert snippet_markup("a «b» & <c>") == "a <b>b</b> &amp; &lt;c&gt;"


def stand_in_command():
    return [sys.executable, str(write(scratch / "claude_stand_in.py", STAND_IN))]


def test_ask_claude_streams_an_answer_about_the_rooms_file(app):
    window = fresh(app, ask_command=stand_in_command())
    window.show_room("Fake")
    window.slots["Fake"].room.files = [str(scratch / "about-this.md")]
    window.activate_action("palace.ask", None)
    panel = window.ask_panel
    assert panel.get_visible() and window.ask_button.get_active()
    assert "about-this.md" in panel.about.get_text()
    panel.prompt.get_buffer().set_text("What is the answer?")
    panel.submit()
    assert pump_until(lambda: panel.request is None and "Forty-two" in panel.answer.get_text()), \
        panel.answer.get_text()
    assert panel.session_id == "s-1" and panel.desk.get_visible()
    panel.close()
    assert not panel.get_visible() and not window.ask_button.get_active()
    close(window)


def test_a_room_can_ask_claude_and_it_asks_at_once(app):
    from mindpalace.host import Host

    window = fresh(app, ask_command=stand_in_command())
    Host(window, "Fake").ask_claude("Explain this", [str(scratch / "card.md")])
    panel = window.ask_panel
    assert panel.get_visible() and "card.md" in panel.about.get_text()
    assert pump_until(lambda: "Forty-two" in panel.answer.get_text())
    close(window)


def test_with_no_claude_the_panel_says_so(app):
    from mindpalace import ask

    window = fresh(app)
    kept = ask.claude.available
    ask.claude.available = lambda: False
    try:
        window.ask_claude()
        assert "not installed" in window.ask_panel.answer.get_text()
        window.ask_panel.prompt.get_buffer().set_text("Anyone?")
        window.ask_panel.submit()
        assert window.ask_panel.request is None
    finally:
        ask.claude.available = kept
    close(window)


def test_a_theme_changed_in_the_palace_reaches_every_room(app):
    """The palace's theme is the one in force, in every room, and they are told."""
    import importlib

    checkout = os.path.dirname(ROOT)
    sys.path.append(checkout)
    other = importlib.import_module("house.housestyle")  # another copy, as a room has
    window = fresh(app)
    window.show_room("Fake")
    room = window.slots["Fake"].room
    try:
        for key in ("moss", "porcelain"):
            window.config["follow_system"] = False
            window.config["theme"] = key
            window.restyle()
            assert other.current().key == key, (key, other.current().key)
        assert room.restyled == 2, room.restyled
        # The desktop turning light or dark reaches the rooms too, after the palace.
        window._watching(True)
        pump(50)
        assert room.restyled == 3, room.restyled
    finally:
        sys.path.remove(checkout)
        window.config["follow_system"] = True
        window.config.save()
    close(window)


def test_the_dialogs_open(app):
    window = fresh(app)
    window.show_preferences()
    window.show_shortcuts()
    window.show_about()
    window.show_search()
    pump(80)
    close(window)


def test_the_real_rooms_open_here(app):
    """Mnemosyne and The Eye, as they are on main, built in a real palace."""
    from gi.repository import Gtk

    make_home()
    window = fresh(app, specs=[rooms.HOME] + [s for s in rooms.discover() if s.kind == "room"])
    trouble = []
    for slot in list(window.slots.values()):
        if slot.spec.kind != "room":
            continue
        window.show_room(slot.spec.title)
        pump(300)
        from mindpalace.door import BrokenRoom

        if isinstance(slot.room, BrokenRoom):
            trouble.append(f"{slot.spec.title}: {slot.room.problem}")
            continue
        if slot.room.widget.get_root() is not window:
            trouble.append(f"{slot.spec.title} is not in the palace's window")
        keys = list(getattr(slot.room, "accels", None) or [])
        # Not compared to what was set: GTK hands accelerators back normalised.
        # And only those GTK can parse: a room's bad key must not cost its others.
        good = [(action, wanted) for action, wanted in keys
                if any(Gtk.accelerator_parse(key)[0] for key in wanted)]
        missing = [action for action, _wanted in good if not app.get_accels_for_action(action)]
        if missing:
            trouble.append(f"{slot.spec.title}'s keys were not taken up: {missing[:3]}")
        window.show_room("Home")
        pump(40)
        if any(app.get_accels_for_action(action) for action, _wanted in keys):
            trouble.append(f"{slot.spec.title}'s keys outlived it")
    from mindpalace.window import PalaceWindow

    for window_ in app.get_windows():
        if not isinstance(window_, PalaceWindow):
            trouble.append(f"a room put a window in the palace's list: {window_}")
    assert not trouble, trouble
    close(window)


def test_study_reaches_the_real_mnemosyne(app):
    where = make_home()
    note = write(where.studies / "Biology" / "study-me.md", "# Study\n\nFront :: Back\n")
    window = fresh(app, specs=[rooms.HOME] + [s for s in rooms.discover() if s.title == "Mnemosyne"])
    assert window.open_target(f"study:{note}"), "the room declined its own scheme"
    assert window.current.spec.title == "Mnemosyne"
    room = window.slots["Mnemosyne"].room
    assert not room.open(str(note)), "a plain note is not Mnemosyne's to open"
    pump(200)
    room.window.close_activity()
    close(window)


def test_a_key_gtk_cannot_read_does_not_cost_the_others(app):
    from mindpalace.window import _parsable

    assert _parsable(["<Page_Up>", "p", "<Control>x"]) == ["p", "<Control>x"]
    assert _parsable([]) == []


def test_the_arguments_are_a_room_and_targets():
    # Importing app.py needs GTK, but no display.
    from mindpalace.app import split_arguments

    assert split_arguments([]) == (None, [])
    assert split_arguments(["a.md", "b.png"]) == (None, ["a.md", "b.png"])
    assert split_arguments(["--room", "Fathom", "a.md"]) == ("Fathom", ["a.md"])
    assert split_arguments(["--room=The Eye"]) == ("The Eye", [])


def main():
    print("Mind Palace tests")
    print("\nwithout a display")
    plain = [(name, fn) for name, fn in globals().items()
             if name.startswith("test_") and fn.__code__.co_argcount == 0]
    for name, fn in plain:
        check(name[5:].replace("_", " "), fn)

    print("\nwith a display")
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib

    application = Adw.Application(application_id="com.robin.MindPalaceTests",
                                  flags=Gio.ApplicationFlags.NON_UNIQUE)

    def run_window_tests():
        for name, fn in list(globals().items()):
            if name.startswith("test_") and fn.__code__.co_argcount == 1:
                check(name[5:].replace("_", " "), fn, application)
        application.release()
        application.quit()
        return GLib.SOURCE_REMOVE

    def activate(instance):
        # Without the hold there is no main loop left for the timeout to run in.
        instance.hold()
        GLib.timeout_add(100, run_window_tests)

    application.connect("activate", activate)
    application.run([])

    if failures:
        print(f"\n{len(failures)} failed")
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    sys.exit(code)
