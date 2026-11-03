"""Ariadne's suite: what is checked without a screen, and what needs one.

    python3 tests/test_ariadne.py

The first half needs nothing but Python: a thought's text as styled runs,
the arrangement of a thread with no layout, what is read out of Claude's
answer, the library and the command line. The second half builds the real
view — in its own window and in a stand-in for Mind Palace — against a
scratch Order, and drives it: keeping a question, editing one, drawing and
taking away thoughts and arrows, a file changed elsewhere, Claude played by
a script.

Nothing here touches the real brain or reaches a model: `ORDER_HOME` points
at the scratch folder, and `claude` is a stand-in that answers from a file.
"""

import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

scratch = tempfile.mkdtemp(prefix="ariadne-tests-")
for name, folder in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                     ("XDG_CACHE_HOME", "cache"), ("LOCALAPPDATA", "local")):
    os.environ[name] = os.path.join(scratch, folder)
os.environ["APPS_NO_UPDATE"] = "1"
HOME = os.path.join(scratch, "home")
os.environ["ORDER_HOME"] = HOME
os.environ.pop("ANAM_HOME", None)
os.environ["ORDER_CLAUDE"] = sys.executable          # "installed" — never asked
BRAIN = os.path.join(HOME, "SecondBrain")
for sub in ("questions", "threads", "inbox"):
    os.makedirs(os.path.join(BRAIN, sub), exist_ok=True)

from ariadne import cli, inline, layout, thinking, threads  # noqa: E402
from ariadne.library import Library  # noqa: E402
from ariadne.threads import Edge, Question, Thread  # noqa: E402

failures = []
TZ = timezone(timedelta(hours=1))

THREAD = """---
kind: thread
title: Compatibilism holds
answers: ["Is free will compatible with a deterministic universe?"]
created: 2026-10-27T13:20:00+01:00
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
status: open
revisit: 2026-01-01
---

What prompted it.

## Revisits

- 2026-02-01 — still open; leaning towards [[Compatibilism holds]]
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


def write(rel, text):
    path = os.path.join(BRAIN, *rel.split("/"))
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def clear_brain():
    for sub in ("questions", "threads"):
        folder = os.path.join(BRAIN, sub)
        for name in os.listdir(folder):
            os.remove(os.path.join(folder, name))


# ------------------------------------------------------- without a display --

def test_a_thought_reads_as_it_was_marked():
    plain, runs = inline.render("A **bold** and *em* `x` [[Fémek#Áram|metals]] and [w](http://x)")
    assert plain == "A bold and em x metals and w", plain
    data = plain.encode("utf-8")
    spans = {(r.kind, data[r.start:r.end].decode(), r.target) for r in runs}
    assert ("strong", "bold", "") in spans and ("em", "em", "") in spans
    assert ("code", "x", "") in spans
    assert ("link", "metals", "Fémek") in spans and ("link", "w", "http://x") in spans
    link = next(r for r in runs if r.target == "Fémek")
    assert inline.link_at(runs, link.start) is link and inline.link_at(runs, 0) is None
    assert "<a href=\"wiki:F%C3%A9mek\">metals</a>" in inline.markup("[[Fémek|metals]]")
    assert inline.parse_href("wiki:F%C3%A9mek") == ("Fémek", True)
    assert inline.markup("a < b & **c**") == "a &lt; b &amp; <b>c</b>"


def test_a_thread_with_no_layout_reads_left_to_right():
    places = layout.arrange(["a", "b", "c", "d"],
                            [("a", "c"), ("b", "c"), ("c", "d"), ("d", "a")])
    assert set(places) == {"a", "b", "c", "d"}
    # d -> a closes a cycle, which is broken for the arrangement only.
    assert places["a"][0] < places["c"][0] < places["d"][0], places
    tall = layout.arrange(["a", "b"], [], heights={"a": 400, "b": 50})
    assert tall["b"][1] - tall["a"][1] >= 400, "a tall card was laid over the next"


def test_a_free_spot_is_never_on_a_card():
    places = {"a": (0, 0), "b": (layout.COLUMN, 0)}
    x, y = layout.free_spot(places, (0, 0))
    assert all(abs(x - px) > 100 or abs(y - py) > 100 for px, py in places.values())


def test_claudes_answer_becomes_ghosts_and_only_new_ones():
    thread = Thread.parse(THREAD)
    reply = ("Here:\n```\n## n1 · Causes need not compel\nA cause explains; it does not force.\n"
             "-> c : because\n-> zz\n\n## a · Every choice has causes\n-> n1 : but\n-> c\n```")
    proposal = thinking.read(reply, thread)
    assert [n.id for n in proposal.nodes] == ["n1"]
    assert proposal.edges == [("n1", Edge("c", "because")), ("a", Edge("n1", "but"))], \
        proposal.edges
    mapping = thinking.accept(thread, proposal, {"n1": (500, 300)})
    real = mapping["n1"]
    assert thread.node(real).title == "Causes need not compel"
    assert Edge(real, "but") in thread.node("a").edges
    assert thread.layout[real] == (500.0, 300.0)
    assert not thinking.read("Nothing to add, really.", thread)
    for prompt in (thinking.prompt_for_node(thread, thread.node("a")),
                   thinking.prompt_for_question(thread, "Why?"),
                   thinking.prompt_for_thread(thread)):
        assert "## a · Every choice has causes" in prompt and "n1" in prompt


def test_the_library_knows_what_it_wrote_and_what_it_did_not():
    clear_brain()
    question_path = write("questions/Free will.md", QUESTION)
    thread_path = write("threads/Compatibilism holds.md", THREAD)
    library = Library()
    library.refresh()
    question = library.find(question_path)
    thread = library.find(thread_path)
    assert isinstance(question, Question) and isinstance(thread, Thread)
    assert library.threads_for(question) == [thread]
    assert library.questions_of(thread) == [question]
    assert library.resolve("compatibilism HOLDS") is not None
    assert library.on_disk() == library._stamps, "nothing changed: nothing to read again"
    question.add_revisit("closer now", date(2026, 3, 1))
    library.save(question)
    assert not library.changed_elsewhere(question)
    assert not question.changed, "a question just written still counts as changed"
    with open(question_path, "a", encoding="utf-8") as fh:
        fh.write("- 2026-04-01 — written by hand\n")
    os.utime(question_path, ns=(1, 1_700_000_000_000_000_000))
    assert library.changed_elsewhere(question)
    fresh = library.reread(question)
    assert fresh.revisits[-1] == ("2026-04-01", "written by hand")


def test_the_command_line_keeps_a_question_without_a_window():
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["--ask", "Why", "is", "anything?"])
    path = out.getvalue().strip()
    assert code == 0 and os.path.isfile(path), (code, path)
    question = Question.load(path)
    assert question.title == "Why is anything?" and question.status == "open"
    assert cli.main([]) is None and cli.main(["--ask"]) is None
    os.remove(path)


# --------------------------------------------------------- with a display --

def test_a_question_kept_is_a_file_at_once_and_undo_takes_it_back(view):
    asked = datetime(2026, 10, 28, 7, 41, tzinfo=TZ)
    toasts = []
    view.toast = lambda text, undo=None: toasts.append((text, undo))
    try:
        question = view._kept("What is a number?", "On the bus.", asked)
    finally:
        del view.toast
    assert question.path.is_file()
    text = read(question.path)
    assert "asked: 2026-10-28T07:41:00+01:00" in text and "On the bus." in text
    assert question in view.library.questions
    assert any(row._item is question for row in rows(view.sidebar.question_list))
    toasts[-1][1]()
    assert not question.path.exists() and question not in view.library.questions


def test_the_quick_question_takes_the_moment_it_opened(view):
    from ariadne.dialogs import QuickQuestion

    kept = []
    dialog = QuickQuestion(lambda title, text, asked: kept.append((title, text, asked)))
    opened = dialog.asked
    dialog.entry.set_text("  Is time   real? ")
    dialog.text.get_buffer().set_text("Watching a clock.")
    dialog.keep()
    assert kept == [("Is time real?", "Watching a clock.", opened)]


def test_editing_a_question_writes_it_a_moment_later(view):
    clear_brain()
    path = write("questions/Free will.md", QUESTION)
    view.reload()
    question = view.library.find(path)
    view.open_item(question)
    page = view.question_page
    page.text.get_buffer().set_text("What prompted it, said better.")
    page.status.set_selected(threads.STATUSES.index("living"))
    page.flush()
    text = read(path)
    assert "status: living" in text and "What prompted it, said better." in text
    assert "- 2026-02-01 — still open" in text, "the revisits were lost"
    page.revisit_entry.set_text("still thinking")
    page.add_revisit()
    assert read(path).rstrip().endswith(f"- {date.today().isoformat()} — still thinking")
    page._set_revisit(date(2030, 1, 1))
    assert "revisit: 2030-01-01" in read(path)


def test_opening_a_question_does_not_leave_its_title_selected(view):
    clear_brain()
    path = write("questions/Free will.md", QUESTION)
    view.reload()
    view.open_item(view.library.find(path))
    assert view.question_page.title.get_selection_bounds() in (None, False, ()) \
        or view.question_page.title.get_selection_bounds()[0] is False, \
        "the next key would have replaced the question"


def test_a_renamed_question_takes_its_threads_with_it(view):
    clear_brain()
    q_path = write("questions/Free will.md", QUESTION)
    t_path = write("threads/Compatibilism holds.md", THREAD)
    view.reload()
    view.open_item(view.library.find(q_path))
    view.question_page.title.set_text("Is free will real?")
    view.question_page.flush()
    assert 'answers: ["Is free will real?"]' in read(t_path), read(t_path)
    assert [t.title for t in view.library.threads_for(view.library.find(q_path))] == \
        ["Compatibilism holds"]


def test_the_canvas_draws_arrows_and_takes_them_away_with_an_undo(view):
    clear_brain()
    path = write("threads/Compatibilism holds.md", THREAD)
    view.reload()
    thread = view.library.find(path)
    view.open_item(thread)
    page = view.thread_page
    assert page.canvas.auto and set(page.canvas.cards) == {"a", "b", "c"}
    assert "layout:" not in read(path), "an arrangement nobody asked to keep was written"

    page._connect("b", "a")
    assert "-> a" in read(path).split("## b ·")[1].split("## c")[0]
    assert "layout:" in read(path), "drawing an arrow should keep where things are"

    toasts = []
    view.toast = lambda text, undo=None: toasts.append((text, undo))
    try:
        page._delete(("edge", ("a", thread.node("a").edges[0])))
        assert "-> c : therefore" not in read(path)
        toasts[-1][1]()
        assert "-> c : therefore" in read(path)
        page._delete(("node", "c"))
        assert "## c ·" not in read(path) and "-> c" not in read(path)
        toasts[-1][1]()
        assert "## c · Freedom is compatible with determinism" in read(path)
        assert "-> c : therefore" in read(path)
    finally:
        del view.toast


def test_a_thought_added_on_the_canvas_is_written_where_it_was_put(view):
    clear_brain()
    path = write("threads/Compatibilism holds.md", THREAD)
    view.reload()
    view.open_item(view.library.find(path))
    page = view.thread_page
    editors = []
    page.edit_thought = lambda node_id, fresh=False: editors.append((node_id, fresh))
    try:
        page.add_thought((700, 420))
    finally:
        del page.edit_thought
    made = editors[0][0]
    assert editors == [(made, True)] and made not in ("a", "b", "c")
    node = page.thread.node(made)
    node.title, node.text = "Moved by reasons", "Reasons are causes too."
    page.save()
    text = read(path)
    assert f"## {made} · Moved by reasons\nReasons are causes too." in text
    assert f"{made}: [700, 420]" in text


def test_the_canvas_finds_what_is_under_the_pointer(view):
    clear_brain()
    path = write("threads/Compatibilism holds.md",
                 THREAD.replace("What I want", "[[Free will]] asks it. What I want"))
    view.reload()
    view.open_item(view.library.find(path))
    canvas = view.thread_page.canvas
    pump(100)
    card = canvas.cards["a"]
    assert canvas.card_at(card.x + 5, card.y + 5) is card
    assert canvas.card_at(card.x - 500, card.y) is None
    assert canvas.handle_at(*card.handle()) is card
    # The link is the first thing in the thought's text.
    link = canvas.link_at(card.x + 16, card.text_top + card.y + 6)
    assert link is not None and link.target == "Free will" and link.wiki
    source, edge, start, end, _box = next(g for g in canvas._edge_geometry() if g[0] == "b")
    middle = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    assert canvas.edge_at(*middle) == ("b", edge)
    before = canvas.scale
    canvas.zoom(1.5, (10, 10))
    assert canvas.scale > before
    assert abs(canvas.to_world(*canvas.to_screen(33, 44))[0] - 33) < 1e-6


def test_a_file_changed_elsewhere_is_read_again(view):
    clear_brain()
    path = write("threads/Compatibilism holds.md", THREAD)
    view.reload()
    view.open_item(view.library.find(path))
    write("threads/Compatibilism holds.md", THREAD.replace("## c · Freedom is compatible",
                                                           "## c · Freedom fits"))
    os.utime(path, ns=(1, 1_800_000_000_000_000_000))
    toasts = []
    view.toast = lambda text, undo=None: toasts.append((text, undo))
    try:
        view.check_disk()
    finally:
        del view.toast
    assert view.current.node("c").title.startswith("Freedom fits")
    assert view.thread_page.canvas.cards["c"].node.title.startswith("Freedom fits")
    assert toasts and "changed elsewhere" in toasts[-1][0]


STANDIN = """
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "reply.txt"), encoding="utf-8") as fh:
    reply = fh.read()
for event in ({"type": "system", "session_id": "s-1"},
              {"type": "result", "result": reply, "is_error": False, "session_id": "s-1"}):
    print(json.dumps(event), flush=True)
"""


def standin(reply):
    folder = os.path.join(scratch, "claude")
    os.makedirs(folder, exist_ok=True)
    script = os.path.join(folder, "standin.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(STANDIN)
    with open(os.path.join(folder, "reply.txt"), "w", encoding="utf-8") as fh:
        fh.write(reply)
    return [sys.executable, script]


def test_claude_proposes_ghosts_and_nothing_is_written_until_accepted(view):
    clear_brain()
    path = write("threads/Compatibilism holds.md", THREAD)
    view.reload()
    view.open_item(view.library.find(path))
    page = view.thread_page
    page.command = standin("## n1 · Reasons are causes\nAnd so not outside the chain.\n"
                           "-> c : therefore\n")
    page.canvas.selection = ("node", "a")
    page.think()
    settle(lambda: page.proposal is not None or not page.stop_button.get_visible(), 15000)
    assert page.proposal is not None, page.ghost_label.get_text()
    assert set(page.canvas.ghosts) == {"n1"} and "n1" not in read(path)
    page.discard()
    assert not page.canvas.ghosts and "Reasons are causes" not in read(path)

    page.think()
    settle(lambda: page.proposal is not None, 15000)
    page.accept()
    text = read(path)
    assert " · Reasons are causes\nAnd so not outside the chain.\n-> c : therefore" in text, text
    assert not page.canvas.ghosts and len(page.canvas.cards) == 4


def test_the_room_works_hosted_in_another_window(view_app):
    from gi.repository import Adw

    from ariadne import room

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
    assert (room.TITLE, room.ICON) == ("Ariadne", "ariadne") and isinstance(room.ORDER, int)
    assert room.KINDS == ("question", "thread")
    window = Adw.ApplicationWindow(application=view_app)
    window.set_content(built.widget)
    built.widget.insert_action_group("room", built.actions)
    window.present()
    pump(200)
    path = write("threads/Compatibilism holds.md", THREAD)
    built.view.reload()
    assert built.open(path) and host.shown[-1] == "Compatibilism holds"
    assert not built.open(os.path.join(BRAIN, "inbox", "nothing.md"))
    assert {action for action, _keys in built.accels} >= {"room.keep-question", "room.arrange"}
    # The palace's own keys are not asked for (ROOMS.md).
    taken = {key for _action, keys in built.accels for key in keys}
    assert not taken & {"<Control><Shift>a", "F9", "<Control>question"}, taken
    assert built.context() == [path]
    built.shown()
    built.widget.activate_action("room.arrange", None)
    assert "layout:" in read(path)
    # A link to a note that is not Ariadne's goes to the palace.
    note = os.path.join(BRAIN, "inbox", "A note.md")
    with open(note, "w", encoding="utf-8") as fh:
        fh.write("# A note\n")
    built.view.library.resolve = lambda target, near=None: note
    built.view.follow_link("A note")
    assert host.opened == [note]
    assert built.can_close()
    built.save_state()
    window.close()


def test_the_window_fits_in_a_tiling_column(window):
    minimum = window.get_content().measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    assert minimum <= 455, f"the content cannot be made narrower than {minimum}px"


def test_nothing_is_cut_off_where_it_has_room(view_app):
    # What matters is what is inside: a breakpoint bin reports the size it was asked
    # for whatever its child needs, and clips the child to it.
    from gi.repository import Adw

    from ariadne.view import AriadneView

    clear_brain()
    path = write("questions/Free will.md", QUESTION)
    for width in (455, 700, 781, 1100):
        view = AriadneView()
        shell = Adw.ApplicationWindow(application=view_app, default_width=width,
                                      default_height=520)
        shell.set_content(view)
        shell.present()
        pump(200)
        view.reload()
        view.open(path)
        pump(120)
        needed = view.toasts.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        assert needed <= width, f"{needed}px of content in a {width}px window"
        shell.destroy()
        pump(30)


def test_no_key_does_two_things(window):
    from ariadne.view import ACCELERATORS, SHORTCUTS

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
    for keys in ("Ctrl+N", "Ctrl+Shift+N", "Ctrl+Enter", "Ctrl+L", "Ctrl+Shift+A", "F9"):
        assert keys in sheet, keys


# ------------------------------------------------------------------ helpers --

def rows(listing):
    child = listing.get_first_child()
    while child is not None:
        if hasattr(child, "_item"):
            yield child
        child = child.get_next_sibling()


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
    print("Ariadne tests")
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
    from ariadne import widgets
    from ariadne.window import MainWindow

    app = Adw.Application(application_id="com.robin.AriadneTests",
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
