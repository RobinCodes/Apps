r"""The source editor: a Gtk.TextView that knows what Markdown looks like.

GtkSourceView 5 is not installed on this machine, so the three things it would
have given us are done directly: syntax highlighting on the text buffer, a
line-number gutter drawn beside it, and the editing courtesies that are the
whole difference between writing Markdown in a text box and writing it in an
editor.

The highlighting is *semantic* rather than decorative. A heading is drawn at
the size it will be; bold source is bold, a link is underlined, a fence is
monospace on a tinted ground. The markers themselves are dimmed rather than
hidden — hiding them is a trick that looks wonderful until you try to put the
cursor between two asterisks you can no longer see.

The courtesies are the other half. Enter continues the list you are in,
renumbers as it goes, and gets out of the way when you press it twice on an
empty item. Tab indents the item rather than inserting a tab. Ctrl+B wraps the
selection and unwraps it again if it is already wrapped. Paste a URL over
selected text and you get a link. None of it is clever; all of it is the thing
you were about to type by hand.

The gutter is a separate DrawingArea *outside* the ScrolledWindow, painted
from the scroll offset. Putting it inside would fight the TextView's own
Scrollable implementation.
"""

from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import cards as cardnotes, widgets  # noqa: E402
from .completion import LinkCompletion  # noqa: E402
from .config import DEFAULTS  # noqa: E402

HIGHLIGHT_DEBOUNCE_MS = 80
FULL_HIGHLIGHT_LIMIT = 250_000   # past this, only the visible region is tagged
INDENT = "  "

CURSOR = "\x00CURSOR\x00"
SELECTION = "\x00SEL\x00"

# What Ctrl+± and a pinch are allowed to reach. Below the first the gutter
# stops being readable; above the second a line of prose has four words in it.
MIN_FONT_SIZE = 6
MAX_FONT_SIZE = 32


# --------------------------------------------------------------- the rules --

_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})\s*(\S*)")
_HEADING = re.compile(r"^( {0,3}#{1,6})(\s+.*)?$")
_QUOTE = re.compile(r"^(\s*(?:>\s?)+)(.*)$")
_BULLET = re.compile(r"^(\s*)([-*+])(\s+)(.*)$")
_ORDERED = re.compile(r"^(\s*)(\d{1,9})([.)])(\s+)(.*)$")
_TASK = re.compile(r"^(\[[ xX]\])(\s*)(.*)$")
_RULE = re.compile(r"^ {0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$")
_TABLE_ROW = re.compile(r"^\s{0,3}\|.*\|?\s*$")
_TABLE_DELIM = re.compile(r"^\s{0,3}\|?[\s:-]*-[\s:|-]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)\s*$")
_FRONT = re.compile(r"^(---|\+\+\+)\s*$")
_REF_DEF = re.compile(r"^ {0,3}\[[^\]]+\]:\s*\S+")
_HTML = re.compile(r"^\s{0,3}</?[a-zA-Z!][^>]*>")

_INLINE = re.compile(
    r"(?P<code>(?<!`)(`+)(?!`)(?:[^`]|(?!\2)`)+?\2(?!`))"
    # The wiki link is brain.py's `_WIKILINK` without its groups, which is
    # the one the backlinks are counted with; the suite holds the two together.
    r"|(?P<wikilink>!?\[\[[^\[\]\n|#^]+(?:#[^\[\]\n|]*)?(?:\|[^\[\]\n]*)?\]\])"
    r"|(?P<image>!\[[^\]]*\]\((?:[^()\s]|\([^)]*\))*\))"
    r"|(?P<link>\[[^\]]*\]\((?:[^()\s]|\([^)]*\))*\))"
    r"|(?P<reflink>\[[^\]]*\]\[[^\]]*\])"
    r"|(?P<footref>\[\^[^\]]+\])"
    r"|(?P<autolink><[a-zA-Z][a-zA-Z0-9+.-]*:[^<>\s]*>)"
    r"|(?P<url>(?<![\w/])(?:https?://|www\.)[^\s<>\[\]()]+)"
    r"|(?P<strongem>(?<!\*)\*\*\*(?!\s)(?:[^*]|\*(?!\*\*))+?\*\*\*)"
    r"|(?P<strong>(?<!\*)\*\*(?!\s)(?:[^*]|\*(?!\*))+?\*\*(?!\*)"
    r"|(?<![\w_])__(?!\s)[^_]+?__(?![\w_]))"
    r"|(?P<emphasis>(?<!\*)\*(?!\s|\*)[^*\n]+?\*(?!\*)"
    r"|(?<![\w_])_(?!\s)[^_\n]+?_(?![\w_]))"
    r"|(?P<strike>~~(?!\s)(?:[^~]|~(?!~))+?~~)"
    r"|(?P<mark>==(?!\s)(?:[^=]|=(?!=))+?==)"
    r"|(?P<math>(?<![\w$])\$(?!\s)(?:[^$\n\\]|\\.)+?(?<!\s)\$(?!\d))"
    r"|(?P<entity>&(?:#x?[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});)"
    r"|(?P<escape>\\[\\`*_{}\[\]()#+\-.!|~<>\"'$=])"
    r"|(?P<tag></?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*)?/?>)"
)

# Tag name -> (light, dark) appearance. Keeping both here rather than two
# tables means a colour can never be added to one theme and forgotten in the
# other, which is how a dark-mode editor ends up with black text on black.
STYLES = {
    "h1":        {"scale": 1.55, "weight": 800, "light": "#1b3a6b", "dark": "#a8c7ff"},
    "h2":        {"scale": 1.35, "weight": 800, "light": "#1b3a6b", "dark": "#a8c7ff"},
    "h3":        {"scale": 1.20, "weight": 700, "light": "#245078", "dark": "#9ec5f0"},
    "h4":        {"scale": 1.10, "weight": 700, "light": "#245078", "dark": "#9ec5f0"},
    "h5":        {"scale": 1.04, "weight": 700, "light": "#245078", "dark": "#9ec5f0"},
    "h6":        {"scale": 1.00, "weight": 700, "light": "#3b6188", "dark": "#8fb4dd"},
    "marker":    {"light": "#9aa0a6", "dark": "#6f7680"},
    "strong":    {"weight": 800},
    "emphasis":  {"style": Pango.Style.ITALIC},
    "strike":    {"strike": True, "light": "#77767b", "dark": "#9a9996"},
    "mark":      {"light_bg": "#fff3a3", "dark_bg": "#6b5a1233"},
    "code":      {"mono": True, "light": "#a33d1f", "dark": "#ffab8f",
                  "light_bg": "#00000010", "dark_bg": "#ffffff14"},
    "fence":     {"mono": True, "light_bg": "#00000010", "dark_bg": "#ffffff0d"},
    "fenceline": {"mono": True, "light": "#7a7a7a", "dark": "#9aa0a6"},
    "quote":     {"style": Pango.Style.ITALIC, "light": "#3a6b4a", "dark": "#94d8a9"},
    "listmark":  {"weight": 800, "light": "#8a3ffc", "dark": "#d0a8ff"},
    "task":      {"weight": 800, "light": "#1a7f37", "dark": "#7ee787"},
    "link":      {"underline": True, "light": "#0b57d0", "dark": "#7fb0ee"},
    "url":       {"light": "#0b57d0", "dark": "#7fb0ee"},
    "math":      {"light": "#137a52", "dark": "#7ee0a8"},
    "rule":      {"weight": 800, "light": "#9aa0a6", "dark": "#6f7680"},
    "table":     {"mono": True, "light": "#5a4b8a", "dark": "#c0b3ea"},
    "html":      {"mono": True, "light": "#77767b", "dark": "#9a9996"},
    "front":     {"mono": True, "light": "#77767b", "dark": "#9a9996",
                  "light_bg": "#00000008", "dark_bg": "#ffffff0a"},
    "escape":    {"light": "#9aa0a6", "dark": "#6f7680"},
    "refdef":    {"light": "#8250df", "dark": "#d2a8ff"},
    # Cards (SYSTEM.md §7). The `::` and the Q:/A: are what make a line a
    # question, so they carry the colour, the way a list marker does; a gap
    # is tinted like a highlight, in the cards' own amber, because the words
    # in it are the ones the card will hide.
    "cardmark":  {"weight": 800, "light": "#a34f00", "dark": "#ffb86b"},
    "cloze":     {"light_bg": "#ffe2bf", "dark_bg": "#ff9f4033"},
}

HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def _head_width(match) -> int:
    """How many characters of a matched list line are the marker and its space.

    Not len(match.group(0)) — that is the whole line, including the text, and
    using it is how Backspace at the start of an item deletes the item.
    """
    body = (match.group(4) if match.re is _BULLET else match.group(5)) or ""
    return len(match.group(0)) - len(body)


class Editor(Gtk.Box):
    """Gutter + text view, with Markdown highlighting and the courtesies."""

    def __init__(self, on_changed=None, on_cursor=None, on_scrolled=None,
                 complete=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.on_changed = on_changed
        self.on_cursor = on_cursor
        self.on_scrolled = on_scrolled

        self._highlight_source = 0
        self._current_line = -1
        self._font_family = str(DEFAULTS["editor_font_family"])
        self._font_size = int(DEFAULTS["editor_font_size"])
        self._loading = False
        self._focus_mode = False
        self._typewriter = False
        self._show_gutter = True
        self.markdown = True

        self.buffer = Gtk.TextBuffer()
        self.view = Gtk.TextView(
            buffer=self.buffer,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            left_margin=12,
            right_margin=12,
            top_margin=8,
            bottom_margin=400,   # room to scroll the last line off the bottom
            pixels_below_lines=2,
            accepts_tab=False,
            hexpand=True,
            vexpand=True,
        )
        self.view.add_css_class("editor-text")

        self._css = Gtk.CssProvider()
        self.view.get_style_context().add_provider(
            self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._apply_font()

        self.scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_child(self.view)

        self.gutter = Gtk.DrawingArea()
        self.gutter.add_css_class("editor-gutter")
        self.gutter.set_draw_func(self._draw_gutter)
        self.gutter.set_content_width(44)

        self.append(self.gutter)
        self.append(self.scroller)

        self._make_tags()

        self.buffer.connect("changed", self._on_changed)
        self.buffer.connect("notify::cursor-position", self._on_cursor_moved)
        self.scroller.get_vadjustment().connect("value-changed", self._on_scrolled)
        self.view.connect("paste-clipboard", self._on_paste)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.view.add_controller(keys)

        # `[[` offers the notes there are to link to; `complete(query)` is
        # where they come from, which the editor neither knows nor needs to.
        self.completion = LinkCompletion(self, complete) if complete else None
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self.completion and self.completion.close())
        self.view.add_controller(focus)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self._on_ctrl_scroll)
        self.scroller.add_controller(scroll)
        self._scroll_carry = 0.0

        pinch = Gtk.GestureZoom()
        pinch.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        pinch.connect("begin", lambda *_: setattr(self, "_pinch_from", self._font_size))
        pinch.connect("scale-changed", self._on_pinch)
        self.scroller.add_controller(pinch)
        self._pinch_from = self._font_size

        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self._retheme())

    # -- appearance --------------------------------------------------------

    def _make_tags(self):
        table = self.buffer.get_tag_table()
        for name in STYLES:
            if not table.lookup(name):
                self.buffer.create_tag(name)
        # Created after the syntax tags, so they paint above them.
        for name in ("current-line", "dimmed", "found", "found-current"):
            if not table.lookup(name):
                self.buffer.create_tag(name)
        self._retheme()

    def _retheme(self):
        dark = Adw.StyleManager.get_default().get_dark()
        table = self.buffer.get_tag_table()
        for name, style in STYLES.items():
            tag = table.lookup(name)
            colour = style.get("dark" if dark else "light")
            background = style.get("dark_bg" if dark else "light_bg")
            tag.set_property("foreground", colour)
            tag.set_property("background", background)
            tag.set_property("weight", style.get("weight", 400))
            tag.set_property("style", style.get("style", Pango.Style.NORMAL))
            tag.set_property("strikethrough", bool(style.get("strike")))
            tag.set_property("underline",
                             Pango.Underline.SINGLE if style.get("underline")
                             else Pango.Underline.NONE)
            tag.set_property("scale", style.get("scale", 1.0))
            tag.set_property("family", "monospace" if style.get("mono") else None)

        table.lookup("current-line").set_property(
            "paragraph-background", "#ffffff0d" if dark else "#00000008")
        table.lookup("dimmed").set_property("foreground", "#8a8a8a" if dark else "#a0a0a0")
        table.lookup("found").set_property("background", "#f5c21160" if dark else "#f9e08a")
        table.lookup("found-current").set_property("background",
                                                   "#ff7800a0" if dark else "#ffb060")
        self.gutter.queue_draw()

    def _apply_font(self):
        # A family that is not on this machine is no disaster: fontconfig
        # substitutes, and `monospace` at the end means what it substitutes is
        # at least still a fixed-width face.
        family = widgets.css_font_family(self._font_family) or "monospace"
        if family != "monospace":
            family += ", monospace"
        self._css.load_from_string(
            f".editor-text {{ font-family: {family}; font-size: {self._font_size}pt; }}")

    def _font_description(self) -> Pango.FontDescription:
        """The same font as a Pango description, for the gutter to draw with."""
        description = Pango.FontDescription()
        description.set_family(self._font_family or "monospace")
        description.set_size(self._font_size * Pango.SCALE)
        return description

    def set_font(self, family: str, points: int):
        self._font_family = str(family or DEFAULTS["editor_font_family"])
        self._font_size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(points)))
        self._apply_font()
        self.gutter.queue_draw()

    def set_font_size(self, points: int):
        self.set_font(self._font_family, points)

    def get_font_family(self) -> str:
        return self._font_family

    def get_font_size(self) -> int:
        return self._font_size

    def set_wrap(self, wrap: bool):
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if wrap else Gtk.WrapMode.NONE)
        self.scroller.set_policy(
            Gtk.PolicyType.NEVER if wrap else Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC)

    def set_gutter_visible(self, visible: bool):
        self._show_gutter = bool(visible)
        self.gutter.set_visible(self._show_gutter)

    def set_focus_mode(self, on: bool):
        """Dim everything but the paragraph the cursor is in."""
        self._focus_mode = bool(on)
        self._apply_focus()

    def set_typewriter(self, on: bool):
        """Keep the line being typed in the middle of the view."""
        self._typewriter = bool(on)
        if on:
            self._centre_cursor()

    # -- the gutter --------------------------------------------------------

    def _draw_gutter(self, area, cr, width, height, *_):
        dark = Adw.StyleManager.get_default().get_dark()
        buf = self.buffer
        view = self.view

        visible = view.get_visible_rect()
        first = view.get_line_at_y(visible.y)[0].get_line()
        last = view.get_line_at_y(visible.y + visible.height)[0].get_line()

        layout = self.gutter.create_pango_layout("")
        layout.set_font_description(self._font_description())

        digits = max(2, len(str(max(1, buf.get_line_count()))))
        layout.set_text("0" * digits, -1)
        wanted = layout.get_pixel_size()[0] + 20
        if area.get_content_width() != wanted:
            area.set_content_width(wanted)

        cursor_line = buf.get_iter_at_mark(buf.get_insert()).get_line()

        for line in range(first, last + 1):
            it = buf.get_iter_at_line(line)[1]
            y, _ = view.get_line_yrange(it)
            current = line == cursor_line
            if dark:
                cr.set_source_rgba(1, 1, 1, 0.85 if current else 0.30)
            else:
                cr.set_source_rgba(0, 0, 0, 0.75 if current else 0.30)
            layout.set_text(str(line + 1), -1)
            text_width = layout.get_pixel_size()[0]
            cr.move_to(width - 10 - text_width, y - visible.y)
            PangoCairo.show_layout(cr, layout)

        if dark:
            cr.set_source_rgba(1, 1, 1, 0.10)
        else:
            cr.set_source_rgba(0, 0, 0, 0.08)
        cr.rectangle(width - 1, 0, 1, height)
        cr.fill()

    # -- text in and out ---------------------------------------------------

    def get_text(self) -> str:
        start, end = self.buffer.get_bounds()
        return self.buffer.get_text(start, end, True)

    def set_text(self, text: str):
        self._loading = True
        self.buffer.set_text(text)
        self.buffer.set_modified(False)
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self._loading = False
        self._highlight_now()
        self.gutter.queue_draw()

    @property
    def modified(self) -> bool:
        return self.buffer.get_modified()

    def mark_saved(self):
        self.buffer.set_modified(False)

    def cursor_position(self) -> tuple[int, int]:
        it = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        return it.get_line() + 1, it.get_line_offset() + 1

    def selection_length(self) -> int:
        bounds = self.buffer.get_selection_bounds()
        if not bounds:
            return 0
        return bounds[1].get_offset() - bounds[0].get_offset()

    def selected_text(self) -> str:
        bounds = self.buffer.get_selection_bounds()
        return self.buffer.get_text(bounds[0], bounds[1], True) if bounds else ""

    def goto_line(self, line: int, focus: bool = True):
        line = max(1, min(line, self.buffer.get_line_count()))
        it = self.buffer.get_iter_at_line(line - 1)[1]
        self.buffer.place_cursor(it)
        self.view.scroll_to_iter(it, 0.22, True, 0.0, 0.35)
        if focus:
            self.view.grab_focus()

    def goto_offset(self, offset: int, select_to: int | None = None):
        buf = self.buffer
        start = buf.get_iter_at_offset(max(0, min(offset, buf.get_char_count())))
        if select_to is None:
            buf.place_cursor(start)
        else:
            end = buf.get_iter_at_offset(max(0, min(select_to, buf.get_char_count())))
            buf.select_range(start, end)
        self.view.scroll_to_iter(start, 0.25, True, 0.0, 0.4)

    def top_visible_line(self) -> tuple[int, float]:
        """The first line showing, and how far into it the view has scrolled."""
        visible = self.view.get_visible_rect()
        it, _ = self.view.get_line_at_y(visible.y)
        line = it.get_line()
        top, height = self.view.get_line_yrange(it)
        within = (visible.y - top) / height if height else 0.0
        return line, max(0.0, min(1.0, within))

    def scroll_line_to_top(self, line: int):
        it = self.buffer.get_iter_at_line(max(0, min(line, self.buffer.get_line_count() - 1)))[1]
        top, _ = self.view.get_line_yrange(it)
        adjustment = self.scroller.get_vadjustment()
        limit = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        adjustment.set_value(max(0.0, min(top, limit)))

    # -- markdown editing --------------------------------------------------

    def insert_snippet(self, snippet: str):
        """Insert text, honouring the selection and cursor markers."""
        buf = self.buffer
        bounds = buf.get_selection_bounds()
        selected = buf.get_text(bounds[0], bounds[1], True) if bounds else ""

        text = snippet.replace(SELECTION, selected)
        offset = text.find(CURSOR)
        text = text.replace(CURSOR, "")

        buf.begin_user_action()
        if bounds:
            buf.delete(bounds[0], bounds[1])
        start = buf.get_iter_at_mark(buf.get_insert())
        start_offset = start.get_offset()
        buf.insert(start, text)
        if offset >= 0:
            buf.place_cursor(buf.get_iter_at_offset(start_offset + offset))
        buf.end_user_action()
        self.view.grab_focus()

    def wrap_selection(self, left: str, right: str | None = None):
        """Put `left`/`right` around the selection, or take them off again.

        Toggling matters more than wrapping: Ctrl+B on text that is already
        bold should unbolden it, and an editor where it makes `****text****`
        instead is one you stop using Ctrl+B in.
        """
        right = left if right is None else right
        buf = self.buffer
        bounds = buf.get_selection_bounds()

        if not bounds:
            # No selection: take the word under the cursor, or open an empty
            # pair and put the cursor inside it.
            word = self._word_bounds()
            if word is None:
                buf.begin_user_action()
                buf.insert_at_cursor(left + right)
                it = buf.get_iter_at_mark(buf.get_insert())
                it.backward_chars(len(right))
                buf.place_cursor(it)
                buf.end_user_action()
                self.view.grab_focus()
                return
            bounds = word

        start, end = bounds
        inner = buf.get_text(start, end, True)

        if inner.startswith(left) and inner.endswith(right) and \
                len(inner) >= len(left) + len(right):
            replacement = inner[len(left):len(inner) - len(right)]
        else:
            outer_start = start.copy()
            outer_end = end.copy()
            ok_left = outer_start.backward_chars(len(left))
            ok_right = outer_end.forward_chars(len(right))
            if ok_left and ok_right and \
                    buf.get_text(outer_start, start, True) == left and \
                    buf.get_text(end, outer_end, True) == right:
                buf.begin_user_action()
                buf.delete(end, outer_end)
                start = buf.get_iter_at_offset(outer_start.get_offset())
                inner_start = buf.get_iter_at_offset(outer_start.get_offset() + len(left))
                buf.delete(start, inner_start)
                buf.end_user_action()
                self.view.grab_focus()
                return
            replacement = left + inner + right

        offset = start.get_offset()
        buf.begin_user_action()
        buf.delete(start, end)
        buf.insert(buf.get_iter_at_offset(offset), replacement)
        buf.select_range(buf.get_iter_at_offset(offset),
                         buf.get_iter_at_offset(offset + len(replacement)))
        buf.end_user_action()
        self.view.grab_focus()

    def _word_bounds(self):
        """The word the cursor is in or against, so Ctrl+B needs no selection."""
        buf = self.buffer
        it = buf.get_iter_at_mark(buf.get_insert())

        def wordish(char):
            return char.isalnum() or char in "_\u2019'"

        start = it.copy()
        while not start.starts_line():
            probe = start.copy()
            probe.backward_char()
            if not wordish(probe.get_char()):
                break
            start = probe
        end = it.copy()
        while not end.ends_line() and wordish(end.get_char()):
            end.forward_char()
        if start.get_offset() == end.get_offset():
            return None
        return start, end

    def set_heading(self, level: int):
        """Make the lines of the selection headings of `level`, or plain at 0."""
        buf = self.buffer
        first, last = self._selected_lines()
        buf.begin_user_action()
        for number in range(first, last + 1):
            text = self._line_text(number)
            body = re.sub(r"^ {0,3}#{1,6}\s*", "", text)
            if level and body.strip() == text.strip() == "":
                body = ""
            new = ("#" * level + " " + body).rstrip() if level else body
            if new != text:
                self._replace_line(number, new)
        buf.end_user_action()
        self.view.grab_focus()

    def toggle_quote(self):
        buf = self.buffer
        first, last = self._selected_lines()
        lines = [self._line_text(number) for number in range(first, last + 1)]
        quoting = not all(_QUOTE.match(line) or not line.strip() for line in lines)
        buf.begin_user_action()
        for offset, line in enumerate(lines):
            if quoting:
                self._replace_line(first + offset, "> " + line if line.strip() else ">")
            else:
                match = _QUOTE.match(line)
                if match:
                    self._replace_line(first + offset, match.group(2))
        buf.end_user_action()
        self.view.grab_focus()

    def toggle_list(self, ordered=False, task=False):
        """Turn the selected lines into a list, or back into plain paragraphs."""
        buf = self.buffer
        first, last = self._selected_lines()
        lines = [self._line_text(number) for number in range(first, last + 1)]

        def stripped(line):
            match = _BULLET.match(line) or _ORDERED.match(line)
            if not match:
                return None
            body = match.group(4) if match.re is _BULLET else match.group(5)
            check = _TASK.match(body)
            return check.group(3) if check else body

        already = all(stripped(line) is not None for line in lines if line.strip())
        if task:
            already = all(_TASK.match(stripped(line) or "") is not None
                          for line in lines if line.strip()) and already

        buf.begin_user_action()
        number = 1
        for offset, line in enumerate(lines):
            if not line.strip():
                continue
            body = stripped(line)
            if already:
                self._replace_line(first + offset, body if body is not None else line)
                continue
            indent = re.match(r"\s*", line).group(0)
            if body is None:
                body = line.strip()
            body = re.sub(r"^\[[ xX]\]\s*", "", body)
            marker = f"{number}. " if ordered else "- "
            prefix = "[ ] " if task else ""
            self._replace_line(first + offset, f"{indent}{marker}{prefix}{body}")
            number += 1
        buf.end_user_action()
        self.view.grab_focus()

    def toggle_task(self):
        """Tick or untick the checkbox on the cursor's line."""
        buf = self.buffer
        first, last = self._selected_lines()
        buf.begin_user_action()
        for number in range(first, last + 1):
            line = self._line_text(number)
            match = _BULLET.match(line) or _ORDERED.match(line)
            if not match:
                continue
            head_length = _head_width(match)
            body = line[head_length:]
            check = _TASK.match(body)
            if check:
                ticked = check.group(1)[1] in "xX"
                body = ("[ ]" if ticked else "[x]") + check.group(2) + check.group(3)
            else:
                body = "[ ] " + body
            self._replace_line(number, line[:head_length] + body)
        buf.end_user_action()
        self.view.grab_focus()

    def set_task_at(self, line: int, column: int, ticked: bool):
        """Tick the checkbox at an exact position — the preview clicking back."""
        buf = self.buffer
        if not 0 <= line < buf.get_line_count():
            return
        text = self._line_text(line)
        if column < 0 or column + 3 > len(text) or text[column] != "[":
            match = re.search(r"\[[ xX]\]", text)
            if not match:
                return
            column = match.start()
        start = buf.get_iter_at_line_offset(line, column)[1]
        end = buf.get_iter_at_line_offset(line, column + 3)[1]
        buf.begin_user_action()
        buf.delete(start, end)
        buf.insert(buf.get_iter_at_line_offset(line, column)[1], "[x]" if ticked else "[ ]")
        buf.end_user_action()

    def make_link(self, url: str = ""):
        """`[selection](url)`, with the cursor wherever there is still work."""
        selected = self.selected_text()
        if selected and url:
            self.insert_snippet(f"[{SELECTION}]({url}){CURSOR}")
        elif selected:
            self.insert_snippet(f"[{SELECTION}]({CURSOR})")
        elif url:
            self.insert_snippet(f"[{CURSOR}]({url})")
        else:
            self.insert_snippet(f"[{CURSOR}]()")

    def insert_table(self, columns=3, rows=2):
        head = "| " + " | ".join(f"Column {n + 1}" for n in range(columns)) + " |"
        rule = "|" + "|".join(" --- " for _ in range(columns)) + "|"
        body = "\n".join("| " + " | ".join(" " for _ in range(columns)) + " |"
                         for _ in range(rows))
        self.insert_snippet(f"\n{head}\n{rule}\n{body}\n{CURSOR}")

    def toggle_comment(self):
        """Markdown has no comment, so this uses the HTML one everyone uses."""
        buf = self.buffer
        first, last = self._selected_lines()
        lines = [self._line_text(number) for number in range(first, last + 1)]
        body = "\n".join(lines)
        buf.begin_user_action()
        if body.strip().startswith("<!--") and body.strip().endswith("-->"):
            new = body.strip()[4:-3].strip("\n")
            new = re.sub(r"^\s*\n", "", new)
        else:
            new = "<!--\n" + body + "\n-->"
        start = buf.get_iter_at_line(first)[1]
        end = buf.get_iter_at_line(last)[1]
        if not end.ends_line():
            end.forward_to_line_end()
        buf.delete(start, end)
        buf.insert(buf.get_iter_at_line(first)[1], new)
        buf.end_user_action()
        self.view.grab_focus()

    # -- line helpers ------------------------------------------------------

    def _selected_lines(self) -> tuple[int, int]:
        buf = self.buffer
        bounds = buf.get_selection_bounds()
        if not bounds:
            line = buf.get_iter_at_mark(buf.get_insert()).get_line()
            return line, line
        first, last = bounds[0].get_line(), bounds[1].get_line()
        if bounds[1].starts_line() and last > first:
            last -= 1
        return first, last

    def _line_text(self, number: int) -> str:
        buf = self.buffer
        if not 0 <= number < buf.get_line_count():
            return ""
        start = buf.get_iter_at_line(number)[1]
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        return buf.get_text(start, end, True)

    def _replace_line(self, number: int, text: str):
        buf = self.buffer
        start = buf.get_iter_at_line(number)[1]
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        buf.delete(start, end)
        buf.insert(buf.get_iter_at_line(number)[1], text)

    # -- keys --------------------------------------------------------------

    def _on_key(self, _controller, keyval, _keycode, state):
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK

        # While the link list is open, the keys that move through a list
        # move through it, and the rest are typing.
        if self.completion is not None and self.completion.key(keyval, state):
            return True

        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not ctrl and not shift:
            return self._on_enter()

        if keyval == Gdk.KEY_Tab and not ctrl and not shift:
            return self._on_tab()

        if keyval == Gdk.KEY_ISO_Left_Tab or (keyval == Gdk.KEY_Tab and shift):
            self._indent_lines(add=False)
            return True

        if keyval == Gdk.KEY_BackSpace and not ctrl and not self.buffer.get_selection_bounds():
            return self._on_backspace()

        return False

    def _on_enter(self) -> bool:
        """Continue whatever construct the cursor is inside."""
        buf = self.buffer
        it = buf.get_iter_at_mark(buf.get_insert())
        line_number = it.get_line()
        line = self._line_text(line_number)
        before = line[:it.get_line_offset()]

        quote = _QUOTE.match(before)
        quote_prefix = quote.group(1) if quote else ""
        rest = before[len(quote_prefix):]

        bullet = _BULLET.match(rest)
        ordered = None if bullet else _ORDERED.match(rest)

        if bullet or ordered:
            match = bullet or ordered
            body = match.group(4) if bullet else match.group(5)
            task = _TASK.match(body)
            content = task.group(3) if task else body

            # An empty item: Enter gets out of the list rather than making
            # another one nobody asked for.
            if not content.strip():
                start = buf.get_iter_at_line(line_number)[1]
                end = buf.get_iter_at_line_offset(line_number, len(before))[1]
                buf.begin_user_action()
                buf.delete(start, end)
                if quote_prefix.strip():
                    buf.insert(buf.get_iter_at_line(line_number)[1], quote_prefix)
                buf.insert_at_cursor("\n")
                buf.end_user_action()
                return True

            indent = match.group(1)
            if bullet:
                marker = f"{match.group(2)}{match.group(3)}"
            else:
                marker = f"{int(match.group(2)) + 1}{match.group(3)}{match.group(4)}"
            prefix = "[ ] " if task else ""
            buf.begin_user_action()
            buf.insert_at_cursor(f"\n{quote_prefix}{indent}{marker}{prefix}")
            buf.end_user_action()
            if ordered:
                GLib.idle_add(self._renumber_from, line_number)
            return True

        if quote_prefix.strip():
            if not rest.strip():
                start = buf.get_iter_at_line(line_number)[1]
                end = buf.get_iter_at_line_offset(line_number, len(before))[1]
                buf.begin_user_action()
                buf.delete(start, end)
                buf.insert_at_cursor("\n")
                buf.end_user_action()
                return True
            buf.insert_at_cursor("\n" + quote_prefix)
            return True

        # Inside a table, Enter starts a row with the same number of cells.
        if _TABLE_ROW.match(line) and line.count("|") >= 2 and rest.strip():
            cells = line.count("|") - 1
            buf.insert_at_cursor("\n| " + " | ".join(" " for _ in range(cells)) + " |")
            it = buf.get_iter_at_mark(buf.get_insert())
            it.set_line_offset(2)
            buf.place_cursor(it)
            return True

        indent = re.match(r"[ \t]*", line).group(0)
        if indent and rest.strip():
            buf.insert_at_cursor("\n" + indent)
            return True
        return False

    def _on_tab(self) -> bool:
        buf = self.buffer
        if buf.get_selection_bounds():
            self._indent_lines(add=True)
            return True
        it = buf.get_iter_at_mark(buf.get_insert())
        line = self._line_text(it.get_line())
        before = line[:it.get_line_offset()]
        # Inside a list marker, Tab means "nest this item".
        match = _BULLET.match(line) or _ORDERED.match(line)
        if match and len(before) <= _head_width(match):
            self._indent_lines(add=True)
            return True
        # Inside a table, Tab jumps to the next cell.
        if _TABLE_ROW.match(line):
            position = line.find("|", it.get_line_offset())
            if position != -1:
                target = min(position + 2, len(line))
                buf.place_cursor(buf.get_iter_at_line_offset(it.get_line(), target)[1])
                return True
        buf.insert_at_cursor(INDENT)
        return True

    def _on_backspace(self) -> bool:
        """Backspace at the start of an item's text takes the marker off."""
        buf = self.buffer
        it = buf.get_iter_at_mark(buf.get_insert())
        line_number = it.get_line()
        line = self._line_text(line_number)
        column = it.get_line_offset()
        match = _BULLET.match(line) or _ORDERED.match(line)
        if not match or column != _head_width(match) or column == 0:
            return False
        indent = match.group(1)
        start = buf.get_iter_at_line(line_number)[1]
        end = buf.get_iter_at_line_offset(line_number, column)[1]
        buf.begin_user_action()
        buf.delete(start, end)
        if indent:
            buf.insert(buf.get_iter_at_line(line_number)[1], indent)
        buf.end_user_action()
        return True

    def _indent_lines(self, add: bool):
        buf = self.buffer
        first, last = self._selected_lines()
        buf.begin_user_action()
        for number in range(first, last + 1):
            line = self._line_text(number)
            if add:
                if line.strip():
                    buf.insert(buf.get_iter_at_line(number)[1], INDENT)
            else:
                strip = 0
                while strip < len(INDENT) and strip < len(line) and line[strip] == " ":
                    strip += 1
                if strip == 0 and line[:1] == "\t":
                    strip = 1
                if strip:
                    start = buf.get_iter_at_line(number)[1]
                    end = buf.get_iter_at_line_offset(number, strip)[1]
                    buf.delete(start, end)
        buf.end_user_action()

    def _renumber_from(self, line_number: int):
        """Make an ordered list count properly after an insertion.

        Only the run the cursor is in, and only where the numbers are already
        wrong — rewriting lines that are already right would put a dozen
        pointless entries on the undo stack.
        """
        buf = self.buffer
        first = line_number
        while first > 0:
            match = _ORDERED.match(self._line_text(first - 1))
            if not match:
                break
            first -= 1

        number = None
        buf.begin_user_action()
        line = first
        while line < buf.get_line_count():
            text = self._line_text(line)
            match = _ORDERED.match(text)
            if not match:
                break
            if number is None:
                number = int(match.group(2))
            else:
                number += 1
            if int(match.group(2)) != number:
                head = f"{match.group(1)}{number}{match.group(3)}{match.group(4)}"
                self._replace_line(line, head + match.group(5))
            line += 1
        buf.end_user_action()
        return GLib.SOURCE_REMOVE

    def _on_paste(self, _view):
        """A URL pasted over a selection becomes a link around it."""
        bounds = self.buffer.get_selection_bounds()
        if not bounds:
            return
        selected = self.buffer.get_text(bounds[0], bounds[1], True)
        if not selected.strip() or "\n" in selected:
            return

        clipboard = self.get_display().get_clipboard()

        def arrived(source, result):
            try:
                text = source.read_text_finish(result) or ""
            except GLib.Error:
                return
            if not re.fullmatch(r"(?:https?://|mailto:|www\.)\S+", text.strip()):
                return
            url = text.strip()
            if url.startswith("www."):
                url = "https://" + url
            still = self.buffer.get_selection_bounds()
            if not still or self.buffer.get_text(still[0], still[1], True) != selected:
                return
            offset = still[0].get_offset()
            self.buffer.begin_user_action()
            self.buffer.delete(still[0], still[1])
            self.buffer.insert(self.buffer.get_iter_at_offset(offset),
                               f"[{selected}]({url})")
            self.buffer.end_user_action()

        clipboard.read_text_async(None, arrived)

    def _on_ctrl_scroll(self, controller, _dx, dy):
        if not (controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK):
            return False
        if controller.get_unit() == Gdk.ScrollUnit.SURFACE:
            # A touchpad sends pixels, dozens of events a swipe. One point of
            # text size per 24 of them, rather than one per event.
            self._scroll_carry += dy
            steps = int(self._scroll_carry / 24)
            if steps:
                self._scroll_carry -= steps * 24
                self.set_font_size(self._font_size - steps)
            return True
        self.set_font_size(self._font_size + (-1 if dy > 0 else 1))
        return True

    def _on_pinch(self, _gesture, scale):
        self.set_font_size(round(self._pinch_from * scale))

    # -- change and focus --------------------------------------------------

    def _on_changed(self, *_):
        if self._loading:
            return
        if self._highlight_source:
            GLib.source_remove(self._highlight_source)
        self._highlight_source = GLib.timeout_add(HIGHLIGHT_DEBOUNCE_MS, self._highlight_now)
        self.gutter.queue_draw()
        if self.completion is not None:
            self.completion.schedule()
        if self.on_changed:
            self.on_changed()

    def _on_scrolled(self, *_):
        self.gutter.queue_draw()
        if self.completion is not None and self.completion.active:
            self.completion.schedule()       # it points at a place that moved
        if self.on_scrolled:
            self.on_scrolled()

    def in_code_block(self, it) -> bool:
        """Whether `it` is in a fence or the front matter, as last highlighted."""
        table = self.buffer.get_tag_table()
        line = self.buffer.get_iter_at_line(it.get_line())[1]
        return any(line.has_tag(table.lookup(name))
                   for name in ("fence", "fenceline", "front"))

    def close(self):
        """Let go of what is attached to the view. The tab is going away."""
        if self.completion is not None:
            self.completion.destroy()

    def _on_cursor_moved(self, *_):
        buf = self.buffer
        line = buf.get_iter_at_mark(buf.get_insert()).get_line()
        if line != self._current_line:
            tag = buf.get_tag_table().lookup("current-line")
            if 0 <= self._current_line < buf.get_line_count():
                start = buf.get_iter_at_line(self._current_line)[1]
                end = start.copy()
                end.forward_line()
                buf.remove_tag(tag, start, end)
            start = buf.get_iter_at_line(line)[1]
            end = start.copy()
            end.forward_line()
            buf.apply_tag(tag, start, end)
            self._current_line = line
            self.gutter.queue_draw()
            if self._focus_mode:
                self._apply_focus()
            if self._typewriter:
                self._centre_cursor()
        if self.completion is not None:
            self.completion.schedule()
        if self.on_cursor:
            self.on_cursor()

    def _centre_cursor(self):
        self.view.scroll_to_mark(self.buffer.get_insert(), 0.0, True, 0.0, 0.5)

    def _apply_focus(self):
        """Dim every paragraph but the one the cursor is in."""
        buf = self.buffer
        tag = buf.get_tag_table().lookup("dimmed")
        start, end = buf.get_bounds()
        buf.remove_tag(tag, start, end)
        if not self._focus_mode:
            return

        line = buf.get_iter_at_mark(buf.get_insert()).get_line()
        first = line
        while first > 0 and self._line_text(first - 1).strip():
            first -= 1
        last = line
        while last + 1 < buf.get_line_count() and self._line_text(last + 1).strip():
            last += 1

        above = buf.get_iter_at_line(first)[1]
        buf.apply_tag(tag, start, above)
        below = buf.get_iter_at_line(last)[1]
        if not below.ends_line():
            below.forward_to_line_end()
        buf.apply_tag(tag, below, end)

    # -- highlighting ------------------------------------------------------

    def set_markdown(self, on: bool):
        """Whether to read the text as Markdown — off for a plain .txt."""
        self.markdown = bool(on)
        self._highlight_now()

    def _highlight_now(self):
        self._highlight_source = 0
        buf = self.buffer
        table = buf.get_tag_table()
        start, end = buf.get_bounds()
        for name in STYLES:
            buf.remove_tag(table.lookup(name), start, end)
        if not self.markdown:
            return GLib.SOURCE_REMOVE

        text = buf.get_text(start, end, True)
        lines = text.split("\n")

        first_line, last_line = 0, len(lines) - 1
        if len(text) > FULL_HIGHLIGHT_LIMIT:
            visible = self.view.get_visible_rect()
            first_line = max(0, self.view.get_line_at_y(visible.y - 1500)[0].get_line())
            last_line = min(len(lines) - 1,
                            self.view.get_line_at_y(
                                visible.y + visible.height + 1500)[0].get_line())

        def tag(name, line, begin, stop):
            if begin >= stop:
                return
            buf.apply_tag(table.lookup(name),
                          buf.get_iter_at_line_offset(line, begin)[1],
                          buf.get_iter_at_line_offset(line, min(stop, len(lines[line])))[1])

        in_fence = None
        in_front = lines and _FRONT.match(lines[0]) is not None
        front_closer = lines[0].strip() if in_front else ""

        for number, line in enumerate(lines):
            visible = first_line <= number <= last_line

            if in_front:
                if visible:
                    tag("front", number, 0, len(line))
                if number > 0 and line.strip() in (front_closer, "..."):
                    in_front = False
                continue

            fence = _FENCE.match(line)
            if in_fence is not None:
                if fence and fence.group(2)[0] == in_fence[0] and \
                        len(fence.group(2)) >= len(in_fence):
                    if visible:
                        tag("fenceline", number, 0, len(line))
                    in_fence = None
                elif visible:
                    tag("fence", number, 0, len(line))
                continue
            if fence:
                in_fence = fence.group(2)
                if visible:
                    tag("fenceline", number, 0, len(line))
                continue

            if not visible:
                continue
            self._highlight_line(number, line, tag, lines)

        self._highlight_cards(text, lines, tag, first_line, last_line)
        return GLib.SOURCE_REMOVE

    def _highlight_cards(self, text, lines, tag, first, last):
        """The card syntax, on the lines cards.py makes cards of and no others.

        Which lines those are is the scheduler's answer, not a guess made
        here: `::` in a fence, in a code span or in a note with `cards: false`
        is not a card, and so it is not coloured as one. Where on the line the
        `::` or the gap is, is read with cards.py's own helpers for the same
        reason.
        """
        try:
            found = cardnotes.parse_text(text, "note.md")
        except Exception:  # noqa: BLE001 - colour is not worth an exception
            return
        starts = {card.line - 1 for card in found}
        done = set()
        for card in found:
            number = card.line - 1
            if number in done or not first <= number <= last or number >= len(lines):
                continue
            done.add(number)
            line = lines[number]
            if card.form in ("line", "both", "both-r"):
                split = cardnotes._separator(line)
                if split:
                    colons = line.index(":", split[0])
                    tag("cardmark", number, colons, colons + (2 if card.form == "line" else 3))
            elif card.form == "qa":
                cue = line.find("Q:")
                tag("cardmark", number, cue, cue + 2)
                answer = number + card.front.count("\n") + 1
                if answer < len(lines) and lines[answer].lstrip().startswith("A:"):
                    cue = lines[answer].find("A:")
                    tag("cardmark", answer, cue, cue + 2)
            else:
                # A sentence with gaps runs to the blank line, or to the next
                # line that starts a card of its own.
                while True:
                    self._highlight_gaps(number, lines[number], tag)
                    number += 1
                    if number >= len(lines) or not lines[number].strip() or number in starts:
                        break

    def _highlight_gaps(self, number, line, tag):
        masked = cardnotes._mask(line)
        for gap in cardnotes._CLOZE.finditer(masked):
            begin, stop = gap.start(), gap.end()
            inner = line[begin + 2:stop - 2]
            bar = inner.find("|")
            answer_end = begin + 2 + (bar if bar >= 0 else len(inner))
            tag("marker", number, begin, begin + 2)
            tag("cloze", number, begin + 2, answer_end)
            tag("marker", number, answer_end, stop)      # the hint, and the }}

    def _highlight_line(self, number, line, tag, lines):
        offset = 0

        quote = _QUOTE.match(line)
        if quote:
            tag("quote", number, 0, len(line))
            tag("marker", number, 0, len(quote.group(1)))
            offset = len(quote.group(1))
            line = quote.group(2)

        if _RULE.match(line) and not _BULLET.match(line):
            tag("rule", number, offset, offset + len(line))
            return

        if _REF_DEF.match(line):
            tag("refdef", number, offset, offset + len(line))
            return

        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1).strip())
            tag(HEADING_TAGS[level - 1], number, offset, offset + len(line))
            tag("marker", number, offset, offset + len(heading.group(1)))
            self._highlight_inline(number, line, offset, tag, skip_headings=True)
            return

        if _SETEXT.match(line) and number > 0 and lines[number - 1].strip():
            tag("h1" if line.strip()[0] == "=" else "h2", number, offset, offset + len(line))
            return

        if _TABLE_DELIM.match(line) and "|" in line:
            tag("table", number, offset, offset + len(line))
            return
        if _TABLE_ROW.match(line):
            tag("table", number, offset, offset + len(line))

        if _HTML.match(line):
            tag("html", number, offset, offset + len(line))
            return

        marker = _BULLET.match(line) or _ORDERED.match(line)
        if marker:
            head = _head_width(marker)
            tag("listmark", number, offset + len(marker.group(1)), offset + head)
            body_at = offset + head
            body = line[head:]
            check = _TASK.match(body)
            if check:
                tag("task", number, body_at, body_at + len(check.group(1)))

        self._highlight_inline(number, line, offset, tag)

    def _highlight_inline(self, number, line, offset, tag, skip_headings=False):
        for match in _INLINE.finditer(line):
            kind = match.lastgroup
            begin, stop = match.start(), match.end()
            name = {
                "code": "code", "wikilink": "link",
                "image": "link", "link": "link", "reflink": "link",
                "footref": "link", "autolink": "url", "url": "url",
                "strongem": "strong", "strong": "strong", "emphasis": "emphasis",
                "strike": "strike", "mark": "mark", "math": "math",
                "entity": "escape", "escape": "escape", "tag": "html",
            }.get(kind)
            if not name:
                continue
            if skip_headings and name in ("strong", "emphasis"):
                # A heading is already bold; making part of it bolder reads as
                # a rendering bug rather than as emphasis.
                continue
            tag(name, number, offset + begin, offset + stop)

            # The syntax itself stays visible but steps back, so the text
            # inside it is what the eye lands on.
            width = {"strongem": 3, "strong": 2, "emphasis": 1, "strike": 2,
                     "mark": 2, "math": 1}.get(kind)
            if width:
                tag("marker", number, offset + begin, offset + begin + width)
                tag("marker", number, offset + stop - width, offset + stop)
            elif kind == "wikilink":
                # As with [text](url): what is shown is what stands out, so
                # with a |label the target before it steps back with the
                # brackets.
                text = line[begin:stop]
                lead = text.find("|") + 1 if "|" in text else text.find("[[") + 2
                tag("marker", number, offset + begin, offset + begin + lead)
                tag("marker", number, offset + stop - 2, offset + stop)
            elif kind in ("link", "image", "reflink"):
                bracket = line.find("]", begin)
                if bracket != -1:
                    lead = 2 if kind == "image" else 1
                    tag("marker", number, offset + begin, offset + begin + lead)
                    tag("marker", number, offset + bracket, offset + stop)
