"""Week: the Sunday review's page — what was done, what held, what is slipping.

One chart and some lists. The chart is the reviews on each of the last seven
days, one bar a day in the theme's accent, with the number written over today
and over the busiest day only (a number on every bar is a table pretending to
be a chart); hovering a bar says the rest. Under it the figures HANDBOOK.md
§1.6 asks for — reviews, retention this week and over thirty days against the
aim, new cards met and waiting, the streak — then the tests counting down,
and the lists: the leeches to rewrite, what slipped, each deck, the drills.
Last, which FSRS weights are in use, and a button to fit them to the log.

The printer button in the header puts the week on one page (paper.render_week),
the leeches with their file and line, for rewriting with the sheet beside you.
"""

from __future__ import annotations

from datetime import timedelta

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk, Pango, PangoCairo  # noqa: E402

from . import cards, dates, fit, housestyle, jobs, paper, stats, widgets, winenv  # noqa: E402
from .library import LEECH_LAPSES, study_day  # noqa: E402
from .trainers import DrillLog, load_drills  # noqa: E402


class BarChart(Gtk.DrawingArea):
    """Reviews per day: thin bars with rounded tops, on a recessive baseline."""

    def __init__(self):
        super().__init__(content_height=150, hexpand=True, has_tooltip=True)
        self.days: list[stats.Day] = []
        self.set_draw_func(self._draw)
        self.connect("query-tooltip", self._tooltip)
        self._slots: list[tuple[float, float, stats.Day]] = []

    def set_days(self, days):
        self.days = list(days)
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        self._slots = []
        if not self.days:
            return
        # The bars in the theme's accent, the words in the widget's own ink.
        accent = Gdk.RGBA()
        if not accent.parse(housestyle.current().accent):
            accent.parse("#3584e4")
        fg = self.get_color()
        label_h = 18
        top_pad = 18
        plot_h = max(10, height - label_h - top_pad)
        biggest = max((d.reviews for d in self.days), default=0) or 1
        slot = width / len(self.days)
        bar = min(34.0, slot * 0.5)
        today = self.days[-1].day
        busiest = max(self.days, key=lambda d: d.reviews)

        layout = PangoCairo.create_layout(cr)
        font = Pango.FontDescription.from_string("Sans 8")
        layout.set_font_description(font)

        for index, day in enumerate(self.days):
            x = index * slot + (slot - bar) / 2
            h = plot_h * day.reviews / biggest
            base = top_pad + plot_h
            self._slots.append((index * slot, (index + 1) * slot, day))
            if h > 0:
                radius = min(4.0, bar / 2, h)
                cr.set_source_rgba(accent.red, accent.green, accent.blue, 0.9)
                cr.move_to(x, base)
                cr.line_to(x, base - h + radius)
                cr.arc(x + radius, base - h + radius, radius, 3.1416, 4.7124)
                cr.line_to(x + bar - radius, base - h)
                cr.arc(x + bar - radius, base - h + radius, radius, 4.7124, 6.2832)
                cr.line_to(x + bar, base)
                cr.close_path()
                cr.fill()
            if day.reviews and (day.day == today or day is busiest):
                layout.set_text(str(day.reviews), -1)
                _, logical = layout.get_pixel_extents()
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.85)
                cr.move_to(index * slot + (slot - logical.width) / 2, base - h - logical.height - 2)
                PangoCairo.show_layout(cr, layout)
            layout.set_text(dates.short_weekday(day.day)[:2], -1)
            _, logical = layout.get_pixel_extents()
            alpha = 0.85 if day.day == today else 0.55
            cr.set_source_rgba(fg.red, fg.green, fg.blue, alpha)
            cr.move_to(index * slot + (slot - logical.width) / 2, base + 3)
            PangoCairo.show_layout(cr, layout)
        # The baseline, quiet.
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.18)
        cr.set_line_width(1)
        cr.move_to(0, top_pad + plot_h + 0.5)
        cr.line_to(width, top_pad + plot_h + 0.5)
        cr.stroke()

    def _tooltip(self, _widget, x, _y, _keyboard, tooltip):
        for start, end, day in self._slots:
            if start <= x < end:
                text = (f"{dates.spoken(day.day)}: {day.reviews} "
                        f"review{'s' if day.reviews != 1 else ''}")
                if day.again:
                    text += f", {day.again} Again"
                if day.paper:
                    text += f", {day.paper} on paper"
                if day.ms:
                    text += f", {day.ms / 60000:.0f} min"
                tooltip.set_text(text)
                return True
        return False


class WeekPage(Adw.PreferencesPage):
    name = "week"
    title = "Week"
    icon = "x-office-calendar-symbolic"

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.chart_group = Adw.PreferencesGroup(title="The last seven days")
        self.print_button = Gtk.Button(icon_name="document-print-symbolic",
                                       css_classes=["flat"], valign=Gtk.Align.CENTER,
                                       tooltip_text="Print the week: the figures, the "
                                                    "tests and the leeches, on one page")
        self.print_button.connect("clicked", lambda *_: self.print_week())
        self.chart_group.set_header_suffix(self.print_button)
        self.chart = BarChart()
        self.chart.set_margin_top(6)
        self.chart.set_margin_bottom(6)
        self.chart_group.add(self.chart)
        self.figures = Adw.PreferencesGroup()
        self.tests = Adw.PreferencesGroup(
            title="Tests",
            description="Until the day, a deck's new cards come first, and every one "
                        "is met three days before it. Set one from the Decks page.")
        self.ahead = Adw.PreferencesGroup(title="Coming up")
        self.leeches = Adw.PreferencesGroup(
            title="Leeches",
            description=f"Forgotten {LEECH_LAPSES} times or more after being learned. "
                        "Rewrite them — two facts in one card, a vague front, or "
                        "something never understood (HANDBOOK.md §2.9). Click to open "
                        "the note.")
        self.slipping = Adw.PreferencesGroup(
            title="Slipping",
            description="Known, then forgotten this week. A card that keeps slipping "
                        "is usually asking two things at once — worth rewriting.")
        self.decks = Adw.PreferencesGroup(title="By deck")
        self.schedule = Adw.PreferencesGroup(title="The schedule")
        self.drills = Adw.PreferencesGroup(title="Drills")
        for group in (self.chart_group, self.figures, self.tests, self.ahead,
                      self.leeches, self.slipping, self.decks, self.schedule, self.drills):
            self.add(group)
        self._fitting = False

    def _note_row(self, item, subtitle):
        library = self.window.library
        row = Adw.ActionRow(title=widgets.esc(item.card.front[:120]),
                            subtitle=widgets.esc(subtitle), activatable=True, title_lines=2)
        row.connect("activated", lambda _r, c=item.card: self.window.open_note(
            library.places.home / c.path, c.line))
        row.add_suffix(Gtk.Image(icon_name="document-edit-symbolic"))
        return row

    def reload(self):
        library = self.window.library
        if not library.loaded:
            return
        now = cards.now()
        week = stats.week(library, now)
        self.chart.set_days(week.days)
        today = study_day(now)

        widgets.clear(self.figures)
        aim = float(library.learning["desired_retention"])
        retention = (f"{week.retention:.0%}" if week.retention is not None else "—")
        held, tested = stats.measured(library, now, 30)
        month = f"{held / tested:.0%} of {tested}" if tested else "—"
        rows = [
            ("Reviews", f"{week.reviews}" + (f" · {week.minutes:.0f} min on screen"
                                             if week.minutes >= 1 else "")),
            ("Retention this week", f"{retention} (the aim is {aim:.0%})"),
            ("Retention, 30 days", month),
            ("New cards met", str(week.new_cards)),
            ("New cards waiting", str(stats.waiting(library))),
            ("Streak", f"{week.streak} day{'s' if week.streak != 1 else ''}"),
        ]
        for title, value in rows:
            row = Adw.ActionRow(title=title)
            row.add_suffix(Gtk.Label(label=value, css_classes=["dim-label", "numeric"]))
            widgets.group_add(self.figures, row)

        widgets.clear(self.tests)
        tests = library.tests(now)
        self.tests.set_visible(bool(tests))
        for deck, day in tests.items():
            left = (day - today).days
            when = "today" if left == 0 else "tomorrow" if left == 1 else f"in {left} days"
            counts = library.counts(now, deck)
            subtitle = (f"{dates.spoken(day)} · {stats.waiting(library, deck)} new "
                        f"waiting · {counts['due']} due")
            widgets.group_add(self.tests, Adw.ActionRow(
                title=widgets.esc(f"{deck} — {when}"), subtitle=widgets.esc(subtitle)))

        widgets.clear(self.ahead)
        coming = stats.due_ahead(library, now, 7)
        parts = []
        for offset, count in enumerate(coming[1:], start=1):
            day = today + timedelta(days=offset)
            parts.append(f"{'Tomorrow' if offset == 1 else dates.short_weekday(day)} {count}")
        widgets.group_add(self.ahead, Adw.ActionRow(
            title=f"{coming[0]} due now", subtitle=" · ".join(parts)))

        widgets.clear(self.leeches)
        leeches = library.leeches()
        self.leeches.set_visible(bool(leeches))
        for item, count in leeches[:20]:
            widgets.group_add(self.leeches, self._note_row(
                item, f"{item.card.deck} · forgotten {count} times · open to rewrite"))

        widgets.clear(self.slipping)
        self.slipping.set_visible(bool(week.slipping))
        for item, count in week.slipping[:12]:
            widgets.group_add(self.slipping, self._note_row(
                item, f"{item.card.deck} · forgotten {count}× this week"))

        widgets.clear(self.decks)
        self.decks.set_visible(bool(week.decks))
        for deck in week.decks[:20]:
            bits = [f"{deck.reviews} review{'s' if deck.reviews != 1 else ''}"]
            if deck.retention is not None:
                bits.append(f"{deck.retention:.0%} held")
            if deck.due:
                bits.append(f"{deck.due} due")
            widgets.group_add(self.decks, Adw.ActionRow(
                title=widgets.esc(deck.deck), subtitle=" · ".join(bits)))

        self._schedule(library)
        self._drills(library, now)

    def _schedule(self, library):
        widgets.clear(self.schedule)
        spaced = fit.spaced_reviews(library.histories())
        fitted = int(library.learning["weights_reviews"])
        if library.learning["weights"]:
            title = f"Fitted to your own {fitted} reviews"
            subtitle = ("FSRS's weights, adjusted to how your memory actually behaved. "
                        f"Fitted again when the log reaches {int(fitted * fit.REFIT_GROWTH)}.")
        else:
            title = "FSRS-5's default weights"
            subtitle = (f"Fitted to your own log at {fit.MIN_REVIEWS} spaced reviews; "
                        f"there are {spaced} so far.")
        row = Adw.ActionRow(title=title, subtitle=subtitle, subtitle_lines=3)
        button = Gtk.Button(label="Fit now", valign=Gtk.Align.CENTER,
                            sensitive=spaced >= 50 and library.exists() and not self._fitting,
                            tooltip_text="Fit the schedule to the log now")
        button.connect("clicked", lambda *_: self.fit_now())
        row.add_suffix(button)
        widgets.group_add(self.schedule, row)

    def fit_now(self):
        library = self.window.library
        self._fitting = True
        self.window.toast("Fitting the schedule to the log…")

        def done(result):
            self._fitting = False
            if result is None:
                self.window.toast("Nothing to fit yet")
                return
            self.window.toast(f"Fitted to {result.reviews} reviews: the log is predicted "
                              f"{_gain(result):.0%} better")
            self.window.refresh()

        def failed(exc):
            self._fitting = False
            widgets.error_toast(self, exc)

        jobs.run(lambda: library.fit_if_due(force=True), done, failed)

    def _drills(self, library, now):
        widgets.clear(self.drills)
        results = []
        if library.exists():
            start = now - timedelta(days=7)
            results = DrillLog(library.places.learning / "drills", library.machine).since(start)
        self.drills.set_visible(bool(results))
        titles = {d.id: d.title for d in load_drills(library.places)}
        by_drill: dict[str, list] = {}
        for result in results:
            by_drill.setdefault(result.drill, []).append(result)
        for drill, runs in sorted(by_drill.items()):
            best = max(r.score for r in runs)
            last = runs[-1]
            name = titles.get(drill) or last.extra.get("title") or drill
            subtitle = (f"{len(runs)} run{'s' if len(runs) != 1 else ''} · best {best:.0%}"
                        f" · last {last.score:.0%}"
                        + (f" in {last.ms / 1000:.1f} s" if last.ms else ""))
            widgets.group_add(self.drills, Adw.ActionRow(title=widgets.esc(name),
                                                         subtitle=subtitle))

    def print_week(self):
        library = self.window.library
        if not library.loaded:
            return
        now = cards.now()
        summary = stats.for_paper(library, now)
        target = f"{winenv.cache_home('mnemosyne')}/weeks/{study_day(now):%Y-%m-%d}.pdf"

        def drawn(path):
            self.window.launch_file(path)

        jobs.run(lambda: paper.render_week(summary, target), drawn,
                 lambda exc: widgets.error_toast(self, exc))


def _gain(result) -> float:
    if not result.loss_before:
        return 0.0
    return max(0.0, (result.loss_before - result.loss_after) / result.loss_before)
