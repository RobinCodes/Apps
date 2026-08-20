"""Sessions, and the rationing of the processes behind them.

The whole design of this app follows from one measurement: a `claude` child
process holds about 450 MB resident. On a 4 GB machine you can afford two of
them, not ten — and yet "several instances at once" is the entire point of a
desktop front end.

What makes that work is that Claude Code sessions are resumable. So a session
here is a *conversation*, not a process. It owns a transcript and an identity
that outlive any child; the child is a resource we lend it while it is being
used, and take back when it goes quiet. Waking a sleeping session re-launches
with `--resume <session-id>`, which restores the full context — the user only
notices the first reply of the turn takes a moment longer.

Manager rations those slots: it hands one over when a session needs to speak,
evicts the least recently used idle session when it must, and queues a turn
rather than exceeding the budget when every slot is busy.

There are two queues here and they answer different questions. Manager's is
about *memory*: whose turn gets a process next. A session's `outbox` is about
*you*: messages typed while Claude was still working, sent one per turn as it
comes free, so thinking ahead never means waiting for the reply first.

This module is the glue between the GTK-free Backend and the UI, so it is the
one place that marshals worker-thread events onto the main loop.
"""

from __future__ import annotations

import os
import time

from gi.repository import GLib

from . import config
from .backend import (
    REPORT_COMMANDS,
    STANDALONE_COMMANDS,
    Backend,
    run_standalone,
    split_command,
)

# Redrawing a label on every token would peg a low-end CPU; batching to ~12
# frames a second still reads as live typing.
DELTA_FLUSH_MS = 80


class Session:
    """One conversation: its transcript, its state, and maybe a live process."""

    def __init__(self, meta, manager):
        self.meta = meta
        self.manager = manager
        self.entries = config.load_transcript(meta)
        self.backend = None
        self.state = "asleep"  # asleep | starting | ready | busy | waiting | gone
        self.pending_permission = None
        self.status_note = ""
        # Messages typed while the session was working, sent one per turn.
        # In memory only: they are a few seconds of intent, not history.
        self.outbox = []
        self.commands = []
        self.live_delta = ""
        self.live_delta_kind = "text"
        self.last_activity = meta.last_used
        self.listeners = []

        self._delta_buf = ""
        self._delta_timer = 0
        self._dirty = False
        # A report command in flight: what was typed, and the answer so far.
        self._quiet = None

    # ------------------------------------------------------------- listeners --

    def subscribe(self, callback):
        self.listeners.append(callback)
        return callback

    def unsubscribe(self, callback):
        if callback in self.listeners:
            self.listeners.remove(callback)

    def _notify(self, what, **data):
        for callback in list(self.listeners):
            try:
                callback(what, data)
            except Exception:  # noqa: BLE001 - a broken view must not stall a session
                pass

    def _set_state(self, state, note=""):
        if state == self.state and note == self.status_note:
            return
        self.state = state
        self.status_note = note
        self._notify("state")
        self.manager.session_changed(self)

    # -------------------------------------------------------------- commands --

    def remember_commands(self, items):
        """Keep what the child says it understands, descriptions and all.

        Two frames carry the list and they are not equally useful: the
        handshake reply has a description and an argument hint for each
        command, while the init frame has only names. The names arrive second,
        so taking whatever came last would throw the descriptions away.
        """
        detailed = [item for item in items or []
                    if isinstance(item, dict) and item.get("name")]
        named = [{"name": item} for item in items or [] if isinstance(item, str) and item]
        commands = detailed or named
        if not commands:
            return
        if any(item.get("description") for item in commands) or not self.commands:
            self.commands = commands
            self.manager.remember_commands(commands)

    def command_list(self):
        """Everything the composer can complete, live child or not."""
        return self.commands or self.manager.config.commands

    def command_names(self):
        names = set()
        for item in self.command_list():
            names.add(item.get("name", ""))
            names.update(item.get("aliases") or [])
        return names - {""}

    # ------------------------------------------------------------ transcript --

    def _append(self, entry):
        self.entries.append(entry)
        self._dirty = True
        self._notify("entry_added", index=len(self.entries) - 1, entry=entry)
        return len(self.entries) - 1

    def _update(self, index, **changes):
        if not 0 <= index < len(self.entries):
            return
        self.entries[index].update(changes)
        self._dirty = True
        self._notify("entry_updated", index=index, entry=self.entries[index])

    def flush_transcript(self):
        if self._dirty:
            config.save_transcript(self.meta, self.entries)
            self._dirty = False

    def note(self, text):
        """Record something the app did, inline in the conversation."""
        self._append({"role": "notice", "text": text})
        self.flush_transcript()

    def clear_transcript(self):
        """Forget the visible history and start a fresh Claude Code session."""
        self.sleep()
        self.entries = []
        self.meta.claude_session_id = ""
        self._dirty = True
        self.flush_transcript()
        self._notify("reloaded")

    # ----------------------------------------------------------------- input --

    def send(self, text):
        """Speak now if the session is free, otherwise get in line."""
        text = text.strip()
        if not text:
            return
        name, _ = split_command(text)
        if name in STANDALONE_COMMANDS:
            # Nothing to do with this conversation, so it neither waits for
            # the turn in front of it nor wakes a sleeping session to ask.
            self.run_report(name)
            return
        if self.busy:
            self.queue_message(text)
            return
        self._dispatch(text)

    @property
    def busy(self):
        return self.state in ("busy", "starting", "waiting")

    def queue_message(self, text):
        text = text.strip()
        if not text:
            return
        self.outbox.append(text)
        self._notify("outbox")
        self.manager.session_changed(self)

    def take_queued(self, index):
        """Pull one message back out of the queue; returns its text."""
        if not 0 <= index < len(self.outbox):
            return ""
        text = self.outbox.pop(index)
        self._notify("outbox")
        self.manager.session_changed(self)
        return text

    def clear_outbox(self):
        """Empty the queue and hand the messages back to the caller."""
        if not self.outbox:
            return []
        texts, self.outbox = self.outbox, []
        self._notify("outbox")
        self.manager.session_changed(self)
        return texts

    def _send_next_queued(self):
        """Called when a turn ends: let the next typed-ahead message go."""
        if self.manager.closing or self.busy or not self.outbox:
            return False
        text = self.outbox.pop(0)
        self._notify("outbox")
        self._dispatch(text)
        return True

    def _dispatch(self, text):
        self.meta.last_used = self.last_activity = time.time()
        name, _ = split_command(text)
        if name in REPORT_COMMANDS:
            # Asked of the child, kept out of the conversation: no user turn,
            # no reply in the transcript, and no title taken from it either.
            self._quiet = {"name": name, "text": text, "answer": []}
        else:
            self._append({"role": "user", "text": text, "t": self.last_activity})
            if self.meta.name in ("", "New session"):
                self.meta.name = _title_from(text)
                self.manager.session_changed(self)

        if self.backend is None:
            if not self.manager.request_slot(self):
                self._set_state("waiting", "Waiting for a free slot")
                self.manager.enqueue(self, text)
                return
            self._spawn()
        self._set_state("busy")
        self.backend.send(text)

    def run_report(self, name):
        """Ask for a standalone report; the answer arrives on a card.

        The card goes up empty first: three seconds is long enough that a
        window doing nothing at all would look like a window that missed the
        key press.
        """
        self._notify("report", name=name, text="", pending=True)

        def answered(text, error):
            GLib.idle_add(self._report_done, name, text, error)

        run_standalone(name, self.meta.cwd, answered)

    def _report_done(self, name, text, error):
        self._notify("report", name=name, text=text or error, pending=False,
                     failed=bool(error))
        return GLib.SOURCE_REMOVE

    def interrupt(self):
        if self.backend is not None:
            self.backend.interrupt()
            self._set_state("busy", "Interrupting…")

    def set_permission_mode(self, mode):
        self.meta.permission_mode = mode
        if self.backend is not None:
            self.backend.set_permission_mode(mode)
        self.manager.session_changed(self)

    def rename(self, name):
        """Call this conversation something other than its opening line."""
        name = " ".join(name.split())[:60]
        if not name or name == self.meta.name:
            return
        self.meta.name = name
        self.manager.session_changed(self)
        self.manager.save()

    def set_model(self, model):
        """Model is fixed at spawn, so changing it re-launches on next turn."""
        if model == self.meta.model:
            return
        self.meta.model = model
        if self.backend is not None:
            self.sleep(note="Model changed — will apply on next message")
        self.manager.session_changed(self)

    def answer_permission(self, allow, always=False):
        request = self.pending_permission
        if request is None:
            return
        self.pending_permission = None
        if self.backend is not None:
            self.backend.answer_permission(
                request["request_id"], allow, updated_input=request.get("input")
            )
            if allow and always:
                for suggestion in request.get("suggestions") or []:
                    if suggestion.get("type") == "setMode" and suggestion.get("mode"):
                        self.set_permission_mode(suggestion["mode"])
                        break
        self._notify("permission_cleared")
        self._set_state("busy")

    def answer_question(self, answers, annotations=None, response=""):
        """Hand back what you chose to a waiting AskUserQuestion call.

        `answers` is keyed by the text of each question; leaving one out says
        you skipped it, and an empty map says you answered none of them.
        `response` is words typed instead of choosing — the composer routes
        them here rather than queueing them, since a turn stopped on a
        question would never reach a queued message.
        """
        request = self.pending_permission
        if request is None:
            return
        self.pending_permission = None
        self._notify("permission_cleared")

        if self.backend is None:
            # The child went away while the question was on screen, so there
            # is nothing left to answer — but what you typed is still yours to
            # say, and goes out as an ordinary message that wakes it up again.
            if response:
                self.send(response)
            return

        if response:
            # Typed instead of picked is still something you said, so it goes
            # into the transcript like any other turn of yours.
            self.meta.last_used = self.last_activity = time.time()
            self._append({"role": "user", "text": response, "t": self.last_activity})
        self.backend.answer_question(
            request["request_id"],
            request.get("input") or {},
            answers,
            annotations,
            response,
        )
        self._set_state("busy")

    def _forget_pending(self):
        """Drop a request that is no longer being waited on, card and all."""
        if self.pending_permission is None:
            return
        self.pending_permission = None
        self._notify("permission_cleared")

    # ------------------------------------------------------------- lifecycle --

    def _spawn(self):
        self._set_state("starting", "Resuming…" if self.meta.claude_session_id else "Starting…")
        self.backend = Backend(
            cwd=self.meta.cwd,
            model=self.meta.model,
            permission_mode=self.meta.permission_mode,
            session_id=self.meta.claude_session_id or None,
            on_event=self._on_event_thread,
        )
        self.backend.start()

    def sleep(self, note=""):
        """Give the process back. The conversation itself is untouched."""
        self._flush_delta()
        if self.backend is not None:
            self.backend.stop()
            self.backend = None
            self.manager.release_slot(self)
        self._forget_pending()
        self.flush_transcript()
        self._set_state("asleep", note)

    # ---------------------------------------------------------------- events --

    def _on_event_thread(self, event):
        GLib.idle_add(self._on_event, event, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _on_event(self, event):
        kind, data = event.kind, event.data
        self.last_activity = time.time()

        if kind == "ready":
            if data.get("session_id"):
                self.meta.claude_session_id = data["session_id"]
            self.remember_commands(data.get("commands"))
            self.manager.session_changed(self)

        elif kind == "handshake":
            self.remember_commands(data.get("commands"))

        elif kind == "delta":
            if self._quiet is not None:
                return GLib.SOURCE_REMOVE  # a report is not written out live
            if data.get("block") == "thinking" and not self.manager.config.show_thinking:
                return GLib.SOURCE_REMOVE
            self.live_delta_kind = data.get("block", "text")
            self._delta_buf += data.get("text", "")
            if not self._delta_timer:
                self._delta_timer = GLib.timeout_add(DELTA_FLUSH_MS, self._flush_delta)

        elif kind == "block":
            self._clear_delta()
            block = data.get("block")
            if self._quiet is not None:
                if block == "text":
                    self._quiet["answer"].append(data.get("text", ""))
                    return GLib.SOURCE_REMOVE
                # Thinking, or a tool: this is a real turn after all.
                self._end_quiet(report=False)
            if block == "text":
                text = data.get("text", "")
                if text.strip():
                    self._append({"role": "assistant", "text": text})
            elif block == "thinking":
                if self.manager.config.show_thinking and data.get("text", "").strip():
                    self._append({"role": "thinking", "text": data["text"]})
            elif block == "tool_use":
                self._append({
                    "role": "tool",
                    "id": data.get("id", ""),
                    "name": data.get("name", "tool"),
                    "input": data.get("input") or {},
                    "result": "",
                    "is_error": False,
                    "done": False,
                })

        elif kind == "tool_result":
            self._resolve_tool(data)

        elif kind == "permission":
            self.pending_permission = data
            note = ("Waiting on your answer" if data.get("questions")
                    else f"Waiting on you — {data.get('display_name', 'tool')}")
            self._set_state("busy", note)
            self._notify("permission", request=data)

        elif kind == "denied":
            self._append({
                "role": "notice",
                "text": f"{data.get('tool_name', 'Tool')} was blocked: {data.get('message', '')}",
            })

        elif kind == "turn_end":
            self._clear_delta()
            # Interrupting a run abandons whatever it was asking about, and
            # the child never mentions it again: the card has to go, or it
            # would answer a request that no longer exists.
            self._forget_pending()
            if self._quiet is not None:
                self._end_quiet(report=True)
            else:
                self._append({
                    "role": "turn",
                    "cost": data.get("cost") or 0.0,
                    "duration_ms": data.get("duration_ms") or 0,
                    "is_error": data.get("is_error", False),
                })
            self.flush_transcript()
            self._set_state("ready")
            self.manager.turn_finished(self)
            self._send_next_queued()

        elif kind == "rate_limit":
            info = data.get("info") or {}
            if info.get("status") not in ("allowed", None):
                percent = int((info.get("utilization") or 0) * 100)
                self._notify("rate_limit", percent=percent, info=info)

        elif kind == "notice":
            self._append({"role": "notice", "text": data.get("message", "")})

        elif kind == "stderr":
            self._notify("stderr", message=data.get("message", ""))

        elif kind == "error":
            self._end_quiet(report=False)
            self._append({"role": "error", "text": data.get("message", "")})
            self._set_state("ready")
            self._send_next_queued()

        elif kind == "exit":
            self._clear_delta()
            self._forget_pending()
            self._end_quiet(report=False)
            if self.backend is not None:
                self.backend = None
                self.manager.release_slot(self)
                code = data.get("code")
                if code not in (0, None):
                    self._append({
                        "role": "error",
                        "text": f"Claude Code exited unexpectedly (code {code}). "
                                "Your next message will resume this conversation.",
                    })
            self.flush_transcript()
            self._set_state("asleep")
            self.manager.turn_finished(self)
            # Queued work outlives the process it was meant for: a session
            # evicted, crashed or relaunched for a model change wakes back up
            # rather than stranding what you typed.
            self._send_next_queued()

        return GLib.SOURCE_REMOVE

    def _end_quiet(self, report):
        """Finish a report command — as a report, or as the turn it became.

        The guess that a command only reports is made from its name, and a
        project can define a command of its own by the same name that really
        does talk to the model. So the moment one thinks, picks up a tool or
        fails, what was said goes back into the conversation where it belongs,
        starting with the message that was held back.
        """
        quiet, self._quiet = self._quiet, None
        if quiet is None:
            return
        answer = "\n\n".join(part for part in quiet["answer"] if part.strip()).strip()
        if report:
            if answer:
                self._notify("report", name=quiet["name"], text=answer)
            return
        self._append({"role": "user", "text": quiet["text"], "t": self.last_activity})
        if answer:
            self._append({"role": "assistant", "text": answer})

    def _resolve_tool(self, data):
        tool_id = data.get("id")
        content = data.get("content", "")
        if len(content) > config.MAX_TOOL_RESULT_CHARS:
            trimmed = len(content) - config.MAX_TOOL_RESULT_CHARS
            content = content[: config.MAX_TOOL_RESULT_CHARS] + f"\n… {trimmed} more characters"
        for index in range(len(self.entries) - 1, -1, -1):
            entry = self.entries[index]
            if entry.get("role") == "tool" and entry.get("id") == tool_id:
                self._update(index, result=content, is_error=data.get("is_error", False), done=True)
                return
        self._append({
            "role": "tool", "id": tool_id, "name": "tool", "input": {},
            "result": content, "is_error": data.get("is_error", False), "done": True,
        })

    # ---------------------------------------------------------------- deltas --

    def _flush_delta(self):
        self._delta_timer = 0
        if self._delta_buf:
            self.live_delta += self._delta_buf
            self._delta_buf = ""
            self._notify("delta", text=self.live_delta, kind=self.live_delta_kind)
        return GLib.SOURCE_REMOVE

    def _clear_delta(self):
        if self._delta_timer:
            GLib.source_remove(self._delta_timer)
            self._delta_timer = 0
        self._delta_buf = ""
        if self.live_delta:
            self.live_delta = ""
            self._notify("delta", text="", kind=self.live_delta_kind)


class Manager:
    """Owns every session and rations the live-process budget between them."""

    def __init__(self, cfg):
        self.config = cfg
        self.sessions = [Session(meta, self) for meta in cfg.sessions]
        self.live = []  # sessions holding a slot, most recently used last
        self.queue = []  # (session, text) turns waiting for a slot
        self.on_changed = None
        self.closing = False
        self._pumping = False
        GLib.timeout_add_seconds(60, self._sweep_idle)

    # ----------------------------------------------------------- bookkeeping --

    def session_changed(self, session=None):
        if self.on_changed:
            self.on_changed(session)

    def remember_commands(self, commands):
        """Keep the child's command list on disk, so the next launch can
        complete a slash command before anything has been started."""
        if commands == self.config.commands:
            return
        self.config.commands = commands
        self.config.save()

    def new_session(self, cwd=None, model=None, permission_mode=None):
        meta = config.SessionMeta(
            cwd=cwd or self.config.last_cwd,
            model=model or "default",
            permission_mode=permission_mode or "bypassPermissions",
        )
        self.config.sessions.append(meta)
        self.config.last_cwd = meta.cwd
        session = Session(meta, self)
        self.sessions.append(session)
        self.save()
        return session

    def remove_session(self, session):
        session.sleep()
        config.drop_transcript(session.meta)
        if session in self.sessions:
            self.sessions.remove(session)
        if session.meta in self.config.sessions:
            self.config.sessions.remove(session.meta)
        self.queue = [item for item in self.queue if item[0] is not session]
        self.save()

    def save(self):
        for session in self.sessions:
            session.flush_transcript()
        self.config.save()
        self.session_changed()

    def shutdown(self):
        # Set first: stopping a child emits `exit`, and a session with a queued
        # message would otherwise relaunch one on the way out.
        self.closing = True
        for session in list(self.sessions):
            session.sleep()
        self.save()

    # ---------------------------------------------------------------- slots --

    def request_slot(self, session):
        """Grant a live process to `session`, evicting an idle one if needed."""
        if session in self.live:
            self._touch(session)
            return True
        if len(self.live) >= self.config.max_live and not self._evict_one():
            return False
        self.live.append(session)
        return True

    def release_slot(self, session):
        if session in self.live:
            self.live.remove(session)
        self._pump_queue()

    def _touch(self, session):
        if session in self.live:
            self.live.remove(session)
            self.live.append(session)

    def _evict_one(self):
        for candidate in list(self.live):
            # A session with messages queued is between turns, not idle;
            # evicting it here would only make it claim a slot back.
            if candidate.outbox:
                continue
            if candidate.state in ("ready", "asleep") and candidate.pending_permission is None:
                candidate.sleep(note="Slept to free memory")
                return True
        return False

    def enqueue(self, session, text):
        self.queue.append((session, text))

    def turn_finished(self, session):
        self._touch(session)
        self._pump_queue()

    def _pump_queue(self):
        # Freeing a slot re-enters here: request_slot evicts a session, which
        # sleeps it, which releases its slot, which pumps again. The guard
        # keeps one frame in charge of the queue so two of them cannot both
        # claim the same waiting turn.
        if self._pumping:
            return
        self._pumping = True
        try:
            self._drain_queue()
        finally:
            self._pumping = False

    def _drain_queue(self):
        while self.queue:
            session, text = self.queue[0]
            if session not in self.sessions:
                self.queue.pop(0)
                continue
            if not self.request_slot(session):
                return
            self.queue.pop(0)
            session._spawn()
            session._set_state("busy")
            session.backend.send(text)

    def _sweep_idle(self):
        """Reclaim memory from sessions nobody has touched in a while."""
        limit = self.config.idle_sleep_min * 60
        if limit <= 0:
            return GLib.SOURCE_CONTINUE
        now = time.time()
        for session in list(self.live):
            idle = now - session.last_activity
            if session.state == "ready" and idle > limit:
                session.sleep(note="Slept after being idle")
        return GLib.SOURCE_CONTINUE


def _title_from(text):
    """A session name taken from its opening message."""
    line = " ".join(text.split())
    return (line[:38] + "…") if len(line) > 39 else line or "New session"


def short_path(path):
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path
