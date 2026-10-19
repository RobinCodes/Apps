"""The preferences dialog.

Every control here writes its setting the moment it moves and the window
applies it straight away, so the dialog is also the preview — which matters
more than usual for this app, since most of what is in it is about how
something looks and the only way to judge that is to see it. There is no OK
button to press and nothing to undo, because nothing is pending.

The dialog is an Adw.PreferencesDialog and therefore looks like Adwaita
rather than like glass. That is on purpose: the stylesheet pulls the
backgrounds off its rows so the glass shows through, but the layout, the
focus order and the keyboard handling are all libadwaita's, and a dialog
is one of the places where matching the rest of the desktop is worth more
than matching the rest of the app.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Adw, Gtk, Pango  # noqa: E402

from . import housestyle  # noqa: E402
from .shell import monospace_family  # noqa: E402


def monospace_filter() -> Gtk.CustomFilter:
    """A font filter that keeps only the fixed-width families.

    A proportional font in a terminal is not a preference, it is a bug that
    takes a while to notice, so the picker never offers one.
    """
    def keep(item):
        family = item
        if isinstance(item, Pango.FontFace):
            family = item.get_family()
        return isinstance(family, Pango.FontFamily) and family.is_monospace()

    return Gtk.CustomFilter.new(keep)


class PrefsDialog(Adw.PreferencesDialog):
    """`apply` is called with no arguments whenever anything changes."""

    __gtype_name__ = "PrismPrefs"

    def __init__(self, config, apply):
        super().__init__(title="Preferences", search_enabled=True)
        self._config = config
        self._apply = apply
        self.set_size_request(380, 480)
        self.add(self._look_page())
        self.add(self._text_page())
        self.add(self._behaviour_page())
        self.add(self._shell_page())

    # ------------------------------------------------------------ pieces --

    def _changed(self, key, value):
        if self._config.set(key, value):
            self._apply()

    def _switch(self, group, title, subtitle, key):
        row = Adw.SwitchRow(title=title, subtitle=subtitle,
                            active=self._config[key])
        row.connect("notify::active",
                    lambda r, _p: self._changed(key, r.get_active()))
        group.add(row)
        return row

    def _spin(self, group, title, subtitle, key, low, high, step=1, digits=0):
        row = Adw.SpinRow.new_with_range(low, high, step)
        row.set_title(title)
        row.set_subtitle(subtitle)
        row.set_digits(digits)
        row.set_value(self._config[key])
        caster = float if digits else int
        row.connect("notify::value",
                    lambda r, _p: self._changed(key, caster(r.get_value())))
        group.add(row)
        return row

    def _choice(self, group, title, subtitle, key, options):
        """options is [(value, label), ...]."""
        labels = Gtk.StringList()
        for _value, label in options:
            labels.append(label)
        row = Adw.ComboRow(title=title, subtitle=subtitle, model=labels)
        current = self._config[key]
        for index, (value, _label) in enumerate(options):
            if value == current:
                row.set_selected(index)
                break
        row.connect("notify::selected",
                    lambda r, _p: self._changed(key, options[r.get_selected()][0]))
        group.add(row)
        return row

    def _slider(self, group, title, subtitle, key, low, high, step, digits=2):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, low, high, step)
        scale.set_size_request(190, -1)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        scale.set_digits(digits)
        scale.set_value(self._config[key])
        scale.set_valign(Gtk.Align.CENTER)
        scale.connect("value-changed",
                      lambda s: self._changed(key, round(s.get_value(), digits)))
        row.add_suffix(scale)
        group.add(row)
        return row

    # ------------------------------------------------------------- pages --

    def _look_page(self):
        page = Adw.PreferencesPage(title="Look", icon_name="applications-graphics-symbolic")

        # The rows come from the house, so this app's theme list and its
        # order are the same as every other app's here. Each theme is a
        # palette and a glass to put it behind; in this one it is also the
        # sixteen ANSI colours, which is why they are designed together.
        page.add(housestyle.theme_group(
            self._config, self._apply,
            description=housestyle.THEME_BLURB + " In a terminal a theme is "
            "also the sixteen ANSI colours, which is why the palette and the "
            "glass are drawn together rather than picked separately."))

        material = Adw.PreferencesGroup(
            title="Glass",
            description="How much light the window lets through, and how much "
                        "of it moves. Turning the motion off costs nothing in "
                        "looks when the window is still, and saves a repaint "
                        "thirty times a second when it is not.")
        page.add(material)
        self._slider(material, "Opacity", "Of the window as a whole",
                     "opacity", 0.55, 1.0, 0.01)
        self._switch(material, "Living light",
                     "The slow colour behind the glass", "motion")
        self._switch(material, "Specular highlight",
                     "A sheen that follows the pointer", "specular")
        self._switch(material, "Frosting",
                     "Fine grain, which also stops gradients banding", "grain")
        self._spin(material, "Panel blur",
                   "How far the command palette blurs the terminal behind it",
                   "panel_blur", 0, 80, 2)

        shape = Adw.PreferencesGroup(title="Shape")
        page.add(shape)
        self._spin(shape, "Corner radius", "", "corner_radius", 0, 28)
        self._spin(shape, "Padding", "Between the glass and the first glyph",
                   "pane_padding", 0, 48)
        self._choice(shape, "Tab bar", "", "tab_bar",
                     [("auto", "Only with more than one tab"),
                      ("always", "Always"), ("never", "Never")])
        return page

    def _text_page(self):
        page = Adw.PreferencesPage(title="Text", icon_name="font-x-generic-symbolic")

        font = Adw.PreferencesGroup(title="Font")
        page.add(font)

        row = Adw.ActionRow(title="Family and size",
                            subtitle="Fixed-width families only")
        dialog = Gtk.FontDialog(title="Terminal font")
        dialog.set_filter(monospace_filter())
        button = Gtk.FontDialogButton(dialog=dialog)
        button.set_level(Gtk.FontLevel.FONT)
        button.set_valign(Gtk.Align.CENTER)
        desc = Pango.FontDescription()
        desc.set_family(monospace_family(self._config["font_family"]))
        desc.set_size(int(self._config["font_size"] * Pango.SCALE))
        button.set_font_desc(desc)

        def picked(*_args):
            chosen = button.get_font_desc()
            if chosen is None:
                return
            family = chosen.get_family() or ""
            size = chosen.get_size() / Pango.SCALE
            moved = self._config.set("font_family", family)
            moved |= self._config.set("font_size", float(size or 11.0))
            if moved:
                self._apply()

        button.connect("notify::font-desc", picked)
        row.add_suffix(button)
        font.add(row)

        self._slider(font, "Line height", "", "line_height", 0.8, 2.0, 0.05)
        self._slider(font, "Letter spacing", "", "letter_spacing", 0.8, 2.0, 0.05)
        self._switch(font, "Bold is bright",
                     "Draw bold text in the bright half of the palette",
                     "bold_is_bright")

        cursor = Adw.PreferencesGroup(title="Cursor")
        page.add(cursor)
        self._choice(cursor, "Shape", "", "cursor_shape",
                     [("beam", "Beam"), ("block", "Block"),
                      ("underline", "Underline")])
        self._switch(cursor, "Blink", "", "cursor_blink")
        return page

    def _behaviour_page(self):
        page = Adw.PreferencesPage(title="Behaviour",
                                   icon_name="preferences-system-symbolic")

        scroll = Adw.PreferencesGroup(title="Scrolling")
        page.add(scroll)
        self._spin(scroll, "Scrollback", "Lines kept; -1 for no limit",
                   "scrollback_lines", -1, 1000000, 1000)
        self._switch(scroll, "Scroll on output",
                     "Jump to the bottom whenever anything is printed",
                     "scroll_on_output")
        self._switch(scroll, "Scroll on keystroke",
                     "Jump to the bottom when you type", "scroll_on_keystroke")

        text = Adw.PreferencesGroup(title="Selection")
        page.add(text)
        self._switch(text, "Copy on select",
                     "Selecting text copies it straight away", "copy_on_select")
        self._switch(text, "Check what is pasted",
                     "Ask first when the clipboard holds more than one line",
                     "paste_guard")
        self._switch(text, "Hide the pointer while typing", "", "mouse_autohide")

        rest = Adw.PreferencesGroup(title="Everything else")
        page.add(rest)
        self._switch(rest, "Clickable links",
                     "URLs and file:line references in the output", "hyperlinks")
        self._switch(rest, "Sixel graphics", "", "sixel")
        self._switch(rest, "Visible bell", "Light the pane up briefly",
                     "visual_bell")
        self._switch(rest, "Audible bell", "", "audible_bell")
        self._switch(rest, "Ask before closing",
                     "When something is still running in the tab",
                     "confirm_close")
        return page

    def _shell_page(self):
        page = Adw.PreferencesPage(title="Shell", icon_name="utilities-terminal-symbolic")

        group = Adw.PreferencesGroup(
            title="What runs",
            description="Empty means your login shell, which is what you want "
                        "unless you know otherwise.")
        page.add(group)

        shell = Adw.EntryRow(title="Command", text=self._config["shell"])
        shell.connect("notify::text",
                      lambda r, _p: self._changed("shell", r.get_text()))
        group.add(shell)
        self._switch(group, "Login shell",
                     "Start it as a login shell", "login_shell")

        start = Adw.EntryRow(title="Run at startup",
                             text=self._config["startup_command"])
        start.connect("notify::text",
                      lambda r, _p: self._changed("startup_command", r.get_text()))
        group.add(start)

        session = Adw.PreferencesGroup(title="Session")
        page.add(session)
        self._switch(session, "Reopen last session",
                     "Put back the tabs and their directories", "restore_session")
        self._switch(session, "Directory in the title", "", "cwd_in_title")
        return page
