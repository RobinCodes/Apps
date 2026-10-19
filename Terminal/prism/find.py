"""Searching the scrollback.

VTE does the searching — it has the text, it knows where the lines wrapped,
and it can highlight a hit in place, which is most of the value. What this
adds is the bar: a regex that is built from what has been typed so far, the
two toggles that change how it is built, and the wrap-around that makes
Enter keep working at the end of the buffer.

There is deliberately no "3 of 47" counter. VTE will find the next match but
it will not count them, so a counter would mean pulling the whole scrollback
out as a string and running the pattern over it again on every keystroke —
ten thousand lines of text re-scanned to print a number. What the bar says
instead is whether the last search found anything, which is the part anyone
actually acts on.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Vte", "3.91")

from gi.repository import Gdk, GLib, Gtk, Vte  # noqa: E402

from . import glass  # noqa: E402
from .terminal import (PCRE2_CASELESS, PCRE2_MULTILINE, PCRE2_NO_UTF_CHECK,
                       PCRE2_UTF)  # noqa: E402


def build_regex(text: str, regex_mode: bool, case_sensitive: bool):
    """A VTE search regex, or None when `text` cannot make one.

    An unfinished regular expression is the normal state of the field while
    somebody is typing one — "(" is on the way to "(foo|bar)" — so a pattern
    that will not compile is not an error to report, it is a search that has
    not been asked for yet.
    """
    if not text:
        return None
    pattern = text if regex_mode else GLib.Regex.escape_string(text, -1)
    flags = PCRE2_UTF | PCRE2_NO_UTF_CHECK | PCRE2_MULTILINE
    if not case_sensitive:
        flags |= PCRE2_CASELESS
    try:
        return Vte.Regex.new_for_search(pattern, -1, flags)
    except (GLib.Error, TypeError):
        return None


class FindBar(Gtk.Box):
    """The bar itself: a card that docks to the top right of the window."""

    __gtype_name__ = "PrismFindBar"

    def __init__(self, scheme, light, config, pane_for_search, on_closed):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.START)
        self.set_margin_top(58)
        self.set_margin_end(22)
        self._pane_for_search = pane_for_search
        self._on_closed = on_closed

        self.card = glass.Card(glass.Material.card(
            scheme, config["opacity"], grain=config["grain"],
            frosted=bool(config["panel_blur"])),
            radius=int(config["corner_radius"]) + 4, light=light)
        self.append(self.card)

        row = Gtk.Box(spacing=6)
        row.set_margin_start(10)
        row.set_margin_end(10)
        row.set_margin_top(9)
        row.set_margin_bottom(9)
        self.card.append(row)

        self.entry = Gtk.Entry(placeholder_text="Find in output")
        self.entry.set_size_request(230, -1)
        self.entry.connect("changed", lambda _e: self._rearm())
        self.entry.connect("activate", lambda _e: self.step(1))
        row.append(self.entry)

        self.case = Gtk.ToggleButton(label="Aa", tooltip_text="Match case",
                                     css_classes=["toggle", "flat"])
        self.case.connect("toggled", lambda _b: self._rearm())
        row.append(self.case)

        self.regex = Gtk.ToggleButton(label=".*", tooltip_text="Regular expression",
                                      css_classes=["toggle", "flat"])
        self.regex.connect("toggled", lambda _b: self._rearm())
        row.append(self.regex)

        for icon, tip, step in (("go-up-symbolic", "Previous match", -1),
                                ("go-down-symbolic", "Next match", 1)):
            button = Gtk.Button(icon_name=icon, tooltip_text=tip,
                                css_classes=["flat"])
            button.connect("clicked", lambda _b, s=step: self.step(s))
            row.append(button)

        close = Gtk.Button(icon_name="window-close-symbolic",
                           tooltip_text="Close (Esc)", css_classes=["flat"])
        close.connect("clicked", lambda _b: self.close())
        row.append(close)

        self.status = Gtk.Label(xalign=0.0)
        self.status.add_css_class("palette-hint")
        self.status.set_margin_start(12)
        self.status.set_margin_bottom(8)
        self.status.set_visible(False)
        self.card.append(self.status)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # ------------------------------------------------------------------- --

    def restyle(self, scheme, light, config):
        self.card.set_material(glass.Material.card(
            scheme, config["opacity"], grain=config["grain"],
            frosted=bool(config["panel_blur"])))
        self._radius = int(config["corner_radius"]) + 4
        self.card.set_light(light)

    def open(self, seed: str = ""):
        self.set_visible(True)
        if seed:
            self.entry.set_text(seed)
        self._rearm()
        GLib.idle_add(self._focus_entry)

    def _focus_entry(self):
        self.entry.grab_focus()
        self.entry.select_region(0, -1)
        return False

    def close(self):
        self.set_visible(False)
        pane = self._pane_for_search()
        if pane is not None:
            pane.terminal.search_set_regex(None, 0)
            pane.terminal.unselect_all()
        self.status.set_visible(False)
        self._on_closed()

    def _rearm(self):
        """Rebuild the pattern and search from where the view is now.

        Searching as the pattern grows, rather than only on Enter, is what
        makes a find bar feel like a find bar — but only forwards and only
        from the current position, so that typing does not drag the view
        backwards through the buffer.
        """
        pane = self._pane_for_search()
        if pane is None:
            return
        regex = build_regex(self.entry.get_text(), self.regex.get_active(),
                            self.case.get_active())
        pane.terminal.search_set_wrap_around(True)
        pane.terminal.search_set_regex(regex, 0)
        if regex is None:
            self.status.set_visible(False)
            pane.terminal.unselect_all()
            return
        self._report(pane.terminal.search_find_next())

    def step(self, direction: int):
        pane = self._pane_for_search()
        if pane is None:
            return
        found = (pane.terminal.search_find_next() if direction > 0
                 else pane.terminal.search_find_previous())
        self._report(found)

    def _report(self, found: bool):
        if found:
            self.status.set_visible(False)
        else:
            self.status.set_text("No matches")
            self.status.set_visible(True)

    def _on_key(self, _controller, keyval, _code, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.step(-1 if state & Gdk.ModifierType.SHIFT_MASK else 1)
            return True
        return False
