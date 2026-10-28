"""Preferences: the house Look page, then how this app learns and prints.

The Learning page writes `SecondBrain/config/learning.yaml` — the settings the
schedule depends on, which follow Robin to every machine (see config.py). The
rest is this machine's: how big a card is drawn, how small a sheet is printed,
where the observation trainer looks for photographs.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import housestyle, widgets  # noqa: E402
from .config import NEW_RANGE, RETENTION_RANGE  # noqa: E402


def preferences(window) -> Adw.PreferencesDialog:
    dialog = Adw.PreferencesDialog(title="Preferences")
    dialog.add(housestyle.look_page(window.config, window.glazing.restyle))
    dialog.add(_learning_page(window))
    dialog.add(_paper_page(window))
    return dialog


def _learning_page(window):
    library = window.library
    learning = library.learning
    page = Adw.PreferencesPage(title="Learning", icon_name="accessories-dictionary-symbolic")
    here = library.exists()

    schedule = Adw.PreferencesGroup(
        title="The schedule",
        description=("Kept in the brain, in config/learning.yaml, so every machine "
                     "schedules the same log the same way." if here else
                     "There is no brain on this machine, so these cannot be saved: "
                     "they live in SecondBrain/config/learning.yaml."))
    schedule.set_sensitive(here)

    new = Adw.SpinRow.new_with_range(*NEW_RANGE, 1)
    new.set_title("New cards a day")
    new.set_subtitle("Each new card costs about ten reviews over the next month.")
    new.set_value(int(learning["new_per_day"]))

    retention = Adw.SpinRow.new_with_range(RETENTION_RANGE[0] * 100,
                                           RETENTION_RANGE[1] * 100, 1)
    retention.set_title("Desired retention, %")
    retention.set_subtitle("The chance of remembering a card when it comes back. "
                           "Higher means more reviews for a little more certainty; "
                           "90 is the usual answer.")
    retention.set_value(round(float(learning["desired_retention"]) * 100))

    # A spin button changes its value on every click; the file is written and
    # the notes reread once it has settled.
    pending = {}

    def changed(key, value):
        pending[key] = value
        if pending.get("_source"):
            GLib.source_remove(pending["_source"])
        pending["_source"] = GLib.timeout_add(600, settle)

    def settle():
        pending.pop("_source", None)
        wrote = all(learning.set(key, value) for key, value in list(pending.items()))
        pending.clear()
        if wrote:
            window.refresh()
        else:
            window.toast("Could not write config/learning.yaml")
        return GLib.SOURCE_REMOVE

    new.connect("notify::value", lambda r, _p: changed("new_per_day", int(r.get_value())))
    retention.connect("notify::value",
                      lambda r, _p: changed("desired_retention", r.get_value() / 100))
    schedule.add(new)
    schedule.add(retention)
    page.add(schedule)

    roots = Adw.PreferencesGroup(
        title="Where cards are",
        description="Folders read for cards, relative to your home folder. Every "
                    ".md and .tex file under them counts.")
    roots.set_sensitive(here)
    add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"],
                     tooltip_text="Add a folder", valign=Gtk.Align.CENTER)
    roots.set_header_suffix(add)

    def fill():
        widgets.clear(roots)
        for root in learning["roots"]:
            exists = (library.places.home / root).exists()
            row = Adw.ActionRow(title=widgets.esc(root),
                                subtitle="" if exists else "Not on this machine")
            remove = widgets.flat("user-trash-symbolic", "Stop reading this folder")
            remove.connect("clicked", lambda _b, r=root: drop(r))
            row.add_suffix(remove)
            widgets.group_add(roots, row)

    def drop(root):
        learning.set("roots", [r for r in learning["roots"] if r != root])
        fill()
        window.refresh()

    def chosen(source, result):
        try:
            folder = source.select_folder_finish(result)
        except GLib.Error:
            return
        if folder is None or not folder.get_path():
            return
        rel = library.places.relative(folder.get_path())
        if rel not in learning["roots"]:
            learning.set("roots", learning["roots"] + [rel])
            fill()
            window.refresh()

    def pick(*_):
        dialog = Gtk.FileDialog(title="A folder to read cards from")
        dialog.set_initial_folder(Gio.File.new_for_path(str(library.places.home)))
        dialog.select_folder(window._parent_window(), None, chosen)

    add.connect("clicked", pick)
    fill()
    page.add(roots)
    page.add(_decks_group(window))

    machine = Adw.PreferencesGroup(title="This machine")
    for title, value in (("Name in the logs", library.machine),
                         ("The brain", str(library.places.brain))):
        row = Adw.ActionRow(title=title, subtitle=widgets.esc(value), subtitle_selectable=True)
        machine.add(row)
    page.add(machine)
    return page


def _paper_page(window):
    config = window.config
    page = Adw.PreferencesPage(title="Cards and paper", icon_name="document-print-symbolic")

    screen = Adw.PreferencesGroup(title="On screen")
    size = Adw.SpinRow.new_with_range(11, 36, 1)
    size.set_title("Card text size")
    size.set_subtitle("In points. A card is read at arm's length.")
    size.set_value(int(config["card_font_size"]))

    def resized(row, _param):
        config["card_font_size"] = int(row.get_value())
        config.save()
        window.apply_card_size()

    size.connect("notify::value", resized)
    screen.add(size)
    context = Adw.SwitchRow(title="Show where a card comes from",
                            subtitle="The deck and the headings above it in the note",
                            active=bool(config["show_context"]))

    def toggled(row, _param):
        config["show_context"] = row.get_active()
        config.save()

    context.connect("notify::active", toggled)
    screen.add(context)
    page.add(screen)

    paper = Adw.PreferencesGroup(title="On paper")
    sheet = Adw.SpinRow.new_with_range(7, 14, 0.5)
    sheet.set_digits(1)
    sheet.set_title("Sheet text size")
    sheet.set_subtitle("In points. 9.5 fits about twenty cards to a side of A4.")
    sheet.set_value(float(config["sheet_font_size"]))

    def sheet_resized(row, _param):
        config["sheet_font_size"] = float(row.get_value())
        config.save()

    sheet.connect("notify::value", sheet_resized)
    paper.add(sheet)
    page.add(paper)

    trainers = Adw.PreferencesGroup(title="Trainers")
    photos = Adw.ActionRow(title="Photographs for “What changed?”",
                           subtitle=widgets.esc(config["photos_folder"]))
    choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)

    def chosen(source, result):
        try:
            folder = source.select_folder_finish(result)
        except GLib.Error:
            return
        if folder and folder.get_path():
            config["photos_folder"] = window.library.places.relative(folder.get_path())
            config.save()
            photos.set_subtitle(widgets.esc(config["photos_folder"]))

    choose.connect("clicked", lambda *_: Gtk.FileDialog(title="Photographs").select_folder(
        window._parent_window(), None, chosen))
    photos.add_suffix(choose)
    trainers.add(photos)
    page.add(trainers)
    return page


def deck_groups(decks: list[str]) -> list[tuple[str, list[str]]]:
    """Decks gathered under the folders they share, for a list that fits a column.

    Grouped by their first part, and each group named by everything its decks
    have in common — so the hundred Digitalization subjects sit under
    "Programming/Websites/Digitalization/DataHU" rather than under
    "Programming", and a deck alone in its group is its own group.
    """
    first: dict[str, list[str]] = {}
    for deck in decks:
        first.setdefault(deck.split("/")[0], []).append(deck)
    out = []
    for members in first.values():
        parts = [m.split("/") for m in members]
        common = []
        for column in zip(*parts):
            if len(set(column)) != 1:
                break
            common.append(column[0])
        if len(members) == 1:
            common = parts[0]
        elif len(common) == min(len(p) for p in parts):
            common = common[:-1]
        out.append(("/".join(common), sorted(members)))
    return sorted(out)


def _decks_group(window):
    """Every deck, with a switch: off takes it out of every queue and sheet."""
    library = window.library
    group = Adw.PreferencesGroup(
        title="Decks",
        description="Switch off what you are not learning — a Digitalization subject "
                    "you do not take, a language you dropped. Kept in learning.yaml as "
                    "“off”, so it holds on every machine. Nothing is deleted.")
    group.set_sensitive(library.exists() and library.loaded)

    def switch(name, active):
        if library.set_deck(name, active):
            window.refresh()
        else:
            window.toast("Could not write config/learning.yaml")

    for prefix, members in deck_groups(library.all_decks()):
        if len(members) == 1 and members[0] == prefix:
            row = Adw.SwitchRow(title=widgets.esc(prefix),
                                active=not library.deck_is_off(prefix))
            row.connect("notify::active", lambda r, _p, n=prefix: switch(n, r.get_active()))
            group.add(row)
            continue
        expander = Adw.ExpanderRow(title=widgets.esc(prefix or "Decks"),
                                   subtitle=f"{len(members)} decks",
                                   show_enable_switch=bool(prefix),
                                   enable_expansion=not library.deck_is_off(prefix)
                                   if prefix else True)
        if prefix:
            expander.connect("notify::enable-expansion",
                             lambda r, _p, n=prefix: switch(n, r.get_enable_expansion()))
        for name in members:
            shown = name[len(prefix):].lstrip("/") if prefix else name
            row = Adw.SwitchRow(title=widgets.esc(shown or name),
                                active=not library.deck_is_off(name))
            row.connect("notify::active", lambda r, _p, n=name: switch(n, r.get_active()))
            expander.add_row(row)
        group.add(expander)
    return group
