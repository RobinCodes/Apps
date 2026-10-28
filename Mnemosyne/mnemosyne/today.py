"""Today: what is due, what is new, what is out on paper, and the day's drills.

The first page, and deliberately a short one. Three numbers and one button,
because the whole of the method on an ordinary day is "do today's reviews",
and a page that offered ten things to do first would be a page that made the
reviews the eleventh. Below them: the sheets printed today and not typed back
yet, and the drills already done today, with their scores.

When nothing is due it says so, and says when the next ones are — calm
rather than congratulatory, because an empty queue is the normal state of a
system that is working, not an achievement.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from . import cards, dates, widgets  # noqa: E402
from .library import study_day  # noqa: E402
from .stats import due_ahead  # noqa: E402
from .trainers import DrillLog, load_drills  # noqa: E402


class TodayPage(Gtk.Box):
    name = "today"
    title = "Today"
    icon = "appointment-soon-symbolic"

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.stack = Gtk.Stack(vexpand=True)
        self.append(self.stack)

        self.missing = Adw.StatusPage(
            icon_name="folder-symbolic", title="No brain on this machine",
            description="Mnemosyne learns from the notes in the second brain and "
                        "writes every answer into it. Clone it to ~/SecondBrain — "
                        "or run `order sync` — and come back.")
        self.stack.add_named(self.missing, "missing")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                       margin_top=18, margin_bottom=24, margin_start=14, margin_end=14)
        self.day = widgets.label(css=("day-title",))
        body.append(self.day)

        numbers = Gtk.Box(spacing=6, homogeneous=True)
        self.due = widgets.BigNumber("due")
        self.new = widgets.BigNumber("new")
        self.paper = widgets.BigNumber("on paper")
        for widget in (self.due, self.new, self.paper):
            numbers.append(widget)
        body.append(numbers)

        self.calm = widgets.label(css=("calm",), xalign=0.5)
        body.append(self.calm)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.start = widgets.pill("Start reviewing", tooltip="Ctrl+Enter")
        self.start.set_action_name("win.study-today")
        buttons.append(self.start)
        self.print_button = widgets.pill("Print the sheet", css="", tooltip="Ctrl+P")
        self.print_button.set_action_name("win.print-sheet")
        buttons.append(self.print_button)
        body.append(buttons)

        self.sheets_group = Adw.PreferencesGroup(
            title="On paper",
            description="Printed today and not typed back yet. Their cards are left "
                        "out of the screen's queue until they are.")
        body.append(self.sheets_group)

        self.drills_group = Adw.PreferencesGroup(title="Today's drills")
        body.append(self.drills_group)

        self.stack.add_named(widgets.scrolled(body, 640), "today")
        self.stack.add_named(Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True),
                             "loading")
        self.stack.set_visible_child_name("loading")

    def reload(self):
        library = self.window.library
        if not library.exists():
            self.stack.set_visible_child_name("missing")
            return
        now = cards.now()
        day = study_day(now)
        self.day.set_text(dates.spoken(day))
        counts = library.counts(now)
        self.due.set(counts["due"])
        self.new.set(counts["new"])
        self.paper.set(counts["paper"])
        total = counts["due"] + counts["new"]
        self.start.set_sensitive(total > 0)
        self.print_button.set_sensitive(total > 0)
        if total:
            minutes = max(1, round(counts["due"] * 8 / 60 + counts["new"] * 20 / 60))
            self.calm.set_text(f"About {minutes} minute{'s' if minutes != 1 else ''}.")
        else:
            ahead = due_ahead(library, now)
            tomorrow = ahead[1] if len(ahead) > 1 else 0
            if not library.deck.items:
                self.calm.set_text("No cards yet. Write one in a note as Front :: Back, "
                                   "and it is here the next time the notes are read.")
            elif counts["paper"]:
                self.calm.set_text("The rest of today is on paper.")
            else:
                self.calm.set_text(f"Nothing due. Tomorrow: {tomorrow}.")
        self._sheets(now)
        self._drills(now)
        self.stack.set_visible_child_name("today")

    def _sheets(self, now):
        widgets.clear(self.sheets_group)
        sheets = self.window.library.open_sheets(now)
        self.sheets_group.set_visible(bool(sheets))
        for sheet in sheets:
            row = Adw.ActionRow(title=f"Sheet {sheet.id}",
                                subtitle=f"{len(sheet.rows)} cards · printed "
                                         f"{sheet.made:%H:%M}", activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, s=sheet: self.window.pages["paper"].enter(s))
            widgets.group_add(self.sheets_group, row)

    def _drills(self, now):
        widgets.clear(self.drills_group)
        library = self.window.library
        log = DrillLog(library.places.learning / "drills", library.machine)
        start = datetime.combine(study_day(now), datetime.min.time(), now.tzinfo) \
            + timedelta(hours=4)
        done = log.since(start)
        titles = {d.id: d.title for d in load_drills(library.places)}
        if not done:
            self.drills_group.set_description("None yet today.")
            row = Adw.ActionRow(title="Open the trainers", activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda *_: self.window.show_page("trainers"))
            widgets.group_add(self.drills_group, row)
            return
        self.drills_group.set_description("")
        for result in reversed(done):
            subtitle = f"{result.score:.0%} of {result.n}"
            if result.ms:
                subtitle += f" · {result.ms / 1000:.1f} s each"
            subtitle += f" · {result.when:%H:%M}"
            title = titles.get(result.drill) or result.extra.get("title") or result.drill
            widgets.group_add(self.drills_group,
                              Adw.ActionRow(title=widgets.esc(title), subtitle=subtitle))
