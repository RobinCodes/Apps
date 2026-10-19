"""The settings, as a dialog.

Everything here applies the moment it is changed. There is no OK button,
because every one of these settings is reversible and visible: a translucency
slider you have to confirm is a translucency slider you cannot judge.

`on_change(key)` is handed in by the window, which knows what each setting
costs — restyling is instant, re-listing a folder is not, and changing the
tile size has to rebuild the grid's cells.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from . import housestyle, winenv  # noqa: E402


class Preferences:
    def __init__(self, parent: Gtk.Widget, config, on_change):
        self.config = config
        self.on_change = on_change
        self.parent = parent
        self.dialog = Adw.PreferencesDialog()
        self.dialog.set_title("Settings")
        self.dialog.set_search_enabled(True)
        self.dialog.add(self._appearance())
        self.dialog.add(self._listing())
        self.dialog.add(self._behaviour())

    def present(self) -> None:
        self.dialog.present(self.parent)

    # -- the plumbing -----------------------------------------------------

    def _set(self, key: str, value) -> None:
        if self.config.get(key) == value:
            return
        self.config[key] = value
        self.config.save()
        self.on_change(key)

    def _switch(self, title: str, subtitle: str, key: str) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.set_active(bool(self.config[key]))
        row.connect("notify::active",
                    lambda r, _p: self._set(key, r.get_active()))
        return row

    def _slider(self, title: str, subtitle: str, key: str,
                low: float, high: float, step: float,
                digits: int = 0, scale: float = 1.0) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        adjustment = Gtk.Adjustment(lower=low, upper=high, step_increment=step,
                                    page_increment=step * 4,
                                    value=float(self.config[key]) * scale)
        slider = Gtk.Scale(adjustment=adjustment, draw_value=True,
                           digits=digits)
        slider.set_size_request(190, -1)
        slider.set_valign(Gtk.Align.CENTER)
        slider.connect("value-changed",
                       lambda s: self._set(key, _shaped(self.config[key],
                                                        s.get_value() / scale)))
        row.add_suffix(slider)
        return row

    def _choice(self, title: str, subtitle: str, key: str,
                options: list[tuple[str, str]]) -> Adw.ComboRow:
        row = Adw.ComboRow(title=title, subtitle=subtitle)
        row.set_model(Gtk.StringList.new([label for label, _value in options]))
        values = [value for _label, value in options]
        if self.config[key] in values:
            row.set_selected(values.index(self.config[key]))
        row.connect("notify::selected",
                    lambda r, _p: self._set(key, values[r.get_selected()]))
        return row

    # -- the groups -------------------------------------------------------

    def _appearance(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Look", icon_name="applications-graphics-symbolic")

        # The theme rows come from the house, so this app's list and its
        # order are the same as every other app's here. `on_change("theme")`
        # is what the window listens for to rebuild the sheet.
        page.add(housestyle.theme_group(
            self.config, lambda: self.on_change("theme")))

        glass = Adw.PreferencesGroup(
            title="Glass",
            description="The window is a single translucent pane. How much of "
                        "the desktop comes through it, and how far the "
                        "floating parts blur what is behind them.")
        glass.add(self._switch("Glass", "Translucency and blur. Off is a plain "
                               "opaque window, which is faster on an old "
                               "graphics chip.", "glass"))
        glass.add(self._slider("Transparency",
                               "How much shows through. All the way to the "
                               "right is opaque.",
                               "translucency", 45, 100, 1, 0, 100.0))
        glass.add(self._slider("Blur",
                               "Only the parts that float over something: "
                               "dialogs, the palette, the sliding sidebar.",
                               "glass_blur", 0, 80, 2))
        glass.add(self._slider("Corners", "How round the window is.",
                               "rounding", 0, 28, 1))
        page.add(glass)

        rest = Adw.PreferencesGroup(title="Everything else")
        rest.add(self._switch("Colour the icons",
                              "Tinted by what kind of file it is, so the one "
                              "video in a folder of stills is findable "
                              "without reading.", "colour_icons"))
        rest.add(self._switch("Animations",
                              "Fades and slides. Off is instant.", "animations"))
        rest.add(self._choice("Row height", "", "row_height",
                              [("Compact", "compact"), ("Normal", "normal"),
                               ("Roomy", "roomy")]))
        rest.add(self._slider("Tile size", "How big the icons are in grid view.",
                              "icon_size", 48, 192, 8))
        page.add(rest)
        return page

    def _listing(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Listing", icon_name="view-list-symbolic")

        what = Adw.PreferencesGroup(title="What is shown")
        what.add(self._switch("Hidden files",
                             "Names beginning with a dot, and whatever a "
                             "folder's own .hidden file asks to hide.",
                             "show_hidden"))
        what.add(self._switch("Folders first",
                             "Folders above files, whichever way the sort "
                             "goes.", "folders_first"))
        what.add(self._switch("Thumbnails",
                             "Pictures of images, videos and PDFs, cached "
                             "where the rest of the desktop can use them too.",
                             "show_thumbnails"))
        what.add(self._slider("Largest file to thumbnail",
                              "In megabytes. A two-gigabyte video is not worth "
                              "decoding for a 72-pixel tile.",
                              "thumbnail_limit_mb", 0, 500, 10))
        what.add(self._switch("Folder sizes",
                             "Count what is inside every folder on sight. "
                             "Honest, and slow on a big tree.",
                             "folder_sizes"))
        what.add(self._switch("Git status",
                             "A coloured dot on anything git has an opinion "
                             "about, when the folder is in a repository.",
                             "git_status"))
        page.add(what)

        columns = Adw.PreferencesGroup(
            title="Columns", description="In the detail view. Name is always "
                                         "there.")
        for key, title in (("size", "Size"), ("type", "Type"),
                           ("modified", "Modified"),
                           ("permissions", "Access"), ("owner", "Owner")):
            row = Adw.SwitchRow(title=title)
            row.set_active(bool(self.config["columns"].get(key, False)))
            row.connect("notify::active", self._column_toggled, key)
            columns.add(row)
        page.add(columns)
        return page

    def _column_toggled(self, row, _param, key: str) -> None:
        columns = dict(self.config["columns"])
        columns[key] = row.get_active()
        self.config["columns"] = columns
        self.config.save()
        self.on_change("columns")

    def _behaviour(self) -> Adw.PreferencesPage:
        page = Adw.PreferencesPage(title="Behaviour",
                                   icon_name="preferences-system-symbolic")

        opening = Adw.PreferencesGroup(title="Opening")
        opening.add(self._switch("Single click opens",
                                 "Rather than selects. Double click still "
                                 "works either way.", "single_click"))
        opening.add(self._switch("Reopen the last tabs",
                                 "Start with whatever was open when the "
                                 "window was closed.", "restore_session"))

        terminal = Adw.EntryRow(title="Terminal")
        terminal.set_text(self.config["terminal"])
        terminal.set_show_apply_button(True)
        found = winenv.terminal_argv("~")
        terminal.set_tooltip_text(
            "Empty means whichever this machine has — "
            + (found[0] if found else "and it has none"))
        terminal.connect("apply",
                         lambda r: self._set("terminal", r.get_text().strip()))
        opening.add(terminal)
        page.add(opening)

        asking = Adw.PreferencesGroup(
            title="Asking first",
            description="Deleting for good always asks. The trash is itself "
                        "an undo, so it does not have to.")
        asking.add(self._switch("Before moving to the trash", "",
                                "confirm_trash"))
        asking.add(self._switch("Before deleting for good", "",
                                "confirm_delete"))
        asking.add(self._slider("Ask when more than this many are selected",
                                "Zero never asks.",
                                "confirm_many", 0, 500, 10))
        page.add(asking)

        searching = Adw.PreferencesGroup(title="Searching")
        searching.add(self._slider("Largest file to search inside",
                                   "In megabytes.",
                                   "search_content_max_mb", 0, 256, 4))
        searching.add(self._switch("Follow links while searching",
                                   "Off is safer: a link back up the tree is "
                                   "a search that never ends.",
                                   "search_follow_links"))
        page.add(searching)
        return page


def _shaped(old, value):
    """Keep a setting the type it started as — a float stays a float."""
    if isinstance(old, bool):
        return bool(value)
    if isinstance(old, int):
        return int(round(value))
    return round(float(value), 3)


def show(parent: Gtk.Widget, config, on_change) -> None:
    Preferences(parent, config, on_change).present()
