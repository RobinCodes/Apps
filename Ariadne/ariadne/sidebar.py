"""The list at the side: every question kept, and every thread.

Questions come in the order they want attention: the ones whose revisit date
has come first, with a small badge — a nudge, not an alarm, because a question
kept for years owes nobody an answer this week — then the open and living
ones, newest first, then the answered and the let go, quieter.
"""

from __future__ import annotations

from datetime import date

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, Pango  # noqa: E402

from . import brain  # noqa: E402

STATUS_WORDS = {"open": "open", "living": "living", "answered": "answered",
                "let-go": "let go"}

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


def day_label(day, short=True) -> str:
    """`27 Oct 2026` — in English whatever the machine's locale, like the rest of the app."""
    month = MONTHS[day.month - 1]
    return f"{day.day} {month[:3] if short else month} {day.year}"


def asked_label(question) -> str:
    day = question.asked_date
    return f"asked {day_label(day)}" if day else "asked"


class Sidebar(Gtk.Box):
    """`on_open(item)` for a row; `on_keep()` and `on_new_thread()` for the buttons."""

    def __init__(self, on_open, on_keep, on_new_thread):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("ariadne-sidebar")
        self.on_open = on_open
        self.questions = []
        self.threads = []
        self.current = None

        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top.add_css_class("ariadne-sidebar-top")
        self.search = Gtk.SearchEntry(placeholder_text="Find a question or thread")
        self.search.connect("search-changed", lambda *_: self.fill())
        top.append(self.search)
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        switcher = Gtk.StackSwitcher(stack=self.stack, hexpand=True)
        top.append(switcher)
        self.append(top)

        self.question_list = self._list()
        self.thread_list = self._list()
        self.stack.add_titled(self._scrolled(self.question_list), "questions", "Questions")
        self.stack.add_titled(self._scrolled(self.thread_list), "threads", "Threads")
        self.append(self.stack)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                         margin_start=8, margin_end=8, margin_top=6, margin_bottom=8)
        keep = Gtk.Button(label="Keep a Question", hexpand=True,
                          css_classes=["suggested-action"], tooltip_text="Ctrl+N")
        keep.connect("clicked", lambda _b: on_keep())
        bottom.append(keep)
        thread = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New thread (Ctrl+Shift+N)")
        thread.connect("clicked", lambda _b: on_new_thread())
        bottom.append(thread)
        self.append(bottom)

    def _list(self):
        listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        listing.add_css_class("navigation-sidebar")
        listing.connect("row-activated", lambda _l, row: self.on_open(row._item))
        return listing

    def _scrolled(self, child):
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(child)
        return scroller

    def show(self, questions, threads, current=None):
        self.questions, self.threads, self.current = questions, threads, current
        self.fill()

    def page(self) -> str:
        return self.stack.get_visible_child_name() or "questions"

    def set_page(self, name: str):
        if name in ("questions", "threads"):
            self.stack.set_visible_child_name(name)

    def fill(self):
        query = brain.key(self.search.get_text())
        today = date.today()
        for listing in (self.question_list, self.thread_list):
            while (row := listing.get_first_child()) is not None:
                listing.remove(row)

        for question in self.questions:
            if query and query not in brain.key(question.title + " " + question.text):
                continue
            quiet = question.status in ("answered", "let-go")
            detail = f"{STATUS_WORDS[question.status]} · {asked_label(question)}"
            badge = "ask again" if question.due(today) else None
            self._row(self.question_list, question, question.title, detail, quiet, badge)

        for thread in self.threads:
            if query and query not in brain.key(thread.title + " " + " ".join(
                    n.title for n in thread.nodes)):
                continue
            count = len(thread.nodes)
            detail = f"{count} {'thought' if count == 1 else 'thoughts'}"
            if thread.answers:
                detail += f" · answers {len(thread.answers)}"
            self._row(self.thread_list, thread, thread.title, detail, False, None)

        for listing, empty_text in (
                (self.question_list, "No questions kept yet. Ctrl+N keeps one."),
                (self.thread_list, "No threads yet.")):
            listing.set_placeholder(Gtk.Label(
                label="Nothing matches." if query else empty_text, wrap=True,
                justify=Gtk.Justification.CENTER, css_classes=["ariadne-row-detail"],
                margin_top=24))

    def _row(self, listing, item, title, detail, quiet, badge):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_top=4, margin_bottom=4)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = Gtk.Label(label=title or "Untitled", xalign=0, wrap=True, lines=3,
                          ellipsize=Pango.EllipsizeMode.END, hexpand=True,
                          wrap_mode=Pango.WrapMode.WORD_CHAR,
                          css_classes=["ariadne-row-title"])
        top.append(label)
        if badge:
            top.append(Gtk.Label(label=badge, valign=Gtk.Align.START,
                                 css_classes=["ariadne-due"]))
        box.append(top)
        box.append(Gtk.Label(label=detail, xalign=0, css_classes=["ariadne-row-detail"]))
        row = Gtk.ListBoxRow(child=box)
        if quiet:
            row.add_css_class("ariadne-row-quiet")
        row._item = item
        listing.append(row)
        if self.current is not None and item.path == self.current.path:
            listing.select_row(row)
