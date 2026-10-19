"""What can be checked without spending a token.

Two halves, as the layout intends: the protocol and the session state need no
display, and the widgets need one but no API. Run it with

    python3 tests/test_claudedesk.py

and add CLAUDE_DESK_LIVE=1 to also drive a real `claude` child — that half
does spend tokens, so it is off by default.
"""

import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
scratch = tempfile.mkdtemp(prefix="claude-desk-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(scratch, "data")
# An app built by a test must not go to the network for an update.
os.environ["APPS_NO_UPDATE"] = "1"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gio, Gtk  # noqa: E402

from claudedesk import backend, chat, config, widgets, winenv  # noqa: E402
from claudedesk import window as window_mod  # noqa: E402
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
# A permission ask the CLI escalated past the permission mode, with every
# field it uses to say so: the reason, the pair of booleans about the buttons,
# and a warning dressed for a terminal.
PERMISSION = {
    "request_id": "p1", "tool_name": "Bash", "display_name": "Bash",
    "input": {"command": "rm -rf build"}, "suggestions": [{"type": "setMode",
                                                           "mode": "acceptEdits"}],
    "tool_use_id": "toolu_2", "questions": [],
    "reason_type": "safetyCheck", "reason": "\x1b[31mrm -rf\x1b[0m is destructive",
    "classifier_approvable": False, "default_to_no": True, "no_always": True,
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


def children_of(widget):
    kids, child = [], widget.get_first_child()
    while child is not None:
        kids.append(child)
        child = child.get_next_sibling()
    return kids


def rows_of(listbox):
    """The rows, and not the placeholder sitting in the chain beside them.

    A list box's placeholder is one of its children and turns up in the same
    sibling walk, so a test asking for "the last row" would otherwise be
    handed the empty-state label.
    """
    return [child for child in children_of(listbox)
            if isinstance(child, Gtk.ListBoxRow)]


def descendants(widget, kind):
    found = [widget] if isinstance(widget, kind) else []
    child = widget.get_first_child()
    while child is not None:
        found += descendants(child, kind)
        child = child.get_next_sibling()
    return found


def text_in(widget):
    """Every string a widget tree would show, as one blob."""
    found = []
    if isinstance(widget, Gtk.Label):
        found.append(widget.get_label())
    if isinstance(widget, Gtk.Button) and widget.get_label():
        found.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        found.append(text_in(child))
        child = child.get_next_sibling()
    return "\n".join(part for part in found if part)


def with_config_file(payload, check_it):
    """Run `check_it` against a Config read from `payload`, then put it back."""
    saved = None
    if os.path.exists(config.CONFIG_FILE):
        with open(config.CONFIG_FILE, encoding="utf-8") as handle:
            saved = handle.read()
    os.makedirs(config.CONFIG_DIR, exist_ok=True)
    with open(config.CONFIG_FILE, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    try:
        check_it(config.Config())
    finally:
        if saved is None:
            os.remove(config.CONFIG_FILE)
        else:
            with open(config.CONFIG_FILE, "w", encoding="utf-8") as handle:
                handle.write(saved)


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


def test_unicode_survives_the_pipe():
    """The child is Node, and Node writes real UTF-8 rather than \\u escapes.

    Decoded in the locale's encoding — cp1252 on a Western Windows install —
    an em dash arrives as three wrong characters, and the first byte cp1252
    has no character for raises inside the reader thread, which dies without
    a word and takes the conversation with it. This drives a stand-in child
    that answers exactly the way the real one does.

    The stand-in's source is kept pure ASCII, so what is under test is the
    pipe and nothing else.
    """
    text = "Hi — 你好 😀 Привет"
    source = (
        "import sys, json\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "text = " + ascii(text) + "\n"
        "frame = {'type': 'assistant',\n"
        "         'message': {'content': [{'type': 'text', 'text': text}]}}\n"
        "sys.stdout.write(json.dumps(frame, ensure_ascii=False) + '\\n')\n"
        "sys.stdout.flush()\n"
    )

    seen = []

    class StandIn(backend.Backend):
        def _argv(self):
            return [sys.executable, "-c", source]

    child = StandIn(cwd=os.getcwd(), model="default", permission_mode="manual",
                    on_event=seen.append)
    child.start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not any(e.kind == "exit" for e in seen):
        time.sleep(0.02)
    child.stop()

    blocks = [e for e in seen if e.kind == "block" and e.data.get("block") == "text"]
    assert blocks, [(e.kind, e.data) for e in seen]
    assert blocks[0].data["text"] == text, repr(blocks[0].data["text"])


def test_permission_modes_are_wire_names():
    """Every mode in the dropdown is a name the control channel accepts.

    A name the CLI does not know is not refused: it is coerced to `default`
    and answered with success. So an invented mode is not an error anyone
    sees, it is a setting that silently does nothing — which is what this app
    shipped as "manual" for a year.
    """
    # The set_permission_mode enum, claude 2.1.274.
    wire = {"default", "acceptEdits", "bypassPermissions", "plan", "dontAsk", "auto"}
    keys = [key for key, _ in backend.PERMISSION_MODES]
    assert set(keys) <= wire, set(keys) - wire
    assert len(set(keys)) == len(keys), "a mode listed twice"

    # And a config still saying "manual" is read as the mode it really was.
    with_config_file(
        {"sessions": [{"uid": "u1", "name": "Old", "permission_mode": "manual"}]},
        lambda cfg: _assert(cfg.sessions[0].permission_mode == "default"),
    )


def _assert(value, message=""):
    assert value, message


def test_groups_sort_the_sidebar():
    # On a config of its own, and put back afterwards: five chats called A to
    # E are a clear test and a confusing thing to leave in the file the
    # window tests then open.
    with_config_file({}, _groups_sort_the_sidebar)


def _groups_sort_the_sidebar(cfg):
    manager = Manager(cfg)
    chats = [manager.new_session(cwd="/tmp") for _ in range(5)]
    for session, name in zip(chats, "ABCDE"):
        session.rename(name)
    a, b, c, d, e = chats

    # Favourites lead, the ungrouped middle follows, and the archive is last
    # however late it was filed. Within a section the chats keep the order
    # they were started in — a stable sort, so favouriting D leaves it under
    # B rather than reshuffling the two.
    b.set_group(config.FAVOURITE_GROUP)
    e.set_group(config.ARCHIVE_GROUP)
    d.set_group(config.FAVOURITE_GROUP)
    assert [s.meta.name for s in manager.ordered_sessions()] == ["B", "D", "A", "C", "E"]
    assert cfg.group_order() == [config.FAVOURITE_GROUP, "", config.ARCHIVE_GROUP]

    # There are three sections and no making a fourth: what used to be a
    # group of your own is a tag now, so asking to be filed under one asks
    # for nothing and lands in the ungrouped middle.
    a.set_group("Reading")
    assert a.meta.group == ""

    # Every way of naming one of the three reaches the one section, so no
    # second archive can end up sitting beside the real one.
    e.set_group("Archived")
    assert e.meta.group == config.ARCHIVE_GROUP
    b.set_group("  FAVOURITE  ")
    assert b.meta.group == config.FAVOURITE_GROUP


def test_tags_go_many_to_a_chat():
    with_config_file({}, _tags_go_many_to_a_chat)


def _tags_go_many_to_a_chat(cfg):
    manager = Manager(cfg)
    a, b = (manager.new_session(cwd="/tmp") for _ in range(2))

    # The thing one group could never do: two labels on one chat, and one
    # label on two chats.
    a.set_tags(["Lyndon", "windows"])
    b.add_tag("windows")
    assert a.meta.tags == ["Lyndon", "windows"] and b.meta.tags == ["windows"]
    assert cfg.all_tags() == ["Lyndon", "windows"], "sorted, and case is not an order"

    # A tag is not a place: filing the chat somewhere leaves them all on it.
    a.set_group(config.ARCHIVE_GROUP)
    assert a.meta.tags == ["Lyndon", "windows"]

    # Ticking one that is already on takes it off, and taking the last copy
    # of a tag off anything is what ends the tag.
    a.toggle_tag("Lyndon")
    assert a.meta.tags == ["windows"] and cfg.all_tags() == ["windows"]
    a.toggle_tag("Lyndon")
    assert a.meta.tags == ["windows", "Lyndon"], "and back on at the end"
    a.add_tag("windows")
    assert a.meta.tags == ["windows", "Lyndon"], "already wearing it"

    # One line, trimmed, capped, each one once — and never a group's name.
    a.set_tags(["  two   words  ", "two words", "x" * 90,
                config.ARCHIVE_GROUP, "Favourites"])
    assert a.meta.tags == ["two words", "x" * config.MAX_TAG_NAME]

    # A group of your own making, in a file written before tags existed, is a
    # tag now: the name meant the same thing, and the list of them is read
    # from where it used to live.
    with_config_file(
        {"groups": ["Reading", "Gone"],
         "sessions": [{"uid": "u1", "name": "A", "group": "Reading"},
                      {"uid": "u2", "name": "B", "group": "favourite",
                       "tags": ["Reading"]}]},
        _old_groups_are_tags_now,
    )


def _old_groups_are_tags_now(cfg):
    one, two = cfg.sessions
    assert one.group == "" and one.tags == ["Reading"], "it kept the name, not the place"
    assert two.group == config.FAVOURITE_GROUP and two.tags == ["Reading"], \
        "a built-in group is still a group"
    _assert(cfg.tags == ["Reading"], cfg.tags)  # "Gone" is worn by nothing


def test_the_childs_permission_mode_wins():
    """Asking for a mode and being in it are different things.

    Bypass is refused where it was never consented to, and a name the CLI
    does not know becomes `default`. A dropdown showing what was asked for
    would be a promise nothing is keeping — and every prompt after it would
    look like the app ignoring the setting.
    """
    # Its own config: the notice it writes is a transcript entry, and the
    # window tests open the first session in the file expecting an empty one.
    with_config_file({}, _childs_permission_mode_wins)


def _childs_permission_mode_wins(cfg):
    manager, session = fresh_session(cfg=cfg)
    notices = []
    manager.notice_listeners.append(notices.append)
    session.meta.permission_mode = "bypassPermissions"

    session._on_event(Event("mode", {"mode": "default", "error": ""}))
    assert session.meta.permission_mode == "default"
    assert any("Ask every time" in note for note in notices), notices
    assert session.entries[-1]["role"] == "notice"

    # An echo that agrees changes nothing and says nothing.
    before = len(session.entries)
    session._on_event(Event("mode", {"mode": "default", "error": ""}))
    assert len(session.entries) == before

    # The mode a spawn settled on arrives on the init frame instead.
    session._on_event(Event("ready", {"session_id": "s", "permission_mode": "plan"}))
    assert session.meta.permission_mode == "plan"


def test_the_default_model_is_claude_codes_own_setting():
    """Default means whatever ~/.claude/settings.json says.

    So showing it is reading that file, and changing it is writing that file —
    one key of it. It belongs to the terminal as much as to this app, and to
    every other session on the machine, which is why a file that cannot be
    read is a refusal rather than a fresh start.
    """
    saved = config.CLAUDE_DIR, config.CLAUDE_SETTINGS
    config.CLAUDE_DIR = os.path.join(scratch, "claude")
    config.CLAUDE_SETTINGS = os.path.join(config.CLAUDE_DIR, "settings.json")
    try:
        # Nothing written: Default is Claude Code's own choice and this app
        # has no business naming one.
        assert config.read_default_model() == ""

        os.makedirs(config.CLAUDE_DIR, exist_ok=True)
        settings = {"model": "opus", "theme": "dark-daltonized",
                    "permissions": {"defaultMode": "bypassPermissions"}}
        with open(config.CLAUDE_SETTINGS, "w", encoding="utf-8") as handle:
            json.dump(settings, handle)
        assert config.read_default_model() == "opus"

        assert config.write_default_model("haiku") is True
        with open(config.CLAUDE_SETTINGS, encoding="utf-8") as handle:
            kept = json.load(handle)
        assert kept["model"] == "haiku", kept
        assert kept["theme"] == "dark-daltonized", "the rest of the file survives"
        assert kept["permissions"] == {"defaultMode": "bypassPermissions"}

        # "Whatever Claude Code picks" is the key being absent, not "default".
        assert config.write_default_model("") is True
        with open(config.CLAUDE_SETTINGS, encoding="utf-8") as handle:
            assert "model" not in json.load(handle)
        assert config.read_default_model() == ""

        with open(config.CLAUDE_SETTINGS, "w", encoding="utf-8") as handle:
            handle.write("{ not json at all")
        assert config.write_default_model("opus") is False
        with open(config.CLAUDE_SETTINGS, encoding="utf-8") as handle:
            assert handle.read() == "{ not json at all", "left exactly as it was"
    finally:
        config.CLAUDE_DIR, config.CLAUDE_SETTINGS = saved

    # A pinned id and an alias are the same answer to "which model is Default".
    assert backend.model_label("claude-opus-5-20260101") == "Opus"
    assert backend.model_label("sonnet") == "Sonnet"
    assert backend.model_label("") == ""
    assert backend.model_label("claude-nova-9") == "claude-nova-9", "say what you were told"


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

    def answer_permission(self, request_id, allow, updated_input=None, message=None):
        self.answered.append((request_id, allow, updated_input))

    def set_permission_mode(self, mode):
        self.answered.append(("mode", mode, None))

    def stop(self):
        pass


def fresh_session(state="ready", cfg=None):
    manager = Manager(cfg if cfg is not None else config.Config())
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


def test_usage_is_announced_in_bands():
    """The headers repeat every turn; the toasts must not."""
    manager, session = fresh_session()
    heard = []
    manager.watch(on_notice=heard.append)

    def usage(five=None, week=None, five_reset=1000, week_reset=9000, **info):
        windows = {}
        if five is not None:
            windows["five_hour"] = {"utilization": five, "resetsAt": five_reset}
        if week is not None:
            windows["seven_day"] = {"utilization": week, "resetsAt": week_reset}
        session._on_event(Event("rate_limit", {"info": dict(
            {"status": "allowed", "unifiedWindows": windows}, **info)}))

    # The weekly window says nothing at all until it is worth knowing about.
    for fraction in (0.05, 0.4, 0.62, 0.69):
        usage(week=fraction)
    assert heard == []

    usage(week=0.71)
    assert heard == ["Weekly usage at 71%"]
    for fraction in (0.712, 0.72, 0.749):  # the same band, said once
        usage(week=fraction)
    usage(week=0.75)
    usage(week=0.93)  # a jump skips the bands it passed
    assert heard == ["Weekly usage at 71%", "Weekly usage at 75%", "Weekly usage at 93%"]

    # The five-hour window counts from the start, ten at a time.
    heard.clear()
    for fraction in (0.04, 0.12, 0.19, 0.2, 0.27):
        usage(five=fraction)
    assert heard == ["5-hour usage at 12%", "5-hour usage at 20%"]

    # Rolling over is a fresh window, and its bands are news again.
    heard.clear()
    usage(five=0.03, five_reset=2000)
    usage(five=0.14, five_reset=2000)
    assert heard == ["5-hour usage at 14%"]

    # A limit reached is said once, and not twice over with its own 100%.
    heard.clear()
    for _ in range(3):
        usage(week=1.0, status="rejected", rateLimitType="seven_day", resetsAt=9000)
    assert len(heard) == 1 and heard[0].startswith("Weekly limit reached")

    # A child too old for per-window headers still reports the one it has.
    manager.rate_limits = manager.rate_limits.__class__()
    heard.clear()
    for fraction in (0.83, 0.84, 0.88):
        session._on_event(Event("rate_limit", {"info": {
            "status": "allowed_warning", "rateLimitType": "seven_day_opus",
            "utilization": fraction, "resetsAt": 9000}}))
    assert heard == ["Weekly usage at 83%", "Weekly usage at 88%"]


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


def test_permission_card_says_why_it_is_asking():
    answered = []
    card = chat.PermissionCard(PERMISSION, lambda allow, always: answered.append(allow),
                               mode="bypassPermissions")
    said = text_in(card)

    # The mode is the question being asked of the app, so it goes first.
    assert "Bypass all checks" in said and "safety check" in said
    assert "rm -rf is destructive" in said, said
    assert "\x1b" not in said, "terminal escapes never reach a Pango label"
    # suppress_always_allow_rule: accepting once must not stand for always.
    assert "Allow —" not in said, said
    # default_to_no: Allow must not be one stray key press away.
    assert card.deny is not None and card.allow is not None

    # In a mode that does ask, the same line explains rather than excuses.
    plain = text_in(chat.PermissionCard(PERMISSION, lambda *a: None, mode="default"))
    assert "Asked because" in plain and "Bypass" not in plain

    # Nothing to say is said with nothing: no empty line above the command.
    bare = dict(PERMISSION, reason_type="", reason="", classifier_approvable=None,
                ask_rule={}, mcp_server="")
    assert "Asked because" not in text_in(chat.PermissionCard(bare, lambda *a: None))


def test_folders_are_ranked_by_use_and_recency():
    day = 86400
    now = time.time()
    cfg = config.Config()
    cfg.folders = []

    with tempfile.TemporaryDirectory() as root:
        daily = os.path.join(root, "daily")
        ancient = os.path.join(root, "ancient")
        detour = os.path.join(root, "detour")
        for path in (daily, ancient, detour):
            os.mkdir(path)

        cfg.folders = [
            # Used a lot, but a year ago: must not sit on top forever.
            config.FolderUse(path=ancient, uses=400, last_used=now - 365 * day),
            # The everyday folder: fewer uses, but current.
            config.FolderUse(path=daily, uses=30, last_used=now - day),
            # Opened once this morning: present, but not ahead of the above.
            config.FolderUse(path=detour, uses=1, last_used=now),
        ]
        assert cfg.recent_folders() == [daily, detour, ancient]

        # A folder that has gone is not offered -- picking it could only fail.
        gone = os.path.join(root, "unmounted")
        cfg.folders.append(config.FolderUse(path=gone, uses=99, last_used=now))
        assert gone not in cfg.recent_folders()

        # Choosing one counts, and counting it does not duplicate the row.
        before = len(cfg.folders)
        cfg.remember_folder(detour)
        assert len(cfg.folders) == before
        assert [f.uses for f in cfg.folders if f.path == detour] == [2]

        # The list is capped, and it is the coldest that go.
        for i in range(config.MAX_REMEMBERED_FOLDERS + 10):
            cfg.remember_folder(f"/nowhere/{i}")
        assert len(cfg.folders) <= config.MAX_REMEMBERED_FOLDERS
        assert any(f.path == daily for f in cfg.folders), "the live favourite survives"

    # Upgrading from a config that only knew one folder: the open sessions say
    # where the work is, so the first picker is already useful.
    cfg2 = config.Config()
    cfg2.sessions = [config.SessionMeta(cwd="/a", last_used=now),
                     config.SessionMeta(cwd="/a", last_used=now),
                     config.SessionMeta(cwd="/b", last_used=now - day)]
    cfg2.last_cwd = "/c"
    cfg2._seed_folders()
    seeded = {f.path: f.uses for f in cfg2.folders}
    assert seeded == {"/a": 2, "/b": 1, "/c": 1}


def test_folder_picker_offers_recents_and_browsing():
    picked, browsed = [], []
    window = Gtk.Window()
    paths = ["/tmp", "/usr/share"]
    dialog = widgets.choose_folder(window, paths, picked.append,
                                   lambda: browsed.append(True))

    listbox = dialog.listbox
    rows = []
    child = listbox.get_first_child()
    while child is not None:
        rows.append(child)
        child = child.get_next_sibling()

    assert [row.path for row in rows] == ["/tmp", "/usr/share", None], \
        "the remembered folders, then the way out to the filesystem"
    # A folder is named by its own basename, with the parent underneath, so two
    # folders called `src` are still tellable apart.
    assert _labels(rows[1]) == ["share", "/usr"]
    assert _labels(rows[-1])[0] == "Other location…"
    # The home folder is written the short way, and `/` does not get a second
    # line repeating itself.
    assert widgets.folder_labels(os.path.expanduser("~/src")) == ("src", "~")
    assert widgets.folder_labels("/") == ("/", "")

    # Driving the list the way a click does.
    listbox.emit("row-activated", rows[1])
    assert picked == ["/usr/share"] and browsed == []

    dialog = widgets.choose_folder(window, paths, picked.append,
                                   lambda: browsed.append(True))
    listbox = dialog.listbox
    listbox.emit("row-activated", listbox.get_last_child())
    assert browsed == [True], "Other location reaches the file chooser"
    assert picked == ["/usr/share"], "and does not also pick a folder"


def _labels(row):
    """Every label under a row, in the order they are drawn."""
    found = []

    def walk(widget):
        if isinstance(widget, Gtk.Label):
            found.append(widget.get_label())
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(row)
    return found


def test_the_row_menu_files_once_and_tags_as_often_as_you_like():
    filed, tagged, made = [], [], []
    popover = widgets.row_menu(["Lyndon", "windows"], config.FAVOURITE_GROUP,
                               ["windows"], filed.append, tagged.append,
                               lambda: made.append(True))

    # Above: the three sections, with a tick on the one the chat is in.
    groups = rows_of(popover.listbox)
    assert [row.group for row in groups] == [config.FAVOURITE_GROUP, "",
                                             config.ARCHIVE_GROUP]
    assert groups[0].tick.get_opacity() == 1.0 and groups[1].tick.get_opacity() == 0.0

    # Below: every tag in use, ticked where the chat wears it, and the line
    # that makes a new one.
    tags = rows_of(popover.taglist)
    assert [row.tag for row in tags] == ["Lyndon", "windows", None]
    assert tags[1].worn and not tags[0].worn

    # A tag toggles in place, because a chat that wants one usually wants
    # two; the menu is still there for the second.
    popover.taglist.emit("row-activated", tags[0])
    assert tagged == ["Lyndon"] and tags[0].worn
    popover.taglist.emit("row-activated", tags[0])
    assert tagged == ["Lyndon", "Lyndon"] and not tags[0].worn
    popover.taglist.emit("row-activated", tags[2])
    assert made == [True] and tagged == ["Lyndon", "Lyndon"]

    # Filing is one of three and settles the matter, so it only ever happens
    # once per opening of the menu.
    popover.listbox.emit("row-activated", groups[2])
    assert filed == [config.ARCHIVE_GROUP]


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

def native_icons(window):
    """The big and the small icon Windows holds for `window`; 0 for none."""
    import ctypes

    user32 = ctypes.WinDLL("user32")
    user32.SendMessageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                    ctypes.c_void_p)
    hwnd = winenv.window_handle(window)
    return [user32.SendMessageW(hwnd, 0x007F, which, None) or 0   # WM_GETICON
            for which in (1, 0)]                                  # ICON_BIG, ICON_SMALL


def test_answering_a_prompt_does_not_jump_to_the_top(app):
    """Clicking Allow must leave the focus in the composer.

    Every message label in the thread is selectable, which also makes it a
    focus stop. Destroy the focused Allow button without saying where the
    focus should go and GTK finds somewhere itself — the first stop inside
    the scrolled window, which is the opening paragraph of the conversation,
    and a selectable label selects all of its text when the focus arrives.
    The symptom is answering a question and being thrown to the top of the
    chat with your own first sentence highlighted.
    """
    window = app.props.active_window
    session = window.manager.ordered_sessions()[0]
    window.select(session)
    view = window.views[session.meta.uid]

    session.entries = [
        {"role": "user", "text": "The first paragraph of my first message."},
        {"role": "assistant", "text": "Noted."},
    ]
    view.reload()

    session.backend = Child()
    session.pending_permission = dict(PERMISSION)
    session._notify("permission", request=session.pending_permission)
    card = view._prompt_card
    assert isinstance(card, chat.PermissionCard)

    card.allow.grab_focus()
    assert window.get_focus() is card.allow, "the card should have the keyboard"
    view._answer_permission(True, False)
    assert window.get_focus() is view.composer.view, window.get_focus()
    assert view._prompt_card is None

    # The same goes for a question, and for a report card being dismissed.
    session.backend = Child()
    session.pending_permission = dict(QUESTION, questions=backend.questions_in(QUESTION))
    session._notify("permission", request=session.pending_permission)
    view._prompt_card.send.grab_focus()
    view._answer_question({}, {}, "")
    assert window.get_focus() is view.composer.view

    session._notify("report", name="context", text="27.6k", pending=False)
    view._report_card.get_last_child().grab_focus()
    view._clear_report()
    assert window.get_focus() is view.composer.view
    session.entries = []
    view.reload()


def test_one_click_files_a_chat(app):
    window = app.props.active_window
    manager = window.manager
    chat_one = manager.ordered_sessions()[0]
    chat_one.set_group("")
    window.select(chat_one)
    window._rebuild_sidebar()
    row = window.rows[chat_one.meta.uid]

    # The one button left on the row, and the same button again to undo it:
    # archiving puts a chat behind one line at the bottom, not out of reach.
    row.toggle_archive()
    window._rebuild_sidebar()
    assert chat_one.meta.uid not in window.rows, "the archive starts closed"
    assert isinstance(rows_of(window.listbox)[-1], window_mod.ArchiveRow)
    assert chat_one not in window.ordered, "and Ctrl+1…9 counts the rows shown"

    window._toggle_archive_section()
    window._rebuild_sidebar()
    assert chat_one.meta.uid in window.rows
    assert manager.ordered_sessions()[-1] is chat_one
    row = window.rows[chat_one.meta.uid]
    assert row.archive.get_icon_name() == "go-up-symbolic", "and says which way it goes"

    # The star is in the menu now rather than on a button of its own, with
    # the other two sections and with the tags.
    window._row_menu(row)
    offered = text_in(row.menu.get_popover())
    for label in ("Favourites", "Archived", "Chats", "Tags", "New tag…"):
        assert label in offered, offered
    row.menu.get_popover().listbox.emit(
        "row-activated", rows_of(row.menu.get_popover().listbox)[0])
    assert chat_one.meta.group == config.FAVOURITE_GROUP, "one click, from in there"

    window._toggle_archive_section()
    chat_one.set_group("")
    window._rebuild_sidebar()


def test_tags_label_a_chat_and_filter_the_list(app):
    window = app.props.active_window
    chats = window.manager.ordered_sessions()
    for session in chats:
        session.set_group("")
        session.set_tags([])
    first = chats[0]
    window.select(first)
    window._rebuild_sidebar()
    assert not window.tag_scroller.get_visible(), "nothing tagged, nothing new on screen"

    # Making a tag puts it on the row and a chip above the list, without the
    # list itself having moved.
    window._new_tag(first, "  Lyndon  ")
    assert first.meta.tags == ["Lyndon"]
    row = window.rows[first.meta.uid]
    assert row.tags.get_visible() and "#Lyndon" in row.tags.get_label()
    assert window.tag_scroller.get_visible()
    assert "#Lyndon" in text_in(window.tag_bar)

    # A group's name is not a tag, and the dialog says so rather than looking
    # as though it had not worked.
    window._new_tag(first, "Archived")
    assert first.meta.tags == ["Lyndon"] and first.meta.group == ""

    # The chip narrows the list to the chats wearing that tag, and a second
    # press widens it again.
    window._filter_by_tag("Lyndon")
    window._rebuild_sidebar()
    assert list(window.rows) == [first.meta.uid]
    assert window.ordered == [first], "and Ctrl+1…9 counts what is left"
    window._filter_by_tag("Lyndon")
    window._rebuild_sidebar()
    assert len(window.rows) == len(chats)

    # An archived chat keeps its tags and is found by them — behind the
    # archive's own line, which is where an archived chat lives.
    first.set_group(config.ARCHIVE_GROUP)
    window._filter_by_tag("Lyndon")
    window._rebuild_sidebar()
    assert first.meta.uid not in window.rows
    assert isinstance(rows_of(window.listbox)[-1], window_mod.ArchiveRow)
    assert "1" in text_in(rows_of(window.listbox)[-1])

    # Taking the last copy of a tag off ends it, so the filter it was on goes
    # too rather than leaving a sidebar that can only be empty.
    first.toggle_tag("Lyndon")
    window._rebuild_sidebar()
    assert window.tag_filter == "" and not window.tag_scroller.get_visible()
    assert len(window.rows) == len(chats) - 1, "the archived one is behind its line"

    first.set_group("")
    window._rebuild_sidebar()

    # The empty state is a child of the list box like the rows are, so the
    # rebuild has to walk past it rather than hand it to remove(): doing that
    # unparented it for good, and from then on the sidebar had no word to
    # show for a filter that matched nothing.
    def placeholder():
        return [child for child in children_of(window.listbox)
                if not isinstance(child, Gtk.ListBoxRow)]

    assert placeholder(), "the empty state survived the rebuild"
    assert "tagged" in text_in(placeholder()[0])
    window._rebuild_sidebar()
    assert placeholder(), "and survives every rebuild after it"


def test_the_header_names_the_default_model(app):
    window = app.props.active_window
    session = window.manager.ordered_sessions()[0]
    window.select(session)
    was = session.meta.model

    session.meta.model = "default"
    window.cfg.default_model = "claude-sonnet-5"
    window._sync_header()
    assert window.model_strings.get_string(0) == "Default (Sonnet)"
    assert window.model_drop.get_selected() == 0, "naming it must not change it"
    assert "Preferences" in window.model_drop.get_tooltip_text()

    # What a child actually picked up beats the settings file: an env var or a
    # managed policy can have the last word after it.
    session._on_event(Event("ready", {"session_id": "s", "model": "claude-opus-5"}))
    window._sync_header()
    assert window.model_strings.get_string(0) == "Default (Opus)"
    assert window.manager.config.default_model == "claude-opus-5"

    session.meta.model = was
    session.live_model = ""
    window._sync_header()


def test_the_sidebar_can_be_dragged(app):
    window = app.props.active_window
    paned = window.paned
    sidebar = paned.get_start_child()
    width = window.cfg.sidebar_width

    # The seam is the whole feature: a wider sidebar is a narrower chat.
    paned.set_position(width + 90)
    assert window.cfg.sidebar_width == width + 90, "the width dragged to is kept"

    # Neither pane can be dragged away — shrink_*_child=False makes each
    # pane's own minimum the end of the travel, so GTK stops the drag rather
    # than letting go of a width this would have to correct afterwards. What
    # is checked here is that a width under the floor is never *remembered*.
    paned.set_position(20)
    assert window.cfg.sidebar_width == width + 90
    paned.set_position(width + 90)

    # Toggling hides the pane rather than closing the seam, and comes back to
    # the width it was.
    window._toggle_sidebar()
    assert not sidebar.get_visible()
    window._toggle_sidebar()
    assert sidebar.get_visible() and paned.get_position() == width + 90

    # Adw.HeaderBar only divides the window buttons between two panes when it
    # finds an Adw split view above it, and there is a Paned there now. Two
    # full sets is the failure: a duplicated close button, and a pane as wide
    # as two sets of controls before anything is in it.
    filled = [c for c in descendants(window, Gtk.WindowControls) if not c.get_empty()]
    assert len(filled) == 1, [c.get_side().value_nick for c in filled]
    assert descendants(paned.get_end_child(), Gtk.WindowControls) == \
        [c for c in filled], "they belong to the conversation's header"

    # A label that cannot shorten sets the minimum width of the pane holding
    # it, so the footer would otherwise decide how narrow the sidebar can go.
    assert window.memory_label.get_ellipsize().value_nick == "end"
    window.cfg.sidebar_width = width


def test_the_window_fits_a_tiling_column(app):
    """A draggable sidebar needs the window to fit the column it is in.

    This is the test that was missing when the Paned went in: dragging was
    checked at 1120 px, where there was slack, and the window's own minimum
    was 835 px — so in a 683 px niri column the position was clamped to the
    sidebar's floor at both ends and dragging did nothing at all. The handle
    was there, the cursor changed over it, and it could not move.
    """
    window = app.props.active_window
    minimum = window.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    assert minimum <= 700, (
        f"{minimum}px minimum is wider than a tiling column, which leaves the "
        "sidebar pinned to its floor with nothing to drag"
    )
    # Which it only manages because the dropdowns step off the header. The
    # breakpoint decides when; this is that the move itself works, and puts
    # everything back where it was — pack_end's order depends on what is
    # already packed, so the naive version moved the ⋮ button across them.
    was = children_of(window.header_end)
    window._set_compact(True)
    assert window.control_bar.get_child() is window.controls
    assert window.control_bar.get_visible()
    assert window.controls not in children_of(window.header_end)
    window._set_compact(False)
    assert not window.control_bar.get_visible()
    assert children_of(window.header_end) == was, "the header came back in a different order"
    assert children_of(window.header_end)[0] is window.header_menu

    # Idempotent either way: a breakpoint can apply twice over a resize.
    window._set_compact(False)
    assert children_of(window.header_end) == was


def test_on_windows_the_window_wears_the_apps_icon_not_pythons(app):
    """GTK 4 sets no icon on a window there, so each one wore pythonw.exe's:
    the Python logo, in Alt+Tab and on the taskbar."""
    if not winenv.WINDOWS:
        return
    big, small = native_icons(app.props.active_window or app.get_windows()[0])
    assert big and small, "the window has no icon of its own"


def test_the_new_window_flag_never_reaches_the_paths():
    from claudedesk.app import split_new_window_flag

    for argv, expected in (
        (["-n", "/tmp"], (True, ["/tmp"])),
        (["--new-window", "/tmp"], (True, ["/tmp"])),
        (["--new-window"], (True, [])),
        (["/tmp"], (False, ["/tmp"])),
        ([], (False, [])),
    ):
        got = split_new_window_flag(list(argv))
        assert got == expected, f"{argv} gave {got}, wanted {expected}"


def test_two_windows_share_one_manager(app):
    """The invariant the whole thing rests on.

    A Manager per window would mean two sets of live Claude processes over the
    same sessions, each rationing itself against the budget without knowing
    about the other, and both writing the same file.
    """
    from claudedesk.window import MainWindow

    first = app.front_window() or app.new_window()
    second = app.new_window()
    try:
        assert first is not second
        assert second.manager is first.manager, "a window built a Manager of its own"
        assert second.manager is app.manager
        assert second.cfg is first.cfg
        # The sessions are the application's, so both windows see all of them.
        assert second.manager.sessions is first.manager.sessions
        # What a window keeps to itself is the view onto them.
        assert second.views is not first.views
        assert app.front_window() in [w for w in app.get_windows()
                                      if isinstance(w, MainWindow)]
    finally:
        second.destroy()


def test_both_windows_hear_about_a_change(app):
    """One slot would mean the second window silently replacing the first."""
    first = app.front_window() or app.new_window()
    second = app.new_window()
    try:
        heard = []
        first_watch = app.manager.watch(lambda s: heard.append("first"))
        second_watch = app.manager.watch(lambda s: heard.append("second"))
        app.manager.session_changed(app.manager.sessions[0])
        assert "first" in heard and "second" in heard, heard

        # And a window that has gone must stop hearing, or every later change
        # is delivered into widgets that are not there.
        app.manager.unwatch(*second_watch)
        heard.clear()
        app.manager.session_changed(app.manager.sessions[0])
        assert heard == ["first"], heard
        app.manager.unwatch(*first_watch)
    finally:
        second.destroy()


def test_closing_one_window_leaves_the_sessions_running(app):
    """Only the last window out stops the children."""
    first = app.front_window() or app.new_window()
    second = app.new_window()
    assert not app.manager.closing

    # Closing one of two is not the end of anything.
    second.close()
    assert not app.manager.closing, "closing one window stopped the other's sessions"
    assert first in app.get_windows()


def test_the_new_window_action_does_not_steal_the_new_session_key(app):
    from gi.repository import Gtk

    assert app.lookup_action("new-window") is not None, "no app.new-window action"

    def parsed(accels):
        return {Gtk.accelerator_parse(a)[1:] for a in accels}

    # Ctrl+N is a new session; a whole window is the shifted version.
    assert parsed(app.get_accels_for_action("app.new-window")) == \
        parsed(["<Control><Shift>n"]), app.get_accels_for_action("app.new-window")
    assert parsed(app.get_accels_for_action("app.new-window")).isdisjoint(
        parsed(["<Control>n"])), "a new window would steal the new-session key"


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
    check("unicode survives the pipe, both directions", test_unicode_survives_the_pipe)
    check("the menu matches on name, alias and substring", test_command_matching)

    print("sessions")
    check("every permission mode is a name the protocol takes",
          test_permission_modes_are_wire_names)
    check("the three groups section the sidebar, and there is no fourth",
          test_groups_sort_the_sidebar)
    check("tags go many to a chat, and end when nothing wears them",
          test_tags_go_many_to_a_chat)
    check("the child's permission mode wins over the one asked for",
          test_the_childs_permission_mode_wins)
    check("the default model is Claude Code's own setting",
          test_the_default_model_is_claude_codes_own_setting)
    check("a pending question holds the session and its slot", test_pending_question)
    check("the described command list wins over the names-only one", test_commands_remembered)
    check("report commands are never messages", test_reports_are_not_messages)
    check("a standalone report never waits for the turn", test_standalone_never_waits)
    check("usage is announced in bands, not on every header", test_usage_is_announced_in_bands)

    print("widgets")
    Adw.init()
    widgets.install_css()
    check("the question card collects an answer", test_question_card)
    check("the permission card says why it is asking",
          test_permission_card_says_why_it_is_asking)
    check("the command menu completes as you type", test_command_menu)
    check("the row menu files once and tags as often as you like",
          test_the_row_menu_files_once_and_tags_as_often_as_you_like)
    check("folders are ranked by use and recency", test_folders_are_ranked_by_use_and_recency)
    check("the folder picker offers recents, then the filesystem",
          test_folder_picker_offers_recents_and_browsing)

    print("window")
    from claudedesk.app import ClaudeDeskApp
    app = ClaudeDeskApp()
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
    failure = []

    def run():
        try:
            test_window(app)
            print("  ok  cards, commands and Escape, in the real window")
            test_answering_a_prompt_does_not_jump_to_the_top(app)
            print("  ok  answering a prompt does not jump to the top of the chat")
            test_one_click_files_a_chat(app)
            print("  ok  one click archives a chat, and the menu files it anywhere")
            test_tags_label_a_chat_and_filter_the_list(app)
            print("  ok  tags label a chat, and the chips filter the list")
            test_the_header_names_the_default_model(app)
            print("  ok  the header names the model Default means")
            test_the_sidebar_can_be_dragged(app)
            print("  ok  the sidebar can be dragged, and keeps its width")
            test_the_window_fits_a_tiling_column(app)
            print("  ok  the window fits a tiling column, so the drag has room")
            test_on_windows_the_window_wears_the_apps_icon_not_pythons(app)
            print("  ok  on Windows, the window wears the app's icon, not Python's")
            test_the_new_window_flag_never_reaches_the_paths()
            print("  ok  the new-window flag never reaches the paths")
            test_two_windows_share_one_manager(app)
            print("  ok  two windows share one manager")
            test_both_windows_hear_about_a_change(app)
            print("  ok  both windows hear about a change")
            test_closing_one_window_leaves_the_sessions_running(app)
            print("  ok  closing one window leaves the sessions running")
            test_the_new_window_action_does_not_steal_the_new_session_key(app)
            print("  ok  the new-window action does not steal the new-session key")
        except BaseException as exc:  # noqa: BLE001 - reported after the loop
            failure.append(exc)
        finally:
            if app.manager is not None:
                app.manager.shutdown()
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
