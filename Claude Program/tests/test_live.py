"""The half that drives a real `claude` child. This one spends tokens.

    CLAUDE_DESK_LIVE=1 python3 tests/test_claudedesk.py

Everything here is about the two things only a real child can prove: that an
answered question comes back as an answer, and that a report command is
answered without the conversation ever hearing about it.
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("XDG_CONFIG_HOME",
                      os.path.join(tempfile.mkdtemp(prefix="claude-desk-live-"), "config"))
os.environ.setdefault("XDG_DATA_HOME",
                      os.path.join(tempfile.mkdtemp(prefix="claude-desk-live-"), "data"))

from gi.repository import GLib  # noqa: E402

from claudedesk import config  # noqa: E402
from claudedesk.manager import Manager  # noqa: E402

ASK = ("Use the AskUserQuestion tool right now to ask me whether I prefer tea or "
       "coffee. Do nothing else first.")


def drive(session, steps, done_when, timeout=240):
    """Run `steps` one per free turn, until `done_when(log)` is true."""
    loop, log, stage = GLib.MainLoop(), [], {"n": 0}

    def on_event(what, data):
        if what == "report" and not data.get("pending"):
            log.append(("report", data))
        elif what == "permission" and data["request"].get("questions"):
            log.append(("question", data["request"]))
        elif what == "state" and session.state == "ready" and stage["n"] < len(steps):
            GLib.idle_add(session.send, steps[stage["n"]])
            stage["n"] += 1
        if done_when(log):
            GLib.idle_add(loop.quit)

    session.subscribe(on_event)
    GLib.idle_add(session.send, steps[0])
    stage["n"] = 1
    GLib.timeout_add_seconds(timeout, lambda: (log.append(("timeout", {})), loop.quit()))
    loop.run()
    return log


def test_question_answered():
    manager = Manager(config.Config())
    session = manager.new_session(cwd="/tmp", permission_mode="bypassPermissions")
    answered = []

    def on_event(what, data):
        if what == "permission" and data["request"].get("questions"):
            questions = data["request"]["questions"]
            answered.append(questions)
            if len(answered) == 1:      # what the card sends when you pick
                GLib.idle_add(session.answer_question,
                              {q["question"]: q["options"][0]["label"] for q in questions}, {}, "")
            else:                       # what the composer sends when you type
                GLib.idle_add(session.answer_question, {}, {}, "Neither — mate, please.")

    session.subscribe(on_event)
    drive(session, [ASK, ASK], lambda log: len(answered) == 2 and session.state == "ready")
    results = [e for e in session.entries
               if e.get("role") == "tool" and e.get("name") == "AskUserQuestion"]
    manager.shutdown()

    assert len(results) == 2, [e.get("result") for e in results]
    assert "did not answer" not in results[0]["result"], results[0]["result"]
    assert ("have been answered" in results[0]["result"]
            or "The user answered" in results[0]["result"]), results[0]["result"]
    assert "The user responded" in results[1]["result"], results[1]["result"]
    assert any(e.get("text") == "Neither — mate, please." for e in session.entries)
    print("  ok  a question comes back answered, picked or typed")


def test_reports_live():
    manager = Manager(config.Config())
    session = manager.new_session(cwd="/tmp", permission_mode="bypassPermissions")
    log = drive(session, ["/context", "Reply with exactly: pong"],
                lambda log: any(name == "report" for name, _ in log) and session.state == "ready")
    reports = [data for name, data in log if name == "report"]
    entries = [(e.get("role"), str(e.get("text", ""))[:40]) for e in session.entries]

    # /usage is answered by a child of its own, over the top of a busy session.
    session._set_state("busy")
    solo = GLib.MainLoop()
    session.subscribe(lambda what, data: solo.quit()
                      if what == "report" and data.get("name") == "usage"
                      and not data.get("pending") else None)
    session.run_report("usage")
    GLib.timeout_add_seconds(90, solo.quit)
    solo.run()
    usage = [data for name, data in log if name == "report" and data["name"] == "usage"]
    manager.shutdown()

    assert reports and reports[0]["name"] == "context", reports
    assert "%" in reports[0]["text"] or "Tokens" in reports[0]["text"], reports[0]["text"][:200]
    assert [role for role, _ in entries].count("user") == 1, entries
    assert entries[0] == ("user", "Reply with exactly: pong"), entries
    assert session.command_list() and any(c.get("description") for c in session.command_list())
    print("  ok  a report answers onto a card and never into the conversation")
    assert usage and "%" in usage[0]["text"], "the standalone report answers over a busy turn"
    assert not usage[0]["failed"]


def main():
    test_question_answered()
    test_reports_live()


if __name__ == "__main__":
    main()
