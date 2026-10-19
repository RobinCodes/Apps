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

from . import theme as theming, winenv

# Claude Code's own settings file. "Default" in the model dropdown means
# whatever this says, so the only honest way to show what Default *is* — and
# the only honest place to change it — is here, rather than in a setting of
# our own that the CLI would never read. It is the terminal's file too, which
# is why writing it changes one key and leaves everything else alone.
CLAUDE_DIR = (os.environ.get("CLAUDE_CONFIG_DIR")
              or os.path.join(os.path.expanduser("~"), ".claude"))
CLAUDE_SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")

CONFIG_DIR = winenv.config_home("claude-desk")
DATA_DIR = winenv.data_home("claude-desk")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
TRANSCRIPT_DIR = os.path.join(DATA_DIR, "transcripts")

# A live child process costs roughly 450 MB of RSS. Two at once is what a
# 4 GB machine can carry alongside a browser; the rest sleep and resume.
DEFAULT_MAX_LIVE = 2
DEFAULT_IDLE_SLEEP_MIN = 10

# Past this a transcript is being kept for scrollback, not for reading, and
# the cost of holding it in memory stops being worth it.
MAX_TOOL_RESULT_CHARS = 20000

# How a remembered folder loses its claim on the shortlist. Weighting the use
# count by a half-life means the folder you have been in all week outranks the
# one you lived in last spring, without a single visit to a new folder
# immediately displacing somewhere you return to every few days.
FOLDER_HALF_LIFE_DAYS = 30

# Enough that a long-running project is never pushed off the end, few enough
# that the file stays small and the scoring stays honest.
MAX_REMEMBERED_FOLDERS = 40

# What the picker shows before it starts scrolling.
FOLDER_SHORTLIST = 8

# How wide the sidebar opens, and how far it can be dragged. A pane is only
# draggable down to the width its own contents insist on, so a floor set here
# binds only while it is the higher of the two — which is the case for the
# sidebar, and is deliberately not for the conversation: what stops the
# sidebar growing is the conversation's own header, and the chat figure here
# is a floor under that rather than a constraint of its own. Raising it costs
# travel directly — a 420 here left 68 px of drag in a tiling column.
DEFAULT_SIDEBAR_WIDTH = 300
MIN_SIDEBAR_WIDTH = 190
MIN_CHAT_WIDTH = 320

# What the window comes back at. Kept here with the rest of the geometry
# rather than in the window, because the window has to know it before it has
# read anything — `set_default_size` has to happen before the map or the
# frame is drawn once at the wrong size and then again at the right one.
DEFAULT_WINDOW_WIDTH = 1120
DEFAULT_WINDOW_HEIGHT = 740
MIN_WINDOW_WIDTH = 480
MIN_WINDOW_HEIGHT = 360

# Filing a chat and labelling it are two different things, so the app does
# both. A group is where a chat *is*: one of these three sections and only
# one at a time, which is what lets the sidebar put the list in order, and
# what makes the archive somewhere a chat goes rather than something written
# on it. There are three, they are built in, and you cannot make a fourth.
FAVOURITE_GROUP = "favourite"
ARCHIVE_GROUP = "archive"
GROUP_LABELS = {FAVOURITE_GROUP: "Favourites", ARCHIVE_GROUP: "Archived"}
UNGROUPED_LABEL = "Chats"

# A tag is what a chat is *about*, and one chat can carry as many as it
# earns — which is what a group of your own was always being used for and
# could only ever half do, a chat about two things having to pick one. Tags
# cut across the groups: an archived chat keeps its tags, and the filter
# above the sidebar finds it by one.
#
# Long enough for a project name, short enough to read in a 290 px sidebar.
MAX_TAG_NAME = 24


@dataclass
class FolderUse:
    """One working folder the app has been pointed at, and how often."""

    path: str
    uses: int = 1
    last_used: float = field(default_factory=time.time)


def folder_score(folder, now=None):
    """How strong a claim a folder has on the shortlist.

    "Where I work" is both how often and how recently, and either alone gets
    it wrong: sorting by count alone pins last year's big project to the top,
    and sorting by recency alone lets one detour push out the folder you open
    every morning. So the count is the weight and age only discounts it.
    """
    now = time.time() if now is None else now
    age_days = max(0.0, (now - folder.last_used) / 86400.0)
    return folder.uses * 0.5 ** (age_days / FOLDER_HALF_LIFE_DAYS)


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
    # Which section the sidebar files this chat under: one of the two
    # built-in names, or "" for the ungrouped middle.
    group: str = ""
    # What it is about, in the order the tags were put on. Any number of
    # them, and independent of the group above.
    tags: list[str] = field(default_factory=list)

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
        # Every tag any chat is wearing, sorted. Sorted rather than kept in
        # the order they were made, because unlike the groups they replaced
        # these do not order anything themselves: what they want is to be in
        # the same place in the menu every time you go to tick one.
        self.tags: list[str] = []
        self.show_archived = False
        self.sidebar_width = DEFAULT_SIDEBAR_WIDTH
        self.window_width = DEFAULT_WINDOW_WIDTH
        self.window_height = DEFAULT_WINDOW_HEIGHT
        self.window_maximized = False
        # The house style's own settings — the theme, the glass. A mapping
        # rather than attributes, because that is what the shared preference
        # rows and the Glazing both expect, and `save` on it is this one.
        self.house = theming.Settings(self.save)
        # Claude Code's model setting, read once rather than on every header
        # sync. What a running child reports beats it — see Session.live_model.
        self.default_model = read_default_model()
        # Every folder a session has been deliberately pointed at, so the next
        # one can be started from a list instead of a file chooser.
        self.folders: list[FolderUse] = []
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
        self.show_archived = bool(raw.get("show_archived", False))
        try:
            self.sidebar_width = max(MIN_SIDEBAR_WIDTH, int(raw.get("sidebar_width")))
        except (TypeError, ValueError):
            self.sidebar_width = DEFAULT_SIDEBAR_WIDTH
        self.commands = [item for item in raw.get("commands") or []
                         if isinstance(item, dict) and item.get("name")]
        self.house.take(raw)
        # Clamped rather than trusted: a hand-edited file with a width of 20
        # is a window nobody can find, and one with a width of "big" would
        # otherwise raise on the line that sets the default size.
        for name, floor, default in (
                ("window_width", MIN_WINDOW_WIDTH, DEFAULT_WINDOW_WIDTH),
                ("window_height", MIN_WINDOW_HEIGHT, DEFAULT_WINDOW_HEIGHT)):
            value = raw.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int):
                value = default
            setattr(self, name, max(floor, min(32767, value)))
        self.window_maximized = bool(raw.get("window_maximized", False))
        self.sessions = []
        for item in raw.get("sessions") or []:
            try:
                meta = SessionMeta(**item)
            except TypeError:
                continue  # a session written by a newer version — skip it
            # "manual" was this app's own name for asking every time, and no
            # name the CLI knows: it coerced it to `default` on the way in, so
            # the mode worked and the saved value was fiction. Write the real
            # one down now rather than leave the header unable to show it.
            if meta.permission_mode == "manual":
                meta.permission_mode = "default"
            # A group of your own making, from a file written before tags
            # existed, is a tag now: the name meant the same thing then, and
            # this is the one place that knows it was ever a group. The chat
            # lands in the ungrouped middle wearing it.
            moved = clean_tag(meta.group)
            meta.group = clean_group(meta.group)
            meta.tags = clean_tags([moved, *meta.tags])
            self.sessions.append(meta)
        # After the sessions, so a tag left behind by a chat that has since
        # been deleted is dropped rather than kept as a name nothing wears.
        # `groups` is what the same list was called when a chat could only be
        # in one of them, and reading it is what carries those names over.
        self.tags = clean_tags(raw.get("tags") or raw.get("groups") or [])
        self.tags.sort(key=str.lower)
        self.prune_tags()
        self.folders = []
        for item in raw.get("folders") or []:
            try:
                self.folders.append(FolderUse(**item))
            except TypeError:
                continue  # written by a newer version — skip it
        if "folders" not in raw:
            self._seed_folders()

    def _seed_folders(self):
        """Upgrading from a version that only remembered one folder.

        The open sessions already say where the work happens, so the picker
        can be useful the first time it opens rather than after a week of
        relearning what the config could have told it.
        """
        seen = {}
        for meta in self.sessions:
            folder = seen.get(meta.cwd)
            if folder is None:
                seen[meta.cwd] = FolderUse(path=meta.cwd, last_used=meta.last_used)
            else:
                folder.uses += 1
                folder.last_used = max(folder.last_used, meta.last_used)
        if self.last_cwd not in seen:
            seen[self.last_cwd] = FolderUse(path=self.last_cwd)
        self.folders = list(seen.values())

    def remember_folder(self, path):
        """Count one deliberate use of `path`."""
        if not path:
            return
        for folder in self.folders:
            if folder.path == path:
                folder.uses += 1
                folder.last_used = time.time()
                break
        else:
            self.folders.append(FolderUse(path=path))
        if len(self.folders) > MAX_REMEMBERED_FOLDERS:
            self.folders.sort(key=folder_score, reverse=True)
            del self.folders[MAX_REMEMBERED_FOLDERS:]

    def recent_folders(self, limit=FOLDER_SHORTLIST):
        """The folders worth offering, best first.

        A folder that has since been deleted or unmounted is left out rather
        than offered: choosing it could only fail, and on this machine a
        project folder can live on a drive that is not always there.
        """
        alive = [folder for folder in self.folders if os.path.isdir(folder.path)]
        alive.sort(key=folder_score, reverse=True)
        return [folder.path for folder in alive[:limit]]

    # ------------------------------------------------------------------ tags --

    def remember_tag(self, name):
        """Keep a tag you have just put on a chat, in its sorted place."""
        name = clean_tag(name)
        if name and name not in self.tags:
            self.tags.append(name)
            self.tags.sort(key=str.lower)
        return name

    def prune_tags(self):
        """Forget a tag nothing is wearing any more.

        A tag is made by putting it on a chat, so one nothing wears is a tag
        you have taken off everything — and keeping it would mean a list of
        names that only ever grows and a second piece of UI to delete them
        again.
        """
        used = {tag for meta in self.sessions for tag in meta.tags}
        self.tags = [name for name in self.tags if name in used]

    def all_tags(self):
        """Every tag in use, in the order the menu and the filter show them."""
        return list(self.tags)

    def group_order(self):
        """Every section the sidebar can show, top to bottom.

        Favourites lead, the ungrouped middle comes next, and Archived is
        last — a chat you archive should move out of the way, not jump to the
        top of a new section.
        """
        return [FAVOURITE_GROUP, "", ARCHIVE_GROUP]

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        payload = {
            "max_live": self.max_live,
            "idle_sleep_min": self.idle_sleep_min,
            "show_thinking": self.show_thinking,
            "last_cwd": self.last_cwd,
            "active_uid": self.active_uid,
            "tags": self.tags,
            "show_archived": self.show_archived,
            "sidebar_width": self.sidebar_width,
            "window_width": self.window_width,
            "window_height": self.window_height,
            "window_maximized": self.window_maximized,
            "commands": self.commands,
            **self.house.payload(),
            "sessions": [asdict(session) for session in self.sessions],
            "folders": [asdict(folder) for folder in self.folders],
        }
        tmp = CONFIG_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp, CONFIG_FILE)
        except OSError:
            pass


def read_default_model():
    """What `claude` reaches for when nothing asks for a model.

    Empty when the file says nothing, which means Claude Code's own built-in
    choice — not a model this app can name.
    """
    try:
        with open(CLAUDE_SETTINGS, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return ""
    model = raw.get("model") if isinstance(raw, dict) else None
    return model if isinstance(model, str) else ""


def write_default_model(name):
    """Change Claude Code's `model` setting and leave the rest of it alone.

    Read, change the one key, write beside it and replace — the same care the
    app's own config gets, and more warranted here: this file is shared with
    the terminal and with every other session on the machine. An empty name
    removes the key, which is how you say "whatever Claude Code picks".

    A file that exists but cannot be read is a refusal, not an empty one:
    starting from {} there would quietly replace every other setting in it
    with nothing.
    """
    raw = {}
    if os.path.exists(CLAUDE_SETTINGS):
        try:
            with open(CLAUDE_SETTINGS, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return False
    if not isinstance(raw, dict):
        return False

    if name:
        raw["model"] = name
    elif "model" not in raw:
        return True
    else:
        del raw["model"]

    tmp = CLAUDE_SETTINGS + ".tmp"
    try:
        os.makedirs(CLAUDE_DIR, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(raw, handle, indent=2)
        os.replace(tmp, CLAUDE_SETTINGS)
    except OSError:
        return False
    return True


def clean_group(name):
    """Which of the three sections a name asks for.

    The built-in two are matched case-insensitively, by key or by label, so
    every way of saying "archive" lands in the one archive. Anything else is
    not a group — there is no making one — and means the ungrouped middle.
    """
    name = " ".join(str(name or "").split())
    for key, label in GROUP_LABELS.items():
        if name.lower() in (key, label.lower()):
            return key
    return ""


def clean_tag(name):
    """A tag as it is stored: one line, trimmed, capped, and not a group.

    A built-in group's name is refused rather than cleaned up into something
    close to it: a #Archived tag sitting beside the real archive would be two
    things answering to one name, and the archive is the one that works.
    """
    name = " ".join(str(name or "").split())[:MAX_TAG_NAME]
    return "" if clean_group(name) else name


def clean_tags(names):
    """A list of tags as it is stored: each one once, in the order given."""
    out = []
    for name in names:
        name = clean_tag(name)
        if name and name not in out:
            out.append(name)
    return out


def group_label(name):
    """What a group calls itself in a header."""
    return GROUP_LABELS.get(name) or UNGROUPED_LABEL


def tag_label(name):
    """What a tag calls itself anywhere it is shown."""
    return f"#{name}"


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
