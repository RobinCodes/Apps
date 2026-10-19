"""The rendered page: Markdown blocks as ordinary GTK widgets.

The obvious way to preview Markdown is a web view, and it costs a browser —
three hundred megabytes of one — to draw a page of text. This machine has
less spare memory than that, so the preview is built out of the widgets GTK
already has: a heading is a label, a code block is a monospace label in a
frame, a table is a grid, a task is a real check button that writes back to
the file when you click it.

Two things make it feel live rather than regenerated.

*Widgets are reused.* Re-creating every widget on every keystroke is what
makes a preview flicker and what makes a long document unusable. Each block
is given a signature — its kind and its source text — and a rebuild reuses
the widget of any block whose signature it has seen before. Typing in one
paragraph therefore rebuilds one paragraph.

*Every widget remembers its line.* `widget._line` is where in the source the
block began, which is what the window uses to keep the two halves scrolled
together, and what lets a click in the preview put the cursor in the text
that produced it.

Images are loaded from disk. They are not fetched from the network unless the
setting says to: a note that quietly makes an HTTP request every time it is
opened is a tracking pixel waiting to happen, and an editor should not be the
thing that fires it.
"""

from __future__ import annotations

import os
import re
import urllib.parse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import (Adw, Gdk, GdkPixbuf, GLib, Graphene, Gtk,  # noqa: E402
                           Pango)

from . import highlight, latex, markdown, widgets  # noqa: E402
from .config import DEFAULTS  # noqa: E402

MAX_IMAGE_WIDTH = 900
CODE_LINE_LIMIT = 4000

MIN_FONT_SIZE = 6
MAX_FONT_SIZE = 40


# ---------------------------------------------------------- the page font --
#
# The size everything on the page is a ratio of, as a rule on the display
# rather than on the preview widget.
#
# A provider added to one widget's style context styles that widget and
# nothing beneath it, and a page here is a tree of labels, grids and check
# buttons rather than one widget drawing text. A rule on the display reaches
# all of them, and `.md-page` is worn by nothing else. That it is one rule for
# every window is not a compromise either: the font is one line in one config
# file, and a page in a second window drawn at some other size would be a bug.

_page_css = Gtk.CssProvider()
_page_css_installed = False
_page_font = (str(DEFAULTS["preview_font_family"]),
              int(DEFAULTS["preview_font_size"]))


def set_page_font(family: str, points: int):
    """Draw every rendered page in this family, at this size in points."""
    global _page_font
    points = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(points)))
    family = str(family or "")
    _page_font = (family, points)

    rule = f".md-page {{ font-size: {points}pt;"
    css_family = widgets.css_font_family(family)
    if css_family:
        rule += f" font-family: {css_family};"
    _page_css.load_from_string(rule + " }")
    _install_page_css()


def page_font() -> tuple[str, int]:
    """The family and point size the page is being drawn at."""
    return _page_font


def _install_page_css():
    global _page_css_installed
    display = Gdk.Display.get_default()
    if _page_css_installed or display is None:
        return
    Gtk.StyleContext.add_provider_for_display(
        display, _page_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _page_css_installed = True


# So a page has its font from the first one drawn, whether or not anything
# ever sets one. There may be no display yet; the first Preview installs it.
set_page_font(*_page_font)


# ------------------------------------------------------------ inline spans --


def to_pango(spans, dark=False) -> str:
    """A list of inline Nodes to Pango markup for one label."""
    try:
        return "".join(_span(node, dark) for node in spans or [])
    except Exception:  # noqa: BLE001 - a label must never fail to render
        return esc(markdown.spans_text(spans))


def _span(node, dark):
    kind = node.kind
    if kind == "text":
        return esc(node.text)
    if kind == "code":
        shade = "#ffffff1f" if dark else "#0000000f"
        return f'<tt><span background="{shade}">{esc(" " + node.text + " ")}</span></tt>'
    if kind == "math":
        return latex.inline_markup(node.text)
    if kind == "break":
        return "\n"
    if kind == "emph":
        return f"<i>{to_pango(node.children, dark)}</i>"
    if kind == "strong":
        return f"<b>{to_pango(node.children, dark)}</b>"
    if kind == "strike":
        return f"<s>{to_pango(node.children, dark)}</s>"
    if kind == "underline":
        return f"<u>{to_pango(node.children, dark)}</u>"
    if kind == "mark":
        shade = "#ffd60033" if dark else "#fff3a3"
        return f'<span background="{shade}">{to_pango(node.children, dark)}</span>'
    if kind == "sub":
        return f"<sub>{to_pango(node.children, dark)}</sub>"
    if kind == "sup":
        return f"<sup>{to_pango(node.children, dark)}</sup>"
    if kind == "small":
        return f'<span size="85%">{to_pango(node.children, dark)}</span>'
    if kind == "kbd":
        shade = "#ffffff26" if dark else "#00000012"
        return (f'<tt><span background="{shade}" size="92%">'
                f'{esc(" " + markdown.node_text(node) + " ")}</span></tt>')
    if kind == "link":
        label = to_pango(node.children, dark) or esc(node.url)
        return f'<a href="{esc(node.url)}" title="{esc(node.title)}">{label}</a>'
    if kind == "image":
        # Only reached for an image sitting among text; one on its own line
        # becomes a picture instead. See _paragraph.
        label = esc(node.text) or esc(os.path.basename(node.url))
        return f'<a href="{esc(node.url)}">🖼 {label}</a>'
    if kind == "footref":
        return (f'<a href="#fn-{esc(node.text)}">'
                f'<sup>[{esc(node.text)}]</sup></a>')
    return esc(markdown.node_text(node))


def esc(text):
    return GLib.markup_escape_text(text or "")


# ------------------------------------------------------------- the preview --


class Preview(Gtk.ScrolledWindow):
    """The rendered document, and the handful of things you can do to it.

    `on_line` is called with a source line when a block is clicked.
    `on_task` is called with (line, column, ticked) when a checkbox changes.
    `on_link` is called with a URL; returning True means it was handled.
    """

    def __init__(self, on_line=None, on_task=None, on_link=None):
        super().__init__(hexpand=True, vexpand=True)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.on_line = on_line
        self.on_task = on_task
        self.on_link = on_link

        self.base_dir = ""
        self.load_remote_images = False
        self._width_limit = 0          # 0 = full width, else a reading measure
        self._cache: dict[str, list[Gtk.Widget]] = {}
        self._anchors: dict[str, Gtk.Widget] = {}
        self._document: markdown.Document | None = None
        self._applying_task = False

        self.column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.column.add_css_class("md-page")
        self.column.set_valign(Gtk.Align.START)

        self.clamp = Adw.Clamp(child=self.column, maximum_size=10_000,
                               tightening_threshold=720)
        self.set_child(self.clamp)
        self.add_css_class("preview-area")
        _install_page_css()

        # Whether the pointer is over the preview, which is how the document
        # decides that a scroll here was the user's doing rather than a
        # rebuild settling. GTK4 has no has-pointer property to ask.
        self._pointer_inside = False
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self._set_pointer(True))
        motion.connect("leave", lambda *_: self._set_pointer(False))
        self.add_controller(motion)

        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self._retheme())

    # -- appearance --------------------------------------------------------

    def _set_pointer(self, inside):
        self._pointer_inside = inside

    def has_pointer(self) -> bool:
        return self._pointer_inside

    def set_reading_width(self, columns: int):
        """Clamp the text to a measure, or 0 for the whole pane."""
        self._width_limit = int(columns or 0)
        self.clamp.set_maximum_size(self._width_limit or 10_000)

    def _retheme(self):
        if self._document is not None:
            self._cache.clear()
            self.render(self._document, self.base_dir)

    # -- building ----------------------------------------------------------

    def render(self, document: markdown.Document, base_dir: str = ""):
        """Rebuild the page, reusing whatever widgets still apply."""
        self._document = document
        self.base_dir = base_dir or ""
        dark = Adw.StyleManager.get_default().get_dark()

        # Everything currently on screen, filed by signature so the new page
        # can claim it back. A widget not claimed is simply dropped.
        old = self._cache
        self._cache = {}
        self._anchors = {}

        keep = []
        for block in document.blocks:
            signature = _signature(block, dark)
            pool = old.get(signature)
            widget = pool.pop() if pool else None
            if widget is None:
                widget = self._build(block, dark, document)
            if widget is None:
                continue
            widget._line = block.line
            widget._end_line = block.end_line
            self._cache.setdefault(signature, []).append(widget)
            keep.append(widget)
            if block.kind == "heading" and block.anchor:
                self._anchors[block.anchor] = widget

        if document.footnotes:
            widget = self._footnotes(document, dark)
            keep.append(widget)

        _replace_children(self.column, keep)

    def _build(self, block, dark, document):
        builder = getattr(self, "_" + block.kind, None)
        if builder is None:
            return None
        try:
            return builder(block, dark, document)
        except Exception:  # noqa: BLE001 - one bad block is not a blank page
            import traceback

            traceback.print_exc()
            return _text_label(markdown.spans_text(getattr(block, "spans", None) or []),
                               ["md-para"])

    # -- blocks ------------------------------------------------------------

    def _heading(self, block, dark, _document):
        label = Gtk.Label(
            xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
            selectable=True, use_markup=True, can_focus=False,
        )
        label.set_markup(to_pango(block.spans, dark))
        label.add_css_class("md-heading")
        label.add_css_class(f"md-h{block.level}")
        self._clickable(label, block.line)
        label.connect("activate-link", self._link_clicked)
        return label

    def _para(self, block, dark, _document):
        pictures = [node for node in _walk(block.spans) if node.kind == "image"]
        text_only = [node for node in block.spans
                     if not (node.kind == "text" and not node.text.strip())]

        if pictures and all(node.kind == "image" for node in text_only):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.add_css_class("md-figure")
            for node in pictures:
                box.append(self._picture(node, dark))
            self._clickable(box, block.line)
            return box

        label = _text_label(None, ["md-para"])
        label.set_markup(to_pango(block.spans, dark))
        label.connect("activate-link", self._link_clicked)
        if not pictures:
            self._clickable(label, block.line)
            return label

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(label)
        for node in pictures:
            box.append(self._picture(node, dark))
        self._clickable(box, block.line)
        return box

    def _picture(self, node, dark):
        path = _local_path(node.url, self.base_dir)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("md-figure")

        if path and os.path.isfile(path):
            try:
                box.append(_image_widget(path, node.title or node.url))
            except GLib.Error as exc:
                box.append(_notice(f"Could not read {os.path.basename(path)}: "
                                   f"{exc.message}", "md-missing"))
        elif _is_remote(node.url) and not self.load_remote_images:
            box.append(_notice(f"🌐 {node.url}\nRemote images are not fetched — "
                               f"turn that on in the menu if you want them.",
                               "md-remote"))
        elif _is_remote(node.url):
            box.append(_notice(f"🌐 {node.url}", "md-remote"))
        else:
            box.append(_notice(f"🖼 {node.url} — not found", "md-missing"))

        caption = markdown.node_text(node) or node.title
        if caption:
            label = Gtk.Label(label=caption, xalign=0.5, wrap=True,
                              css_classes=["md-caption"])
            box.append(label)
        return box

    def _code(self, block, dark, _document):
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.add_css_class("md-code")

        language = highlight.language_of(block.language)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.add_css_class("md-code-head")
        header.append(Gtk.Label(label=block.language or language or "text", xalign=0,
                                hexpand=True, css_classes=["md-code-lang"]))
        copy = Gtk.Button(icon_name="edit-copy-symbolic",
                          css_classes=["flat", "md-code-copy"],
                          tooltip_text="Copy this block")
        copy.connect("clicked", lambda _b: _copy(self, block.text))
        header.append(copy)
        frame.append(header)

        body = block.text
        truncated = ""
        if body.count("\n") > CODE_LINE_LIMIT:
            lines = body.split("\n")
            truncated = f"\n… {len(lines) - CODE_LINE_LIMIT} more lines"
            body = "\n".join(lines[:CODE_LINE_LIMIT])

        label = Gtk.Label(xalign=0, yalign=0, selectable=True, use_markup=True,
                          css_classes=["md-code-body"])
        label.set_markup(highlight.markup(body, block.language, dark) + esc(truncated))
        label.set_wrap(False)

        scroller = Gtk.ScrolledWindow(hexpand=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_propagate_natural_height(True)
        scroller.set_child(label)
        frame.append(scroller)
        self._clickable(frame, block.line)
        return frame

    def _quote(self, block, dark, document):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.add_css_class("md-quote")
        for child in block.children:
            widget = self._build(child, dark, document)
            if widget is not None:
                widget._line = child.line
                box.append(widget)
        self._clickable(box, block.line)
        return box

    def _list(self, block, dark, document):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                      spacing=6 if not block.tight else 1)
        box.add_css_class("md-list")
        for offset, item in enumerate(block.items):
            box.append(self._item(block, item, offset, dark, document))
        self._clickable(box, block.line)
        return box

    def _item(self, block, item, offset, dark, document):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.add_css_class("md-item")
        row.set_baseline_position(Gtk.BaselinePosition.TOP)

        if item.task is None:
            marker = Gtk.Label(
                label=f"{block.start + offset}." if block.ordered else "•",
                xalign=1.0, css_classes=["md-marker"],
            )
            marker.set_width_chars(3 if block.ordered else 1)
            # Baseline, not top: a bullet aligned to the top of the row sits
            # above the line it belongs to, because the text below it carries
            # half its leading above its own first baseline.
            marker.set_valign(Gtk.Align.BASELINE_FILL)
            row.append(marker)
        else:
            check = Gtk.CheckButton(active=item.task, css_classes=["md-task"])
            check.set_valign(Gtk.Align.BASELINE_FILL)
            check._line = item.line
            check.connect("toggled", self._task_toggled, item)
            row.append(check)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        content.set_valign(Gtk.Align.BASELINE_FILL)
        # The item's baseline is its first line of text, which is what the
        # marker beside it has to line up with.
        content.set_baseline_child(0)
        for child in item.children:
            widget = self._build(child, dark, document)
            if widget is not None:
                widget._line = child.line
                if child.kind == "para":
                    widget.add_css_class("md-item-text")
                content.append(widget)
        if item.task:
            content.add_css_class("md-task-done")
        row.append(content)
        return row

    def _task_toggled(self, button, item):
        if self._applying_task or self.on_task is None:
            return
        if item.task_line < 0:
            return
        self.on_task(item.task_line, item.task_column, button.get_active())

    def _table(self, block, dark, _document):
        grid = Gtk.Grid()
        grid.add_css_class("md-table")
        grid.set_column_homogeneous(False)

        for column, cell in enumerate(block.header):
            grid.attach(self._cell(cell, block.aligns[column], dark, head=True), column, 0, 1, 1)
        for row_index, row in enumerate(block.rows, start=1):
            for column, cell in enumerate(row):
                grid.attach(self._cell(cell, block.aligns[column], dark), column, row_index, 1, 1)

        # No scroller around it. One would let a wide table keep its columns,
        # but a scrolled window inside a vertical box claims the height it is
        # offered rather than the height it needs, and a table with a hand's
        # depth of empty space under it looks broken. The cells wrap instead,
        # which is what a narrow window wants anyway.
        grid.set_halign(Gtk.Align.FILL)
        grid.set_valign(Gtk.Align.START)
        self._clickable(grid, block.line)
        return grid

    def _cell(self, spans, align, dark, head=False):
        xalign = {"left": 0.0, "center": 0.5, "right": 1.0}[align]
        label = Gtk.Label(xalign=xalign, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                          selectable=True, use_markup=True, hexpand=True,
                          max_width_chars=48, can_focus=False)
        label.set_markup(to_pango(spans, dark))
        label.add_css_class("md-cell")
        if head:
            label.add_css_class("md-cell-head")
        label.connect("activate-link", self._link_clicked)
        return label

    def _rule(self, block, _dark, _document):
        rule = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        rule.add_css_class("md-rule")
        return rule

    def _math(self, block, _dark, _document):
        label = Gtk.Label(xalign=0.5, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                          selectable=True, use_markup=True, css_classes=["md-math"])
        try:
            body = latex.markup(latex.parse(block.text))
        except Exception:  # noqa: BLE001
            body = esc(block.text)
        label.set_markup(f'<span size="120%">{body}</span>')
        label.set_tooltip_text(block.text)
        self._clickable(label, block.line)
        return label

    def _html(self, block, _dark, _document):
        label = Gtk.Label(xalign=0, yalign=0, selectable=True, wrap=True,
                          wrap_mode=Pango.WrapMode.CHAR, css_classes=["md-html"])
        label.set_text(block.text)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("md-html-box")
        tag = Gtk.Label(label="HTML — shown as written", xalign=0,
                        css_classes=["md-html-tag"])
        box.append(tag)
        box.append(label)
        self._clickable(box, block.line)
        return box

    def _frontmatter(self, block, _dark, _document):
        grid = Gtk.Grid(column_spacing=12, row_spacing=2)
        grid.add_css_class("md-frontmatter")
        row = 0
        for line in block.text.split("\n"):
            match = re.match(r"^([A-Za-z_][\w .-]*)\s*:\s*(.*)$", line)
            if not match:
                continue
            key = Gtk.Label(label=match.group(1), xalign=1.0, css_classes=["md-fm-key"])
            value = Gtk.Label(label=match.group(2).strip().strip("\"'") or "—", xalign=0,
                              wrap=True, selectable=True, hexpand=True,
                              css_classes=["md-fm-value"])
            grid.attach(key, 0, row, 1, 1)
            grid.attach(value, 1, row, 1, 1)
            row += 1
        if row == 0:
            return None
        self._clickable(grid, block.line)
        return grid

    def _footnotes(self, document, dark):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("md-footnotes")
        box.append(Gtk.Separator())
        for label, block in document.footnotes.items():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            marker = Gtk.Label(label=f"[{label}]", xalign=1.0, yalign=0.0,
                               css_classes=["md-footnote-mark"])
            row.append(marker)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            for child in block.children:
                widget = self._build(child, dark, document)
                if widget is not None:
                    inner.append(widget)
            row.append(inner)
            row._line = block.line
            self._anchors[f"fn-{label}"] = row
            box.append(row)
        box._line = min((b.line for b in document.footnotes.values()), default=0)
        return box

    # -- interaction -------------------------------------------------------

    def _clickable(self, widget, line):
        widget._line = line
        gesture = Gtk.GestureClick()
        gesture.set_button(1)
        gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        gesture.connect("released", self._clicked, line)
        widget.add_controller(gesture)

    def _clicked(self, gesture, count, _x, _y, line):
        if count >= 2 and self.on_line is not None:
            self.on_line(line)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _link_clicked(self, _label, url):
        if url.startswith("#"):
            self.scroll_to_anchor(url[1:])
            return True
        if self.on_link is not None:
            return bool(self.on_link(url))
        return False

    def set_task(self, item_line, active):
        """Reflect a checkbox the source has changed, without looping back."""
        self._applying_task = True
        try:
            for widget in _descendants(self.column):
                if isinstance(widget, Gtk.CheckButton) and getattr(widget, "_line", None) == item_line:
                    widget.set_active(active)
        finally:
            self._applying_task = False

    # -- scrolling ---------------------------------------------------------

    def scroll_to_anchor(self, anchor: str):
        widget = self._anchors.get(anchor)
        if widget is not None:
            self._scroll_widget_to_top(widget)

    def scroll_to_line(self, line: int, ratio: float = 0.0):
        """Put the block that line belongs to at the top of the view.

        `ratio` is how far through that block the editor is, which keeps the
        two halves together inside a long code block rather than jumping only
        when a new block starts.
        """
        current, following = self._bracket(line)
        if current is None:
            return
        adjustment = self.get_vadjustment()
        top = _offset_in(self.column, current)
        if top is None:
            return
        target = top
        if following is not None:
            bottom = _offset_in(self.column, following)
            if bottom is not None and bottom > top:
                target = top + (bottom - top) * max(0.0, min(1.0, ratio))
        limit = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        adjustment.set_value(max(0.0, min(target, limit)))

    def line_at_top(self) -> int:
        """Which source line the top of the view is currently showing."""
        adjustment = self.get_vadjustment()
        value = adjustment.get_value()
        best = 0
        for widget in _children(self.column):
            offset = _offset_in(self.column, widget)
            if offset is None:
                continue
            if offset <= value + 2:
                best = getattr(widget, "_line", best)
            else:
                break
        return best

    def _bracket(self, line):
        """The block containing `line`, and the one after it."""
        previous = None
        for widget in _children(self.column):
            start = getattr(widget, "_line", None)
            if start is None:
                continue
            if start > line:
                return previous, widget
            previous = widget
        return previous, None

    def _scroll_widget_to_top(self, widget):
        offset = _offset_in(self.column, widget)
        if offset is None:
            return
        adjustment = self.get_vadjustment()
        limit = max(0.0, adjustment.get_upper() - adjustment.get_page_size())
        adjustment.set_value(max(0.0, min(offset - 8, limit)))


# ---------------------------------------------------------------- helpers --


def _signature(block, dark):
    """What makes two renderings of a block the same widget.

    The theme is in it because it changes how the widget was built rather than
    only how it is styled — a code block's colours are baked into its markup.
    The font is not, because that is a stylesheet and a widget already drawn
    picks up a new one without being built again.
    """
    parts = [block.kind, "d" if dark else "l"]
    for name in ("level", "language", "text", "ordered", "start", "tight", "aligns"):
        value = getattr(block, name, None)
        if value is not None:
            parts.append(repr(value))
    if block.kind in ("heading", "para"):
        parts.append(_spans_key(block.spans))
    elif block.kind == "table":
        parts.append(repr([[_spans_key(cell) for cell in block.header],
                           [[_spans_key(cell) for cell in row] for row in block.rows]]))
    elif block.kind == "list":
        parts.append(repr([(item.task, _blocks_key(item.children)) for item in block.items]))
    elif block.kind == "quote":
        parts.append(_blocks_key(block.children))
    return "\x1f".join(parts)


def _spans_key(spans):
    return repr([(node.kind, node.text, node.url, _spans_key(node.children))
                 for node in spans or []])


def _blocks_key(blocks):
    return repr([_signature(block, False) for block in blocks or []])


def _walk(spans):
    for node in spans or []:
        yield node
        yield from _walk(node.children)


def _children(box):
    child = box.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def _descendants(widget):
    for child in _children(widget):
        yield child
        yield from _descendants(child)


def _replace_children(box, widgets):
    """Make `box` hold exactly `widgets`, in order, moving as few as possible.

    Taking every child out and putting them all back would work, but it makes
    the scrolled window recompute its height from nothing on each keystroke,
    which is visible as a twitch in the scrollbar. Only what actually moved
    is moved.
    """
    wanted = {id(widget) for widget in widgets}
    for child in list(_children(box)):
        if id(child) not in wanted:
            box.remove(child)

    previous = None
    for widget in widgets:
        parent = widget.get_parent()
        if parent is box:
            if widget.get_prev_sibling() is not previous:
                box.remove(widget)
                box.insert_child_after(widget, previous)
        else:
            if parent is not None:
                parent.remove(widget)
            box.insert_child_after(widget, previous)
        previous = widget


def _offset_in(container, widget):
    """The widget's y position inside the container, or None before layout."""
    found, point = widget.compute_point(container, Graphene.Point().init(0, 0))
    return point.y if found else None


def _image_widget(path, tooltip):
    """A picture, scaled down on the way in.

    A photograph off a phone is four thousand pixels wide and no preview needs
    it at that size; decoding it at the width it will be drawn at is the
    difference between a note with pictures in it opening instantly and
    opening after a second of swapping.
    """
    _, width, height = GdkPixbuf.Pixbuf.get_file_info(path)[0:3]
    if width and width > MAX_IMAGE_WIDTH:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            path, MAX_IMAGE_WIDTH, -1, True)
    else:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)

    picture = Gtk.Picture.new_for_paintable(Gdk.Texture.new_for_pixbuf(pixbuf))
    picture.set_can_shrink(True)
    picture.set_content_fit(Gtk.ContentFit.CONTAIN)
    picture.set_halign(Gtk.Align.CENTER)
    # Without a height the picture is happy to be one pixel tall inside a box
    # that has no opinion; with it, it keeps the shape it was drawn at.
    picture.set_size_request(min(pixbuf.get_width(), MAX_IMAGE_WIDTH),
                             pixbuf.get_height())
    picture.add_css_class("md-image")
    picture.set_tooltip_text(tooltip)
    return picture


def _text_label(text, classes):
    label = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                      selectable=True, use_markup=True, can_focus=False)
    for name in classes:
        label.add_css_class(name)
    if text is not None:
        label.set_text(text)
    return label


def _notice(text, css):
    label = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True,
                      css_classes=[css, "md-notice"])
    return label


def _copy(widget, text):
    display = widget.get_display() or Gdk.Display.get_default()
    if display:
        display.get_clipboard().set(text)
        node = widget
        while node is not None and not isinstance(node, Adw.ToastOverlay):
            node = node.get_parent()
        if node is not None:
            node.add_toast(Adw.Toast(title="Copied", timeout=2))


def _is_remote(url: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url or "")) or \
        (url or "").startswith("//")


def _local_path(url: str, base_dir: str) -> str | None:
    """Where an image reference points on disk, or None when it points away."""
    if not url or _is_remote(url):
        if (url or "").startswith("file://"):
            return urllib.parse.unquote(urllib.parse.urlparse(url).path)
        return None
    path = urllib.parse.unquote(url.split("#")[0].split("?")[0])
    if os.path.isabs(path):
        return path
    return os.path.join(base_dir or "", path)
