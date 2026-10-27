"""Claude, from inside any app: whatever an app cannot do, it can ask for.

Every app that joins the second brain can hand a request to Claude Code — the
same `claude` command Claude Desk drives and `anam ask` uses — with the brain
as its working folder and the Order's contract (SYSTEM.md) as its briefing. So
"turn this note into cards", "what did I think about this last year?", "file
these three scans" are one action from wherever the thing is, and the answer
streams back into the app that asked.

    ask = claude.Ask("Make cards from this note", files=[path],
                     on_text=show, on_done=finished)
    ask.start()          # a worker thread; callbacks come from it
    ...
    ask.cancel()

Callbacks run on the worker thread: a GTK caller wraps them in
`GLib.idle_add`. `continue_in_desk(session_id)` opens the conversation in
Claude Desk, for when an answer turns into a piece of work.

Standard library only; vendored by `house/sync.py` with the other brain files.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .brain import places

WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if WINDOWS else 0

# Read-only by default: an answer may read anything in the brain but changes
# nothing unless the caller asks for edits, and then only file edits, which
# git can always undo.
MODES = ("default", "acceptEdits", "plan")


def _candidates() -> list[str]:
    home = Path.home()
    if not WINDOWS:
        return [str(home / p) for p in (".local/bin/claude", ".claude/local/claude",
                                        ".npm-global/bin/claude", ".bun/bin/claude")] + \
            ["/usr/local/bin/claude", "/opt/homebrew/bin/claude"]
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    return [str(p) for p in (
        home / ".local" / "bin" / "claude.exe", home / ".claude" / "local" / "claude.exe",
        appdata / "npm" / "claude.cmd", local / "Programs" / "claude" / "claude.exe",
        home / ".bun" / "bin" / "claude.exe")]


def find() -> str | None:
    """The `claude` command, wherever an installer put it, or None.

    `ORDER_CLAUDE` (or Claude Desk's `CLAUDE_DESK_BIN`) wins. A process started
    from a desktop shortcut has a thinner PATH than a terminal, so the usual
    install places are looked in too.
    """
    for var in ("ORDER_CLAUDE", "CLAUDE_DESK_BIN"):
        named = os.environ.get(var)
        if named and (os.path.isfile(named) or shutil.which(named)):
            return named if os.path.isfile(named) else shutil.which(named)
    found = shutil.which("claude")
    if found:
        return found
    return next((c for c in _candidates() if os.path.isfile(c)), None)


def available() -> bool:
    return find() is not None


def briefing() -> str:
    """What Claude is told about where it is, before the request."""
    p = places()
    return (
        "You are working inside Robin's Order: his second brain (Anamnesis) and the apps "
        "around it. The contract for how everything is laid out, named and learned is "
        f"{(p.brain / 'SYSTEM.md').as_posix()} — read it when the request touches "
        "structure, cards, the inbox or the layout. The brain is "
        f"{p.brain.as_posix()} (vault/nodes holds the nodes; `anam search` finds things; "
        f"the inbox is {p.inbox.as_posix()}). Projects are under {p.projects.as_posix()}. "
        "Cards follow SYSTEM.md §7. Answer in the language the request is written in. "
        "Be concise: the answer is shown in a side panel of the app that asked."
    )


class Ask:
    """One request to Claude Code, streamed back.

    `files` are named in the prompt so Claude reads them itself. `mode` is a
    Claude Code permission mode; anything but the default lets it change files.
    `command` replaces the `claude` binary (tests use a stand-in).
    """

    def __init__(self, prompt: str, *, files=(), mode: str = "default",
                 cwd: Path | str | None = None, on_text=None, on_done=None,
                 resume: str | None = None, command: list[str] | None = None):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.prompt = prompt
        self.files = [Path(f) for f in files]
        self.mode = mode
        self.cwd = Path(cwd) if cwd else places().brain
        self.on_text = on_text or (lambda text: None)
        self.on_done = on_done or (lambda ok, result, session: None)
        self.resume = resume
        self.command = command
        self.session_id: str | None = None
        self.text = ""
        self._proc: subprocess.Popen | None = None
        self._cancelled = False

    def argv(self) -> list[str]:
        base = self.command or [find() or "claude"]
        prompt = self.prompt
        if self.files:
            listed = "\n".join(f"- {f.as_posix()}" for f in self.files)
            prompt += f"\n\nThe files this is about:\n{listed}"
        args = [*base, "-p", prompt, "--output-format", "stream-json", "--verbose",
                "--append-system-prompt", briefing()]
        if self.mode != "default":
            args += ["--permission-mode", self.mode]
        if self.resume:
            args += ["--resume", self.resume]
        return args

    def start(self) -> "Ask":
        threading.Thread(target=self._run, name="claude-ask", daemon=True).start()
        return self

    def cancel(self) -> None:
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def _run(self) -> None:
        cwd = self.cwd if self.cwd.is_dir() else Path.home()
        try:
            self._proc = subprocess.Popen(
                self.argv(), cwd=str(cwd), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
                encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        except OSError as exc:
            self.on_done(False, f"Claude Code could not be started: {exc}", None)
            return
        result = None
        for line in self._proc.stdout:
            event = _parse(line)
            if not event:
                continue
            if event.get("session_id"):
                self.session_id = event["session_id"]
            kind = event.get("type")
            if kind == "assistant":
                for block in (event.get("message") or {}).get("content") or []:
                    if block.get("type") == "text" and block.get("text"):
                        chunk = block["text"] if not self.text else "\n\n" + block["text"]
                        self.text += chunk
                        self.on_text(chunk)
            elif kind == "result":
                result = event
        self._proc.wait()
        if self._cancelled:
            self.on_done(False, "cancelled", self.session_id)
        elif result is not None and not result.get("is_error"):
            self.on_done(True, result.get("result") or self.text, self.session_id)
        else:
            err = (self._proc.stderr.read() if self._proc.stderr else "").strip()
            why = (result or {}).get("result") or err or f"exit {self._proc.returncode}"
            self.on_done(False, why, self.session_id)


def _parse(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def continue_in_desk(session_id: str | None = None, cwd: Path | str | None = None) -> bool:
    """Open Claude Desk from the Apps checkout, on the brain, to carry a conversation on.

    Claude Desk lists every session under the folder it opens, so the one this
    request started is there to pick up. False when Claude Desk is not beside
    this app.
    """
    p = places()
    entry = p.apps / "Claude Program" / "claude-desk"
    if not entry.is_file():
        return False
    folder = str(cwd or p.brain)
    exe = sys.executable
    if WINDOWS and exe.lower().endswith("python.exe"):
        exe = exe[:-len("python.exe")] + "pythonw.exe"
    try:
        subprocess.Popen([exe, str(entry), folder], cwd=folder,
                         creationflags=NO_WINDOW, start_new_session=not WINDOWS)
        return True
    except OSError:
        return False
