"""Memory palaces: the list of them, and a walk through one.

A palace is a note in `learning/palaces/` with `kind: palace` and its loci as
an ordered list of `locus :: item` cards (SYSTEM.md §8). Each locus is an
ordinary card and is scheduled like one; what this page adds is the **walk**:
the loci asked in order, from the front door to the last room — and then
backwards, from the last room to the front door, because a palace that can
only be walked one way is half-built, and the backwards walk is the one that
shows which links are really there and which were only ever carried by the
one before.

A photograph under a locus in the note (`![](front-door.jpg)`) is shown with
it. The real place, seen while the item is recalled into it, is what the
method is made of; a drawing of a room is not the room.

A walk is a drill rather than a review — it does not move the schedule — and
its score goes into the drill log as `palace`, with the palace's title.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from . import widgets  # noqa: E402
from .cardtext import to_markup  # noqa: E402
from .trainers import Drill, DrillLog, Run  # noqa: E402

_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)>]+?)>?(?:\s+\"[^\"]*\")?\s*\)|!\[\[([^\]|#]+)")
_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d{1,3}[.)])\s+")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")


def locus_photos(note: Path, lines_of_loci: list[int], extra_roots=()) -> dict[int, Path]:
    """For each locus (by its 1-based line), the first photograph written under it.

    "Under" is everything after the locus's line up to the next list item at
    the same depth or shallower, or the next heading. Paths are read relative
    to the note first, then to each of `extra_roots`.
    """
    try:
        lines = note.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return {}
    found: dict[int, Path] = {}
    for start in lines_of_loci:
        index = start - 1
        if not 0 <= index < len(lines):
            continue
        item = _ITEM.match(lines[index])
        depth = len(item.group(1)) if item else 0
        for offset, line in enumerate(lines[index:]):
            if offset:
                other = _ITEM.match(line)
                if (other and len(other.group(1)) <= depth) or _HEADING.match(line):
                    break
            m = _IMAGE.search(line)
            if not m:
                continue
            target = (m.group(1) or m.group(2) or "").strip()
            for base in (note.parent, *extra_roots):
                candidate = Path(base) / target
                if candidate.is_file():
                    found[start] = candidate
                    break
            if start in found:
                break
    return found


class PalacesPage(Adw.PreferencesPage):
    name = "palaces"
    title = "Palaces"
    icon = "go-home-symbolic"

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.group = Adw.PreferencesGroup(
            title="Palaces",
            description="Walk one forwards, then backwards. The loci are also cards, "
                        "and are reviewed with everything else.")
        self.add(self.group)

    def reload(self):
        widgets.clear(self.group)
        library = self.window.library
        walks = library.deck.palaces() if library.loaded else {}
        if not walks:
            widgets.group_add(self.group, Adw.ActionRow(
                title="No palaces yet",
                subtitle="A palace is a note in learning/palaces/ with “kind: palace” "
                         "in its front matter and its loci as a numbered list of "
                         "“locus :: item” lines.",
                subtitle_lines=4))
            return
        from . import cards                                  # noqa: PLC0415

        now = cards.now()
        for title, items in sorted(walks.items()):
            due = sum(1 for i in items if i.due(now))
            subtitle = f"{len(items)} loci" + (f" · {due} due" if due else "")
            row = Adw.ActionRow(title=widgets.esc(title), subtitle=subtitle,
                                activatable=True)
            backwards = widgets.flat("go-previous-symbolic", "Walk it backwards")
            backwards.connect("clicked", lambda _b, t=title: self.walk(t, reverse=True))
            forwards = widgets.flat("go-next-symbolic", "Walk it forwards")
            forwards.connect("clicked", lambda _b, t=title: self.walk(t))
            row.add_suffix(backwards)
            row.add_suffix(forwards)
            row.connect("activated", lambda _r, t=title: self.walk(t))
            widgets.group_add(self.group, row)

    def walk(self, title, reverse=False):
        library = self.window.library
        items = library.deck.palaces().get(title, [])
        if not items:
            return
        self.window.open_activity(WalkView(self.window, title, items, reverse))


class WalkView(Gtk.Box):
    def __init__(self, window, palace: str, items, reverse: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.window = window
        self.palace = palace
        self.items = list(reversed(items)) if reverse else list(items)
        self.reverse = reverse
        self.title = f"{palace} — {'backwards' if reverse else 'forwards'}"
        self.index = 0
        self.revealed = False
        self.shown_at = time.monotonic()
        drill = Drill("palace", palace, "palace", params={})
        self.run = Run(drill, len(self.items))
        self.misses: list[str] = []

        library = window.library
        note = library.places.home / self.items[0].card.path
        self.photos = locus_photos(note, [i.card.line for i in self.items],
                                   (library.places.brain, library.places.home))

        self.stack = Gtk.Stack(vexpand=True)
        self.append(self.stack)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=16,
                       margin_bottom=16, margin_start=16, margin_end=16)
        self.position = widgets.label(css=("dim-label",), xalign=0.5)
        self.locus = widgets.label(css=("card-front",), markup=True, xalign=0.5)
        self.locus.set_justify(Gtk.Justification.CENTER)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
                                   css_classes=["locus-photo"], height_request=220)
        self.item_label = widgets.label(css=("card-back",), markup=True, xalign=0.5)
        self.item_label.set_justify(Gtk.Justification.CENTER)
        self.item_revealer = Gtk.Revealer(child=self.item_label,
                                          transition_type=Gtk.RevealerTransitionType.CROSSFADE)
        for widget in (self.position, self.locus, self.picture, self.item_revealer):
            body.append(widget)
        self.stack.add_named(widgets.scrolled(body, 720), "walk")

        self.buttons = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        show = widgets.pill("Show the item", tooltip="Space")
        show.connect("clicked", lambda *_: self.reveal())
        self.buttons.add_named(show, "show")
        marks = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        missed = widgets.pill("Missed", css="destructive-action", tooltip="1")
        missed.connect("clicked", lambda *_: self.mark(False))
        got = widgets.pill("Got it", tooltip="Space")
        got.connect("clicked", lambda *_: self.mark(True))
        marks.append(missed)
        marks.append(got)
        self.buttons.add_named(marks, "mark")
        self.buttons.set_margin_bottom(12)
        self.append(self.buttons)

        self.summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_named(self.summary, "summary")
        self._show()

    def _show(self):
        item = self.items[self.index]
        order = item.card.order or (self.index + 1)
        self.position.set_text(f"Locus {order} · {self.index + 1} of {len(self.items)}")
        self.locus.set_markup(to_markup(item.card.front))
        self.item_label.set_markup(to_markup(item.card.back))
        self.item_revealer.set_reveal_child(False)
        self.revealed = False
        photo = self.photos.get(item.card.line)
        texture = _texture(photo) if photo else None
        self.picture.set_paintable(texture)
        self.picture.set_visible(texture is not None)
        self.buttons.set_visible_child_name("show")
        self.shown_at = time.monotonic()

    def reveal(self):
        self.revealed = True
        self.item_revealer.set_reveal_child(True)
        self.buttons.set_visible_child_name("mark")

    def mark(self, correct: bool):
        if not self.revealed:
            self.reveal()
            return
        item = self.items[self.index]
        self.run.mark(correct, int((time.monotonic() - self.shown_at) * 1000))
        if not correct:
            self.misses.append(f"{item.card.front} → {item.card.back}")
        self.index += 1
        if self.index >= len(self.items):
            self._finish()
        else:
            self._show()

    def _finish(self):
        library = self.window.library
        if library.exists():
            log = DrillLog(library.places.learning / "drills", library.machine)
            self.run.save(log, title=self.palace, direction="backwards" if self.reverse
                          else "forwards")
        page = Adw.StatusPage(icon_name="go-home-symbolic", vexpand=True,
                              title=f"{self.run.right} of {len(self.items)}")
        text = ("Every locus held." if not self.misses else
                "Missed:\n" + "\n".join(self.misses[:12]))
        if not self.reverse and not self.misses:
            text += "\nNow walk it backwards."
        page.set_description(widgets.esc(text))
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        if not self.reverse:
            back = widgets.pill("Walk it backwards")
            back.connect("clicked", lambda *_: self.window.pages["palaces"].walk(
                self.palace, reverse=True))
            buttons.append(back)
        done = widgets.pill("Done", css="")
        done.connect("clicked", lambda *_: self.window.close_activity())
        buttons.append(done)
        page.set_child(buttons)
        self.summary.append(page)
        self.buttons.set_visible(False)
        self.stack.set_visible_child_name("summary")

    def handle_key(self, keyval, state) -> bool:
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        if self.index >= len(self.items):
            return False
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_3, Gdk.KEY_KP_3):
            if self.revealed:
                self.mark(True)
            else:
                self.reveal()
            return True
        if keyval in (Gdk.KEY_1, Gdk.KEY_KP_1):
            if self.revealed:
                self.mark(False)
            else:
                self.reveal()
            return True
        return False


def _texture(path: Path):
    """A photograph as a texture, decoded at the size it is shown at."""
    from . import observe                                    # noqa: PLC0415

    pixbuf = observe.load(str(path), 720)
    return widgets.texture(pixbuf) if pixbuf is not None else None
