"""Trainers: the Mentalist's drills, timed, scored, and written down.

The page lists the drills the curriculum defines in `drills.yaml` (or the
built-in set when there is none), grouped by module, each with today's score
if it has been run. A drill is an activity like a review: it takes the
content, asks its rounds, and ends on a summary — the score, the median time,
and what was missed — which is also the line written to the drill log.

The rule for every trainer is the same: **a wrong answer is a lesson, not a
red cross.** A missed date shows the Doomsday method worked through for that
date; a missed card shows the card; a missed change in a photograph shows
where it was and what was done to it. A right answer moves straight on, so a
run that is going well keeps its rhythm.
"""

from __future__ import annotations

import os
import random
import time
from datetime import date

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import cards, jobs, observe, trainers, widgets  # noqa: E402
from .library import study_day  # noqa: E402
from .trainers import Drill, DrillLog, Run, TRAINER_NAMES  # noqa: E402


def _log(window) -> DrillLog:
    library = window.library
    return DrillLog(library.places.learning / "drills", library.machine)


class TrainersPage(Adw.PreferencesPage):
    name = "trainers"
    title = "Trainers"
    icon = "alarm-symbolic"

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.groups: list[Adw.PreferencesGroup] = []

    def reload(self):
        for group in self.groups:
            self.remove(group)
        self.groups = []
        library = self.window.library
        drills = trainers.load_drills(library.places)
        today = {}
        if library.exists():
            now = cards.now()
            for result in _log(self.window).read():
                if study_day(result.when) == study_day(now):
                    today[result.drill] = result
        builtin = all(not d.source for d in drills)
        sections: dict[str, list[Drill]] = {}
        for drill in drills:
            heading = (f"{drill.source} · {drill.module}" if drill.source and drill.module
                       else drill.source or "Built in")
            sections.setdefault(heading, []).append(drill)
        for heading, members in sections.items():
            group = Adw.PreferencesGroup(title=widgets.esc(heading))
            if builtin:
                group.set_description(
                    "There is no drills.yaml in learning/curricula yet, so these stand "
                    "in. The Mentalist curriculum names its own.")
            for drill in members:
                bits = [TRAINER_NAMES.get(drill.trainer, drill.trainer)]
                if drill.minutes:
                    bits.append(f"{drill.minutes} min")
                done = today.get(drill.id)
                if done:
                    bits.append(f"today {done.score:.0%}"
                                + (f" in {done.ms / 1000:.1f} s" if done.ms else ""))
                row = Adw.ActionRow(title=widgets.esc(drill.title),
                                    subtitle=widgets.esc(" · ".join(bits)), activatable=True)
                if done:
                    row.add_prefix(Gtk.Image(icon_name="object-select-symbolic"))
                row.add_suffix(Gtk.Image(icon_name="media-playback-start-symbolic"))
                row.connect("activated", lambda _r, d=drill: self.start(d))
                group.add(row)
            self.add(group)
            self.groups.append(group)

    def start(self, drill: Drill):
        window = self.window
        if drill.trainer == "cards":
            self._cards(drill)
            return
        view = {
            "observation": ObservationDrill, "deck": DeckDrill, "doomsday": DoomsdayDrill,
            "major": MajorDrill, "calc": CalcDrill,
        }.get(drill.trainer)
        if view:
            window.open_activity(view(window, drill))

    def _cards(self, drill: Drill):
        """A card-review drill: a deck's due cards, scored like any drill."""
        library = self.window.library
        deck = str(drill.param("deck", "") or "")
        match = next((d for d in library.deck.decks()
                      if d == deck or d.rsplit("/", 1)[-1] == deck), deck)
        items = library.today(deck=match) if match else []
        if not items:
            items = [i for i in library.deck.items.values()
                     if i.card.deck == match or i.card.deck.startswith(match + "/")]
        items = items[:drill.int_param("limit", 20)]
        if not items:
            self.window.toast(f"No cards in the deck “{deck}” yet")
            return

        def finished(session):
            summary = session.summary()
            if summary.cards and library.exists():
                times = sorted(ms for _i, _r, ms in session.log)
                _log(self.window).append(drill.id, summary.recalled,
                                         times[len(times) // 2] if times else 0,
                                         summary.cards)

        self.window.study(items, drill.title, on_finished=finished)


# ------------------------------------------------------------ the frame ----


class DrillView(Gtk.Box):
    """Rounds, a timer, the feedback line, and the summary at the end."""

    default_rounds = 10

    def __init__(self, window, drill: Drill):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.window = window
        self.drill = drill
        self.title = drill.title
        self.rng = random.Random()
        self.run = Run(drill, drill.int_param("rounds", self.default_rounds))
        self.asked_at = time.monotonic()
        self.waiting = False
        self.saved = False
        self._timers: list[int] = []

        self.stack = Gtk.Stack(vexpand=True)
        self.append(self.stack)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        top = Gtk.Box(spacing=8, margin_top=8, margin_start=14, margin_end=14)
        self.round_label = Gtk.Label(xalign=0, hexpand=True, css_classes=["dim-label"])
        self.score_label = Gtk.Label(xalign=1, css_classes=["dim-label", "numeric"])
        top.append(self.round_label)
        top.append(self.score_label)
        page.append(top)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                            vexpand=True, valign=Gtk.Align.CENTER, margin_start=16,
                            margin_end=16, margin_top=10, margin_bottom=10)
        middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        middle.append(widgets.clamp(self.body, 720))

        self.feedback = widgets.label(xalign=0.5)
        self.feedback.set_justify(Gtk.Justification.CENTER)
        self.method = widgets.label(css=("drill-method",), xalign=0.0, selectable=True,
                                    wrap=True)
        self.method.set_visible(False)
        notes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=16,
                        margin_end=16)
        notes.append(self.feedback)
        notes.append(self.method)
        middle.append(widgets.clamp(notes, 720))
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           vexpand=True)
        self.scroller.set_child(middle)
        page.append(self.scroller)

        self.next_button = widgets.pill("Next", tooltip="Space or Enter")
        self.next_button.connect("clicked", lambda *_: self.next_question())
        self.next_button.set_visible(False)
        self.next_button.set_margin_top(8)
        self.next_button.set_margin_bottom(12)
        page.append(self.next_button)
        self.stack.add_named(page, "drill")

        self.summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(self.summary, "summary")
        self.misses: list[str] = []
        self.build()
        self._later(60, self.start)

    # -- for the trainers to fill in -----------------------------------------
    def build(self):
        """Put the trainer's own widgets into `self.body`."""

    def start(self):
        self.next_question()

    def ask(self):
        """Pose the next question."""

    def handle(self, keyval, state) -> bool:
        return False

    # -- the rhythm -----------------------------------------------------------
    def _later(self, ms, fn):
        def fire():
            if fire.source in self._timers:
                self._timers.remove(fire.source)
            fn()
            return GLib.SOURCE_REMOVE
        fire.source = GLib.timeout_add(ms, fire)
        self._timers.append(fire.source)
        return fire.source

    def _cancel_timers(self):
        for source in self._timers:
            GLib.source_remove(source)
        self._timers = []

    def next_question(self):
        self._cancel_timers()
        if self.run.done:
            self.finish()
            return
        self.waiting = False
        self.next_button.set_visible(False)
        self.feedback.set_text("")
        self.feedback.set_css_classes([])
        self.method.set_visible(False)
        self.round_label.set_text(f"{len(self.run.marks) + 1} of {self.run.rounds}")
        self.ask()
        self.asked_at = time.monotonic()

    def answered(self, correct: bool, message: str, method: list[str] | None = None,
                 miss: str | None = None, hold: bool = False):
        """Mark the round. A right answer moves on by itself unless `hold` says
        there is something to look at first; a wrong one always waits."""
        if self.waiting:
            return
        ms = int((time.monotonic() - self.asked_at) * 1000)
        self.run.mark(correct, ms)
        self.waiting = True
        self.score_label.set_text(f"{self.run.right} right · {ms / 1000:.1f} s")
        self.feedback.set_text(message)
        self.feedback.set_css_classes(["drill-right" if correct else "drill-wrong"])
        if method:
            self.method.set_text("\n".join(method))
            self.method.set_visible(True)
            # The worked method is the point of a miss: bring it into view.
            adjustment = self.scroller.get_vadjustment()
            self._later(80, lambda: adjustment.set_value(adjustment.get_upper()))
        if not correct and miss:
            self.misses.append(miss)
        if correct and not method and not hold:
            self._later(450, self.next_question)
        else:
            self.next_button.set_visible(True)
            self.next_button.grab_focus()

    def finish(self):
        self._cancel_timers()
        if self.window.library.exists() and not self.saved:
            self.run.save(_log(self.window))
            self.saved = True
        widgets.clear(self.summary)
        page = Adw.StatusPage(icon_name="alarm-symbolic", vexpand=True,
                              title=f"{self.run.right} of {len(self.run.marks)}")
        lines = [f"{self.run.score:.0%}, {self.run.median_ms / 1000:.1f} s an answer "
                 "(the median)."]
        if self.misses:
            lines.append("Missed: " + "; ".join(self.misses[:10]) + ".")
        page.set_description(widgets.esc("\n".join(lines)))
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        again = widgets.pill("Again")
        again.connect("clicked", lambda *_: self.window.open_activity(
            type(self)(self.window, self.drill)))
        done = widgets.pill("Done", css="")
        done.connect("clicked", lambda *_: self.window.close_activity())
        buttons.append(again)
        buttons.append(done)
        page.set_child(buttons)
        self.summary.append(page)
        self.stack.set_visible_child_name("summary")

    def handle_key(self, keyval, state) -> bool:
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        if self.stack.get_visible_child_name() == "summary":
            return False
        if self.waiting and keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.next_question()
            return True
        return self.handle(keyval, state)

    def leave(self):
        self._cancel_timers()
        # A run stopped halfway still says something, once it is most of a run.
        if (not self.saved and len(self.run.marks) * 2 >= self.run.rounds
                and self.window.library.exists()):
            self.run.save(_log(self.window))
            self.saved = True


class TypedDrill(DrillView):
    """A prompt and a field to type the answer into."""

    def build(self):
        self.prompt = widgets.label(css=("drill-prompt",), xalign=0.5)
        self.prompt.set_justify(Gtk.Justification.CENTER)
        self.hint = widgets.label(css=("dim-label",), xalign=0.5)
        self.entry = Gtk.Entry(css_classes=["drill-entry"], halign=Gtk.Align.CENTER,
                               width_chars=14, xalign=0.5)
        self.entry.connect("activate", lambda *_: self._submit())
        for widget in (self.prompt, self.hint, self.entry):
            self.body.append(widget)

    def grab_focus(self):
        return self.entry.grab_focus()

    def ask(self):
        self.entry.set_text("")
        self.entry.set_sensitive(True)
        self.prompt.remove_css_class("red-suit")
        self.question()
        self.entry.grab_focus()

    def question(self):
        """Set the prompt and hint, and remember the answer."""

    def check(self, text: str) -> tuple[bool, str, str | None]:
        """`(right, what to say, what to list as missed)`."""
        return False, "", None

    def _submit(self):
        if self.waiting:
            self.next_question()
            return
        text = self.entry.get_text().strip()
        if not text:
            return
        right, message, miss = self.check(text)
        self.entry.set_sensitive(False)
        self.answered(right, message, miss=miss)


# --------------------------------------------------------------- the deck --


class DeckDrill(TypedDrill):
    default_rounds = 20

    def start(self):
        self.stack_name, self.stack_cards = trainers.load_stack(
            self.window.library.places, self.drill.param("deck"))
        low, high = self.drill.int_param("from", 1), self.drill.int_param("to", 52)
        self.positions = [p for p in sorted(self.stack_cards) if low <= p <= high] \
            or sorted(self.stack_cards)
        self.direction = str(self.drill.param("direction", "both")).lower()
        if self.stack_name == "New deck order":
            self.feedback.set_text("No memorised deck in learning/decks, so this is new "
                                   "deck order — fine for learning the trainer, not worth "
                                   "memorising.")
        super().start()

    def question(self):
        self.position = self.rng.choice(self.positions)
        self.card = self.stack_cards[self.position]
        # "position" asks the card at a position, "card" the position of a card.
        ask_card = self.direction != "card" and (
            self.direction != "both" or self.rng.random() < 0.5)
        self.asking_card = ask_card
        if ask_card:
            self.prompt.set_text(str(self.position))
            self.hint.set_text("Which card? (Q♥, qh, 10d, 4 of clubs)")
        else:
            self.prompt.set_text(self.card)
            if trainers.SUIT_COLOUR.get(self.card[-1]) == "red":
                self.prompt.add_css_class("red-suit")
            self.hint.set_text("Which position?")

    def check(self, text):
        if self.asking_card:
            given = trainers.parse_playing_card(text)
            right = given == self.card
            return right, ("Right." if right else f"{self.position} is {self.card}."), \
                f"{self.position} {self.card}"
        right = text.isdigit() and int(text) == self.position
        return right, ("Right." if right else f"{self.card} is {self.position}."), \
            f"{self.card} {self.position}"


# ----------------------------------------------------------------- major ---


class MajorDrill(TypedDrill):
    default_rounds = 20

    def start(self):
        self.deck_name, self.table = trainers.load_major(self.window.library.places,
                                                         self.drill.param("deck"))
        if not self.table:
            self.prompt.set_text("No Major system deck")
            self.hint.set_text("Put one in learning/decks — a file with “Major” in its "
                               "name and lines like 37 :: mák.")
            self.entry.set_visible(False)
            return
        low, high = self.drill.int_param("from", 0), self.drill.int_param("to", 99)
        self.numbers = trainers.major_numbers(self.table, low, high) or list(self.table)
        self.direction = str(self.drill.param("direction", "number")).lower()
        super().start()

    def question(self):
        self.number = self.rng.choice(self.numbers)
        self.words = self.table[self.number]
        # "number" asks for the word, "word" for the number.
        to_word = self.direction != "word" and (
            self.direction != "both" or self.rng.random() < 0.5)
        self.to_word = to_word
        if to_word:
            self.prompt.set_text(self.number)
            self.hint.set_text("The word")
        else:
            self.prompt.set_text(self.rng.choice(self.words))
            self.hint.set_text("The number")

    def check(self, text):
        if self.to_word:
            keys = {trainers.answer_key(w) for w in self.words}
            right = trainers.answer_key(text) in keys
            return right, ("Right." if right else f"{self.number}: {', '.join(self.words)}."), \
                f"{self.number} {self.words[0]}"
        typed = text.strip()
        right = typed == self.number or (typed.isdigit() and not self.number.startswith("0")
                                         and int(typed) == int(self.number))
        return right, ("Right." if right else f"{self.prompt.get_text()} is {self.number}."), \
            f"{self.prompt.get_text()} {self.number}"


# ------------------------------------------------------------ arithmetic ---


class CalcDrill(TypedDrill):
    def question(self):
        kind = str(self.drill.param("kind", "multiply")).lower()
        digits = self.drill.int_param("digits", 2)
        self.text, self.answer = trainers.calc_problem(kind, digits, self.rng)
        self.prompt.set_text(self.text)
        self.hint.set_text("")

    def check(self, text):
        cleaned = text.replace(" ", "").replace(",", "").replace(".", "")
        right = cleaned.lstrip("-").isdigit() and int(cleaned) == self.answer
        return right, ("Right." if right else f"{self.text} = {self.answer}."), \
            f"{self.text} = {self.answer}"


# -------------------------------------------------------------- doomsday ---


class DoomsdayDrill(DrillView):
    def build(self):
        self.prompt = widgets.label(css=("drill-prompt",), xalign=0.5)
        self.prompt.set_justify(Gtk.Justification.CENTER)
        self.body.append(self.prompt)
        self.body.append(widgets.label("Which weekday? 0 Sunday … 6 Saturday", xalign=0.5,
                                       css=("dim-label",)))
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=7,
                           min_children_per_line=4, homogeneous=True,
                           column_spacing=6, row_spacing=6)
        self.day_buttons = []
        for number, name in enumerate(trainers.WEEKDAYS):
            button = Gtk.Button(label=f"{number} {name[:3]}")
            button.connect("clicked", lambda _b, n=number: self.choose(n))
            grid.append(button)
            self.day_buttons.append(button)
        self.body.append(grid)

    def ask(self):
        self.day = trainers.random_date(self.drill, self.rng, date.today())
        self.prompt.set_text(f"{self.day.day} {trainers.MONTHS[self.day.month - 1]} "
                             f"{self.day.year}")
        for button in self.day_buttons:
            button.set_sensitive(True)

    def choose(self, number: int):
        if self.waiting:
            return
        truth = trainers.weekday(self.day)
        for button in self.day_buttons:
            button.set_sensitive(False)
        if number == truth:
            self.answered(True, f"{trainers.WEEKDAYS[truth]}.")
        else:
            self.answered(False, f"It was a {trainers.WEEKDAYS[truth]} ({truth}).",
                          method=trainers.explain(self.day),
                          miss=f"{self.day.isoformat()} {trainers.WEEKDAYS[truth][:3]}")

    def handle(self, keyval, state) -> bool:
        digits = {getattr(Gdk, f"KEY_{n}"): n for n in range(7)}
        digits.update({getattr(Gdk, f"KEY_KP_{n}"): n for n in range(7)})
        if keyval in digits and not self.waiting:
            self.choose(digits[keyval])
            return True
        return False


# ----------------------------------------------------------- observation ---


class ObservationDrill(DrillView):
    """A photograph for a few seconds, then the same one with something changed."""

    default_rounds = 8

    def build(self):
        self.countdown = Gtk.Label(css_classes=["countdown", "numeric"])
        self.body.append(self.countdown)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
                                   vexpand=True, hexpand=True)
        self.marker = Gtk.DrawingArea(can_target=False)
        self.marker.set_draw_func(self._draw_marker)
        frame = Gtk.Overlay(vexpand=True, height_request=260)
        frame.set_child(self.picture)
        frame.add_overlay(self.marker)
        click = Gtk.GestureClick()
        click.connect("released", self._clicked)
        frame.add_controller(click)
        self.body.set_valign(Gtk.Align.FILL)
        self.body.append(frame)
        self.phase = "loading"
        self.change = None
        self.click_at = None
        self.photos: list[str] = []
        self.last = None

    def start(self):
        folder = self.drill.param("folder") or self.window.config["photos_folder"]
        folder = str(folder)
        if not os.path.isabs(folder):
            folder = str(self.window.library.places.home / folder)
        named = bool(self.drill.param("folder"))
        if named and not os.path.isdir(folder):
            # A drill that names a folder of its own (obs-own: your own
            # photographs) gets the folder, so there is somewhere to put them.
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                pass
        self.countdown.set_text("Looking for photographs…")

        def found(photos):
            self.photos = photos
            if not photos:
                self.countdown.set_text("No photographs yet")
                if named:
                    self.feedback.set_text(
                        f"This drill uses your own photographs, from {folder}. Put a "
                        "dozen or more there — rooms you know, the street, the "
                        "classroom, a desk; ordinary scenes with plenty in them — "
                        "and run it again.")
                else:
                    self.feedback.set_text(f"Nothing to look at in {folder}. Choose "
                                           "another folder in Preferences → Cards and "
                                           "paper.")
                return
            self.next_question()

        jobs.run(lambda: observe.find_photos(folder), found,
                 lambda exc: widgets.error_toast(self, exc))

    def ask(self):
        self.phase = "loading"
        self.change = None
        self.click_at = None
        self.picture.set_paintable(None)
        self.marker.queue_draw()
        self.countdown.set_text("…")
        path = observe.pick(self.photos, self.rng, self.last)
        self.last = path
        kinds = self.drill.param("changes") or trainers.CHANGES
        if isinstance(kinds, str):
            kinds = [kinds]
        seed = self.rng.random()

        def prepare():
            pixbuf = observe.load(path)
            if pixbuf is None:
                raise OSError(f"Could not read {os.path.basename(path)}")
            change = trainers.plan_change(pixbuf.get_width(), pixbuf.get_height(),
                                          random.Random(seed), kinds,
                                          interest=observe.detail(pixbuf))
            return pixbuf, observe.apply(pixbuf, change), change

        def ready(result):
            pixbuf, changed, change = result
            self.size = (pixbuf.get_width(), pixbuf.get_height())
            self.original = widgets.texture(pixbuf)
            self.changed = widgets.texture(changed)
            self.change = change
            self._study()

        def failed(exc):
            self.photos = [p for p in self.photos if p != path]
            if self.photos:
                self.ask()
            else:
                widgets.error_toast(self, exc)

        jobs.run(prepare, ready, failed)

    def _study(self):
        self.phase = "study"
        self.picture.set_paintable(self.original)
        seconds = max(1.0, float(self.drill.param("seconds", 10) or 10))
        self.deadline = time.monotonic() + seconds
        self._tick()

    def _tick(self):
        if self.phase != "study":
            return
        left = self.deadline - time.monotonic()
        if left <= 0:
            self._find()
            return
        self.countdown.set_text(f"Look · {left:.1f} s")
        self._later(100, self._tick)

    def _find(self):
        self.phase = "find"
        self.picture.set_paintable(self.changed)
        self.countdown.set_text("What changed? Click it.")
        self.asked_at = time.monotonic()

    def _clicked(self, _gesture, _n, x, y):
        if self.phase != "find" or self.change is None:
            return
        pw, ph = self.picture.get_width(), self.picture.get_height()
        iw, ih = self.size
        scale = min(pw / iw, ph / ih) if iw and ih else 1
        ox, oy = (pw - iw * scale) / 2, (ph - ih * scale) / 2
        ix, iy = (x - ox) / scale, (y - oy) / scale
        self.click_at = (ix, iy)
        hit = self.change.contains(ix, iy)
        self.phase = "shown"
        self.marker.queue_draw()
        what = {"mirror": "mirrored", "recolour": "recoloured",
                "clone": "covered with a patch from elsewhere"}[self.change.kind]
        self.countdown.set_text("")
        self.answered(hit, f"{'Found it' if hit else 'Not there'}: the region was {what}.",
                      miss=what, hold=True)

    def _draw_marker(self, area, cr, width, height):
        if self.phase != "shown" or self.change is None:
            return
        iw, ih = self.size
        pw, ph = self.picture.get_width(), self.picture.get_height()
        scale = min(pw / iw, ph / ih) if iw and ih else 1
        ox, oy = (pw - iw * scale) / 2, (ph - ih * scale) / 2
        c = self.change
        hit = self.click_at is not None and c.contains(*self.click_at)
        cr.set_line_width(3)
        if hit:
            cr.set_source_rgba(0.2, 0.75, 0.35, 0.95)
        else:
            cr.set_source_rgba(0.95, 0.3, 0.25, 0.95)
        cr.rectangle(ox + c.x * scale, oy + c.y * scale, c.w * scale, c.h * scale)
        cr.stroke()
        if self.click_at is not None:
            cx, cy = ox + self.click_at[0] * scale, oy + self.click_at[1] * scale
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.arc(cx, cy, 7, 0, 6.2832)
            cr.fill()
            cr.set_source_rgba(0, 0, 0, 0.8)
            cr.arc(cx, cy, 7, 0, 6.2832)
            cr.set_line_width(2)
            cr.stroke()

    def handle(self, keyval, state) -> bool:
        # Space ends the look early: "I have seen it".
        if self.phase == "study" and keyval == Gdk.KEY_space:
            self._find()
            return True
        return False
