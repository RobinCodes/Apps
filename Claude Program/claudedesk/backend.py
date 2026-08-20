"""One `claude` child process, and the protocol we speak to it.

Claude Code has a machine-facing mode that the terminal UI never shows you:

    claude -p --input-format stream-json --output-format stream-json --verbose

In that mode the process stays alive across turns, reads JSON messages on
stdin and writes JSON messages on stdout — same engine, same tools, same
session files as the terminal, only the presentation is ours to write.

Two details make it usable as an interactive app rather than a batch pipe:

* `--permission-prompt-tool stdio` routes permission questions back to us as
  `can_use_tool` control requests instead of auto-denying them. Without it a
  session in `manual` mode simply refuses every write, which is what the
  terminal would have asked you about. This flag is undocumented in --help.
* The control channel also carries `interrupt` and `set_permission_mode`, so
  Escape and the permission dropdown work the way they do in the terminal.
* The same channel is how Claude asks *you* something. `AskUserQuestion`
  arrives as a `can_use_tool` request like any other, but allowing it is not
  the answer — see QUESTION_TOOL below.

Nothing in this module imports GTK. Events are handed to a plain callable the
owner supplies, and it is the owner's job to get them onto the main loop —
see manager.py. The reader runs on its own thread, so on_event is called from
that thread.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field

CLAUDE_BIN = os.environ.get("CLAUDE_DESK_BIN", "claude")

# A desktop launcher does not run a login shell: it inherits the session's
# PATH, which on XFCE is /usr/local/bin:/usr/bin and little else — no
# ~/.local/bin, no npm prefix. Started from a terminal the app found `claude`;
# started from the Applications menu it did not, and exited before drawing a
# window. So look where installers actually put it, and once found, put that
# directory on PATH so the child — and everything the child shells out to —
# sees what an interactive shell would.
BIN_DIRS = [
    "~/.local/bin",
    "~/.claude/local",
    "~/.npm-global/bin",
    "~/.bun/bin",
    "/usr/local/bin",
    "/opt/homebrew/bin",
]


def resolve_bin(name=None):
    """Absolute path to the `claude` binary, or None if there isn't one."""
    name = name or CLAUDE_BIN
    if os.sep in name:  # CLAUDE_DESK_BIN pointing straight at a binary
        path = os.path.abspath(os.path.expanduser(name))
        return path if os.path.isfile(path) and os.access(path, os.X_OK) else None

    found = shutil.which(name)
    if found:
        return found

    for directory in BIN_DIRS:
        directory = os.path.expanduser(directory)
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            _prepend_path(directory)
            return candidate
    return None


def _prepend_path(directory):
    path = os.environ.get("PATH", "")
    if directory not in path.split(os.pathsep):
        os.environ["PATH"] = os.pathsep.join([directory, path]) if path else directory

# The terminal offers these; we expose the same set minus the two that only
# make sense with a human watching a TTY.
PERMISSION_MODES = [
    ("manual", "Ask every time"),
    ("acceptEdits", "Auto-accept edits"),
    ("auto", "Auto"),
    ("plan", "Plan only"),
    ("bypassPermissions", "Bypass all checks"),
]

MODELS = [
    ("default", "Default"),
    ("opus", "Opus"),
    ("sonnet", "Sonnet"),
    ("haiku", "Haiku"),
    ("fable", "Fable"),
]

# Slash commands the CLI answers by itself. They report on the tool — what
# the plan has been spent on, what is in the context window, which MCP servers
# are up — rather than say anything to the model: no tokens, no reply worth
# keeping. A child has to answer them, but nothing about them belongs in the
# conversation.
#
# Which child, though, is the whole question. A busy one is no use: it reads
# stdin only between turns, so a command written to it mid-turn is answered
# after the very reply you were trying to look past — measurably, three
# seconds of question and twenty of waiting. The terminal answers /usage
# whenever you ask it, so these get a child of their own, started for the
# question and gone a few seconds later. It costs about 380 MB while it runs,
# which is why only the reports that genuinely do not need this conversation
# are answered that way.
STANDALONE_COMMANDS = {"usage", "cost", "stats"}

# The reports only this conversation's own child can give: a fresh one would
# report on itself — an empty context window, MCP servers still connecting —
# so these wait for the turn in front of them.
SESSION_COMMANDS = {"context", "mcp"}

REPORT_COMMANDS = STANDALONE_COMMANDS | SESSION_COMMANDS


def run_standalone(name, cwd, on_done, timeout=60):
    """Answer a standalone report in a throwaway child, off the main thread.

    Plain print mode: one command in, its answer out, the process gone again.
    stdin is closed rather than left open, because the CLI waits three seconds
    for piped input that is never coming — most of the wait, for nothing.

    `on_done(text, error)` is called from a worker thread, like everything
    else here; getting it onto the main loop is the caller's job.
    """
    def run():
        try:
            finished = subprocess.run(
                [resolve_bin() or CLAUDE_BIN, "-p", f"/{name}"],
                cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=timeout,
                env={**os.environ, "CLAUDE_CODE_ENTRYPOINT": "claude-desk"},
            )
        except subprocess.TimeoutExpired:
            on_done("", f"/{name} took too long to answer.")
            return
        except (OSError, ValueError) as exc:
            on_done("", f"Could not run /{name}: {exc}")
            return
        text = (finished.stdout or "").strip()
        if text:
            on_done(text, "")
        else:
            on_done("", (finished.stderr or "").strip() or f"/{name} said nothing.")

    threading.Thread(target=run, daemon=True).start()


def split_command(text):
    """`("name", "argument")` for a line that is a slash command, else `("", "")`.

    Read the way the terminal reads one: the first word of the first line, and
    only if it looks like a command name. `/home/robin/notes.md is stale` is a
    path someone is talking about, and the caller can tell the difference for
    certain by checking the name against the list the child sent.
    """
    line = text.strip()
    if not line.startswith("/") or line.startswith("//"):
        return "", ""
    head, _, rest = line.partition("\n")
    name, _, argument = head[1:].partition(" ")
    if not name or not all(char.isalnum() or char in "-_" for char in name):
        return "", ""
    return name, "\n".join(part for part in (argument.strip(), rest.strip()) if part)


# The tool Claude Code uses to ask *you* something rather than to ask for
# permission to do something. It comes down the same channel as a permission
# request — the terminal draws its picker from a `can_use_tool` request marked
# `requires_user_interaction`, and so do we — but the two are not answered the
# same way. Allowing a permission is the whole answer; allowing a question is
# only consent for it to run, and the tool then reads what you chose out of
# the input it is run with. So the answer travels in `updatedInput`, which
# replaces that input: `answers` maps each question's text to what was picked,
# `annotations` carries anything attached to the choice, and `response` holds
# words typed instead of choosing. Allow it without those — which is all a
# plain Allow button can say — and the tool reports that nobody was there.
QUESTION_TOOL = "AskUserQuestion"


def questions_in(request):
    """The questions in a `can_use_tool` request; empty if it is not one.

    Anything malformed comes back empty and is then shown as an ordinary
    Allow / Deny card. A question we cannot draw is better asked badly than
    swallowed, and the shape is the model's to fill in, so it is checked here
    rather than trusted in the widget.
    """
    if request.get("tool_name") != QUESTION_TOOL:
        return []
    raw = (request.get("input") or {}).get("questions")
    if not isinstance(raw, list):
        return []

    questions = []
    for item in raw:
        if not isinstance(item, dict) or not str(item.get("question", "")).strip():
            continue
        options = []
        for option in item.get("options") or []:
            if isinstance(option, dict) and str(option.get("label", "")).strip():
                options.append({
                    "label": str(option["label"]),
                    "description": str(option.get("description") or ""),
                    "preview": str(option.get("preview") or ""),
                })
        questions.append({
            "question": str(item["question"]),
            "header": str(item.get("header") or ""),
            "options": options,
            "multi": bool(item.get("multiSelect")),
        })
    return questions


@dataclass
class Event:
    """Something the child told us, normalised for the UI."""

    kind: str
    data: dict = field(default_factory=dict)


class Backend:
    """A live `claude` process. Create, start(), send(), stop()."""

    def __init__(self, cwd, model, permission_mode, session_id=None, on_event=None):
        self.cwd = cwd
        self.model = model
        self.permission_mode = permission_mode
        self.session_id = session_id
        self.on_event = on_event or (lambda ev: None)

        self._proc = None
        self._write_lock = threading.Lock()
        self._pending = []  # user turns queued until the handshake lands
        self._ready = False
        self._stopping = False

    # ------------------------------------------------------------ lifecycle --

    def _argv(self):
        argv = [
            resolve_bin() or CLAUDE_BIN,
            "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-prompt-tool", "stdio",
            "--permission-mode", self.permission_mode,
        ]
        if self.model and self.model != "default":
            argv += ["--model", self.model]
        if self.session_id:
            argv += ["--resume", self.session_id]
        return argv

    def start(self):
        if self._proc is not None:
            return
        env = {**os.environ, "CLAUDE_CODE_ENTRYPOINT": "claude-desk"}
        try:
            self._proc = subprocess.Popen(
                self._argv(),
                cwd=self.cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self._emit("error", message=f"Could not start Claude Code: {exc}")
            self._emit("exit", code=-1)
            return

        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

        # The handshake tells the child someone is listening on the control
        # channel; until its reply lands, permission requests would be denied.
        self._write({
            "type": "control_request",
            "request_id": f"init-{uuid.uuid4()}",
            "request": {"subtype": "initialize", "hooks": {}},
        })

    @property
    def alive(self):
        return self._proc is not None and self._proc.poll() is None

    def stop(self):
        """Close stdin and let the child exit; kill it if it dawdles."""
        self._stopping = True
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass

        def reap():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

        threading.Thread(target=reap, daemon=True).start()

    # --------------------------------------------------------------- writing --

    def _write(self, obj):
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        line = json.dumps(obj) + "\n"
        with self._write_lock:
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except (BrokenPipeError, ValueError, OSError):
                return False
        return True

    def send(self, text):
        """Queue a user turn; it goes out as soon as the handshake completes."""
        message = {"type": "user", "message": {"role": "user", "content": text}}
        if not self._ready:
            self._pending.append(message)
            return
        self._write(message)

    def interrupt(self):
        self._write({
            "type": "control_request",
            "request_id": f"int-{uuid.uuid4()}",
            "request": {"subtype": "interrupt"},
        })

    def set_permission_mode(self, mode):
        self.permission_mode = mode
        self._write({
            "type": "control_request",
            "request_id": f"mode-{uuid.uuid4()}",
            "request": {"subtype": "set_permission_mode", "mode": mode},
        })

    def answer_question(self, request_id, tool_input, answers, annotations=None, response=None):
        """Answer an AskUserQuestion request: `allow`, carrying the answer.

        Everything the request arrived with is handed back untouched, because
        `updatedInput` replaces the tool's input rather than adding to it —
        drop `questions` and the tool is run without the questions it asked.
        """
        payload = dict(tool_input or {})
        payload["answers"] = dict(answers or {})
        if annotations:
            payload["annotations"] = dict(annotations)
        if response:
            payload["response"] = response
        self.answer_permission(request_id, True, updated_input=payload)

    def answer_permission(self, request_id, allow, updated_input=None, message=None):
        if allow:
            payload = {"behavior": "allow", "updatedInput": updated_input or {}}
        else:
            payload = {"behavior": "deny", "message": message or "Denied by user."}
        self._write({
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": request_id,
                "response": payload,
            },
        })

    # --------------------------------------------------------------- reading --

    def _emit(self, kind, **data):
        self.on_event(Event(kind, data))

    def _read_stderr(self):
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            line = line.strip()
            # Node/bun chatter on shutdown is not worth showing anyone.
            if line and not self._stopping:
                self._emit("stderr", message=line)

    def _read_stdout(self):
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                self._dispatch(msg)
            except Exception as exc:  # noqa: BLE001 - a bad frame must not kill the reader
                self._emit("error", message=f"Bad message from Claude Code: {exc}")
        code = proc.wait() if proc.poll() is None else proc.returncode
        if not self._stopping:
            self._emit("exit", code=code)

    def _dispatch(self, msg):
        kind = msg.get("type")

        if kind == "control_request":
            self._on_control_request(msg)
        elif kind == "control_response":
            self._on_control_response(msg)
        elif kind == "system":
            self._on_system(msg)
        elif kind == "assistant":
            self._on_assistant(msg)
        elif kind == "user":
            self._on_user(msg)
        elif kind == "stream_event":
            self._on_stream_event(msg)
        elif kind == "result":
            self._emit(
                "turn_end",
                is_error=msg.get("is_error", False),
                subtype=msg.get("subtype", ""),
                cost=msg.get("total_cost_usd") or 0.0,
                duration_ms=msg.get("duration_api_ms") or 0,
                usage=msg.get("usage") or {},
                result=msg.get("result") or "",
            )
        elif kind == "rate_limit_event":
            self._emit("rate_limit", info=msg.get("rate_limit_info") or {})

    def _on_control_request(self, msg):
        request = msg.get("request") or {}
        if request.get("subtype") != "can_use_tool":
            return
        self._emit(
            "permission",
            request_id=msg.get("request_id"),
            questions=questions_in(request),
            tool_name=request.get("tool_name", "tool"),
            display_name=request.get("display_name") or request.get("tool_name", "tool"),
            description=request.get("description") or "",
            title=request.get("title") or "",
            input=request.get("input") or {},
            suggestions=request.get("permission_suggestions") or [],
            tool_use_id=request.get("tool_use_id"),
        )

    def _on_control_response(self, msg):
        if self._ready:
            return
        self._ready = True
        response = (msg.get("response") or {}).get("response") or {}
        self._emit("handshake", commands=response.get("commands") or [])
        pending, self._pending = self._pending, []
        for message in pending:
            self._write(message)

    def _on_system(self, msg):
        subtype = msg.get("subtype")
        if subtype == "init":
            self.session_id = msg.get("session_id") or self.session_id
            self._emit(
                "ready",
                session_id=self.session_id,
                model=msg.get("model", ""),
                cwd=msg.get("cwd", ""),
                tools=msg.get("tools") or [],
                commands=msg.get("slash_commands") or [],
                permission_mode=msg.get("permissionMode", ""),
            )
        elif subtype == "permission_denied":
            self._emit(
                "denied",
                tool_name=msg.get("tool_name", "tool"),
                message=msg.get("message", ""),
            )
        elif subtype == "thinking_tokens":
            self._emit("thinking_tokens", tokens=msg.get("estimated_tokens", 0))
        elif subtype in ("compact_boundary", "context_compacted"):
            self._emit("notice", message="Context compacted.")

    def _on_assistant(self, msg):
        # Claude Code emits one assistant frame per content block, so each of
        # these is a finished block — the authoritative version of whatever the
        # deltas have been sketching.
        for block in (msg.get("message") or {}).get("content") or []:
            btype = block.get("type")
            if btype == "text":
                self._emit("block", block="text", text=block.get("text", ""))
            elif btype == "thinking":
                self._emit("block", block="thinking", text=block.get("thinking", ""))
            elif btype == "tool_use":
                self._emit(
                    "block",
                    block="tool_use",
                    id=block.get("id", ""),
                    name=block.get("name", "tool"),
                    input=block.get("input") or {},
                )

    def _on_user(self, msg):
        # Tool results come back addressed to us as synthetic user turns.
        content = (msg.get("message") or {}).get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if block.get("type") != "tool_result":
                continue
            self._emit(
                "tool_result",
                id=block.get("tool_use_id", ""),
                content=_flatten(block.get("content")),
                is_error=bool(block.get("is_error")),
            )

    def _on_stream_event(self, msg):
        event = msg.get("event") or {}
        etype = event.get("type")
        if etype == "content_block_delta":
            delta = event.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                self._emit("delta", block="text", text=delta.get("text", ""))
            elif dtype == "thinking_delta":
                self._emit("delta", block="thinking", text=delta.get("thinking", ""))
        elif etype == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                self._emit("tool_pending", name=block.get("name", "tool"))
        elif etype == "message_stop":
            self._emit("delta_end")


def _flatten(content):
    """Tool results arrive as a string or as a list of content blocks."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    parts.append("[image]")
        return "\n".join(parts)
    return str(content)
