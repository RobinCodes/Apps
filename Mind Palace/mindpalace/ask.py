"""Ask Claude: one panel, from every room, about what that room is showing.

`claude.Ask` runs Claude Code with the brain as its folder and SYSTEM.md as its
briefing, and the answer streams into the panel. It is read-only, as every
app's Ask is: it can read anything and change nothing, and an answer that turns
into a piece of work carries on in Claude Desk, which has the conversation.

The panel floats on the window's overlay, above whichever room is showing,
because a side panel packed beside the rooms would push the window's minimum
width past a tiling column.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import claude, widgets  # noqa: E402

WIDTH = 330


class AskPanel(Gtk.Box):
    def __init__(self, palace, command=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         css_classes=["palace-ask"], visible=False,
                         halign=Gtk.Align.END, valign=Gtk.Align.FILL,
                         margin_top=56, margin_bottom=12, margin_end=12)
        self.set_size_request(WIDTH, -1)
        self.palace = palace
        self.command = command            # a stand-in for `claude`, in the tests
        self.files: list[str] = []
        self.request = None
        self.session_id = None

        top = Gtk.Box(spacing=6)
        heading = widgets.label("Ask Claude", "heading")
        heading.set_hexpand(True)
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close  (Escape)",
                           css_classes=["flat"])
        close.connect("clicked", lambda _b: self.close())
        top.append(heading)
        top.append(close)
        self.append(top)

        self.about = widgets.label("", "palace-ask-about", wrap=True)
        self.append(self.about)

        self.prompt = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                   css_classes=["palace-ask-prompt"])
        self.prompt.set_size_request(-1, 66)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.prompt.add_controller(keys)
        self.append(self.prompt)

        row = Gtk.Box(spacing=6)
        self.send = Gtk.Button(label="Ask", css_classes=["pill", "suggested-action"],
                               tooltip_text="Ask  (Ctrl+Enter)")
        self.send.connect("clicked", lambda _b: self.submit())
        self.stop = Gtk.Button(label="Stop", css_classes=["pill"], visible=False)
        self.stop.connect("clicked", lambda _b: self.cancel())
        self.desk = Gtk.Button(label="Continue in Claude Desk", css_classes=["flat"],
                               visible=False, hexpand=True, halign=Gtk.Align.END)
        self.desk.connect("clicked", lambda _b: self._continue_in_desk())
        row.append(self.send)
        row.append(self.stop)
        row.append(self.desk)
        self.append(row)

        self.answer = widgets.label("", "palace-ask-answer", wrap=True, selectable=True)
        self.answer.set_valign(Gtk.Align.START)
        scroller = Gtk.ScrolledWindow(child=self.answer, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(scroller)

    # -- opening and closing ---------------------------------------------

    def open(self, files=()) -> None:
        """Show the panel about these files, with the focus in the question."""
        self.set_files(files)
        self.set_visible(True)
        if not self._can_ask():
            self.answer.set_text("Claude Code is not installed on this machine, so "
                                 "there is nobody to ask.")
        self.prompt.grab_focus()

    def close(self) -> None:
        self.set_visible(False)
        self.palace.ask_closed()

    def set_files(self, files) -> None:
        self.files = [str(f) for f in files]
        if not self.files:
            self.about.set_text("About nothing in particular: the brain is open to it.")
        elif len(self.files) == 1:
            self.about.set_text(f"About {os.path.basename(self.files[0])}")
        else:
            self.about.set_text(f"About {os.path.basename(self.files[0])} "
                                f"and {len(self.files) - 1} more")

    def _can_ask(self) -> bool:
        return self.command is not None or claude.available()

    # -- asking ----------------------------------------------------------

    def ask(self, prompt: str, files=()) -> None:
        """A question from a room: shown, and asked at once."""
        self.open(files)
        self.prompt.get_buffer().set_text(prompt)
        self.submit()

    def submit(self) -> None:
        buffer = self.prompt.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()
        if not text or not self._can_ask():
            return
        self.cancel()
        self.answer.set_text("")
        self.session_id = None
        self.desk.set_visible(False)
        self.send.set_visible(False)
        self.stop.set_visible(True)

        def on_text(chunk):
            GLib.idle_add(self._add_text, chunk)

        def on_done(ok, result, session):
            GLib.idle_add(self._finished, request, ok, result, session)

        request = claude.Ask(text, files=self.files, on_text=on_text, on_done=on_done,
                             command=self.command)
        self.request = request
        request.start()

    def cancel(self) -> None:
        request, self.request = self.request, None
        if request is not None:
            request.cancel()
        self.stop.set_visible(False)
        self.send.set_visible(True)

    def _add_text(self, chunk: str) -> bool:
        self.answer.set_text(self.answer.get_text() + chunk)
        return GLib.SOURCE_REMOVE

    def _finished(self, request, ok, result, session) -> bool:
        if request is not self.request:
            return GLib.SOURCE_REMOVE     # stopped, or a newer question has started
        self.request = None
        self.stop.set_visible(False)
        self.send.set_visible(True)
        self.session_id = session
        self.desk.set_visible(bool(session))
        if not ok:
            shown = self.answer.get_text()
            why = str(result).strip()
            self.answer.set_text(f"{shown}\n\n{why}".strip())
        elif not self.answer.get_text() and result:
            self.answer.set_text(str(result))
        return GLib.SOURCE_REMOVE

    def _continue_in_desk(self) -> None:
        if not claude.continue_in_desk(self.session_id):
            self.palace.toast("Claude Desk is not beside this app")

    def _on_key(self, _controller, keyval, _code, state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) \
                and state & Gdk.ModifierType.CONTROL_MASK:
            self.submit()
            return True
        return False
