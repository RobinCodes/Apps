"""Paper, in the window: print today's sheet, type its ticks back, print a deck.

The typing back is the part that has to be fast, because it is the part that
gets skipped when it is not. It is one text field: each digit typed is the
tick on the next row — 1 Again, 2 Hard, 3 Good, 4 Easy, and 0, a dash or a
space for a row left blank — and the list underneath follows along, showing
the row being filled and what each digit said. Backspace goes back a row.
Forty cards is forty keystrokes and an Enter, which is what SYSTEM.md's "under
a minute" means.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from . import cards, cli, fsrs, jobs, paper, widgets  # noqa: E402
from .cardtext import to_markup  # noqa: E402


class PaperPage(Adw.PreferencesPage):
    name = "paper"
    title = "Paper"
    icon = "document-print-symbolic"

    def __init__(self, window):
        super().__init__()
        self.window = window

        self.today_group = Adw.PreferencesGroup(
            title="Today's sheet",
            description="Fold it down the middle: questions on the left, answers on "
                        "the right, four boxes a row to tick.")
        self.today_row = Adw.ActionRow(title="Today")
        self.print_button = Gtk.Button(label="Print", css_classes=["suggested-action"],
                                       valign=Gtk.Align.CENTER, tooltip_text="Ctrl+P")
        self.print_button.connect("clicked", lambda *_: self.print_today())
        self.today_row.add_suffix(self.print_button)
        self.today_group.add(self.today_row)
        include = Adw.SwitchRow(title="Include new cards",
                                subtitle="Meet today's new cards on paper too",
                                active=bool(window.config["sheet_include_new"]))
        include.connect("notify::active", self._include_toggled)
        self.today_group.add(include)
        self.add(self.today_group)

        self.open_group = Adw.PreferencesGroup(
            title="Enter results",
            description="A sheet that has been answered: type its ticks back, a digit a row.")
        self.add(self.open_group)

        self.flash_group = Adw.PreferencesGroup(
            title="Flashcards",
            description="A deck, ten cards to a page. Print double-sided, flipping on "
                        "the long edge, and each back lands behind its front.")
        self.deck_row = Adw.ComboRow(title="Deck")
        flash_button = Gtk.Button(label="Print", valign=Gtk.Align.CENTER)
        flash_button.connect("clicked", lambda *_: self._print_chosen_deck())
        self.deck_row.add_suffix(flash_button)
        self.flash_group.add(self.deck_row)
        self.add(self.flash_group)

        self.done_group = Adw.PreferencesGroup(title="Typed back")
        self.add(self.done_group)
        self._decks: list[str] = []

    def _include_toggled(self, row, _param):
        self.window.config["sheet_include_new"] = row.get_active()
        self.window.config.save()
        self.reload()

    def reload(self):
        library = self.window.library
        if not library.exists():
            self.today_row.set_title("No brain on this machine")
            self.today_row.set_subtitle("Sheets are recorded in it, so they can be typed "
                                        "back anywhere.")
            self.print_button.set_sensitive(False)
            return
        counts = library.counts()
        include = bool(self.window.config["sheet_include_new"])
        total = counts["due"] + (counts["new"] if include else 0)
        self.print_button.set_sensitive(total > 0)
        self.today_row.set_title(f"{total} card{'s' if total != 1 else ''}"
                                 if total else "Nothing to print")
        parts = [f"{counts['due']} due"]
        if include:
            parts.append(f"{counts['new']} new")
        if counts["paper"]:
            parts.append(f"{counts['paper']} already on paper")
        self.today_row.set_subtitle(" · ".join(parts))

        widgets.clear(self.open_group)
        sheets = library.sheets.all()
        waiting = [s for s in sheets if s.entered is None]
        for sheet in waiting:
            row = Adw.ActionRow(title=f"Sheet {sheet.id}",
                                subtitle=f"{sheet.title} · {len(sheet.rows)} cards",
                                activatable=True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, s=sheet: self.enter(s))
            widgets.group_add(self.open_group, row)
        if not waiting:
            widgets.group_add(self.open_group,
                              Adw.ActionRow(title="No sheet is waiting", sensitive=False))

        widgets.clear(self.done_group)
        entered = [s for s in sheets if s.entered is not None][:6]
        self.done_group.set_visible(bool(entered))
        for sheet in entered:
            counts = paper.tally(sheet.ratings or [])
            bits = [f"{counts[r]} {fsrs.RATING_NAMES[r]}" for r in fsrs.RATINGS if counts[r]]
            widgets.group_add(self.done_group, Adw.ActionRow(
                title=f"Sheet {sheet.id}",
                subtitle=f"{sheet.title} · " + (" · ".join(bits) or "nothing ticked")))

        self._decks = library.deck.decks() if library.loaded else []
        self.deck_row.set_model(Gtk.StringList.new(self._decks))
        self.flash_group.set_sensitive(bool(self._decks))

    # -- printing -------------------------------------------------------------
    def print_today(self):
        library = self.window.library
        if not library.exists():
            self.window.toast("There is no brain on this machine")
            return
        if not library.loaded:
            self.window.refresh(then=self.print_today)
            return
        config = self.window.config
        sheet = library.print_today(include_new=bool(config["sheet_include_new"]))
        if sheet is None:
            self.window.toast("Nothing is due today")
            return
        size = float(config["sheet_font_size"])

        def draw():
            return paper.render_sheet(sheet, cli.sheet_path(sheet.id), size,
                                      library.places.home)

        def drawn(path):
            self.window.toast(f"Sheet {sheet.id}: {len(sheet.rows)} cards")
            self.window.launch_file(path)
            self.window.reload_pages()

        jobs.run(draw, drawn, lambda exc: widgets.error_toast(self, exc))

    def _print_chosen_deck(self):
        index = self.deck_row.get_selected()
        if 0 <= index < len(self._decks):
            self.print_flashcards(self._decks[index])

    def print_flashcards(self, deck):
        library = self.window.library
        chosen = sorted((i.card for i in library.deck.items.values()
                         if (i.card.deck == deck or i.card.deck.startswith(deck + "/"))
                         and i.card.form != "both-r"),
                        key=lambda c: (c.path, c.line))
        if not chosen:
            self.window.toast("That deck has no cards")
            return
        from . import winenv                                # noqa: PLC0415

        safe = "".join(ch if ch.isalnum() or ch in "-_ " else "-" for ch in deck)
        target = f"{winenv.cache_home('mnemosyne')}/flashcards/{safe}.pdf"

        def draw():
            return paper.render_flashcards(deck, chosen, target)

        def drawn(path):
            self.window.toast(f"{len(chosen)} flashcards from {deck}")
            self.window.launch_file(path)

        jobs.run(draw, drawn, lambda exc: widgets.error_toast(self, exc))

    # -- typing back ----------------------------------------------------------
    def enter(self, sheet):
        self.window.open_activity(EntryView(self.window, sheet))


class EntryView(Gtk.Box):
    """A sheet's ticks, typed back a digit a row."""

    def __init__(self, window, sheet: paper.Sheet):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.window = window
        self.sheet = sheet
        self.title = f"Sheet {sheet.id}"
        self._normalising = False

        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                      margin_top=12, margin_start=14, margin_end=14)
        top.append(widgets.label(f"{sheet.title} · {len(sheet.rows)} cards",
                                 css=("heading",)))
        top.append(widgets.label("One digit a row: 1 Again · 2 Hard · 3 Good · 4 Easy. "
                                 "0, a dash or a space for a row left blank. "
                                 "Enter writes them.", css=("dim-label",)))
        self.entry = Gtk.Entry(css_classes=["ticks-entry"], max_length=len(sheet.rows),
                               placeholder_text="3341…", input_purpose=Gtk.InputPurpose.DIGITS)
        self.entry.connect("changed", self._changed)
        self.entry.connect("activate", lambda *_: self.save())
        top.append(self.entry)
        self.tally = widgets.label("", css=("dim-label",))
        top.append(self.tally)
        self.append(widgets.clamp(top, 680))

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                css_classes=["boxed-list"])
        self.rows = []
        for index, row in enumerate(sheet.rows, start=1):
            line = Gtk.Box(spacing=8, css_classes=["tick-row"])
            line.append(Gtk.Label(label=str(index), css_classes=["tick-number"],
                                  xalign=1, valign=Gtk.Align.START))
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            text.append(widgets.label(to_markup(row.front), markup=True, ellipsize=True,
                                      lines=2))
            text.append(widgets.label(to_markup(row.back), markup=True, ellipsize=True,
                                      lines=1, css=("dim-label", "caption")))
            line.append(text)
            value = Gtk.Label(label="·", css_classes=["tick-value"], valign=Gtk.Align.CENTER)
            line.append(value)
            self.list.append(line)
            self.rows.append((line, value))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=10,
                       margin_bottom=10, margin_start=14, margin_end=14)
        body.append(self.list)
        self.scroller = widgets.scrolled(body, 680)
        self.append(self.scroller)

        bottom = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_bottom=10, margin_top=6)
        self.save_button = widgets.pill("Write the answers")
        self.save_button.connect("clicked", lambda *_: self.save())
        bottom.append(self.save_button)
        self.append(bottom)
        self._changed(self.entry)

    def grab_focus(self):
        return self.entry.grab_focus()

    def _changed(self, entry):
        if self._normalising:
            return
        text = entry.get_text()
        ratings = paper.parse_ticks(text, len(self.sheet.rows))
        clean = "".join(str(r) if r else "-" for r in ratings)
        if clean != text:
            # Keep only what means something, so the cursor counts rows.
            self._normalising = True
            entry.set_text(clean)
            entry.set_position(-1)
            self._normalising = False
        for index, (line, value) in enumerate(self.rows):
            rating = ratings[index] if index < len(ratings) else None
            value.set_text(str(rating) if rating else ("–" if index < len(ratings) else "·"))
            for r in fsrs.RATINGS:
                value.remove_css_class(f"r{r}")
            if rating:
                value.add_css_class(f"r{rating}")
            if index == len(ratings):
                line.add_css_class("current")
            else:
                line.remove_css_class("current")
        current = min(len(ratings), len(self.rows) - 1)
        if self.rows:
            self._scroll_to(self.rows[current][0])
        counts = paper.tally(ratings)
        bits = [f"{counts[r]} {fsrs.RATING_NAMES[r]}" for r in fsrs.RATINGS if counts[r]]
        left = len(self.sheet.rows) - len(ratings)
        if left:
            bits.append(f"{left} to go")
        self.tally.set_text(" · ".join(bits) or "Nothing typed yet")
        self.save_button.set_sensitive(any(ratings))

    def _scroll_to(self, widget):
        adjustment = self.scroller.get_vadjustment()
        ok, bounds = widget.compute_bounds(self.scroller.get_child())
        if not ok:
            return
        top, height = bounds.get_y(), bounds.get_height()
        page = adjustment.get_page_size()
        if top < adjustment.get_value():
            adjustment.set_value(max(0, top - 8))
        elif top + height > adjustment.get_value() + page:
            adjustment.set_value(top + height - page + 8)

    def save(self):
        ratings = paper.parse_ticks(self.entry.get_text(), len(self.sheet.rows))
        if not any(ratings):
            return
        ratings += [None] * (len(self.sheet.rows) - len(ratings))
        try:
            written = self.window.library.enter_sheet(self.sheet, ratings, cards.now())
        except OSError as exc:
            widgets.error_toast(self, exc)
            return
        self.window.toast(f"{written} answer{'s' if written != 1 else ''} from paper "
                          f"written to the log")
        self.window.close_activity()

    def handle_key(self, keyval, state) -> bool:
        # The entry has the keys; Ctrl+Enter writes from anywhere.
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            self.save()
            return True
        return False
