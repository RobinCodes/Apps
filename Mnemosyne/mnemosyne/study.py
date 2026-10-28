"""Reviewing on screen: one card at a time, and a calm word at the end.

The view is one card: where it comes from (the deck and the headings above it
in the note), the question, and — after Space — the answer, with the four
ratings underneath, each saying when the card would come back if chosen. That
interval is the honest version of the button: "Good" is not a judgement, it is
"see this again in nine days", and seeing the nine days is what makes Hard
and Easy mean something.

Keys, because a review is a hundred small decisions and a mouse makes each of
them slower:

    Space   show the answer, then Good        1-4   Again, Hard, Good, Easy
    S       skip (once)                       E     open the note at this card
    X       ask Claude to explain it          Esc   stop here

It is an *activity*: the window gives it the whole content while it runs and
takes it back when it ends (see window.py).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import fsrs, widgets  # noqa: E402
from .cardtext import image_paths, to_markup  # noqa: E402
from .library import LEECH_LAPSES, lapses  # noqa: E402
from .session import Session  # noqa: E402
from .stats import due_ahead  # noqa: E402

RATING_CSS = {1: "again", 2: "hard", 3: "good", 4: "easy"}
DIGIT_KEYS = {Gdk.KEY_1: 1, Gdk.KEY_2: 2, Gdk.KEY_3: 3, Gdk.KEY_4: 4,
              Gdk.KEY_KP_1: 1, Gdk.KEY_KP_2: 2, Gdk.KEY_KP_3: 3, Gdk.KEY_KP_4: 4}


class StudyView(Gtk.Box):
    def __init__(self, window, session: Session, on_finished=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.window = window
        self.session = session
        self.on_finished = on_finished
        self.title = session.title
        self.summary_shown = False

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               vexpand=True)
        self.append(self.stack)
        self.stack.add_named(self._card_page(), "card")
        self.summary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(self.summary_box, "summary")
        GLib.idle_add(self._show_current)

    # ------------------------------------------------------------ building --
    def _card_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)

        self.progress = Gtk.ProgressBar(css_classes=["session-progress"])
        page.append(self.progress)

        top = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=6)
        self.count = Gtk.Label(xalign=0, hexpand=True, css_classes=["dim-label"])
        top.append(self.count)
        self.new_tag = Gtk.Label(label="new", css_classes=["new-tag"], visible=False,
                                 valign=Gtk.Align.CENTER)
        top.append(self.new_tag)
        self.leech_tag = Gtk.Label(label="leech", css_classes=["new-tag", "leech-tag"],
                                   visible=False, valign=Gtk.Align.CENTER,
                                   tooltip_text="Forgotten again and again: rewrite it (E)")
        top.append(self.leech_tag)
        page.append(top)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                       margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        self.context = widgets.label(css=("card-context",))
        self.front = widgets.label(css=("card-front",), markup=True, selectable=False)
        self.rule = Gtk.Box(css_classes=["card-rule"])
        self.back = widgets.label(css=("card-back",), markup=True, selectable=True)
        self.back_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE, transition_duration=120)
        answer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        answer.append(self.rule)
        answer.append(self.back)
        self.back_pics = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        answer.append(self.back_pics)
        self.back_revealer.set_child(answer)
        self.front_pics = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for widget in (self.context, self.front, self.front_pics, self.back_revealer):
            body.append(widget)
        page.append(widgets.scrolled(body, 760))

        # The bottom: one button before the answer, four after it.
        self.actions = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                                 transition_duration=100, vhomogeneous=False)
        show = widgets.pill("Show answer", tooltip="Space")
        show.connect("clicked", lambda *_: self.reveal())
        show.set_margin_top(6)
        show.set_margin_bottom(6)
        self.actions.add_named(show, "show")

        ratings = Gtk.Box(spacing=6, homogeneous=True, margin_top=4, margin_bottom=4,
                          margin_start=10, margin_end=10)
        self.rating_buttons = {}
        for rating in fsrs.RATINGS:
            name = fsrs.RATING_NAMES[rating]
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            inner.append(Gtk.Label(label=name, css_classes=["rating-name"]))
            when = Gtk.Label(label="", css_classes=["rating-when"])
            inner.append(when)
            button = Gtk.Button(child=inner, css_classes=["rating", RATING_CSS[rating]],
                                tooltip_text=f"{name} ({rating})")
            button.connect("clicked", lambda _b, r=rating: self.rate(r))
            ratings.append(button)
            self.rating_buttons[rating] = (button, when)
        self.actions.add_named(ratings, "rate")
        page.append(self.actions)

        tools = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER, margin_bottom=6)
        self.skip_button = Gtk.Button(label="Skip", css_classes=["flat"],
                                      tooltip_text="Ask it again at the end (S)")
        self.skip_button.connect("clicked", lambda *_: self.skip())
        tools.append(self.skip_button)
        note = Gtk.Button(label="Open note", css_classes=["flat"],
                          tooltip_text="Open the note this card is written in (E)")
        note.connect("clicked", lambda *_: self.open_note())
        tools.append(note)
        self.explain_button = Gtk.Button(label="Explain", css_classes=["flat"],
                                         tooltip_text="Ask Claude to explain this card (X)",
                                         visible=self.window.assistant.available)
        self.explain_button.connect("clicked", lambda *_: self.explain())
        tools.append(self.explain_button)
        page.append(tools)
        return page

    # ------------------------------------------------------------- showing --
    def _show_current(self):
        item = self.session.current
        if item is None:
            self._finish()
            return GLib.SOURCE_REMOVE
        card = item.card
        total = max(1, self.session.done + self.session.remaining)
        self.progress.set_fraction(self.session.done / total)
        self.count.set_text(f"{self.session.remaining} to go"
                            + (f" · {self.session.done} done" if self.session.done else ""))
        self.new_tag.set_visible(item.new)
        self.leech_tag.set_visible(lapses(item) >= LEECH_LAPSES)
        crumbs = [card.deck] + list(card.context)
        show_context = bool(self.window.config["show_context"])
        self.context.set_text(" › ".join(c for c in crumbs if c))
        self.context.set_visible(show_context)
        self.front.set_markup(to_markup(card.front))
        short = len(card.front) < 70 and "\n" not in card.front
        self.front.set_xalign(0.5 if short else 0.0)
        self.front.set_justify(Gtk.Justification.CENTER if short else Gtk.Justification.LEFT)
        self.back.set_markup(to_markup(card.back))
        self._pictures(self.front_pics, card.front, card, locus=bool(card.palace))
        self._pictures(self.back_pics, card.back, card)
        short_back = len(card.back) < 70 and "\n" not in card.back
        self.back.set_xalign(0.5 if short_back and short else 0.0)
        self.back.set_justify(Gtk.Justification.CENTER if short_back and short
                              else Gtk.Justification.LEFT)
        self.back_revealer.set_reveal_child(False)
        self.actions.set_visible_child_name("show")
        self.skip_button.set_sensitive(self.session.can_skip())
        self.session.show()
        return GLib.SOURCE_REMOVE

    def _pictures(self, box, text, card, locus=False):
        """The pictures a side of a card shows — and a locus's photograph with its front."""
        widgets.clear(box)
        places = self.window.library.places
        note = places.home / card.path
        files = image_paths(text, note, places.brain, places.home)
        if locus:
            from .palaces import locus_photos                # noqa: PLC0415

            photo = locus_photos(note, [card.line], (places.brain, places.home)).get(card.line)
            if photo:
                files.append(photo)
        for file in files[:3]:
            texture = _texture(file)
            if texture is None:
                continue
            picture = Gtk.Picture(paintable=texture, content_fit=Gtk.ContentFit.CONTAIN,
                                  can_shrink=True, height_request=min(260, texture.get_height()),
                                  css_classes=["locus-photo"])
            box.append(picture)
        box.set_visible(box.get_first_child() is not None)

    def reveal(self):
        if self.session.current is None or self.session.revealed:
            return
        self.session.reveal()
        for rating, days in self.session.preview().items():
            _button, when = self.rating_buttons[rating]
            when.set_text(fsrs.describe_days(days))
        self.back_revealer.set_reveal_child(True)
        self.actions.set_visible_child_name("rate")
        self.rating_buttons[fsrs.GOOD][0].grab_focus()

    def rate(self, rating: int):
        if self.session.current is None:
            return
        if not self.session.revealed:
            self.reveal()
            return
        try:
            self.session.answer(rating)
        except OSError as exc:
            widgets.error_toast(self, exc)
            return
        self._show_current()

    def skip(self):
        if self.session.skip():
            self._show_current()

    def open_note(self):
        item = self.session.current
        if item is not None:
            self.window.open_note(self.window.library.places.home / item.card.path,
                                  item.card.line)

    def explain(self):
        item = self.session.current
        if item is not None and self.window.assistant.available:
            self.window.assistant.explain(item.card)

    # ------------------------------------------------------------- the end --
    def _finish(self):
        if self.summary_shown:
            return
        self.summary_shown = True
        summary = self.session.summary()
        widgets.clear(self.summary_box)
        page = Adw.StatusPage(icon_name="emblem-ok-symbolic", vexpand=True)
        if not summary.answers:
            page.set_title("Nothing to review")
            page.set_description("Everything here is learned for today.")
        else:
            page.set_title("Done for now")
            minutes = summary.seconds / 60
            took = (f"in {minutes:.0f} minutes" if minutes >= 1.5
                    else f"in {summary.seconds:.0f} seconds" if summary.seconds
                    else "")
            lines = [f"{summary.cards} card{'s' if summary.cards != 1 else ''} {took}".strip() + "."]
            if summary.cards:
                remembered = summary.cards - summary.first_again
                lines.append(f"{remembered} remembered the first time "
                             f"({summary.recalled:.0%}).")
            if summary.first_again:
                lines.append(f"{summary.first_again} came back and "
                             f"{'was' if summary.first_again == 1 else 'were'} fixed before the end.")
            if summary.new:
                lines.append(f"{summary.new} new card{'s' if summary.new != 1 else ''} met.")
            ahead = due_ahead(self.window.library)
            if len(ahead) > 1:
                lines.append(f"Tomorrow: {ahead[1]} due.")
            page.set_description("\n".join(lines))
        done = widgets.pill("Back")
        done.connect("clicked", lambda *_: self.window.close_activity())
        page.set_child(done)
        self.summary_box.append(page)
        self.stack.set_visible_child_name("summary")
        if self.on_finished:
            self.on_finished(self.session)

    # ------------------------------------------------------------ the keys --
    def handle_key(self, keyval, state) -> bool:
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        if self.summary_shown:
            if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Escape):
                self.window.close_activity()
                return True
            return False
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self.session.revealed:
                self.rate(fsrs.GOOD)
            else:
                self.reveal()
            return True
        if keyval in DIGIT_KEYS:
            if self.session.revealed:
                self.rate(DIGIT_KEYS[keyval])
            else:
                self.reveal()
            return True
        if keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self.skip()
            return True
        if keyval in (Gdk.KEY_e, Gdk.KEY_E):
            self.open_note()
            return True
        if keyval in (Gdk.KEY_x, Gdk.KEY_X):
            self.explain()
            return True
        if keyval == Gdk.KEY_Escape:
            self.stop()
            return True
        return False

    def stop(self):
        """Esc: stop here, and still say what was done."""
        if self.session.done:
            self.session.pending.clear()
            self._finish()
        else:
            self.window.close_activity()

    def leave(self):
        if not self.summary_shown and self.session.done and self.on_finished:
            self.on_finished(self.session)


_textures: dict = {}


def _texture(file):
    """A picture decoded at the size a card shows it, kept for the session's repeats."""
    key = str(file)
    if key not in _textures:
        from . import observe                                # noqa: PLC0415

        pixbuf = observe.load(key, 640)
        if len(_textures) > 24:
            _textures.clear()
        _textures[key] = widgets.texture(pixbuf) if pixbuf is not None else None
    return _textures[key]
