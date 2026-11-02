"""The two things Ariadne asks you to type into: a question, and a thought."""

from __future__ import annotations

from datetime import datetime, timezone

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402


def _text_view(text="", height=120):
    view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8,
                        left_margin=10, right_margin=10, accepts_tab=False)
    view.get_buffer().set_text(text)
    view.add_css_class("ariadne-text")
    scroller = Gtk.ScrolledWindow(min_content_height=height, max_content_height=320,
                                  propagate_natural_height=True, hexpand=True)
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_child(view)
    return view, scroller


def _buffer_text(view) -> str:
    buffer = view.get_buffer()
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)


class QuickQuestion(Adw.Dialog):
    """Keep a question: type it, press Enter. The moment it was asked is now.

    "Now" is when the dialog opened, not when Enter was pressed — a question
    takes a minute to word, and the moment that matters is the one it struck.
    `on_keep(title, text, asked)` is called on Enter or Keep.
    """

    def __init__(self, on_keep, title=""):
        super().__init__(title="Keep a Question", content_width=520)
        self.add_css_class("ariadne-quick")
        self.asked = datetime.now(timezone.utc).astimezone()
        self.on_keep = on_keep

        self.keep_button = Gtk.Button(label="Keep", css_classes=["suggested-action"],
                                      sensitive=bool(title.strip()))
        self.keep_button.connect("clicked", lambda _b: self.keep())
        self.entry = Gtk.Entry(placeholder_text="What are you wondering?", text=title,
                               hexpand=True)
        self.entry.connect("activate", lambda _e: self.keep())
        self.entry.connect("changed", lambda _e: self.keep_button.set_sensitive(
            bool(self.entry.get_text().strip())))
        self.text, scroller = _text_view(height=90)

        stamp = Gtk.Label(label=f"Asked {self.asked.day} {self.asked:%B %Y, %H:%M}",
                          xalign=0, css_classes=["ariadne-meta"])
        hint = Gtk.Label(label="What prompted it — optional, and worth a line.",
                         xalign=0, css_classes=["ariadne-meta"])

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       margin_start=18, margin_end=18, margin_top=6, margin_bottom=18)
        body.append(self.entry)
        body.append(stamp)
        body.append(hint)
        body.append(scroller)

        header = Adw.HeaderBar()
        header.pack_end(self.keep_button)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(body)
        self.set_child(view)
        self.set_focus(self.entry)

    def keep(self):
        title = " ".join(self.entry.get_text().split())
        if not title:
            return
        if self.get_root() is not None:
            self.close()
        self.on_keep(title, _buffer_text(self.text).strip(), self.asked)


class ThoughtEditor(Adw.Dialog):
    """A thought's title and text. Saved when it closes; Delete takes it away."""

    def __init__(self, node, on_done, on_delete=None):
        super().__init__(title="A Thought", content_width=500)
        self.node = node
        self.on_done = on_done
        self.on_delete = on_delete
        self._deleting = False

        self.title_entry = Gtk.Entry(text=node.title, placeholder_text="The thought, in a line",
                                     hexpand=True)
        self.text, scroller = _text_view(node.text, height=160)
        self.title_entry.connect("activate", lambda _e: self.text.grab_focus())
        hint = Gtk.Label(label="Markdown: **bold**, *emphasis*, `code`, and [[links]] "
                               "to notes, questions and threads.",
                         xalign=0, wrap=True, css_classes=["ariadne-meta"])

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       margin_start=18, margin_end=18, margin_top=6, margin_bottom=18)
        body.append(self.title_entry)
        body.append(scroller)
        body.append(hint)

        header = Adw.HeaderBar()
        if on_delete is not None:
            delete = Gtk.Button(label="Delete", css_classes=["destructive-action"])
            delete.connect("clicked", lambda _b: self._delete())
            header.pack_start(delete)
        done = Gtk.Button(label="Done", css_classes=["suggested-action"])
        done.connect("clicked", lambda _b: self.close())
        header.pack_end(done)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(body)
        self.set_child(view)
        self.set_focus(self.title_entry)
        self.connect("closed", self._closed)

    def _delete(self):
        self._deleting = True
        self.close()
        GLib.idle_add(lambda: (self.on_delete(self.node.id), False)[1])

    def _closed(self, _dialog):
        if self._deleting:
            return
        title = " ".join(self.title_entry.get_text().split())
        text = _buffer_text(self.text).strip("\n")
        self.on_done(self.node.id, title, text)
