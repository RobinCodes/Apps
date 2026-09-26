"""Find and replace: the bar at the bottom, and the searching behind it.

Gtk.TextIter has `forward_search`, and using it for a live find means walking
the buffer from the top on every keystroke to count the matches. That is fine
for a note and wasteful for a thesis, so matching is done once over the text
as a string — which is also the only way to offer a regular expression, and
the only way to make "whole word" and "match case" mean the same thing here as
they do everywhere else.

What the bar owns is the *found* tags on the buffer. They are put on when a
search runs and taken off when it closes, and the editor knows nothing about
them.
"""

from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402

MAX_MATCHES = 5000


def find_all(text: str, needle: str, *, case: bool = False, word: bool = False,
             regex: bool = False) -> list[tuple[int, int]]:
    """Every match as (start, end) character offsets. Never raises."""
    if not needle or not text:
        return []
    try:
        if regex:
            pattern = needle
        else:
            pattern = re.escape(needle)
            if word:
                pattern = r"\b" + pattern + r"\b"
        compiled = re.compile(pattern, 0 if case else re.IGNORECASE)
    except re.error:
        return []

    out = []
    for match in compiled.finditer(text):
        if match.start() == match.end():
            continue          # an empty match would loop for ever
        out.append((match.start(), match.end()))
        if len(out) >= MAX_MATCHES:
            break
    return out


def substitute(text: str, needle: str, replacement: str, *, case: bool = False,
               word: bool = False, regex: bool = False) -> tuple[str, int]:
    """Replace every match; returns the new text and how many there were."""
    if not needle:
        return text, 0
    try:
        if regex:
            compiled = re.compile(needle, 0 if case else re.IGNORECASE)
            return compiled.subn(replacement, text)
        pattern = re.escape(needle)
        if word:
            pattern = r"\b" + pattern + r"\b"
        compiled = re.compile(pattern, 0 if case else re.IGNORECASE)
        return compiled.subn(lambda _m: replacement, text)
    except re.error:
        return text, 0


class FindBar(Gtk.Revealer):
    """The find and replace strip, driving whichever editor it is given."""

    def __init__(self, get_editor):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        self.get_editor = get_editor
        self._matches: list[tuple[int, int]] = []
        self._current = -1
        self._timer = 0

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("find-bar")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.entry = Gtk.SearchEntry(placeholder_text="Find", hexpand=True)
        self.entry.connect("search-changed", lambda *_: self._schedule())
        self.entry.connect("activate", lambda *_: self.next_match())
        self.entry.connect("next-match", lambda *_: self.next_match())
        self.entry.connect("previous-match", lambda *_: self.previous_match())
        self.entry.connect("stop-search", lambda *_: self.close())
        top.append(self.entry)

        self.count = Gtk.Label(label="", css_classes=["find-count"], xalign=1.0)
        top.append(self.count)

        self.previous_button = Gtk.Button(
            icon_name="go-up-symbolic", css_classes=["flat"],
            tooltip_text="Previous match (Shift+Enter)")
        self.previous_button.connect("clicked", lambda _b: self.previous_match())
        top.append(self.previous_button)
        self.next_button = Gtk.Button(
            icon_name="go-down-symbolic", css_classes=["flat"],
            tooltip_text="Next match (Enter)")
        self.next_button.connect("clicked", lambda _b: self.next_match())
        top.append(self.next_button)

        self.case = Gtk.ToggleButton(label="Aa", css_classes=["flat"],
                                     tooltip_text="Match case")
        self.case.connect("toggled", lambda *_: self._schedule())
        top.append(self.case)
        self.word = Gtk.ToggleButton(label="ab|", css_classes=["flat"],
                                     tooltip_text="Whole word only")
        self.word.connect("toggled", lambda *_: self._schedule())
        top.append(self.word)
        self.regex = Gtk.ToggleButton(label=".*", css_classes=["flat"],
                                      tooltip_text="Regular expression")
        self.regex.connect("toggled", lambda *_: self._schedule())
        top.append(self.regex)

        self.replace_toggle = Gtk.ToggleButton(
            icon_name="edit-find-replace-symbolic", css_classes=["flat"],
            tooltip_text="Replace")
        self.replace_toggle.connect("toggled", self._toggle_replace)
        top.append(self.replace_toggle)

        close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"],
                           tooltip_text="Close (Esc)")
        close.connect("clicked", lambda _b: self.close())
        top.append(close)
        box.append(top)

        self.replace_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                                   visible=False)
        self.replace_entry = Gtk.Entry(placeholder_text="Replace with", hexpand=True)
        self.replace_entry.connect("activate", lambda *_: self.replace_one())
        self.replace_row.append(self.replace_entry)
        one = Gtk.Button(label="Replace")
        one.connect("clicked", lambda _b: self.replace_one())
        self.replace_row.append(one)
        every = Gtk.Button(label="All")
        every.connect("clicked", lambda _b: self.replace_all())
        self.replace_row.append(every)
        box.append(self.replace_row)

        self.set_child(box)
        self._optional = (self.previous_button, self.next_button,
                          self.case, self.word, self.regex)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    def set_narrow(self, narrow: bool):
        """Drop the buttons that have keys, when there is no room for them.

        Enter and Shift+Enter still step through the matches, and the three
        option toggles are the sort of thing you set once. Nine widgets in a
        row is what stops a 455px column from fitting the window at all.
        """
        for button in self._optional:
            button.set_visible(not narrow)

    # -- opening and closing -----------------------------------------------

    def open(self, replace=False, seed=""):
        editor = self.get_editor()
        if editor is None:
            return
        if seed:
            self.entry.set_text(seed)
        elif editor.selection_length() and "\n" not in editor.selected_text():
            self.entry.set_text(editor.selected_text())
        self.replace_toggle.set_active(replace)
        self.set_reveal_child(True)
        self.entry.grab_focus()
        self.entry.select_region(0, -1)
        self._search()

    def close(self):
        self.set_reveal_child(False)
        self._clear_tags()
        self._matches = []
        self._current = -1
        editor = self.get_editor()
        if editor is not None:
            editor.view.grab_focus()

    def is_open(self) -> bool:
        return self.get_reveal_child()

    def _toggle_replace(self, button):
        self.replace_row.set_visible(button.get_active())
        if button.get_active():
            self.replace_entry.grab_focus()

    def _on_key(self, _controller, keyval, _keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and \
                state & Gdk.ModifierType.SHIFT_MASK:
            self.previous_match()
            return True
        return False

    # -- searching ---------------------------------------------------------

    def _schedule(self):
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(120, self._search)

    def _search(self):
        self._timer = 0
        editor = self.get_editor()
        if editor is None:
            return GLib.SOURCE_REMOVE

        needle = self.entry.get_text()
        self._clear_tags()
        self._matches = find_all(
            editor.get_text(), needle,
            case=self.case.get_active(), word=self.word.get_active(),
            regex=self.regex.get_active())

        if not needle:
            self.count.set_text("")
            self.remove_css_class("find-no-match")
            return GLib.SOURCE_REMOVE

        if not self._matches:
            self.count.set_text("0")
            self.add_css_class("find-no-match")
            return GLib.SOURCE_REMOVE

        self.remove_css_class("find-no-match")
        buf = editor.buffer
        tag = buf.get_tag_table().lookup("found")
        for start, end in self._matches:
            buf.apply_tag(tag, buf.get_iter_at_offset(start), buf.get_iter_at_offset(end))

        # Start from where the cursor already is, not from the top.
        cursor = buf.get_iter_at_mark(buf.get_insert()).get_offset()
        self._current = next((index for index, (start, _) in enumerate(self._matches)
                              if start >= cursor), 0)
        self._show_current(scroll=False)
        return GLib.SOURCE_REMOVE

    def _clear_tags(self):
        editor = self.get_editor()
        if editor is None:
            return
        buf = editor.buffer
        start, end = buf.get_bounds()
        for name in ("found", "found-current"):
            buf.remove_tag(buf.get_tag_table().lookup(name), start, end)

    def _show_current(self, scroll=True):
        editor = self.get_editor()
        if editor is None or not self._matches:
            return
        buf = editor.buffer
        tag = buf.get_tag_table().lookup("found-current")
        start_iter, end_iter = buf.get_bounds()
        buf.remove_tag(tag, start_iter, end_iter)

        start, end = self._matches[self._current]
        buf.apply_tag(tag, buf.get_iter_at_offset(start), buf.get_iter_at_offset(end))
        self.count.set_text(f"{self._current + 1}/{len(self._matches)}")
        if scroll:
            editor.view.scroll_to_iter(buf.get_iter_at_offset(start), 0.3, True, 0.0, 0.4)

    def next_match(self):
        if not self._matches:
            return
        self._current = (self._current + 1) % len(self._matches)
        self._show_current()

    def previous_match(self):
        if not self._matches:
            return
        self._current = (self._current - 1) % len(self._matches)
        self._show_current()

    # -- replacing ---------------------------------------------------------

    def replace_one(self):
        editor = self.get_editor()
        if editor is None or not self._matches:
            return
        start, end = self._matches[self._current]
        replacement = self.replace_entry.get_text()

        if self.regex.get_active():
            # \1 and friends have to mean what they meant when the match was
            # found, so the pattern is re-matched at exactly this span rather
            # than run over a slice of the text, where an anchor or a
            # lookaround would see a different string either side of it.
            try:
                compiled = re.compile(self.entry.get_text(),
                                      0 if self.case.get_active() else re.IGNORECASE)
                here = compiled.match(editor.get_text(), start, end)
            except re.error:
                return
            if here is None:
                return
            replacement = here.expand(replacement)

        buf = editor.buffer
        buf.begin_user_action()
        buf.delete(buf.get_iter_at_offset(start), buf.get_iter_at_offset(end))
        buf.insert(buf.get_iter_at_offset(start), replacement)
        buf.end_user_action()
        buf.place_cursor(buf.get_iter_at_offset(start + len(replacement)))
        self._search()

    def replace_all(self):
        editor = self.get_editor()
        if editor is None:
            return
        needle = self.entry.get_text()
        if not needle:
            return
        text = editor.get_text()
        line, column = editor.cursor_position()
        new, count = substitute(
            text, needle, self.replace_entry.get_text(),
            case=self.case.get_active(), word=self.word.get_active(),
            regex=self.regex.get_active())
        if count == 0 or new == text:
            self.count.set_text("0")
            return

        buf = editor.buffer
        buf.begin_user_action()
        start, end = buf.get_bounds()
        buf.delete(start, end)
        buf.insert(buf.get_start_iter(), new)
        buf.end_user_action()
        editor.goto_line(line, focus=False)
        self.count.set_text(f"{count} replaced")
        self._matches = []
        self._current = -1
