"""Decks: every deck there is, how much of each is due, and a way into one.

A deck is not made here. It is the folder a note sits in (`Studies/Biology`),
or what the note's front matter calls it (`deck: The Mentalist`), or a file
of its own in `learning/decks/` — SYSTEM.md §7 — so this page is a view of
the notes, not a place to manage anything. Each row says what is due, what is
new and how much is learned, and is the way to study only that deck, go over
all of it, or print it as cut-out cards.

A deck can be given a **test date** from its menu (HANDBOOK.md §1.7, T−14):
until the day its new cards come first, the day's ration rises to meet them all
three days before it, and the row counts down.

Due decks come first; the rest by name. The filter at the top matches any
part of the name, without accents, because the decks are half Hungarian.
"""

from __future__ import annotations

from datetime import date

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import brain, cards, widgets  # noqa: E402


class DecksPage(Gtk.Box):
    name = "decks"
    title = "Decks"
    icon = "accessories-dictionary-symbolic"

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.tests = {}

        self.search = Gtk.SearchEntry(placeholder_text="Filter decks",
                                      margin_top=10, margin_start=14, margin_end=14)
        self.search.connect("search-changed", lambda *_: self._fill())
        bar = widgets.clamp(self.search, 640)
        self.append(bar)

        self.list = Gtk.ListBox(css_classes=["boxed-list"],
                                selection_mode=Gtk.SelectionMode.NONE)
        self.list.set_placeholder(widgets.label("No decks yet. A note with a card in it "
                                                "is a deck.", css=("dim-label",),
                                                xalign=0.5))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=10,
                       margin_bottom=24, margin_start=14, margin_end=14)
        body.append(self.list)
        self.append(widgets.scrolled(body, 640))

        # Row menus act on the deck named in their target.
        group = Gio.SimpleActionGroup()
        for name, handler in (("study", self.study), ("everything", self.everything),
                              ("print", self.print_deck), ("test", self.ask_test_date),
                              ("untest", self.clear_test_date)):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, value, fn=handler: fn(value.get_string()))
            group.add_action(action)
        self.insert_action_group("deck", group)

    def reload(self):
        self._fill()

    def _fill(self):
        self.list.remove_all()
        library = self.window.library
        if not library.loaded:
            return
        wanted = brain.key(self.search.get_text())
        now = cards.now()
        self.tests = library.tests(now)
        # One pass over the day's queue and one over the cards, rather than a
        # queue per deck: a deck's "new today" is its share of the day's one
        # ration, and with a hundred Digitalization subjects a queue apiece
        # was a second of the school laptop's time on every visit.
        today: dict[str, dict] = {}
        for item in library.today(now):
            counts = today.setdefault(item.card.deck, {"due": 0, "new": 0})
            counts["new" if item.new else "due"] += 1
        totals: dict[str, dict] = {}
        for item in library.deck.items.values():
            counts = totals.setdefault(item.card.deck, {"cards": 0, "learned": 0})
            counts["cards"] += 1
            counts["learned"] += not item.new
        rows = []
        for deck in library.deck.decks():
            if wanted and wanted not in brain.key(deck):
                continue
            rows.append((deck, totals.get(deck, {"cards": 0, "learned": 0}),
                         today.get(deck, {"due": 0, "new": 0})))
        # Decks with something to do first, the most due at the top.
        rows.sort(key=lambda r: (r[2]["due"] + r[2]["new"] == 0, -r[2]["due"], r[0].lower()))
        for deck, counts, today in rows:
            self.list.append(self._row(deck, counts, today))

    def _row(self, deck, counts, today):
        *parents, leaf = deck.split("/")
        bits = []
        if today["due"]:
            bits.append(f"{today['due']} due")
        if today["new"]:
            bits.append(f"{today['new']} new today")
        bits.append(f"{counts['learned']} of {counts['cards']} learned")
        test = self.tests.get(deck)
        if test:
            left = (test - cards.now().date()).days
            bits.insert(0, "test today" if left <= 0 else
                        "test tomorrow" if left == 1 else f"test in {left} days")
        subtitle = " · ".join(bits)
        if parents:
            subtitle = "/".join(parents) + " — " + subtitle
        row = Adw.ActionRow(title=widgets.esc(leaf), subtitle=widgets.esc(subtitle),
                            activatable=True)
        row.connect("activated", lambda *_: self.study(deck))

        menu = Gio.Menu()
        entries = [("Study what is due", "deck.study"),
                   ("Go over every card", "deck.everything"),
                   ("Print as flashcards", "deck.print"),
                   ("Set a test date…", "deck.test")]
        if test:
            entries.append(("Clear the test date", "deck.untest"))
        for label, action in entries:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(action, GLib.Variant.new_string(deck))
            menu.append_item(item)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu,
                              css_classes=["flat"], valign=Gtk.Align.CENTER,
                              tooltip_text="More")
        row.add_suffix(more)
        return row

    # -- actions ------------------------------------------------------------
    def study(self, deck):
        items = self.window.library.today(deck=deck)
        if items:
            self.window.study(items, deck.rsplit("/", 1)[-1])
            return
        widgets.confirm(self.window.toasts, "Nothing due here",
                        f"Every card in {deck} is learned for today. Go over all of "
                        "them anyway?", "Go over them", lambda: self.everything(deck))

    def everything(self, deck):
        library = self.window.library
        items = [i for i in library.deck.items.values()
                 if i.card.deck == deck or i.card.deck.startswith(deck + "/")]
        items.sort(key=lambda i: (i.card.path, i.card.line))
        self.window.study(items, deck.rsplit("/", 1)[-1])

    def ask_test_date(self, deck):
        """A calendar, and the deck's test on the day picked."""
        library = self.window.library
        calendar = Gtk.Calendar()
        current = library.learning["tests"].get(deck)
        if current:
            calendar.select_day(GLib.DateTime.new_local(current.year, current.month,
                                                        current.day, 12, 0, 0))
        dialog = Adw.AlertDialog(
            heading=f"Test in {deck.rsplit('/', 1)[-1]}",
            body="Until then this deck's new cards come first, and all of them are "
                 "met three days before the test.")
        dialog.set_extra_child(calendar)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("set", "Set the date")
        dialog.set_response_appearance("set", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("set")
        dialog.set_close_response("cancel")

        def answered(_dialog, response):
            if response != "set":
                return
            picked = calendar.get_date()
            day = date(picked.get_year(), picked.get_month(), picked.get_day_of_month())
            if day < cards.now().date():
                self.window.toast("That day has been")
                return
            if library.set_test(deck, day):
                self.window.refresh()
            else:
                self.window.toast("Could not write config/learning.yaml")

        dialog.connect("response", answered)
        dialog.present(self.window.toasts)

    def clear_test_date(self, deck):
        if self.window.library.set_test(deck, None):
            self.window.refresh()

    def print_deck(self, deck):
        self.window.pages["paper"].print_flashcards(deck)
