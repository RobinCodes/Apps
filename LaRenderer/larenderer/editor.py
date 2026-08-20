"""The source editor: a Gtk.TextView taught to read LaTeX.

GtkSourceView 5 is not installed on this machine — only the GTK3-era 3 and 4 —
so rather than make the app depend on something that isn't here, the three
things it would have given us are done directly: regex syntax highlighting on
the text buffer, a line-number gutter drawn beside it, and the small editing
courtesies (auto-indent, closing \\end{}, comment toggling).

The gutter is a separate DrawingArea *outside* the ScrolledWindow, painted from
the scroll offset. Putting it inside would fight the TextView's own Scrollable
implementation.
"""

from __future__ import annotations

import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

HIGHLIGHT_DEBOUNCE_MS = 90
FULL_HIGHLIGHT_LIMIT = 200_000   # past this, only the visible region is scanned
INDENT = "  "

CURSOR = "%%CURSOR%%"
SELECTION = "%%SEL%%"


# --------------------------------------------------------------------------
# the grammar, such as it is
# --------------------------------------------------------------------------

VERBATIM_ENVS = "verbatim|Verbatim|lstlisting|minted|alltt"

TOKEN = re.compile(
    r"""
      (?P<comment>(?<!\\)%[^\n]*)
    | (?P<verbatim>\\begin\{(?:""" + VERBATIM_ENVS + r""")\*?\}.*?
                   \\end\{(?:""" + VERBATIM_ENVS + r""")\*?\})
    | (?P<display>\\\[.*?\\\]|\$\$.*?\$\$)
    | (?P<inline>\\\(.*?\\\)|(?<!\\)\$(?:\\.|[^$\\\n])*\$)
    | (?P<env>\\(?:begin|end)\s*\{(?P<envname>[^}\n]*)\})
    | (?P<pkg>\\(?:usepackage|documentclass|RequirePackage|LoadClass)\s*
              (?:\[[^\]]*\])?\s*\{(?P<pkgname>[^}\n]*)\})
    | (?P<command>\\(?:[A-Za-z@]+\*?|[^A-Za-z\s]))
    | (?P<special>[&#~^_])
    | (?P<brace>[{}])
    """,
    re.VERBOSE | re.DOTALL,
)

# Commands are still highlighted inside maths; maths is just tinted underneath.
INNER_COMMAND = re.compile(r"\\(?:[A-Za-z@]+\*?|[^A-Za-z\s])")

LIGHT = {
    "comment":  {"foreground": "#767b7f", "style": Pango.Style.ITALIC},
    "command":  {"foreground": "#1a5fb4"},
    "envname":  {"foreground": "#9141ac", "weight": 700},
    "pkgname":  {"foreground": "#c64600", "weight": 700},
    "math":     {"foreground": "#137a52"},
    "brace":    {"foreground": "#8b8e8f"},
    "special":  {"foreground": "#c01c28"},
    "verbatim": {"foreground": "#5e5c64", "background": "rgba(0,0,0,0.05)"},
}

DARK = {
    "comment":  {"foreground": "#9aa0a6", "style": Pango.Style.ITALIC},
    "command":  {"foreground": "#7fb0ee"},
    "envname":  {"foreground": "#e0a3e8", "weight": 700},
    "pkgname":  {"foreground": "#ffbe6f", "weight": 700},
    "math":     {"foreground": "#7ee0a8"},
    "brace":    {"foreground": "#9a9996"},
    "special":  {"foreground": "#ff8a75"},
    "verbatim": {"foreground": "#c0bfbc", "background": "rgba(255,255,255,0.06)"},
}

TAG_NAMES = list(LIGHT)


class Editor(Gtk.Box):
    """Gutter + text view, with LaTeX highlighting and the usual courtesies."""

    def __init__(self, on_changed=None, on_cursor=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.on_changed = on_changed
        self.on_cursor = on_cursor

        self._error_lines: set[int] = set()
        self._warning_lines: set[int] = set()
        self._highlight_source = 0
        self._current_line = -1
        self._font_size = 11
        self._loading = False

        self.buffer = Gtk.TextBuffer()
        self.view = Gtk.TextView(
            buffer=self.buffer,
            monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            left_margin=8,
            right_margin=8,
            top_margin=6,
            bottom_margin=400,   # room to scroll the last line off the bottom
            accepts_tab=False,
            hexpand=True,
            vexpand=True,
        )
        self.view.add_css_class("editor-text")

        self._css = Gtk.CssProvider()
        self.view.get_style_context().add_provider(
            self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._apply_font()

        self.scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_child(self.view)

        self.gutter = Gtk.DrawingArea()
        self.gutter.add_css_class("editor-gutter")
        self.gutter.set_draw_func(self._draw_gutter)
        self.gutter.set_content_width(48)

        self.append(self.gutter)
        self.append(self.scroller)

        self._make_tags()

        self.buffer.connect("changed", self._on_changed)
        self.buffer.connect("notify::cursor-position", self._on_cursor_moved)
        self.scroller.get_vadjustment().connect("value-changed", lambda *_: self.gutter.queue_draw())
        self.view.connect("notify::height-request", lambda *_: self.gutter.queue_draw())

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.view.add_controller(keys)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self._on_ctrl_scroll)
        self.scroller.add_controller(scroll)

        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self._retheme())

    # -- appearance --------------------------------------------------------

    def _make_tags(self):
        table = self.buffer.get_tag_table()
        for name in TAG_NAMES:
            if not table.lookup(name):
                self.buffer.create_tag(name)
        # Created last, so they sit above the syntax tags.
        if not table.lookup("current-line"):
            self.buffer.create_tag("current-line")
        if not table.lookup("error-line"):
            self.buffer.create_tag("error-line")
        self._retheme()

    def _retheme(self):
        dark = Adw.StyleManager.get_default().get_dark()
        palette = DARK if dark else LIGHT
        table = self.buffer.get_tag_table()
        for name, props in palette.items():
            tag = table.lookup(name)
            if not tag:
                continue
            tag.set_property("foreground", props.get("foreground"))
            tag.set_property("background", props.get("background"))
            tag.set_property("style", props.get("style", Pango.Style.NORMAL))
            tag.set_property("weight", props.get("weight", 400))
        table.lookup("current-line").set_property(
            "paragraph-background", "rgba(255,255,255,0.05)" if dark else "rgba(0,0,0,0.04)"
        )
        table.lookup("error-line").set_property(
            "paragraph-background", "rgba(224,27,36,0.20)" if dark else "rgba(224,27,36,0.11)"
        )
        self.gutter.queue_draw()

    def _apply_font(self):
        self._css.load_from_string(
            f".editor-text {{ font-family: monospace; font-size: {self._font_size}pt; }}"
        )

    def set_font_size(self, points: int):
        self._font_size = max(6, min(32, int(points)))
        self._apply_font()
        self.gutter.queue_draw()

    def get_font_size(self) -> int:
        return self._font_size

    # -- the gutter --------------------------------------------------------

    def _draw_gutter(self, area, cr, width, height, *_):
        dark = Adw.StyleManager.get_default().get_dark()
        buf = self.buffer
        view = self.view

        visible = view.get_visible_rect()
        first_iter = view.get_line_at_y(visible.y)[0]
        last_iter = view.get_line_at_y(visible.y + visible.height)[0]
        first = first_iter.get_line()
        last = last_iter.get_line()

        layout = self.gutter.create_pango_layout("")
        layout.set_font_description(Pango.FontDescription(f"monospace {self._font_size}"))

        digits = max(2, len(str(max(1, buf.get_line_count()))))
        layout.set_text("0" * digits, -1)
        text_w = layout.get_pixel_size()[0]
        wanted = text_w + 22
        if area.get_content_width() != wanted:
            area.set_content_width(wanted)

        cursor_line = buf.get_iter_at_mark(buf.get_insert()).get_line()

        for line in range(first, last + 1):
            it = buf.get_iter_at_line(line)[1]
            y, line_h = view.get_line_yrange(it)
            draw_y = y - visible.y

            if line in self._error_lines:
                cr.set_source_rgba(0.88, 0.11, 0.14, 0.85)
                cr.arc(9, draw_y + line_h / 2, 3.2, 0, 6.2832)
                cr.fill()
            elif line in self._warning_lines:
                cr.set_source_rgba(0.90, 0.65, 0.04, 0.85)
                cr.arc(9, draw_y + line_h / 2, 3.2, 0, 6.2832)
                cr.fill()

            current = line == cursor_line
            if dark:
                cr.set_source_rgba(1, 1, 1, 0.85 if current else 0.32)
            else:
                cr.set_source_rgba(0, 0, 0, 0.75 if current else 0.32)

            layout.set_text(str(line + 1), -1)
            tw = layout.get_pixel_size()[0]
            cr.move_to(width - 10 - tw, draw_y)
            PangoCairo.show_layout(cr, layout)

        # A hairline between the numbers and the text.
        if dark:
            cr.set_source_rgba(1, 1, 1, 0.10)
        else:
            cr.set_source_rgba(0, 0, 0, 0.10)
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

    def goto_line(self, line: int, focus: bool = True):
        line = max(1, min(line, self.buffer.get_line_count()))
        it = self.buffer.get_iter_at_line(line - 1)[1]
        self.buffer.place_cursor(it)
        self.view.scroll_to_iter(it, 0.22, True, 0.0, 0.35)
        if focus:
            self.view.grab_focus()

    def set_problem_lines(self, errors, warnings):
        self._error_lines = {n - 1 for n in errors if n > 0}
        self._warning_lines = {n - 1 for n in warnings if n > 0}
        table = self.buffer.get_tag_table()
        tag = table.lookup("error-line")
        start, end = self.buffer.get_bounds()
        self.buffer.remove_tag(tag, start, end)
        for line in self._error_lines:
            if line < self.buffer.get_line_count():
                a = self.buffer.get_iter_at_line(line)[1]
                b = a.copy()
                if not b.ends_line():
                    b.forward_to_line_end()
                self.buffer.apply_tag(tag, a, b)
        self.gutter.queue_draw()

    # -- editing courtesies ------------------------------------------------

    def insert_snippet(self, snippet: str):
        """Insert text, honouring %%SEL%% and %%CURSOR%% markers."""
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

    def insert_package(self, directive: str):
        """Put a \\usepackage line in the preamble, after the last one there."""
        buf = self.buffer
        text = self.get_text()

        anchor = None
        for match in re.finditer(r"^[ \t]*\\usepackage\b.*$", text, re.M):
            anchor = match.end()
        if anchor is None:
            match = re.search(r"^[ \t]*\\documentclass\b.*$", text, re.M)
            anchor = match.end() if match else 0

        buf.begin_user_action()
        it = buf.get_iter_at_offset(anchor)
        buf.insert(it, "\n" + directive)
        buf.end_user_action()
        line = text[:anchor].count("\n") + 2
        self.goto_line(line)

    def toggle_comment(self):
        buf = self.buffer
        bounds = buf.get_selection_bounds()
        if bounds:
            first = bounds[0].get_line()
            last = bounds[1].get_line()
            if bounds[1].starts_line() and last > first:
                last -= 1
        else:
            first = last = buf.get_iter_at_mark(buf.get_insert()).get_line()

        lines = []
        for n in range(first, last + 1):
            a = buf.get_iter_at_line(n)[1]
            b = a.copy()
            if not b.ends_line():
                b.forward_to_line_end()
            lines.append(buf.get_text(a, b, True))

        commenting = not all(line.lstrip().startswith("%") or not line.strip() for line in lines)

        buf.begin_user_action()
        for n in range(first, last + 1):
            a = buf.get_iter_at_line(n)[1]
            b = a.copy()
            if not b.ends_line():
                b.forward_to_line_end()
            line = buf.get_text(a, b, True)
            if commenting:
                if line.strip():
                    buf.insert(a, "% ")
            else:
                stripped = line.lstrip()
                if stripped.startswith("%"):
                    indent = len(line) - len(stripped)
                    removed = 2 if stripped.startswith("% ") else 1
                    c = buf.get_iter_at_line_offset(n, indent)[1]
                    d = buf.get_iter_at_line_offset(n, indent + removed)[1]
                    buf.delete(c, d)
        buf.end_user_action()

    def _on_key(self, _controller, keyval, _keycode, state):
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK

        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not ctrl:
            return self._on_enter()

        if keyval == Gdk.KEY_Tab and not ctrl and not shift:
            if self.buffer.get_selection_bounds():
                self._indent_selection(add=True)
            else:
                self.buffer.insert_at_cursor(INDENT)
            return True

        if keyval == Gdk.KEY_ISO_Left_Tab or (keyval == Gdk.KEY_Tab and shift):
            self._indent_selection(add=False)
            return True

        if ctrl and keyval in (Gdk.KEY_slash, Gdk.KEY_numbersign):
            self.toggle_comment()
            return True

        return False

    def _on_ctrl_scroll(self, controller, _dx, dy):
        """Ctrl+scroll over the text resizes the text, not the preview."""
        if not (controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK):
            return False
        self.set_font_size(self._font_size + (-1 if dy > 0 else 1))
        return True

    def _on_enter(self) -> bool:
        buf = self.buffer
        it = buf.get_iter_at_mark(buf.get_insert())
        line_start = buf.get_iter_at_line(it.get_line())[1]
        before = buf.get_text(line_start, it, True)

        indent = re.match(r"[ \t]*", before).group(0)

        match = re.search(r"\\begin\{([^}\n]+)\}[ \t]*$", before)
        if match:
            name = match.group(1)
            text = self.get_text()
            opened = len(re.findall(r"\\begin\{" + re.escape(name) + r"\}", text))
            closed = len(re.findall(r"\\end\{" + re.escape(name) + r"\}", text))
            if opened > closed:
                body_indent = indent + INDENT
                buf.begin_user_action()
                buf.insert_at_cursor(f"\n{body_indent}\n{indent}\\end{{{name}}}")
                cursor = buf.get_iter_at_mark(buf.get_insert())
                cursor.backward_lines(1)
                cursor.forward_to_line_end()
                buf.place_cursor(cursor)
                buf.end_user_action()
                self.view.scroll_to_mark(buf.get_insert(), 0.0, False, 0, 0)
                return True

        # \item continuation, then plain auto-indent.
        if re.match(r"[ \t]*\\item\b", before) and before.strip() != "\\item":
            buf.insert_at_cursor(f"\n{indent}\\item ")
            return True
        if indent:
            buf.insert_at_cursor("\n" + indent)
            return True
        return False

    def _indent_selection(self, add: bool):
        buf = self.buffer
        bounds = buf.get_selection_bounds()
        if not bounds:
            return
        first = bounds[0].get_line()
        last = bounds[1].get_line()
        if bounds[1].starts_line() and last > first:
            last -= 1
        buf.begin_user_action()
        for n in range(first, last + 1):
            a = buf.get_iter_at_line(n)[1]
            if add:
                buf.insert(a, INDENT)
            else:
                b = a.copy()
                b.forward_chars(len(INDENT))
                if buf.get_text(a, b, True) == INDENT:
                    buf.delete(a, b)
                else:
                    c = a.copy()
                    c.forward_char()
                    if buf.get_text(a, c, True) in (" ", "\t"):
                        buf.delete(a, c)
        buf.end_user_action()

    # -- highlighting ------------------------------------------------------

    def _on_changed(self, *_):
        if self._loading:
            return
        if self._highlight_source:
            GLib.source_remove(self._highlight_source)
        self._highlight_source = GLib.timeout_add(HIGHLIGHT_DEBOUNCE_MS, self._highlight_now)
        self.gutter.queue_draw()
        if self.on_changed:
            self.on_changed()

    def _on_cursor_moved(self, *_):
        buf = self.buffer
        line = buf.get_iter_at_mark(buf.get_insert()).get_line()
        if line != self._current_line:
            tag = buf.get_tag_table().lookup("current-line")
            if 0 <= self._current_line < buf.get_line_count():
                a = buf.get_iter_at_line(self._current_line)[1]
                b = a.copy()
                b.forward_line()
                buf.remove_tag(tag, a, b)
            a = buf.get_iter_at_line(line)[1]
            b = a.copy()
            b.forward_line()
            buf.apply_tag(tag, a, b)
            self._current_line = line
            self.gutter.queue_draw()
        if self.on_cursor:
            self.on_cursor()

    def _highlight_now(self):
        self._highlight_source = 0
        buf = self.buffer
        table = buf.get_tag_table()
        total = buf.get_char_count()

        if total <= FULL_HIGHLIGHT_LIMIT:
            start, end = buf.get_bounds()
        else:
            visible = self.view.get_visible_rect()
            top = self.view.get_line_at_y(visible.y - 2000)[0]
            bottom = self.view.get_line_at_y(visible.y + visible.height + 2000)[0]
            start = buf.get_iter_at_line(top.get_line())[1]
            end = buf.get_iter_at_line(bottom.get_line())[1]
            if not end.ends_line():
                end.forward_to_line_end()

        base = start.get_offset()
        text = buf.get_text(start, end, True)

        for name in TAG_NAMES:
            buf.remove_tag(table.lookup(name), start, end)

        def apply(name, a, b):
            buf.apply_tag(
                table.lookup(name),
                buf.get_iter_at_offset(base + a),
                buf.get_iter_at_offset(base + b),
            )

        for match in TOKEN.finditer(text):
            if match.group("comment") is not None:
                apply("comment", *match.span("comment"))
            elif match.group("verbatim") is not None:
                apply("verbatim", *match.span("verbatim"))
            elif match.group("display") is not None or match.group("inline") is not None:
                group = "display" if match.group("display") is not None else "inline"
                a, b = match.span(group)
                apply("math", a, b)
                for inner in INNER_COMMAND.finditer(text[a:b]):
                    apply("command", a + inner.start(), a + inner.end())
            elif match.group("env") is not None:
                a, b = match.span("env")
                apply("command", a, a + len(match.group("env").split("{")[0]))
                apply("envname", *match.span("envname"))
            elif match.group("pkg") is not None:
                a, b = match.span("pkg")
                apply("command", a, a + len(match.group("pkg").split("{")[0].split("[")[0]))
                apply("pkgname", *match.span("pkgname"))
            elif match.group("command") is not None:
                apply("command", *match.span("command"))
            elif match.group("special") is not None:
                apply("special", *match.span("special"))
            elif match.group("brace") is not None:
                apply("brace", *match.span("brace"))

        return GLib.SOURCE_REMOVE
