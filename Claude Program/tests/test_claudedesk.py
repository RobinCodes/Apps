"""What can be checked without spending a token.

Two halves, as the layout intends: the protocol and the session state need no
display, and the widgets need one but no API. Run it with

    python3 tests/test_claudedesk.py

and add CLAUDE_DESK_LIVE=1 to also drive a real `claude` child — that half
does spend tokens, so it is off by default.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
scratch = tempfile.mkdtemp(prefix="claude-desk-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(scratch, "data")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gio, Gtk  # noqa: E402

from claudedesk import backend, chat, config, widgets  # noqa: E402
from claudedesk.backend import Event  # noqa: E402
from claudedesk.manager import Manager  # noqa: E402

QUESTION = {
    "request_id": "r1", "tool_name": "AskUserQuestion", "display_name": "AskUserQuestion",
    "input": {"questions": [
        {"question": "Tea or coffee?", "header": "Drink", "multiSelect": False,
         "options": [{"label": "Tea", "description": "Lighter."},
                     {"label": "Coffee", "description": "Stronger.", "preview": "espresso"}]},
        {"question": "Extras?", "header": "Extras", "multiSelect": True,
         "options": [{"label": "Milk"}, {"label": "Sugar"}]},
    ]},
    "tool_use_id": "toolu_1", "requires_user_interaction": True,
}
COMMANDS = [
    {"name": "usage", "description": "Show session cost, plan usage, and what's contributing "
     "to your limits", "argumentHint": "", "aliases": ["cost", "stats"]},
    {"name": "context", "description": "Show current context usage", "argumentHint": ""},
    {"name": "code-review", "description": "Review the current diff. Pass --fix to apply.",
     "argumentHint": "[low|medium|high]"},
    {"name": "clear", "description": "Start a new session with empty context",
     "argumentHint": "[name]", "aliases": ["reset", "new"]},
    {"name": "model", "description": "Set the AI model", "argumentHint": "<model>"},
    {"name": "rename", "description": "Rename the current conversation",
     "argumentHint": "[name]", "aliases": ["name"]},
]


def check(name, fn):
    fn()
    print(f"  ok  {name}")


# ------------------------------------------------------------- the protocol --

def test_split_command():
    for text, want in [
        ("/usage", ("usage", "")), ("/model haiku", ("model", "haiku")),
        ("/rename my session", ("rename", "my session")), ("  /context  ", ("context", "")),
        ("/code-review high --fix", ("code-review", "high --fix")),
        ("/init\nand then some", ("init", "and then some")),
        ("hello", ("", "")), ("/home/robin/x.md is stale", ("", "")),
        ("//comment", ("", "")), ("/", ("", "")),
    ]:
        assert backend.split_command(text) == want, text


def test_questions_parsed():
    questions = backend.questions_in(QUESTION)
    assert [q["question"] for q in questions] == ["Tea or coffee?", "Extras?"]
    assert questions[0]["multi"] is False and questions[1]["multi"] is True
    assert questions[0]["options"][1]["preview"] == "espresso"
    # Anything that is not a well-formed question falls back to Allow / Deny.
    assert backend.questions_in({"tool_name": "Bash", "input": {"command": "ls"}}) == []
    assert backend.questions_in({"tool_name": "AskUserQuestion", "input": {}}) == []
    assert backend.questions_in(
        {"tool_name": "AskUserQuestion", "input": {"questions": "nonsense"}}) == []


def test_answer_frame():
    written = []
    child = backend.Backend(cwd="/tmp", model="default", permission_mode="manual")
    child._write = lambda obj: written.append(obj) or True

    child.answer_question("r1", QUESTION["input"], {"Tea or coffee?": "Tea"},
                          {"Tea or coffee?": {"preview": "espresso"}})
    sent = written[-1]["response"]["response"]
    assert written[-1]["response"]["request_id"] == "r1"
    assert sent["behavior"] == "allow"
    # updatedInput replaces the tool's input, so the questions must go back too.
    assert sent["updatedInput"]["questions"] == QUESTION["input"]["questions"]
    assert sent["updatedInput"]["answers"] == {"Tea or coffee?": "Tea"}
    assert "answers" not in QUESTION["input"], "the request itself must not be touched"

    child.answer_question("r2", QUESTION["input"], {}, None, "Neither, actually.")
    sent = written[-1]["response"]["response"]["updatedInput"]
    assert sent["answers"] == {} and sent["response"] == "Neither, actually."


def test_command_matching():
    names = lambda prefix: [c["name"] for c in widgets.match_commands(prefix, COMMANDS)]
    assert names("c") == ["context", "code-review", "clear", "usage"], names("c")
    assert names("stat") == ["usage"], "matched on an alias"
    assert names("view") == ["code-review"], "matched inside the name"
    assert names("zzz") == []
    assert widgets.command_hint(COMMANDS[2]) == "Review the current diff"
    assert widgets.tool_summary("AskUserQuestion", {"questions": [
        {"question": "Tea or coffee?"}, {"question": "Extras?"}]}) == "Tea or coffee? (+1 more)"


# ------------------------------------------------------------- the session --

class Child:
    """A live child, as far as a session can tell."""

    def __init__(self):
        self.sent = []
        self.answered = []

    def send(self, text):
        self.sent.append(text)

    def answer_question(self, request_id, tool_input, answers, annotations, response):
        self.answered.append((request_id, answers, response))

    def stop(self):
        pass


def fresh_session(state="ready"):
    manager = Manager(config.Config())
    session = manager.new_session(cwd="/tmp")
    session.backend = Child()
    manager.live.append(session)
    session._on_event(Event("handshake", {"commands": COMMANDS}))
    session._set_state(state)
    return manager, session


def test_pending_question():
    manager, session = fresh_session()
    session._on_event(Event("permission", dict(QUESTION, questions=backend.questions_in(QUESTION))))
    assert session.state == "busy" and session.status_note == "Waiting on your answer"
    session.answer_question({"Tea or coffee?": "Tea"}, {}, "")
    assert session.pending_permission is None
    assert session.backend.answered[-1][1] == {"Tea or coffee?": "Tea"}

    # A run that ends without us, or a child that dies, takes the card with it.
    for event in (Event("turn_end", {}), Event("exit", {"code": 1})):
        manager, session = fresh_session()
        session._on_event(Event("permission", dict(QUESTION, questions=[{"question": "?"}])))
        session._on_event(event)
        assert session.pending_permission is None, event.kind

    # And a session waiting on you is never the one evicted to free memory.
    manager, session = fresh_session()
    session._on_event(Event("permission", dict(QUESTION, questions=[{"question": "?"}])))
    session.state = "ready"
    assert manager._evict_one() is False


def test_commands_remembered():
    manager, session = fresh_session()
    assert session.command_names() >= {"usage", "cost", "context", "clear"}
    assert manager.config.commands == COMMANDS, "cached for the next launch"
    # The init frame carries names only and must not overwrite the descriptions.
    session._on_event(Event("ready", {"commands": ["usage", "context"], "session_id": "s"}))
    assert any(item.get("description") for item in session.command_list())


def test_reports_are_not_messages():
    manager, session = fresh_session()
    reports = []
    session.subscribe(lambda what, data: reports.append(data) if what == "report" else None)

    session.send("/context")
    assert session.backend.sent == ["/context"], "asked of this conversation's own child"
    assert session.entries == [], "but never a message"
    session._on_event(Event("block", {"block": "text", "text": "27.6k / 1m"}))
    session._on_event(Event("turn_end", {"cost": 0.0, "duration_ms": 9}))
    assert reports[-1]["text"] == "27.6k / 1m" and reports[-1]["name"] == "context"
    assert session.entries == [], "not even a turn meta line"
    assert session.meta.name == "New session", "and no title taken from it"

    # An ordinary message is untouched by any of that.
    session.send("Hello there")
    assert [e["text"] for e in session.entries if e.get("role") == "user"] == ["Hello there"]
    session._on_event(Event("turn_end", {}))

    # A report command that turns out to talk to the model puts itself back.
    session.send("/context")
    session._on_event(Event("block", {"block": "text", "text": "Let me look."}))
    session._on_event(Event("block", {"block": "tool_use", "id": "t", "name": "Bash", "input": {}}))
    assert [e.get("role") for e in session.entries][-3:] == ["user", "assistant", "tool"]
    assert session.entries[-3]["text"] == "/context"


def test_standalone_never_waits():
    manager, session = fresh_session(state="busy")
    asked, reports = [], []
    session.subscribe(lambda what, data: reports.append(data) if what == "report" else None)
    original = backend.run_standalone
    try:
        import claudedesk.manager as module
        module.run_standalone = lambda name, cwd, done: asked.append((name, cwd))
        session.send("/usage")
    finally:
        module.run_standalone = original
    # The child is mid-turn and only reads stdin between turns, so /usage goes
    # to one of its own instead of queueing behind the reply.
    assert asked == [("usage", session.meta.cwd)], asked
    assert session.backend.sent == [] and session.outbox == []
    assert session.entries == [] and session.state == "busy"
    assert reports and reports[0]["pending"] is True


# ------------------------------------------------------------- the widgets --

def test_question_card():
    seen = []
    request = dict(QUESTION, questions=backend.questions_in(QUESTION))
    card = chat.QuestionCard(request, lambda a, n, r: seen.append((a, n, r)))
    drink, extras = card.groups
    assert not card.send.get_sensitive(), "nothing chosen yet, so nothing to send"

    drink._choices[1][1].set_active(True)
    assert drink.answer() == ("Coffee", {"preview": "espresso"})
    assert drink._previews[0][1].get_reveal_child() is True
    drink._choices[0][1].set_active(True)                     # radio: lets the other go
    assert drink.answer() == ("Tea", None)
    extras._choices[0][1].set_active(True)
    extras._choices[1][1].set_active(True)                    # boxes: both count
    assert extras.answer() == ("Milk, Sugar", None)
    card._submit()
    assert seen[-1][0] == {"Tea or coffee?": "Tea", "Extras?": "Milk, Sugar"}

    # Typing is choosing, and in a radio group it drops the tick.
    drink.entry.set_text("Mate, please")
    assert drink.other.get_active() and not drink._choices[0][1].get_active()
    assert drink.answer() == ("Mate, please", None)
    # Nothing in the card selects its own text when focus lands on it.
    assert drink.get_first_child().get_next_sibling().get_selectable() is False


def test_command_menu():
    sent = []
    composer = chat.Composer(lambda text: sent.append(text), lambda: None,
                            commands=lambda: COMMANDS)
    typed = composer.view.get_buffer().set_text

    typed("hello")
    assert not composer.strip.open, "no menu without a slash"
    typed("/co")
    # Name prefixes in the child's own order, then the one matched by alias.
    assert [c["name"] for c in composer.strip.matches] == ["context", "code-review", "usage"]
    composer.strip.move(1)
    typed("/cod")
    assert composer.strip.selected()["name"] == "code-review", "the highlight should hold"

    composer._complete()
    assert composer._raw() == "/code-review " and not composer.strip.open
    assert sent == [], "completing is not sending"
    typed("/code-review high")
    assert not composer.strip.open, "an argument means the command is chosen"

    # Enter finishes a half-typed name and sends a finished one.
    typed("/us")
    assert composer._on_key(None, Gdk.KEY_Return, 0, Gdk.ModifierType(0)) == Gdk.EVENT_STOP
    assert composer._text() == "/usage" and sent == []
    composer._on_key(None, Gdk.KEY_Return, 0, Gdk.ModifierType(0))
    assert sent == ["/usage"]

    typed("/c")
    assert composer.close_commands() is True and composer.close_commands() is False


# ----------------------------------------------------------- the whole app --

def test_window(app):
    window = app.props.active_window
    session = window.manager.sessions[0]

    session.backend = child = Child()
    window.manager.live.append(session)
    session._on_event(Event("handshake", {"commands": COMMANDS}))
    window._rebuild_sidebar()
    window.select(session)
    view = window.views[session.meta.uid]

    # A question fills the card slot, and says so everywhere it should.
    session.pending_permission = dict(QUESTION, questions=backend.questions_in(QUESTION))
    session._set_state("busy", "Waiting on your answer")
    session._notify("permission", request=session.pending_permission)
    assert isinstance(view._prompt_card, chat.QuestionCard)
    assert window.title_widget.get_subtitle() == "Waiting for your answer"
    assert "Waiting on your answer" in view.composer.hint.get_label()

    # Typing while it is up answers it rather than queueing behind it.
    view.composer.view.get_buffer().set_text("Ship it, but rename the flag first.")
    view.composer._fire()
    assert session.outbox == [] and view._prompt_card is None
    assert session.entries[-1]["text"] == "Ship it, but rename the flag first."

    # A report lands on a card of its own and stays out of the transcript.
    session._notify("report", name="context", text="", pending=True)
    assert view._report_card is not None and view._report_card.name == "context"
    session._notify("report", name="context", text="**Tokens:** 27.6k", pending=False)
    assert [e.get("role") for e in session.entries] == ["user"]

    # The commands the window owns never reach the child.
    before = list(child.sent)
    view.composer.view.get_buffer().set_text("/rename Date migration")
    view.composer._fire()
    assert session.meta.name == "Date migration" and child.sent == before
    view.composer.view.get_buffer().set_text("/model haiku")
    view.composer._fire()
    assert session.meta.model == "haiku" and child.sent == before
    assert session.backend is None, "a model change relaunches on the next message"

    # Escape closes the menu before it stops anything.
    view.composer.view.get_buffer().set_text("/co")
    assert view.composer.strip.open
    window.interrupt_session()
    assert not view.composer.strip.open


def main():
    print("protocol")
    check("slash commands are read the way the terminal reads them", test_split_command)
    check("questions are parsed, and malformed ones are not", test_questions_parsed)
    check("an answer goes back inside updatedInput", test_answer_frame)
    check("the menu matches on name, alias and substring", test_command_matching)

    print("sessions")
    check("a pending question holds the session and its slot", test_pending_question)
    check("the described command list wins over the names-only one", test_commands_remembered)
    check("report commands are never messages", test_reports_are_not_messages)
    check("a standalone report never waits for the turn", test_standalone_never_waits)

    print("widgets")
    Adw.init()
    widgets.install_css()
    check("the question card collects an answer", test_question_card)
    check("the command menu completes as you type", test_command_menu)

    print("window")
    from claudedesk.app import ClaudeDeskApp
    app = ClaudeDeskApp()
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
    failure = []

    def run():
        try:
            test_window(app)
            print("  ok  cards, commands and Escape, in the real window")
        except BaseException as exc:  # noqa: BLE001 - reported after the loop
            failure.append(exc)
        finally:
            app.props.active_window.manager.shutdown()
            app.quit()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(900, run)
    app.run([])
    if failure:
        raise failure[0]

    if os.environ.get("CLAUDE_DESK_LIVE"):
        print("live (this half spends tokens)")
        from tests.test_live import main as live
        live()
    print("all good")


if __name__ == "__main__":
    main()
