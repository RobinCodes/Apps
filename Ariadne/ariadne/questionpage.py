"""A question's page: the words, when it was asked, every time it was asked again, and its threads.

Every edit is written to the file a moment after typing stops, the way a
paper notebook keeps what is written in it: there is no Save, because a
question is not a document that is finished.
"""

from __future__ import annotations

from datetime import date, timedelta

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from . import claude, inline  # noqa: E402
from .sidebar import STATUS_WORDS, day_label  # noqa: E402
from .threads import STATUSES  # noqa: E402
from .widgets import clear, heading  # noqa: E402

SAVE_DELAY_MS = 600


class QuestionPage(Gtk.Box):
    def __init__(self, view):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.view = view
        self.question = None
        self._timer = 0
        self._filling = False

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                       margin_start=18, margin_end=18, margin_top=10, margin_bottom=24)
        scroller.set_child(Adw.Clamp(child=body, maximum_size=760, tightening_threshold=560))
        self.append(scroller)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.list_button = Gtk.Button(icon_name="sidebar-show-symbolic", css_classes=["flat"],
                                      tooltip_text="The list (F9)", valign=Gtk.Align.START)
        self.list_button.connect("clicked", lambda _b: view.show_list(True))
        top.append(self.list_button)
        self.title = Gtk.Entry(hexpand=True, css_classes=["ariadne-big-title"],
                               placeholder_text="The question")
        self.title.connect("changed", lambda _e: self._edited())
        top.append(self.title)
        body.append(top)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        meta.add_css_class("ariadne-meta")
        self.status = Gtk.DropDown.new_from_strings([STATUS_WORDS[s] for s in STATUSES])
        self.status.set_tooltip_text("Open: being asked. Living: asked for good. "
                                     "Answered. Let go.")
        self.status.connect("notify::selected", lambda *_: self._edited())
        meta.append(self.status)
        self.revisit = Gtk.MenuButton(tooltip_text="When to ask it again")
        self.revisit.set_popover(self._revisit_popover())
        meta.append(self.revisit)
        self.asked = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        meta.append(self.asked)
        body.append(meta)

        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                 top_margin=4, bottom_margin=4, css_classes=["ariadne-text"])
        self.text.get_buffer().connect("changed", lambda _b: self._edited())
        body.append(self.text)

        body.append(heading("Revisits"))
        self.revisits = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        body.append(self.revisits)
        again = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.revisit_entry = Gtk.Entry(hexpand=True,
                                       placeholder_text="What do you think now? (Ctrl+R)")
        self.revisit_entry.connect("activate", lambda _e: self.add_revisit())
        again.append(self.revisit_entry)
        add = Gtk.Button(label="Add")
        add.connect("clicked", lambda _b: self.add_revisit())
        again.append(add)
        body.append(again)

        body.append(heading("Threads"))
        self.threads = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                   css_classes=["boxed-list"])
        self.threads.connect("row-activated", lambda _l, row: view.open_item(row._thread))
        body.append(self.threads)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        start = Gtk.Button(label="New Thread")
        start.connect("clicked", lambda _b: view.new_thread(self.question))
        buttons.append(start)
        self.link_button = Gtk.MenuButton(label="Link a Thread")
        self.link_button.set_popover(Gtk.Popover())
        self.link_button.connect("notify::active", self._fill_link_menu)
        buttons.append(self.link_button)
        if claude.available():
            think = Gtk.Button(label="Think with Claude", tooltip_text="Ctrl+Shift+A")
            think.connect("clicked", lambda _b: view.think())
            buttons.append(think)
        body.append(buttons)

    # -- showing -------------------------------------------------------------

    def show(self, question):
        self.flush()
        self.question = question
        self._filling = True
        try:
            self.title.set_text(question.title)
            self.status.set_selected(STATUSES.index(question.status))
            self.text.get_buffer().set_text(question.text)
            self._fill_meta()
            self._fill_revisits()
            self.fill_threads()
        finally:
            self._filling = False
        # Not the title: focus there selects it whole, and the next key would
        # replace a question that took a moment to word.
        self.title.select_region(0, 0)
        self.text.grab_focus()

    def _fill_meta(self):
        q = self.question
        day = q.asked_date
        when = q.asked[11:16] if len(q.asked) >= 16 else ""
        self.asked.set_text(f"asked {day_label(day, short=False)}" + (f", {when}" if when else "")
                            if day else "")
        self.revisit.set_label(f"Ask again {day_label(q.revisit)}" if q.revisit else "No revisit")
        if q.due():
            self.revisit.add_css_class("suggested-action")
        else:
            self.revisit.remove_css_class("suggested-action")

    def _fill_revisits(self):
        clear(self.revisits)
        found = self.question.revisits
        if not found:
            self.revisits.append(Gtk.Label(label="Not asked again yet.", xalign=0,
                                           css_classes=["ariadne-meta"]))
        for day, words in found:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.append(Gtk.Label(label=day, valign=Gtk.Align.START,
                                 css_classes=["ariadne-revisit-date"]))
            said = Gtk.Label(xalign=0, wrap=True, hexpand=True, use_markup=True,
                             wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True)
            said.set_markup(inline.markup(words))
            said.connect("activate-link", self._link)
            row.append(said)
            self.revisits.append(row)

    def _link(self, _label, uri):
        target, wiki = inline.parse_href(uri)
        self.view.follow_link(target, wiki)
        return True

    def fill_threads(self):
        clear(self.threads)
        found = self.view.library.threads_for(self.question) if self.question else []
        if not found:
            self.threads.append(Gtk.ListBoxRow(
                child=Gtk.Label(label="No thread answers it yet.", xalign=0, margin_top=8,
                                margin_bottom=8, margin_start=10,
                                css_classes=["ariadne-meta"]),
                activatable=False))
        for thread in found:
            count = len(thread.nodes)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=6, margin_bottom=6,
                          margin_start=10, margin_end=10)
            box.append(Gtk.Label(label=thread.title, xalign=0, css_classes=["ariadne-row-title"]))
            box.append(Gtk.Label(label=f"{count} {'thought' if count == 1 else 'thoughts'}",
                                 xalign=0, css_classes=["ariadne-row-detail"]))
            row = Gtk.ListBoxRow(child=box)
            row._thread = thread
            self.threads.append(row)

    def _fill_link_menu(self, button, _param):
        if not button.get_active() or self.question is None:
            return
        linked = set(map(id, self.view.library.threads_for(self.question)))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        others = [t for t in self.view.library.threads if id(t) not in linked]
        if not others:
            box.append(Gtk.Label(label="Every thread already answers it, or there are none.",
                                 margin_start=8, margin_end=8, margin_top=6, margin_bottom=6))
        for thread in others:
            item = Gtk.Button(label=thread.title, css_classes=["flat"])
            item.get_child().set_xalign(0)
            item.connect("clicked", lambda _b, t=thread: (button.popdown(),
                                                          self.view.link(t, self.question)))
            box.append(item)
        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=320,
                                      min_content_width=260)
        scroller.set_child(box)
        button.get_popover().set_child(scroller)

    def _revisit_popover(self):
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        self.calendar = Gtk.Calendar()
        self.calendar.connect("day-selected", lambda c: self._set_revisit(
            date(c.get_date().get_year(), c.get_date().get_month(),
                 c.get_date().get_day_of_month())))
        box.append(self.calendar)
        quick = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, homogeneous=True)
        for label, days in (("A month", 30), ("Three months", 91), ("A year", 365)):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _b, d=days: (
                popover.popdown(), self._set_revisit(date.today() + timedelta(days=d))))
            quick.append(button)
        box.append(quick)
        never = Gtk.Button(label="No revisit", css_classes=["flat"])
        never.connect("clicked", lambda _b: (popover.popdown(), self._set_revisit(None)))
        box.append(never)
        popover.set_child(box)
        return popover

    # -- editing -------------------------------------------------------------

    def _set_revisit(self, day):
        if self.question is None or self._filling:
            return
        self.question.revisit = day
        self.view.save(self.question)
        self._fill_meta()
        self.view.refresh_list()

    def _edited(self):
        if self._filling or self.question is None:
            return
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(SAVE_DELAY_MS, self._save_tick)

    def _save_tick(self):
        self._timer = 0
        self.flush()
        return GLib.SOURCE_REMOVE

    def flush(self):
        """Whatever has been typed and not yet written, written now."""
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        q = self.question
        if q is None:
            return
        buffer = self.text.get_buffer()
        title = " ".join(self.title.get_text().split()) or q.title
        old_title = q.title
        q.title = title
        q.status = STATUSES[self.status.get_selected()]
        q.text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        if q.changed:
            self.view.save(q)
            if old_title != title:
                self.view.retitled(q, old_title)
            self._fill_meta()
            self.view.refresh_list()

    def add_revisit(self):
        words = " ".join(self.revisit_entry.get_text().split())
        if not words or self.question is None:
            self.revisit_entry.grab_focus()
            return
        self.flush()
        self.question.add_revisit(words)
        self.view.save(self.question)
        self.revisit_entry.set_text("")
        self._fill_revisits()

    def focus_revisit(self):
        self.revisit_entry.grab_focus()
