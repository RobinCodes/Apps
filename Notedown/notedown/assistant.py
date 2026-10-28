"""Claude, beside the note: whatever this editor cannot do, it can ask for.

The request goes to Claude Code (`claude.py`, the house's), with the note
named so Claude reads it itself and the brain as its working folder, and the
answer streams back into this panel. The answer is Markdown, so it is drawn
by the same page the note is — headings, lists, `[[links]]` that open, cards
drawn as cards — rather than as a block of raw text.

Two requests are common enough to be actions of their own:

* **Make cards** asks for the note's cards in the Order's format (SYSTEM.md
  §7). What comes back is read with `cards.py`, the ones the note already has
  are dropped, and the rest are shown as the cards they will be. Nothing is
  written until the button that says how many is pressed, and then it is one
  edit, so Ctrl+Z takes it back out.
* **Continue in Claude Desk** opens the conversation there, for when an
  answer turns into a piece of work.

Read-only throughout: an answer may read anything in the brain but changes
nothing, because Claude Code runs in its default permission mode.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import claude, markdown, order, render, widgets  # noqa: E402

RENDER_DEBOUNCE_MS = 120
INLINE_LIMIT = 20_000       # characters of unsaved note sent with a request


class Assistant(Gtk.Box):
    """The panel. `window` is the Notedown window it serves, held explicitly.

    `command` replaces the `claude` binary; the suite sets a stand-in so that
    nothing it does reaches a real model.
    """

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("assistant")
        self.window = window
        self.command = None
        self.kind = "answer"
        self.text = ""
        self.proposal = ""
        self.proposed = 0
        self.session: str | None = None
        self.running = False
        self._ask: claude.Ask | None = None
        self._about = None
        self._render_timer = 0

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("assistant-header")
        header.append(Gtk.Label(label="Claude", xalign=0, hexpand=True,
                                css_classes=["heading"]))
        close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"],
                           tooltip_text="Close (Ctrl+Shift+A)")
        close.connect("clicked", lambda _b: window.show_claude(False))
        header.append(close)
        self.append(header)

        ask = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ask.add_css_class("assistant-ask")
        self.entry = Gtk.Entry(placeholder_text="Ask about this note…", hexpand=True)
        self.entry.connect("activate", lambda _e: self.ask())
        ask.append(self.entry)
        self.about = Gtk.Label(label="", xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE,
                               css_classes=["assistant-about"])
        ask.append(self.about)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.ask_button = Gtk.Button(label="Ask", css_classes=["suggested-action"])
        self.ask_button.connect("clicked", lambda _b: self.ask())
        buttons.append(self.ask_button)
        self.cards_button = Gtk.Button(label="Make cards",
                                       tooltip_text="Claude proposes cards for this note; "
                                                    "nothing is added until you say so")
        self.cards_button.connect("clicked", lambda _b: self.make_cards())
        buttons.append(self.cards_button)
        self.stop_button = Gtk.Button(label="Stop", visible=False)
        self.stop_button.connect("clicked", lambda _b: self.stop())
        buttons.append(self.stop_button)
        ask.append(buttons)
        self.append(ask)

        self.page = render.Preview(
            on_link=lambda url: window.open_url(self._about or window.active, url))
        self.page.set_reading_width(0)
        self.page.add_css_class("assistant-page")
        self.append(self.page)

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        footer.add_css_class("assistant-footer")
        self.status = Gtk.Label(label="", xalign=0, wrap=True,
                                wrap_mode=Pango.WrapMode.WORD_CHAR,
                                css_classes=["assistant-status"])
        footer.append(self.status)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.add_button = Gtk.Button(label="Add cards", css_classes=["suggested-action"],
                                     visible=False)
        self.add_button.connect("clicked", lambda _b: self.add_cards())
        actions.append(self.add_button)
        self.discard_button = widgets.small_button("Discard")
        self.discard_button.connect("clicked", lambda _b: self.clear())
        actions.append(self.discard_button)
        self.copy_button = widgets.small_button("Copy", tooltip="Copy the answer")
        self.copy_button.connect("clicked", lambda _b: widgets.copy_text(
            self, self.text, "Answer copied"))
        actions.append(self.copy_button)
        self.insert_button = widgets.small_button(
            "Insert", tooltip="Put the answer in the note, at the cursor")
        self.insert_button.connect("clicked", lambda _b: self.insert_answer())
        actions.append(self.insert_button)
        self.desk_button = widgets.small_button(
            "Claude Desk", tooltip="Carry this conversation on in Claude Desk")
        self.desk_button.connect("clicked", lambda _b: self.continue_in_desk())
        actions.append(self.desk_button)
        footer.append(actions)
        self.append(footer)
        self._update_buttons()

    # -- what it is about ---------------------------------------------------

    def focus_request(self):
        self.update_about()
        self.entry.grab_focus()

    def update_about(self):
        document = self.window.active
        if document is None:
            self.about.set_text("")
            return
        selected = document.editor.selection_length()
        self.about.set_text(f"About the selection in {document.name}" if selected
                            else f"About {document.name}")

    def _prompt(self, request, document):
        parts = [request]
        selected = document.editor.selected_text()
        if selected.strip():
            parts.append("The part of the note this is about:\n\n" + selected)
        # Claude reads the file itself; what is on screen but not on disk has
        # to travel with the request, or the answer is about an older note.
        # Within reason: the request is a command line, and Windows stops
        # one at 32,767 characters.
        if not document.path or document.modified:
            state = "not saved yet" if not document.path else "with changes not saved yet"
            text = document.text()
            if len(text) > INLINE_LIMIT:
                text = text[:INLINE_LIMIT] + "\n\n[… the rest is not shown here]"
            parts.append(f"The note as it is in the editor ({state}):\n\n" + text)
        return "\n\n".join(parts)

    # -- asking -------------------------------------------------------------

    def ask(self, request: str | None = None):
        request = (self.entry.get_text() if request is None else request).strip()
        document = self.window.active
        if not request or document is None:
            self.entry.grab_focus()
            return
        self._start(self._prompt(request, document), document, "answer")

    def make_cards(self):
        document = self.window.active
        if document is None or not document.text().strip():
            self._say("There is nothing in this note to make cards of yet.")
            return
        self._start(self._prompt(order.CARDS_PROMPT, document), document, "cards")

    def _start(self, prompt, document, kind):
        self.stop(quiet=True)
        self.kind = kind
        self.text = self.proposal = ""
        self.proposed = 0
        self.session = None
        self._about = document
        files = [document.path] if document.path else []
        ask = claude.Ask(prompt, files=files, command=self.command)
        # Both arrive on Claude's thread; the widgets are only touched here.
        ask.on_text = lambda chunk: GLib.idle_add(self._chunk, ask, chunk)
        ask.on_done = lambda ok, result, session: GLib.idle_add(
            self._done, ask, ok, result, session)
        self._ask = ask
        self.running = True
        self._say("Claude is reading the note…" if kind == "answer"
                  else "Claude is reading the note for cards…")
        self._render_now()
        self._update_buttons()
        ask.start()

    def stop(self, quiet=False):
        if self._ask is not None:
            self._ask.cancel()
            self._ask = None
        if self.running and not quiet:
            self._say("Stopped.")
        self.running = False
        self._update_buttons()

    def _chunk(self, ask, chunk):
        if ask is not self._ask:
            return GLib.SOURCE_REMOVE          # an answer to a question since withdrawn
        self.text += chunk
        if self.kind == "answer":
            self._say("Claude is writing…")
            self._schedule_render()
        return GLib.SOURCE_REMOVE

    def _done(self, ask, ok, result, session):
        if ask is not self._ask:
            return GLib.SOURCE_REMOVE
        self._ask = None
        self.running = False
        self.session = session
        if not ok:
            self._say("Stopped." if result == "cancelled"
                      else f"Claude could not answer: {result}")
        elif self.kind == "cards":
            self.text = result or self.text
            existing = self._about.text() if self._about is not None else ""
            self.proposal, self.proposed = order.proposed_cards(self.text, existing)
            if self.proposed:
                where = self._about.name if self._about is not None else "the note"
                self._say(f"{self.proposed} {'card' if self.proposed == 1 else 'cards'}, "
                          f"to be added at the end of {where}. Nothing is written "
                          f"until you add them.")
            else:
                self._say("Claude found nothing in this note that it does not "
                          "already have a card for.")
        else:
            self.text = result or self.text
            self._say("")
        self._render_now()
        self._update_buttons()
        return GLib.SOURCE_REMOVE

    # -- the answer ---------------------------------------------------------

    def _schedule_render(self):
        if not self._render_timer:
            self._render_timer = GLib.timeout_add(RENDER_DEBOUNCE_MS, self._render_tick)

    def _render_tick(self):
        self._render_timer = 0
        self._render_now()
        return GLib.SOURCE_REMOVE

    def _render_now(self):
        if self._render_timer:
            GLib.source_remove(self._render_timer)
            self._render_timer = 0
        shown = self.proposal if self.kind == "cards" else self.text
        parsed = markdown.parse(shown or "")
        near = self._about.path if self._about is not None else None
        self.window.resolve_links(parsed, near)
        self.page.render(parsed, os.path.dirname(near) if near else "")

    def _say(self, text):
        self.status.set_text(text)
        self.status.set_visible(bool(text))

    def _update_buttons(self):
        idle = not self.running
        answer = idle and self.kind == "answer" and bool(self.text.strip())
        cards = idle and self.kind == "cards" and self.proposed > 0
        self.stop_button.set_visible(self.running)
        self.ask_button.set_sensitive(idle)
        self.cards_button.set_sensitive(idle)
        self.copy_button.set_visible(answer)
        self.insert_button.set_visible(answer)
        self.add_button.set_visible(cards)
        self.add_button.set_label(f"Add {self.proposed} {'card' if self.proposed == 1 else 'cards'}")
        self.discard_button.set_visible(cards)
        self.desk_button.set_visible(idle and bool(self.session))

    def clear(self):
        self.text = self.proposal = ""
        self.proposed = 0
        self.kind = "answer"
        self._say("")
        self._render_now()
        self._update_buttons()

    def insert_answer(self):
        document = self.window.active
        if document is not None and self.text.strip():
            document.editor.insert_snippet(self.text.strip() + "\n")

    def add_cards(self):
        """The proposal, at the end of the note it was made for, as one edit."""
        document = self._about
        if document is None or document not in self.window.documents:
            self._say("That note has been closed; nothing was added.")
            return
        text = document.text()
        lead = ("" if not text or text.endswith("\n\n")
                else "\n" if text.endswith("\n") else "\n\n")
        buf = document.editor.buffer
        buf.begin_user_action()
        offset = buf.get_char_count() + len(lead)
        buf.insert(buf.get_end_iter(), lead + self.proposal + "\n")
        buf.end_user_action()
        document.editor.goto_offset(offset)
        count = self.proposed
        self.clear()
        self._say(f"Added {count} {'card' if count == 1 else 'cards'} to {document.name}. "
                  f"Ctrl+Z in the note takes them out again.")

    def continue_in_desk(self):
        if not claude.continue_in_desk(self.session):
            self._say("Claude Desk is not installed beside the apps, so the "
                      "conversation cannot be carried on there.")
