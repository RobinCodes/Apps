"""Claude, inside Mnemosyne: what the app cannot do itself, it can ask for.

Three things, all through `claude.Ask` (house/claude.py) and all shown in one
panel at the side of the window, where the answer streams in as it is written:

* **Explain this card** (X while reviewing). A card that will not stick is
  usually a card that was never understood, and an explanation — why the
  answer is what it is, and one hook to hang it on — is the fix. Claude is
  given the card and the note it is written in.
* **Make cards from a note.** Claude reads the note and proposes cards in the
  Order's format (SYSTEM.md §7). Nothing is written until they have been
  looked at: each proposal is a tick box, and only the ticked ones are added
  to the note, under a "Cards" heading at its end. Claude itself is asked in
  the read-only mode — the note is changed by this app, after the approval.
* **Ask Claude…** anything, with the card being reviewed attached if there is
  one. A follow-up continues the same conversation; "Continue in Claude Desk"
  opens it there, for when an answer turns into a piece of work.

When Claude Code is not installed none of this is shown.
"""

from __future__ import annotations

import re
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import cards, claude, widgets  # noqa: E402
from .cardtext import to_markup  # noqa: E402

CARDS_PROMPT = (
    "Read the note and propose flashcards for what in it is worth remembering, in "
    "the Order's card format (SYSTEM.md §7): `Front :: Back` on one line, "
    "`Front ::: Back` for a fact worth asking both ways, a `Q:` line followed by an "
    "`A:` line for a longer answer, and `{{answer}}` around the one word that is the "
    "point of a sentence. Ask one thing per card, and keep answers short. Do not "
    "repeat cards the note already has (lines with `::`, `Q:`/`A:` or `{{…}}`). Write "
    "in the note's own language. Reply with the cards only, between a line "
    "`BEGIN CARDS` and a line `END CARDS`, with a blank line between cards.")


def proposals(text: str, path: str) -> list[str]:
    """The cards in Claude's reply, one string each, keeping only those that parse.

    Everything between BEGIN CARDS and END CARDS when the markers are there,
    the whole reply when they are not. A `Q:`/`A:` pair is one proposal; any
    other line that `cards.parse_text` reads as a card is one proposal; the
    rest — chatter, headings, a fence — is dropped.
    """
    block = text
    m = re.search(r"BEGIN CARDS\s*\n(.*?)\n\s*END CARDS", text, re.S)
    if m:
        block = m.group(1)
    block = re.sub(r"^\s*```[^\n]*$", "", block, flags=re.M)
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n", block):
        lines = [ln.rstrip() for ln in paragraph.strip("\n").split("\n") if ln.strip()]
        if not lines:
            continue
        if re.match(r"^\s*(?:[-*+]\s+)?Q:", lines[0]):
            units.append("\n".join(lines))
        else:
            units.extend(lines)
    return [u.strip() for u in units if cards.parse_text(u.strip(), path)]


def append_cards(note: Path, chosen: list[str]) -> None:
    """Add cards to the end of a note, under a "Cards" heading unless it ends in one.

    A blank line between cards, because a cloze line next to another line
    would be read as one paragraph with it. The note's own line endings are
    kept.
    """
    raw = note.read_bytes().decode("utf-8", errors="replace")
    newline = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n").rstrip("\n")
    headings = re.findall(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", text, re.M)
    out = text + "\n\n"
    if not headings or headings[-1].strip().lower() not in ("cards", "kártyák"):
        out += "## Cards\n\n"
    out += "\n\n".join(chosen) + "\n"
    note.write_bytes(out.replace("\n", newline).encode("utf-8"))


class Panel(Gtk.Box):
    def __init__(self, assistant):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         css_classes=["claude-panel"], width_request=260)
        self.assistant = assistant
        top = Gtk.Box(spacing=4)
        self.heading = Gtk.Label(label="Claude", xalign=0, hexpand=True,
                                 css_classes=["heading"],
                                 ellipsize=Pango.EllipsizeMode.END)
        top.append(self.heading)
        self.spinner = Gtk.Spinner()
        top.append(self.spinner)
        self.stop = widgets.flat("process-stop-symbolic", "Stop")
        self.stop.connect("clicked", lambda *_: assistant.cancel())
        self.stop.set_visible(False)
        top.append(self.stop)
        close = widgets.flat("window-close-symbolic", "Close the panel")
        close.connect("clicked", lambda *_: assistant.window.split.set_show_sidebar(False))
        top.append(close)
        self.append(top)

        self.text = widgets.label("", css=("claude-text",), selectable=True, markup=True)
        self.text.set_valign(Gtk.Align.START)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.text)
        self.scroller = scroller
        self.append(scroller)

        self.follow = Gtk.Entry(placeholder_text="Ask a follow-up…")
        self.follow.connect("activate", self._follow_up)
        self.append(self.follow)
        self.desk = Gtk.Button(label="Continue in Claude Desk", css_classes=["flat"],
                               sensitive=False)
        self.desk.connect("clicked", lambda *_: assistant.continue_in_desk())
        self.append(self.desk)
        self.raw = ""

    def begin(self, heading: str, question: str = ""):
        self.heading.set_text(heading)
        self.raw = f"*{question}*\n\n" if question else ""
        self._render()
        self.spinner.start()
        self.stop.set_visible(True)

    def add(self, chunk: str):
        self.raw += chunk
        self._render()
        adjustment = self.scroller.get_vadjustment()
        GLib.idle_add(lambda: adjustment.set_value(adjustment.get_upper()) or False)

    def end(self, ok: bool, message: str = ""):
        self.spinner.stop()
        self.stop.set_visible(False)
        if not ok and message:
            self.raw += f"\n\n*{message}*"
            self._render()
        self.desk.set_sensitive(bool(self.assistant.session_id))

    def _render(self):
        self.text.set_markup(to_markup(self.raw) if self.raw.strip() else "")

    def _follow_up(self, entry):
        question = entry.get_text().strip()
        if not question:
            return
        entry.set_text("")
        self.assistant.start(question, heading=self.heading.get_text(),
                             resume=self.assistant.session_id, question=question)


class Assistant:
    def __init__(self, window):
        self.window = window
        self.available = claude.available()
        self.panel = Panel(self)
        self.current: claude.Ask | None = None
        self.session_id: str | None = None

    # -- running a request --------------------------------------------------
    def start(self, prompt: str, *, files=(), heading="Claude", on_done=None,
              resume=None, question="", show=True):
        self.cancel()
        if show:
            self.panel.begin(heading, question)
            self.window.split.set_show_sidebar(True)

        def text(chunk):
            if show:
                GLib.idle_add(lambda: self.panel.add(chunk) or False)

        def done(ok, result, session):
            def finish():
                if session:
                    self.session_id = session
                if show:
                    self.panel.end(ok, "" if ok else str(result))
                if on_done:
                    on_done(ok, result)
                return False
            GLib.idle_add(finish)

        self.current = claude.Ask(prompt, files=list(files), on_text=text,
                                  on_done=done, resume=resume).start()

    def cancel(self):
        if self.current is not None:
            self.current.cancel()
            self.current = None

    def continue_in_desk(self):
        if not claude.continue_in_desk(self.session_id):
            self.window.toast("Claude Desk is not beside this app")

    # -- the three things -----------------------------------------------------
    def explain(self, card: cards.Card):
        places = self.window.library.places
        prompt = (
            "Explain this flashcard so that it sticks: why the answer is what it is, "
            "and one hook to remember it by. Short — it is read in the middle of a "
            "review.\n\n"
            f"Deck: {card.deck}\n"
            f"Context: {' › '.join(card.context) or '—'}\n"
            f"Front: {card.front}\nBack: {card.back}\n\n"
            f"It is written in {card.path}, line {card.line}.")
        self.start(prompt, files=[places.home / card.path], heading="Explain",
                   question=card.front[:120])

    def ask_dialog(self):
        if not self.available:
            self.window.toast("Claude Code is not installed on this machine")
            return
        dialog = Adw.AlertDialog(heading="Ask Claude",
                                 body="About your cards, a note, anything. The answer "
                                      "appears at the side.")
        entry = Gtk.Entry(placeholder_text="What do you want to know?")
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ask", "Ask")
        dialog.set_response_appearance("ask", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ask")
        dialog.set_close_response("cancel")

        def go(question):
            files = []
            activity = self.window.activity
            session = getattr(activity, "session", None)
            item = session.current if session is not None else None
            if item is not None:
                files.append(self.window.library.places.home / item.card.path)
                question += f"\n\n(I am reviewing the card “{item.card.front}” → " \
                            f"“{item.card.back}”.)"
            self.start(question, files=files, heading="Claude",
                       question=question.split("\n\n")[0])

        def answered(_dialog, response):
            if response == "ask" and entry.get_text().strip():
                go(entry.get_text().strip())

        def activated(_entry):
            dialog.force_close()
            answered(dialog, "ask")

        entry.connect("activate", activated)
        dialog.connect("response", answered)
        dialog.present(self.window.toasts)
        GLib.idle_add(lambda: entry.grab_focus() and False)

    def choose_note_for_cards(self):
        if not self.available:
            self.window.toast("Claude Code is not installed on this machine")
            return
        dialog = Gtk.FileDialog(title="Make cards from a note")
        notes = Gtk.FileFilter()
        notes.set_name("Notes")
        for pattern in ("*.md", "*.markdown"):
            notes.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(notes)
        dialog.set_filters(filters)
        studies = self.window.library.places.studies
        if studies.is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(str(studies)))

        def chosen(source, result):
            try:
                gfile = source.open_finish(result)
            except GLib.Error:
                return
            if gfile and gfile.get_path():
                self.make_cards(Path(gfile.get_path()))

        dialog.open(self.window._parent_window(), None, chosen)

    def make_cards(self, note: Path):
        rel = self.window.library.places.relative(note)

        def done(ok, result):
            if not ok:
                return
            found = proposals(str(result), rel)
            if not found:
                self.window.toast("Claude proposed no cards that read as cards")
                return
            self._approve(note, found)

        self.start(CARDS_PROMPT, files=[note], heading=f"Cards for {note.stem}",
                   on_done=done, question=f"Cards for {note.name}")

    def _approve(self, note: Path, found: list[str]):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        checks = []
        for proposal in found:
            check = Gtk.CheckButton(active=True)
            label = widgets.label(to_markup(proposal), markup=True)
            check.set_child(label)
            box.append(check)
            checks.append((check, proposal))
        scroller = Gtk.ScrolledWindow(propagate_natural_height=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_max_content_height(420)
        scroller.set_child(box)
        dialog = Adw.AlertDialog(
            heading=f"{len(found)} cards for {note.stem}",
            body="Untick any you do not want. The rest are added to the end of the "
                 "note, under “Cards”.")
        dialog.set_extra_child(scroller)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add to the note")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")

        def answered(_dialog, response):
            if response != "add":
                return
            chosen = [proposal for check, proposal in checks if check.get_active()]
            if not chosen:
                return
            try:
                append_cards(note, chosen)
            except OSError as exc:
                widgets.error_toast(self.window.toasts, exc)
                return
            self.window.toast(f"{len(chosen)} card{'s' if len(chosen) != 1 else ''} "
                              f"added to {note.stem}")
            self.window.refresh()

        dialog.connect("response", answered)
        dialog.present(self.window.toasts)
