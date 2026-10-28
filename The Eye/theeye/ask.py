"""Ask Claude about this picture: a side panel the answer streams into.

Whatever a viewer cannot do with a photograph — say what the plant in it is,
read the whiteboard behind the teacher, turn a diagram into the cards it
should be, find the mistake in a worked problem — Claude Code can be asked
to, about exactly the picture on screen. A frame of a video is asked about
as the frame: it is saved beside nothing, in a scratch folder, and that is
what Claude is shown.

It is read-only. The request goes to `claude -p` in its default permission
mode, run in the folder the picture is in, so Claude can read it and what is
beside it and changes nothing; an answer that turns into a piece of work
carries on in Claude Desk, which has the conversation. Hermione's panel works
the same way, so the two read alike.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk  # noqa: E402

from . import claude, widgets  # noqa: E402

WIDTH = 340


class AskPanel(Gtk.Box):
    """The panel's contents; the window puts it on glass and reveals it."""

    __gtype_name__ = "TheEyeAskPanel"

    def __init__(self, toast, on_close):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                         margin_start=14, margin_end=14, margin_top=10,
                         margin_bottom=14)
        self._toast = toast
        self._on_close = on_close
        self.path = ""
        self.folder = ""
        self.session = None
        self.request = None
        self._scratch = ""
        self.set_size_request(WIDTH, -1)

        self.append(widgets.row(
            widgets.label("Ask Claude", "heading", xalign=0.0, hexpand=True),
            widgets.icon_button("window-close-symbolic", "Close  (Esc)",
                                on_click=self.close),
            spacing=2))
        self.subject = widgets.label("", "caption", xalign=0.0, wrap=True)
        self.append(self.subject)

        self.entry = Gtk.Entry(placeholder_text="What should Claude look at, or explain?")
        self.entry.connect("activate", lambda _e: self.ask())
        self.append(self.entry)

        self.ask_button = Gtk.Button(label="Ask")
        self.ask_button.add_css_class("pill")
        self.ask_button.add_css_class("suggested-action")
        self.ask_button.connect("clicked", lambda _b: self.ask())
        self.stop_button = Gtk.Button(label="Stop", sensitive=False)
        self.stop_button.add_css_class("pill")
        self.stop_button.connect("clicked", lambda _b: self.stop())
        self.append(widgets.row(self.ask_button, self.stop_button, spacing=6))

        self.answer = widgets.label("", xalign=0.0, wrap=True, selectable=True,
                                    yalign=0.0)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           vexpand=True)
        self.scroller.set_child(self.answer)
        self.append(self.scroller)

        self.state = widgets.label("", "caption", xalign=0.0, wrap=True)
        self.append(self.state)

        self.copy_button = Gtk.Button(label="Copy", sensitive=False)
        self.copy_button.add_css_class("pill")
        self.copy_button.connect("clicked", lambda _b: widgets.copy_text(
            self, self.answer.get_text(), "Answer copied"))
        self.desk_button = Gtk.Button(label="Continue in Claude Desk", sensitive=False)
        self.desk_button.add_css_class("pill")
        self.desk_button.connect("clicked", lambda _b: self.continue_in_desk())
        self.append(widgets.row(self.copy_button, self.desk_button, spacing=6))

    # -- opening -----------------------------------------------------------

    def about(self, path: str, frame=None) -> bool:
        """Point the panel at a picture, or at a frame (a texture) of a video.

        False, with nothing changed, when Claude Code is not installed.
        """
        if not claude.available():
            self._toast("Claude Code is not installed on this machine — install it "
                        "and sign in once, and every app here can ask it")
            return False
        self._forget_scratch()
        if frame is not None:
            from . import export

            self._scratch = tempfile.mkdtemp(prefix="the-eye-ask-")
            shown = os.path.join(self._scratch, "frame.png")
            export.save_texture(frame, shown)
            self.path, self.folder = shown, self._scratch
            what = f"this frame of {os.path.basename(path)}"
        else:
            self.path, self.folder = path, os.path.dirname(path)
            what = os.path.basename(path)
        self.subject.set_text(f"About {what}. Claude looks at it; it changes nothing.")
        GLib.idle_add(lambda: (self.entry.grab_focus(), GLib.SOURCE_REMOVE)[1])
        return True

    def close(self):
        self.stop()
        self._on_close()

    def _forget_scratch(self):
        if self._scratch and self.request is None:
            shutil.rmtree(self._scratch, ignore_errors=True)
            self._scratch = ""

    # -- asking ------------------------------------------------------------

    def ask(self):
        text = self.entry.get_text().strip()
        if not text or not self.path or self.request is not None:
            return
        self.answer.set_text("")
        self.session = None
        self.state.set_text("Claude is looking…")
        self.ask_button.set_sensitive(False)
        self.stop_button.set_sensitive(True)
        self.copy_button.set_sensitive(False)
        self.desk_button.set_sensitive(False)
        self.request = claude.Ask(
            text, files=[self.path], cwd=self.folder,
            on_text=lambda chunk: GLib.idle_add(self._more, chunk),
            on_done=lambda ok, result, session: GLib.idle_add(
                self._done, ok, result, session)).start()

    def stop(self):
        if self.request is not None:
            self.request.cancel()

    def _more(self, chunk: str):
        self.answer.set_text(self.answer.get_text() + chunk)
        self.state.set_text("Claude is writing…")
        adjustment = self.scroller.get_vadjustment()
        GLib.idle_add(lambda: (adjustment.set_value(adjustment.get_upper()),
                               GLib.SOURCE_REMOVE)[1])
        return GLib.SOURCE_REMOVE

    def _done(self, ok: bool, result: str, session):
        self.request = None
        self.session = session
        self.ask_button.set_sensitive(True)
        self.stop_button.set_sensitive(False)
        if ok:
            if not self.answer.get_text().strip():
                self.answer.set_text(result or "")
            self.state.set_text("")
        elif result == "cancelled":
            self.state.set_text("Stopped.")
        else:
            self.state.set_text(f"Claude could not answer: {(result or '')[:300]}")
        self.copy_button.set_sensitive(bool(self.answer.get_text().strip()))
        self.desk_button.set_sensitive(bool(session))
        return GLib.SOURCE_REMOVE

    def continue_in_desk(self):
        if not claude.continue_in_desk(self.session, cwd=self.folder or None):
            self._toast("Claude Desk is not in this checkout")
