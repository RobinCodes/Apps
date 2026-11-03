"""The things Clio asks you to fill in: an event or a span, a new board, a board's settings."""

from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import editing, widgets  # noqa: E402
from .timelines import Item, palette_colour  # noqa: E402

_LINK = re.compile(r"\[\[([^\]|#]+)")


def _text_view(text="", height=84):
    view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8,
                        left_margin=10, right_margin=10, accepts_tab=False)
    view.get_buffer().set_text(text)
    view.add_css_class("clio-text")
    scroller = Gtk.ScrolledWindow(min_content_height=height, max_content_height=220,
                                  propagate_natural_height=True, hexpand=True)
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_child(view)
    return view, scroller


def _buffer_text(view) -> str:
    buffer = view.get_buffer()
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)


def _sheet(dialog, content, action_button):
    """A dialog's frame: a header bar with the one button, and what scrolls under it."""
    scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=640)
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_child(content)
    header = Adw.HeaderBar()
    header.pack_end(action_button)
    view = Adw.ToolbarView()
    view.add_top_bar(header)
    view.set_content(scroller)
    dialog.set_child(view)


def _body():
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                   margin_start=18, margin_end=18, margin_top=6, margin_bottom=18)


class ItemDialog(Adw.Dialog):
    """An event or a span, new or not, with the date in §11's own syntax.

    `on_save(fields)` is given the keywords `editing.write_item` takes; `on_delete()`,
    when there is one, offers a way to take the item away; `on_link(target)` is a
    `[[link]]` in its notes being followed. Nothing is written from here.
    """

    def __init__(self, board, item: Item | None, on_save, on_delete=None, on_link=None):
        fresh = item is None or not item.title
        kind = "Span" if item is not None and item.span else "Event"
        super().__init__(title=f"New {kind}" if fresh else f"Edit {kind}", content_width=440)
        self.board = board
        self.on_save = on_save
        self.on_delete = on_delete
        self.on_link = on_link

        self.save_button = Gtk.Button(label="Save", css_classes=["suggested-action"])
        self.save_button.connect("clicked", lambda _b: self.save())

        self.event_button = Gtk.ToggleButton(label="Event", hexpand=True)
        self.span_button = Gtk.ToggleButton(label="Span", hexpand=True, group=self.event_button)
        kinds = Gtk.Box(css_classes=["linked"], homogeneous=True)
        kinds.append(self.event_button)
        kinds.append(self.span_button)

        self.start_entry = Gtk.Entry(placeholder_text="2017-09, ~1848-03-15, -0044",
                                     hexpand=True,
                                     text=str(item.start) if item is not None else "")
        self.end_entry = Gtk.Entry(placeholder_text="empty: still going on", hexpand=True,
                                   text=str(item.end) if item is not None and item.end else "")
        self.end_label = Gtk.Label(label="Until", xalign=0)
        dates = Gtk.Grid(column_spacing=8, row_spacing=6)
        self.start_label = Gtk.Label(label="Date", xalign=0)
        dates.attach(self.start_label, 0, 0, 1, 1)
        dates.attach(self.start_entry, 1, 0, 1, 1)
        dates.attach(self.end_label, 0, 1, 1, 1)
        dates.attach(self.end_entry, 1, 1, 1, 1)
        self.echo = Gtk.Label(xalign=0, wrap=True, css_classes=["clio-date-echo"])

        self.title_entry = Gtk.Entry(placeholder_text="What happened",
                                     text=item.title if item is not None else "", hexpand=True)
        self.categories_entry = Gtk.Entry(
            placeholder_text="school, family",
            text=", ".join(item.categories) if item is not None else "", hexpand=True)
        self.chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, row_spacing=4,
                                 column_spacing=4, max_children_per_line=12)
        for name in board.categories():
            chip = Gtk.Button(label=name, css_classes=["flat", "clio-chip"])
            chip.connect("clicked", lambda _b, n=name: self._toggle_category(n))
            self.chips.append(chip)
        self.milestone = Gtk.CheckButton(label="A turning point — drawn larger",
                                         active=bool(item is not None and item.milestone))
        self.notes, notes_box = _text_view("\n".join(item.notes) if item is not None else "")

        body = _body()
        body.append(kinds)
        body.append(dates)
        body.append(self.echo)
        body.append(widgets.heading("Title"))
        body.append(self.title_entry)
        body.append(widgets.heading("Categories"))
        body.append(self.categories_entry)
        if board.categories():
            body.append(self.chips)
        body.append(self.milestone)
        body.append(widgets.heading("Notes"))
        body.append(notes_box)
        links = self._links(item)
        if links is not None:
            body.append(links)
        if on_delete is not None:
            remove = Gtk.Button(label="Take It Away", halign=Gtk.Align.START, margin_top=8,
                                css_classes=["flat", "destructive-action"])
            remove.connect("clicked", lambda _b: self.delete())
            body.append(remove)
        _sheet(self, body, self.save_button)

        self.span_button.connect("toggled", lambda _b: self._kind_changed())
        (self.span_button if item is not None and item.span else self.event_button).set_active(True)
        for entry in (self.start_entry, self.end_entry, self.title_entry):
            entry.connect("changed", lambda _e: self._validate())
            entry.connect("activate", lambda _e: self.save())
        self.categories_entry.connect("activate", lambda _e: self.save())
        self._kind_changed()
        self.set_focus(self.title_entry if item is not None else self.start_entry)
        # The first key typed must add to a title, not replace it.
        GLib.idle_add(self._unselect)

    def _unselect(self):
        for entry in (self.title_entry, self.start_entry):
            entry.select_region(-1, -1)
        return GLib.SOURCE_REMOVE

    def _links(self, item):
        if item is None or self.on_link is None:
            return None
        targets = [t.strip() for note in item.notes for t in _LINK.findall(note)]
        if not targets:
            return None
        box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=4, row_spacing=2,
                          max_children_per_line=6)
        for target in dict.fromkeys(targets):
            button = Gtk.Button(label=target, css_classes=["flat", "clio-chip"],
                                tooltip_text=f"Open “{target}”")
            button.connect("clicked", lambda _b, t=target: self._follow(t))
            box.append(button)
        return box

    def _follow(self, target):
        self.force_close()
        self.on_link(target)

    def _toggle_category(self, name):
        names = editing.read_categories(self.categories_entry.get_text())
        names = [n for n in names if n != name] if name in names else [*names, name]
        self.categories_entry.set_text(", ".join(names))

    def _kind_changed(self):
        span = self.span_button.get_active()
        self.end_label.set_visible(span)
        self.end_entry.set_visible(span)
        self.start_label.set_label("From" if span else "Date")
        self.milestone.set_visible(not span or self.milestone.get_active())
        self._validate()

    def _validate(self) -> bool:
        span = self.span_button.get_active()
        start, end, wrong = editing.read_range(self.start_entry.get_text(),
                                               self.end_entry.get_text(), span)
        if wrong:
            self.echo.set_label(wrong)
            self.echo.set_css_classes(["clio-date-wrong"])
        else:
            text = start.label()
            if span:
                text += f" – {end.label() if end else 'now'}"
            self.echo.set_label(text)
            self.echo.set_css_classes(["clio-date-echo"])
        good = not wrong and bool(self.title_entry.get_text().strip())
        self.save_button.set_sensitive(good)
        return good

    def fields(self) -> dict:
        span = self.span_button.get_active()
        start, end, _ = editing.read_range(self.start_entry.get_text(),
                                           self.end_entry.get_text(), span)
        return {"start": start, "end": end, "span": span,
                "title": " ".join(self.title_entry.get_text().split()),
                "categories": editing.read_categories(self.categories_entry.get_text()),
                "milestone": self.milestone.get_active(),
                "notes": editing.read_notes(_buffer_text(self.notes))}

    def save(self):
        if not self._validate():
            return
        fields = self.fields()
        if self.get_root() is not None:
            self.close()
        self.on_save(fields)

    def delete(self):
        if self.get_root() is not None:
            self.close()
        self.on_delete()


class NewBoardDialog(Adw.Dialog):
    """A board's name, and when it starts counting from if it is someone's life."""

    def __init__(self, on_make):
        super().__init__(title="New Board", content_width=400)
        self.on_make = on_make
        self.make_button = Gtk.Button(label="Make", css_classes=["suggested-action"],
                                      sensitive=False)
        self.make_button.connect("clicked", lambda _b: self.make())
        self.title_entry = Gtk.Entry(placeholder_text="The Árpád house", hexpand=True)
        self.born_entry = Gtk.Entry(placeholder_text="2011-10-10", hexpand=True)
        self.echo = Gtk.Label(xalign=0, wrap=True, css_classes=["clio-date-echo"],
                              label="If it is a life: the axis then shows the age as well.")
        body = _body()
        body.append(widgets.heading("Name"))
        body.append(self.title_entry)
        body.append(widgets.heading("Born (optional)"))
        body.append(self.born_entry)
        body.append(self.echo)
        _sheet(self, body, self.make_button)
        for entry in (self.title_entry, self.born_entry):
            entry.connect("changed", lambda _e: self._validate())
            entry.connect("activate", lambda _e: self.make())
        self.set_focus(self.title_entry)

    def _validate(self) -> bool:
        text = self.born_entry.get_text().strip()
        born = editing.read_date(text)
        wrong = bool(text) and born is None
        self.echo.set_label(editing.DATE_HELP if wrong else
                            born.label() if born is not None else
                            "If it is a life: the axis then shows the age as well.")
        self.echo.set_css_classes(["clio-date-wrong" if wrong else "clio-date-echo"])
        good = not wrong and bool(self.title_entry.get_text().strip())
        self.make_button.set_sensitive(good)
        return good

    def make(self):
        if not self._validate():
            return
        title = " ".join(self.title_entry.get_text().split())
        born = editing.read_date(self.born_entry.get_text())
        if self.get_root() is not None:
            self.close()
        self.on_make(title, born)


def _to_rgba(colour: str) -> Gdk.RGBA:
    parsed = Gdk.RGBA()
    if not parsed.parse(colour or ""):
        parsed.parse("#7f7f7f")
    return parsed


def _to_hex(rgba: Gdk.RGBA) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in
                                          (rgba.red, rgba.green, rgba.blue)))


class _CategoryRow(Gtk.Box):
    """One category: its colour, and its name, which can be changed."""

    def __init__(self, name: str, colour: str, used: bool):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.original = name
        self.original_colour = colour
        self.used = used
        self.colour = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                            valign=Gtk.Align.CENTER)
        self.colour.set_rgba(_to_rgba(colour))
        self.name = Gtk.Entry(text=name, hexpand=True)
        self.append(self.colour)
        self.append(self.name)

    def colour_text(self) -> str:
        # A colour nobody changed stays as it is written: it may be a CSS name.
        chosen = _to_hex(self.colour.get_rgba())
        return self.original_colour if chosen == _to_hex(_to_rgba(self.original_colour)) \
            else chosen


class BoardDialog(Adw.Dialog):
    """A board's name, its `born:`, and its categories: name and colour.

    `on_save(title, born, renames, rows)` gets the new title and date, the renames
    as (old, new), and every category as (name, colour). A new category starts in
    the colour the house palette gives that name.
    """

    def __init__(self, board, on_save):
        super().__init__(title="This Board", content_width=420)
        self.board = board
        self.on_save = on_save
        self.save_button = Gtk.Button(label="Save", css_classes=["suggested-action"])
        self.save_button.connect("clicked", lambda _b: self.save())
        self.title_entry = Gtk.Entry(text=board.title, hexpand=True)
        self.born_entry = Gtk.Entry(text=str(board.born) if board.born else "",
                                    placeholder_text="Not a life: leave it empty", hexpand=True)
        self.echo = Gtk.Label(xalign=0, wrap=True, css_classes=["clio-date-echo"])
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.new_name = Gtk.Entry(placeholder_text="A new category", hexpand=True)
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add this category",
                         css_classes=["flat"])
        add.connect("clicked", lambda _b: self.add_category())
        self.new_name.connect("activate", lambda _e: self.add_category())
        adding = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        adding.append(self.new_name)
        adding.append(add)

        used = {name for item in board.items for name in item.categories}
        for name, colour in editing.category_rows(board):
            self.rows.append(_CategoryRow(name, colour, name in used))

        body = _body()
        body.append(widgets.heading("Name"))
        body.append(self.title_entry)
        body.append(widgets.heading("Born"))
        body.append(self.born_entry)
        body.append(self.echo)
        body.append(widgets.heading("Categories"))
        body.append(self.rows)
        body.append(adding)
        _sheet(self, body, self.save_button)
        self.title_entry.connect("changed", lambda _e: self._validate())
        self.born_entry.connect("changed", lambda _e: self._validate())
        self._validate()

    def _validate(self) -> bool:
        text = self.born_entry.get_text().strip()
        born = editing.read_date(text)
        wrong = bool(text) and born is None
        self.echo.set_label(editing.DATE_HELP if wrong else born.label() if born else
                            "With a date here the axis shows the age as well.")
        self.echo.set_css_classes(["clio-date-wrong" if wrong else "clio-date-echo"])
        good = not wrong and bool(self.title_entry.get_text().strip())
        self.save_button.set_sensitive(good)
        return good

    def _category_rows(self):
        row = self.rows.get_first_child()
        while row is not None:
            yield row
            row = row.get_next_sibling()

    def add_category(self):
        name = " ".join(self.new_name.get_text().split()).strip("{}, ")
        if not name or any(r.name.get_text().strip() == name for r in self._category_rows()):
            self.new_name.set_text("")
            return
        self.rows.append(_CategoryRow(name, palette_colour(name), False))
        self.new_name.set_text("")

    def values(self):
        renames, rows = [], []
        for row in self._category_rows():
            name = " ".join(row.name.get_text().split()).strip("{}, ")
            if not name:
                # Emptied: one nothing wears is gone; one that is worn keeps its name.
                if not row.used:
                    continue
                name = row.original
            if row.original and name != row.original:
                renames.append((row.original, name))
            rows.append((name, row.colour_text()))
        return (" ".join(self.title_entry.get_text().split()),
                editing.read_date(self.born_entry.get_text()), renames, rows)

    def save(self):
        if not self._validate():
            return
        values = self.values()
        if self.get_root() is not None:
            self.close()
        self.on_save(*values)
