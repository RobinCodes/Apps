"""Ask Claude about these: a side panel the answer streams into.

Whatever a file manager cannot do with a selection — summarise a folder of
scans, say which of three drafts is newest in substance, turn a note into
cards — Claude Code can be asked to, about exactly the files selected. The
request is typed into the panel; the answer arrives as it is written.

It is read-only. The request goes to `claude -p` in its default permission
mode, run in the folder the files are in, so Claude can read them and
anything beside them but changes nothing; an answer that turns into a piece
of work carries on in Claude Desk, which has the conversation.

The panel floats over the listing on the window's overlay, like the palette:
a side panel packed beside the list would push the window's minimum width
past a tiling column, and floating is also where the glass is real.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GLib, Gtk  # noqa: E402

from . import claude, dialogs, order  # noqa: E402

WIDTH = 330


class AskPanel(Gtk.Box):
    """One per window; `about(paths, folder)` opens it on a selection."""

    def __init__(self, host):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.host = host
        self.paths: list[str] = []
        self.folder = ""
        self.session: str | None = None
        self.request = None
        self.add_css_class("ask-panel")
        self.set_size_request(WIDTH, -1)
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.FILL)
        self.set_margin_top(66)
        self.set_margin_bottom(14)
        self.set_margin_end(14)
        self.set_visible(False)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label="Ask Claude", xalign=0.0)
        title.add_css_class("preview-title")
        title.set_hexpand(True)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close.add_css_class("pill-flat")
        close.set_tooltip_text("Close  (Escape)")
        close.connect("clicked", lambda _b: self.close())
        top.append(title)
        top.append(close)
        self.append(top)

        self.subject = Gtk.Label(xalign=0.0)
        self.subject.add_css_class("preview-kind")
        self.subject.set_wrap(True)
        self.subject.set_wrap_mode(2)
        self.append(self.subject)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("What should Claude look at or explain?")
        self.entry.connect("activate", lambda _e: self.ask())
        self.append(self.entry)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.ask_button = dialogs._button("Ask", "suggested-action")
        self.ask_button.connect("clicked", lambda _b: self.ask())
        self.stop_button = dialogs._button("Stop")
        self.stop_button.connect("clicked", lambda _b: self.stop())
        self.stop_button.set_sensitive(False)
        buttons.append(self.ask_button)
        buttons.append(self.stop_button)
        self.append(buttons)

        self.answer = Gtk.Label(xalign=0.0, yalign=0.0)
        self.answer.set_wrap(True)
        self.answer.set_wrap_mode(2)
        self.answer.set_selectable(True)
        self.answer.add_css_class("field-value")
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.answer)
        scroller.set_vexpand(True)
        scroller.add_css_class("panel")
        self.answer.set_margin_start(10)
        self.answer.set_margin_end(10)
        self.answer.set_margin_top(8)
        self.answer.set_margin_bottom(8)
        self.scroller = scroller
        self.append(scroller)

        self.state = Gtk.Label(xalign=0.0)
        self.state.add_css_class("where")
        self.state.set_wrap(True)
        self.append(self.state)

        after = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.copy_button = dialogs._button("Copy")
        self.copy_button.connect(
            "clicked", lambda _b: dialogs._to_clipboard(self, self.answer.get_label()))
        self.desk_button = dialogs._button("Continue in Claude Desk")
        self.desk_button.connect("clicked", lambda _b: self.continue_in_desk())
        for button in (self.copy_button, self.desk_button):
            button.set_sensitive(False)
            after.append(button)
        self.append(after)

    # -- opening ------------------------------------------------------------

    def about(self, paths: list[str], folder: str) -> None:
        """Point the panel at a selection — or, with none, at the folder."""
        self.paths = [p for p in paths if p]
        self.folder = folder or (os.path.dirname(self.paths[0]) if self.paths else "")
        if len(self.paths) == 1:
            what = os.path.basename(self.paths[0].rstrip("/\\"))
        elif self.paths:
            what = f"{len(self.paths)} items in {dialogs._short(self.folder)}"
        else:
            what = f"the folder {dialogs._short(self.folder)}"
        self.subject.set_label(f"About {what}. Claude reads them; it changes nothing.")
        self.set_visible(True)
        GLib.idle_add(lambda: (self.entry.grab_focus(), False)[1])

    def close(self) -> None:
        self.stop()
        self.set_visible(False)
        pane = self.host.current_pane()
        if pane is not None:
            pane.view.grab_focus()

    # -- asking -------------------------------------------------------------

    def ask(self) -> None:
        text = self.entry.get_text().strip()
        if not text or self.request is not None:
            return
        self.answer.set_label("")
        self.session = None
        self.state.set_label("Claude is reading…")
        self.ask_button.set_sensitive(False)
        self.stop_button.set_sensitive(True)
        self.copy_button.set_sensitive(False)
        self.desk_button.set_sensitive(False)
        files = self.paths or ([self.folder] if self.folder else [])
        self.request = order.ask(
            text, files=files, cwd=self.folder or None,
            on_text=lambda chunk: GLib.idle_add(self._more, chunk),
            on_done=lambda ok, result, session:
                GLib.idle_add(self._done, ok, result, session))

    def stop(self) -> None:
        if self.request is not None:
            self.request.cancel()

    def _more(self, chunk: str) -> bool:
        self.answer.set_label(self.answer.get_label() + chunk)
        self.state.set_label("Claude is writing…")
        adjustment = self.scroller.get_vadjustment()
        GLib.idle_add(lambda: (adjustment.set_value(adjustment.get_upper()), False)[1])
        return False

    def _done(self, ok: bool, result: str, session: str | None) -> bool:
        self.request = None
        self.session = session
        self.ask_button.set_sensitive(True)
        self.stop_button.set_sensitive(False)
        if ok:
            if not self.answer.get_label().strip():
                self.answer.set_label(result)
            self.state.set_label("")
        elif result == "cancelled":
            self.state.set_label("Stopped.")
        else:
            self.state.set_label(f"Claude could not answer: {result[:300]}")
        self.copy_button.set_sensitive(bool(self.answer.get_label().strip()))
        self.desk_button.set_sensitive(bool(session))
        return False

    def continue_in_desk(self) -> None:
        if not claude.continue_in_desk(self.session, cwd=self.folder or None):
            self.host.toast("Claude Desk is not in this checkout")
