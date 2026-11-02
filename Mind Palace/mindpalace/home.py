"""Home: today at a glance. A native room, which keeps the same contract as any other.

The numbers come from `today.gather`, on a worker, because the cards are a scan
of every note. Home draws what it has, and asks again whenever it comes back
into view if the last answer is more than a minute old. A card with nothing to
say is not drawn: an empty inbox is not news, and neither is a question list
nobody has started.

The note index is brought up to date once, separately, after the first drawing:
on a brain of tens of thousands of notes that can take minutes, and nobody should
wait for it to find out what is due.
"""

from __future__ import annotations

import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from . import brain, jobs, today as todays, widgets  # noqa: E402

STALE_AFTER = 60          # seconds before coming back into view reads again
MNEMOSYNE = "Mnemosyne"

# Spelled out rather than strftime's: GTK sets the locale, and a date in
# Hungarian in the middle of an English window reads as a mistake.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


def _gather():
    return todays.gather(notes=brain.Notes.shared())


def _title_and_path(item) -> tuple[str, Path | None]:
    """A question or an event, from whichever shape its module hands it over in."""
    if isinstance(item, str):
        return item, None
    if isinstance(item, (tuple, list)) and item:
        title = str(item[0])
        path = item[1] if len(item) > 1 else None
    else:
        title = str(getattr(item, "title", item))
        path = getattr(item, "path", None)
    return title, Path(path) if path else None


class HomeRoom:
    def __init__(self, host, gather=None, refresh_notes=None):
        self.host = host
        self.gather = gather or _gather
        self.refresh_notes = refresh_notes or (lambda: brain.Notes.shared().refresh())
        self.today = None
        self._read_at = 0.0
        self._reading = False
        self._index_asked = False

        self.day = widgets.label("", "home-day")
        self.sync = widgets.label("", "home-sync", "dim-label", wrap=True)
        self.spinner = Gtk.Spinner(halign=Gtk.Align.START)
        self.cards = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                                 min_children_per_line=1, max_children_per_line=2,
                                 column_spacing=10, row_spacing=10, homogeneous=False)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       css_classes=["home-page"])
        page.append(self.day)
        page.append(self.sync)
        page.append(self.spinner)
        page.append(self.cards)
        clamp = Adw.Clamp(maximum_size=820, child=page)
        self.widget = Gtk.ScrolledWindow(child=clamp, vexpand=True, hexpand=True,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER)

    # -- the contract ----------------------------------------------------

    def shown(self) -> None:
        if time.monotonic() - self._read_at > STALE_AFTER:
            self.refresh()

    def open(self, _path) -> bool:
        return False

    def save_state(self) -> None:
        pass

    def can_close(self) -> bool:
        return True

    # -- reading ---------------------------------------------------------

    def refresh(self) -> None:
        if self._reading:
            return
        self._reading = True
        if self.today is None:
            self.spinner.set_visible(True)
            self.spinner.start()

        def done(today):
            self._reading = False
            self._read_at = time.monotonic()
            self.spinner.stop()
            self.spinner.set_visible(False)
            self.today = today
            self.draw(today)
            self._bring_the_index_up_to_date()

        def failed(exc):
            self._reading = False
            self._read_at = time.monotonic()
            self.spinner.stop()
            self.spinner.set_visible(False)
            self.sync.set_text(f"Today could not be read: {exc}")

        jobs.run(self.gather, done, failed)

    def _bring_the_index_up_to_date(self) -> None:
        """Once a session, after Home has something to show, and silently if it fails."""
        if self._index_asked:
            return
        self._index_asked = True

        def done(changed):
            if changed:
                self.refresh()

        jobs.run(self.refresh_notes, done, lambda _exc: None)

    # -- drawing ---------------------------------------------------------

    def draw(self, today) -> None:
        day = today.day
        self.day.set_text(f"{WEEKDAYS[day.weekday()]}, {day.day} {MONTHS[day.month - 1]}")
        self._draw_sync(today.sync)
        widgets.clear(self.cards)
        for card in (self._cards_card(today), self._inbox_card(today),
                     self._drill_card(today), self._questions_card(today),
                     self._on_this_day_card(today), self._recent_card(today)):
            if card is not None:
                child = Gtk.FlowBoxChild(child=card, focusable=False)
                self.cards.append(child)

    def _draw_sync(self, sync) -> None:
        self.sync.remove_css_class("warning")
        if sync.when is None:
            self.sync.set_text("Not synced on this machine yet")
        elif sync.failures:
            self.sync.add_css_class("warning")
            count = "1 repository" if sync.failures == 1 else f"{sync.failures} repositories"
            self.sync.set_text(f"{count} failed at the last sync, "
                               f"{todays.age(sync.when)} ago")
        else:
            ago = todays.age(sync.when)
            self.sync.set_text("Synced just now" if ago == "just now"
                               else f"Synced {ago} ago")

    @staticmethod
    def _card(title: str) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       css_classes=["card", "home-card"], hexpand=True)
        card.append(widgets.label(title, "home-card-title"))
        return card

    def _go(self, title: str):
        return lambda: self.host.show(title)

    def _open(self, path):
        return lambda: self.host.open(str(path))

    def _cards_card(self, today) -> Gtk.Box:
        card = self._card("Cards")
        if today.due is None:
            card.append(widgets.label(f"Could not be read: {today.cards_problem}",
                                      "dim-label", wrap=True))
            return card
        numbers = Gtk.Box(spacing=16)
        for value, unit in ((today.due, "due"), (today.new, "new today")):
            one = Gtk.Box(spacing=6)
            one.append(widgets.label(str(value), "home-number"))
            words = widgets.label(unit, "home-unit")
            words.set_valign(Gtk.Align.BASELINE)
            one.append(words)
            numbers.append(one)
        card.append(numbers)
        if today.due or today.new:
            button = Gtk.Button(label="Review", halign=Gtk.Align.START, css_classes=["pill"])
            button.connect("clicked", lambda _b: self.host.show(MNEMOSYNE))
            card.append(button)
        else:
            card.append(widgets.label("Nothing to review today.", "dim-label"))
        return card

    def _inbox_card(self, today) -> Gtk.Box | None:
        items = today.inbox
        if not items:
            return None
        card = self._card("Inbox")
        waiting = widgets.label(f"{len(items)} waiting", "home-number")
        card.append(waiting)
        try:
            oldest = todays.age(items[0].stat().st_mtime)
            card.append(widgets.label("It arrived just now." if oldest == "just now"
                                      else f"The oldest has waited {oldest}.", "dim-label"))
        except OSError:
            pass
        for item in items[:3]:
            card.append(widgets.line_button(item.stem, str(item), self._open(item)))
        if len(items) > 1:
            folder = items[0].parent
            button = Gtk.Button(label="Open the inbox", halign=Gtk.Align.START,
                                css_classes=["pill"])
            button.connect("clicked", lambda _b: self.host.open(str(folder)))
            card.append(button)
        return card

    def _drill_card(self, today) -> Gtk.Box | None:
        drill = today.drill
        if drill is None:
            return None
        card = self._card("Today's drill")
        card.append(widgets.label(drill.title, wrap=True))
        facts = " · ".join(part for part in (
            drill.trainer, f"{drill.minutes} min" if drill.minutes else "",
            f"module {drill.module}" if drill.module else "") if part)
        if facts:
            card.append(widgets.label(facts, "dim-label"))
        button = Gtk.Button(label="To the trainers", halign=Gtk.Align.START,
                            css_classes=["pill"])
        button.connect("clicked", lambda _b: self.host.show(MNEMOSYNE))
        card.append(button)
        return card

    def _questions_card(self, today) -> Gtk.Box | None:
        if not today.questions:
            return None
        card = self._card("Questions to ask again")
        for item in today.questions[:4]:
            title, path = _title_and_path(item)
            if path is not None:
                card.append(widgets.line_button(title, str(path), self._open(path)))
            else:
                card.append(widgets.label(title, wrap=True))
        return card

    def _on_this_day_card(self, today) -> Gtk.Box | None:
        if not today.on_this_day:
            return None
        card = self._card("On this day")
        for item in today.on_this_day[:5]:
            title, _path = _title_and_path(item)
            card.append(widgets.label(title, wrap=True))
        return card

    def _recent_card(self, today) -> Gtk.Box | None:
        if not today.recent:
            return None
        card = self._card("Recent notes")
        for note in today.recent:
            card.append(widgets.line_button(note.title, note.rel, self._open(note.path)))
        return card
