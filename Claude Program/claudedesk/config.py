"""Where the app keeps its settings, its session list, and its transcripts.

Claude Code already stores the real conversation state under ~/.claude — we
never duplicate that. What lives here is only what the desktop app itself
needs: which sessions you have open, what each one is called, and a rendered
copy of each conversation so a window can show history without waking a
448 MB child process just to read it back.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "claude-desk"
)
DATA_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "claude-desk"
)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
TRANSCRIPT_DIR = os.path.join(DATA_DIR, "transcripts")

# A live child process costs roughly 450 MB of RSS. Two at once is what a
# 4 GB machine can carry alongside a browser; the rest sleep and resume.
DEFAULT_MAX_LIVE = 2
DEFAULT_IDLE_SLEEP_MIN = 10

# Past this a transcript is being kept for scrollback, not for reading, and
# the cost of holding it in memory stops being worth it.
MAX_TOOL_RESULT_CHARS = 20000


@dataclass
class SessionMeta:
    """One conversation as the app knows it, whether awake or asleep."""

    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = "New session"
    cwd: str = field(default_factory=os.getcwd)
    model: str = "default"
    permission_mode: str = "bypassPermissions"
    claude_session_id: str = ""
    created: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)

    @property
    def transcript_path(self):
        return os.path.join(TRANSCRIPT_DIR, f"{self.uid}.json")


class Config:
    """Settings plus the session registry, persisted as one small JSON file."""

    def __init__(self):
        self.max_live = DEFAULT_MAX_LIVE
        self.idle_sleep_min = DEFAULT_IDLE_SLEEP_MIN
        self.show_thinking = True
        self.last_cwd = os.path.expanduser("~")
        self.sessions: list[SessionMeta] = []
        self.active_uid = ""
        # The slash commands the last live child listed, kept so the composer
        # can complete them before anything has been launched this time.
        self.commands: list[dict] = []
        self.load()

    def load(self):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return
        self.max_live = int(raw.get("max_live", DEFAULT_MAX_LIVE))
        self.idle_sleep_min = int(raw.get("idle_sleep_min", DEFAULT_IDLE_SLEEP_MIN))
        self.show_thinking = bool(raw.get("show_thinking", True))
        self.last_cwd = raw.get("last_cwd") or os.path.expanduser("~")
        self.active_uid = raw.get("active_uid", "")
        self.commands = [item for item in raw.get("commands") or []
                         if isinstance(item, dict) and item.get("name")]
        self.sessions = []
        for item in raw.get("sessions") or []:
            try:
                self.sessions.append(SessionMeta(**item))
            except TypeError:
                continue  # a session written by a newer version — skip it

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        payload = {
            "max_live": self.max_live,
            "idle_sleep_min": self.idle_sleep_min,
            "show_thinking": self.show_thinking,
            "last_cwd": self.last_cwd,
            "active_uid": self.active_uid,
            "commands": self.commands,
            "sessions": [asdict(session) for session in self.sessions],
        }
        tmp = CONFIG_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp, CONFIG_FILE)
        except OSError:
            pass


def load_transcript(meta):
    try:
        with open(meta.transcript_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def save_transcript(meta, entries):
    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
    tmp = meta.transcript_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(entries, handle)
        os.replace(tmp, meta.transcript_path)
    except OSError:
        pass


def drop_transcript(meta):
    for path in (meta.transcript_path, meta.transcript_path + ".tmp"):
        try:
            os.remove(path)
        except OSError:
            pass
