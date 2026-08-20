"""The conversation view: transcript, live streaming, permissions, composer.

This is the part the user actually looks at, so it carries most of the
app's opinions. Assistant prose sits plainly on the page the way it does on
claude.ai; the user's own turns get a tinted card so the eye can find them
when scrolling back. Tool calls collapse to a single line — the name and the
one argument that matters — and open on click, because a conversation that
prints every command in full is a log, not a chat.

Permission requests and the model's own questions appear inline at the bottom
of the thread rather than as a modal dialog. They are part of the
conversation, they arrive while other sessions may also want attention, and a
dialog would block the window.
"""

from __future__ import annotations

import json
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from . import latex, markdown, widgets  # noqa: E402
from .backend import (  # noqa: E402
    MODELS,
    PERMISSION_MODES,
    QUESTION_TOOL,
    SESSION_COMMANDS,
    split_command,
)

# Older turns stay on disk; only this many are built as widgets up front.
RENDER_LIMIT = 300
# How close to the bottom still counts as being at the bottom — about a line,
# so a reply landing while you read the last one does not unstick the view.
STICK_SLACK = 32
# A code card is laid out at its full height, so these bound how much geometry
# one block can ask for. The copy button always hands over the untouched text.
CODE_LINE_LIMIT = 400
LONG_LINE_LIMIT = 2000


def _label(markup, css=(), selectable=True):
    label = Gtk.Label(
        xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
        selectable=selectable, css_classes=list(css),
    )
    try:
        label.set_markup(markup)
    except GLib.GError:
        label.set_text(markup)
    return label


def render_markdown(box, text, css=()):
    """Turn one Markdown string into a stack of native widgets inside `box`."""
    for block in markdown.blocks(text):
        kind = block[0]
        if kind == "para":
            box.append(_label(markdown.inline(block[1]), css=("msg-text",) + tuple(css)))
        elif kind == "heading":
            box.append(_label(markdown.heading_markup(block[1], block[2]),
                              css=("msg-text", "msg-heading")))
        elif kind == "code":
            box.append(CodeCard(block[2], block[1]))
        elif kind == "bullet":
            row = Gtk.Box(spacing=8, margin_start=8 + block[1] * 16)
            row.append(Gtk.Label(label=block[2], xalign=0, valign=Gtk.Align.START,
                                 css_classes=["dim"]))
            row.append(_label(markdown.inline(block[3]), css=("msg-text",)))
            box.append(row)
        elif kind == "quote":
            box.append(_label(markdown.inline(block[1]), css=("msg-text", "msg-quote")))
        elif kind == "table":
            box.append(TableCard(block[1], block[2], block[3]))
        elif kind == "math":
            box.append(MathCard(block[1]))
        elif kind == "rule":
            box.append(Gtk.Box(css_classes=["msg-rule"]))


def _at_bottom(adjustment):
    return (adjustment.get_value() + adjustment.get_page_size()
            >= adjustment.get_upper() - STICK_SLACK)


def _plural(count, noun):
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _clip(code):
    """Bound what a card renders. Copy still hands over the whole thing."""
    lines = code.rstrip("\n").split("\n")
    if len(lines) > CODE_LINE_LIMIT:
        hidden = len(lines) - CODE_LINE_LIMIT
        lines = lines[:CODE_LINE_LIMIT] + [f"… {hidden} more lines — use Copy for all of it"]
    return "\n".join(line[:LONG_LINE_LIMIT] for line in lines)


class CodeCard(Gtk.Box):
    """A fenced code block: monospace, selectable, with a copy button."""

    def __init__(self, code, language=""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["code-card"],
                         margin_top=4, margin_bottom=4)
        self.code = code

        head = Gtk.Box(spacing=6, css_classes=["code-head"])
        head.append(Gtk.Label(label=language or "text", xalign=0, hexpand=True,
                              css_classes=["monospace"]))
        head.append(widgets.icon_button("edit-copy-symbolic", "Copy", self._copy))
        self.append(head)

        # A label rather than a text view: GtkTextView only works out its
        # height during layout validation, so a ScrolledWindow asking for its
        # natural height up front gets a value that is too small and clips the
        # block. A label reports the exact height for its text, and costs less
        # than a full text buffer for something nobody is going to edit.
        body = Gtk.Label(
            label=_clip(code), xalign=0, yalign=0, selectable=True, wrap=False,
            margin_top=8, margin_bottom=8, margin_start=12, margin_end=12,
            css_classes=["monospace", "code-body"],
        )
        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=520)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_child(body)
        self.append(scroller)

    def _copy(self):
        widgets.copy_to_clipboard(self, self.code)
        widgets.toast(self, "Copied")


_ALIGN = {"left": 0.0, "center": 0.5, "right": 1.0}
# Past this a table cell wraps rather than pushing the table sideways.
CELL_WIDTH = 42


class TableCard(Gtk.Box):
    """A pipe table as a real grid, scrolling sideways when it has to.

    Columns are laid out by their content, so the eye can compare down a
    column — which is the only reason to use a table instead of a list.
    """

    def __init__(self, aligns, header, rows):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["table-card"],
                         margin_top=6, margin_bottom=6)
        grid = Gtk.Grid(css_classes=["md-table"], hexpand=True)
        for column, text in enumerate(header):
            grid.attach(_cell(text, aligns[column], ("md-th",)), column, 0, 1, 1)
        for index, row in enumerate(rows):
            css = ("md-td", "md-td-alt") if index % 2 else ("md-td",)
            for column, text in enumerate(row):
                grid.attach(_cell(text, aligns[column], css), column, index + 1, 1, 1)

        scroller = Gtk.ScrolledWindow(propagate_natural_width=True,
                                      propagate_natural_height=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_child(grid)
        self.append(scroller)


def _cell(text, align, css):
    # width_chars as well as max_width_chars, because a wrapping label reports a
    # minimum height for its *narrowest* possible width — a one-line cell that
    # could wrap to eight asks the grid for eight lines of room and leaves the
    # table trailing a gap. Pinning the minimum width to what the text actually
    # needs makes the minimum and the natural height the same thing.
    label = Gtk.Label(
        xalign=_ALIGN.get(align, 0.0), wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
        width_chars=min(len(text), CELL_WIDTH), max_width_chars=CELL_WIDTH,
        selectable=True, hexpand=True, css_classes=list(css),
    )
    try:
        label.set_markup(markdown.inline(text))
    except GLib.GError:
        label.set_text(text)
    return label


class MathCard(Gtk.Box):
    """A displayed formula, centred, with its LaTeX one click away."""

    def __init__(self, tex):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["math-card"],
                         margin_top=8, margin_bottom=8)
        self.tex = tex
        body = math_row(latex.parse(tex), css=("math", "math-display"))
        body.set_halign(Gtk.Align.CENTER)

        scroller = Gtk.ScrolledWindow(propagate_natural_width=True,
                                      propagate_natural_height=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_child(body)
        self.append(scroller)

        self.set_tooltip_text(f"{tex}\n\nClick to copy the LaTeX")
        click = Gtk.GestureClick()
        click.connect("released", self._copy)
        self.add_controller(click)

    def _copy(self, *_args):
        widgets.copy_to_clipboard(self, self.tex)
        widgets.toast(self, "LaTeX copied")


def math_row(nodes, css=("math",)):
    """One line of maths: flat stretches as a label, stacked parts as boxes.

    Most of a formula is ordinary text with the odd superscript, and Pango
    renders that far better than a pile of boxes would. Only the parts that
    are genuinely two-dimensional — fractions, roots, limits, matrices — are
    worth the widgets, so those are the only ones that get them.
    """
    box = Gtk.Box(spacing=0, valign=Gtk.Align.CENTER, css_classes=list(css))
    run = []

    def flush(more=False):
        # An empty node stands in for whatever is on the other side of the
        # split, so an operator at the seam still knows it has two sides and
        # spaces itself out. Without it `dx =` against a fraction reads `dx=`.
        if run:
            edge = [("mk", "")]
            padded = (edge if box.get_first_child() else []) + run + (edge if more else [])
            box.append(_math_label(latex.markup(padded)))
            run.clear()

    for node in nodes:
        if latex.is_stacked(node):
            flush(more=True)
            box.append(_math_widget(node))
        else:
            run.append(node)
    flush()
    return box


def _math_label(markup, css=()):
    label = Gtk.Label(valign=Gtk.Align.CENTER, css_classes=list(css))
    try:
        label.set_markup(markup)
    except GLib.GError:
        label.set_text(latex.strip_tags(markup))
    return label


def _math_widget(node):
    kind = node[0]
    if kind == "frac":
        return _fraction(node[1], node[2])
    if kind == "sqrt":
        return _radical(node[1], node[2])
    if kind == "script":
        return _limits(node) if node[4] and (node[2] or node[3]) else _sidescript(node)
    if kind == "fenced":
        return _fenced(node[1], node[2], node[3])
    if kind == "matrix":
        return _matrix(node)
    if kind == "group":
        return math_row(node[1])
    if kind == "style":
        return math_row(node[2], css=("math", "math-bold" if node[1] == "b" else "math-italic"))
    return _math_label(latex.markup([node]))


def _fraction(numerator, denominator):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                  css_classes=["math-frac"])
    for index, part in enumerate((numerator, denominator)):
        if index:
            box.append(Gtk.Box(css_classes=["math-rule"]))
        row = math_row(part)
        row.set_halign(Gtk.Align.CENTER)
        box.append(row)
    return box


def _radical(degree, body):
    box = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["math-sqrt"])
    sign = {"3": "∛", "4": "∜"}.get(latex.strip_tags(latex.markup(degree or [])).strip(), "√")
    box.append(_math_label(latex.esc(sign), css=("math-radical",)))
    box.append(math_row(body, css=("math", "math-radicand")))
    return box


def _limits(node):
    """A big operator wearing its limits above and below, as displayed maths."""
    _, base, superscript, subscript, _stacked = node
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                  css_classes=["math-limits"])
    stack = ((superscript, "math-script"), ([base], "math-bigop"), (subscript, "math-script"))
    for part, css in stack:
        if not part:
            continue
        row = math_row(part, css=("math", css))
        row.set_halign(Gtk.Align.CENTER)
        box.append(row)
    return box


def _sidescript(node):
    """Scripts beside a base that is itself stacked — `\\left(…\\right)^n`."""
    _, base, superscript, subscript, _limited = node
    box = Gtk.Box(valign=Gtk.Align.CENTER)
    box.append(_math_widget(base) if latex.is_stacked(base)
               else _math_label(latex.markup([base])))
    marks = ""
    if subscript:
        marks += f"<sub>{latex.markup(subscript)}</sub>"
    if superscript:
        marks += f"<sup>{latex.markup(superscript)}</sup>"
    if marks:
        label = _math_label(marks)
        if superscript and not subscript:
            label.set_valign(Gtk.Align.START)
        elif subscript and not superscript:
            label.set_valign(Gtk.Align.END)
        box.append(label)
    return box


def _fenced(left, inner, right):
    box = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["math-matrix"])
    if left:
        box.append(_bracket(left, "left"))
    box.append(math_row(inner))
    if right:
        box.append(_bracket(right, "right"))
    return box


_BRACKETS = {"(": "paren", ")": "paren", "[": "bracket", "]": "bracket",
             "{": "brace", "}": "brace", "|": "bar", "‖": "bar"}


def _matrix(node):
    _, rows, left, right, style = node
    grid = Gtk.Grid(css_classes=["math-grid"], column_spacing=16, row_spacing=6)
    for row_index, row in enumerate(rows):
        for column, cell in enumerate(row):
            widget = math_row(cell)
            widget.set_halign(Gtk.Align.CENTER if style == "matrix" else Gtk.Align.START)
            grid.attach(widget, column, row_index, 1, 1)

    box = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["math-matrix"])
    if left:
        box.append(_bracket(left, "left"))
    box.append(grid)
    if right:
        box.append(_bracket(right, "right"))
    return box


def _bracket(symbol, side):
    """Delimiters are drawn, not typed: a glyph cannot grow with its contents."""
    kind = _BRACKETS.get(symbol, "bracket")
    return Gtk.Box(css_classes=["math-delim", f"math-{kind}", f"math-delim-{side}"])


class ToolCard(Gtk.Box):
    """One tool call, collapsed to a line until you ask for the detail."""

    def __init__(self, entry):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["tool-card"],
                         margin_top=2, margin_bottom=2)
        self.entry = entry

        self._spinner = Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER)
        self._icon = Gtk.Image(icon_name=widgets.tool_icon(entry.get("name", "")),
                               valign=Gtk.Align.CENTER)
        self._status = Gtk.Image(icon_name="object-select-symbolic", valign=Gtk.Align.CENTER,
                                 css_classes=["dim"])

        self._summary = Gtk.Label(
            label=widgets.tool_summary(entry.get("name", ""), entry.get("input")),
            xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
            css_classes=["tool-summary"],
        )
        name = Gtk.Label(label=entry.get("name", "tool"), css_classes=["tool-name"])

        head = Gtk.Button(css_classes=["flat", "tool-head"])
        row = Gtk.Box(spacing=8)
        row.append(self._spinner)
        row.append(self._icon)
        row.append(name)
        row.append(self._summary)
        row.append(self._status)
        self._chevron = Gtk.Image(icon_name="pan-end-symbolic", css_classes=["dim"])
        row.append(self._chevron)
        head.set_child(row)
        head.connect("clicked", self._toggle)
        self.append(head)

        self._revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                             css_classes=["tool-body"], margin_top=4, margin_bottom=6,
                             margin_start=8, margin_end=8)
        self._revealer.set_child(self._body)
        self.append(self._revealer)
        self._built = False

        self.refresh(entry)

    def _toggle(self, _button):
        opening = not self._revealer.get_reveal_child()
        if opening and not self._built:
            self._build_body()
        self._revealer.set_reveal_child(opening)
        self._chevron.set_from_icon_name("pan-down-symbolic" if opening else "pan-end-symbolic")

    def _build_body(self):
        self._built = True
        widgets.clear_box(self._body)
        payload = self.entry.get("input") or {}
        if payload:
            pretty = json.dumps(payload, indent=2, ensure_ascii=False)
            if len(pretty) > 8000:
                pretty = pretty[:8000] + "\n… truncated"
            self._body.append(Gtk.Label(label="Input", xalign=0, css_classes=["dim", "tool-name"]))
            self._body.append(CodeCard(pretty, "json"))
        result = self.entry.get("result") or ""
        if result:
            self._body.append(Gtk.Label(label="Result", xalign=0, css_classes=["dim", "tool-name"]))
            self._body.append(CodeCard(result, "output"))

    def refresh(self, entry):
        self.entry = entry
        done = entry.get("done")
        failed = entry.get("is_error")
        self._spinner.set_visible(not done)
        self._spinner.set_spinning(not done)
        self._status.set_visible(bool(done))
        if done:
            self._status.set_from_icon_name(
                "dialog-warning-symbolic" if failed else "object-select-symbolic"
            )
            self._status.set_css_classes(["error"] if failed else ["dim"])
        if failed:
            self.add_css_class("tool-failed")
        summary = widgets.tool_summary(entry.get("name", ""), entry.get("input"))
        if done and (not summary or entry.get("name") == QUESTION_TOOL):
            # What was asked stops being the news once you have answered it.
            summary = markdown.first_line(entry.get("result", "")) or summary
        self._summary.set_label(summary)
        if self._built:
            was_open = self._revealer.get_reveal_child()
            self._build_body()
            self._revealer.set_reveal_child(was_open)


class ThinkingRow(Gtk.Box):
    """Reasoning, folded away by default — available, never in the way."""

    def __init__(self, text):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Button(css_classes=["flat", "thinking-row"], halign=Gtk.Align.START)
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Image(icon_name="weather-clear-night-symbolic"))
        row.append(Gtk.Label(label="Thought for a moment"))
        self._chevron = Gtk.Image(icon_name="pan-end-symbolic")
        row.append(self._chevron)
        head.set_child(row)
        head.connect("clicked", self._toggle)
        self.append(head)

        self._revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       margin_start=12, margin_bottom=6, css_classes=["msg-quote", "dim"])
        render_markdown(body, text)
        self._revealer.set_child(body)
        self.append(self._revealer)

    def _toggle(self, _button):
        opening = not self._revealer.get_reveal_child()
        self._revealer.set_reveal_child(opening)
        self._chevron.set_from_icon_name("pan-down-symbolic" if opening else "pan-end-symbolic")


class PermissionCard(Gtk.Box):
    """The terminal's y/n prompt, as something you can actually read."""

    def __init__(self, request, on_answer):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                         css_classes=["perm-card"], margin_top=6, margin_bottom=6)
        self.on_answer = on_answer

        title = Gtk.Box(spacing=8)
        title.append(Gtk.Image(icon_name="dialog-password-symbolic"))
        name = request.get("display_name") or request.get("tool_name", "A tool")
        title.append(Gtk.Label(label=f"Allow {name}?", xalign=0, hexpand=True,
                               css_classes=["perm-title"]))
        self.append(title)

        detail = request.get("description") or widgets.tool_summary(
            request.get("tool_name", ""), request.get("input")
        )
        if detail:
            self.append(_label(markdown.esc(detail), css=("perm-detail",)))

        payload = request.get("input") or {}
        command = payload.get("command") or payload.get("content") or payload.get("new_string")
        if command and len(str(command)) < 4000:
            self.append(CodeCard(str(command), _language_of(request)))

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        deny = Gtk.Button(label="Deny")
        deny.connect("clicked", lambda _b: self.on_answer(False, False))
        buttons.append(deny)

        always_mode = next(
            (s.get("mode") for s in request.get("suggestions") or []
             if s.get("type") == "setMode" and s.get("mode")),
            None,
        )
        if always_mode:
            label = dict(PERMISSION_MODES).get(always_mode, always_mode)
            always = Gtk.Button(label=f"Allow — {label.lower()}")
            always.connect("clicked", lambda _b: self.on_answer(True, True))
            buttons.append(always)

        allow = Gtk.Button(label="Allow", css_classes=["suggested-action"])
        allow.connect("clicked", lambda _b: self.on_answer(True, False))
        buttons.append(allow)
        self.append(buttons)
        widgets.focus_soon(allow)


class QuestionCard(Gtk.Box):
    """Claude asking you something, rather than asking to do something.

    AskUserQuestion arrives on the permission channel and is allowed like a
    permission — but allowing it is not the answer. The answer is the input
    the tool is then run with, so this card collects one and hands it back.
    An Allow button on its own is precisely how the terminal reports that
    nobody was at the keyboard.
    """

    def __init__(self, request, on_answer):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                         css_classes=["perm-card"], margin_top=6, margin_bottom=6)
        self.on_answer = on_answer
        self.groups = []

        questions = request.get("questions") or []
        title = Gtk.Box(spacing=8)
        title.append(Gtk.Image(icon_name="dialog-question-symbolic"))
        title.append(Gtk.Label(
            label="Claude has a question" if len(questions) == 1
            else f"Claude has {len(questions)} questions",
            xalign=0, hexpand=True, css_classes=["perm-title"],
        ))
        self.append(title)

        for index, question in enumerate(questions):
            if index:
                self.append(Gtk.Box(css_classes=["msg-rule"]))
            group = _QuestionGroup(question, self._sync, self._submit)
            self.groups.append(group)
            self.append(group)

        self.hint = Gtk.Label(xalign=0, hexpand=True, wrap=True, css_classes=["ask-hint"])
        buttons = Gtk.Box(spacing=8)
        buttons.append(self.hint)
        skip = Gtk.Button(label="Skip",
                          tooltip_text="Let the turn carry on without an answer")
        skip.connect("clicked", lambda _b: self.on_answer({}, {}, ""))
        buttons.append(skip)
        self.send = Gtk.Button(label="Answer", css_classes=["suggested-action"])
        self.send.connect("clicked", lambda _b: self._submit())
        buttons.append(self.send)
        self.append(buttons)

        self._sync()
        if self.groups:
            self.groups[0].focus()
        else:
            widgets.focus_soon(self.send)

    def _sync(self):
        """Answering some of them is allowed; answering none of them is Skip."""
        answered = sum(1 for group in self.groups if group.answer()[0])
        self.send.set_sensitive(answered > 0)
        missing = len(self.groups) - answered
        if not answered:
            self.hint.set_label("Choose an option, or write an answer of your own")
        elif missing:
            self.hint.set_label(f"{_plural(missing, 'question')} still unanswered")
        else:
            self.hint.set_label("")

    def _submit(self):
        if not self.send.get_sensitive():
            return
        answers, annotations = {}, {}
        for group in self.groups:
            value, annotation = group.answer()
            if not value:
                continue
            answers[group.text] = value
            if annotation:
                annotations[group.text] = annotation
        self.on_answer(answers, annotations, "")


class _QuestionGroup(Gtk.Box):
    """One question, its options, and the box for an answer of your own.

    Options are radio buttons, or check boxes where the model said more than
    one may be chosen. "Something else" is always offered: the tool promises
    the person that escape hatch and tells the model not to spend an option
    on it, so it has to come from here.
    """

    def __init__(self, question, on_change, on_activate):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.text = question.get("question", "")
        self.multi = bool(question.get("multi"))
        self._on_change = on_change
        self._choices = []   # (option, check), in the order they were offered
        self._previews = []  # (check, revealer) for the options that have one

        header = question.get("header") or ""
        if header:
            self.append(Gtk.Label(label=header, halign=Gtk.Align.START,
                                  css_classes=["ask-chip"]))
        # Not selectable, and so not a focus stop: a selectable label selects
        # all of its text the moment focus lands on it, and the card is a row
        # of controls to move through, not prose to copy out of. The question
        # is in the tool card above in any case.
        self.append(_label(markdown.inline(self.text), css=("ask-question",),
                           selectable=False))

        group = None
        for option in question.get("options") or []:
            check = Gtk.CheckButton(css_classes=["ask-option"])
            if not self.multi:
                # The first one starts the group; set_group(None) is a no-op
                # for it and every later one joins what it started.
                check.set_group(group)
                group = group or check
            check.set_child(self._option_child(option))
            check.connect("toggled", self._changed)
            self.append(check)
            self._choices.append((option, check))
            if option.get("preview"):
                revealer = Gtk.Revealer(
                    child=CodeCard(option["preview"], "preview"),
                    transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                    margin_start=28,
                )
                self.append(revealer)
                self._previews.append((check, revealer))

        self.other = Gtk.CheckButton(label="Something else…", css_classes=["ask-option"])
        if not self.multi:
            self.other.set_group(group)
        self.other.connect("toggled", self._changed)
        self.append(self.other)

        self.entry = Gtk.Entry(placeholder_text="Your answer", css_classes=["ask-other"],
                               margin_start=28, margin_bottom=2)
        self.entry.connect("changed", self._typed)
        self.entry.connect("activate", lambda _entry: on_activate())
        self._entry_revealer = Gtk.Revealer(
            child=self.entry, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
        )
        self.append(self._entry_revealer)

    def _option_child(self, option):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        box.append(_label(markdown.inline(option["label"]), css=("ask-label",),
                          selectable=False))
        if option.get("description"):
            box.append(_label(markdown.inline(option["description"]), css=("ask-desc",),
                              selectable=False))
        return box

    def _changed(self, _widget=None):
        for check, revealer in self._previews:
            revealer.set_reveal_child(check.get_active())
        self._entry_revealer.set_reveal_child(self.other.get_active())
        if self.other.get_active() and not self.entry.has_focus():
            GLib.idle_add(self._focus_entry)
        self._on_change()

    def _typed(self, entry):
        # Typing is choosing: it selects "Something else" — and in a group of
        # radio buttons that also lets go of whatever was picked before.
        if entry.get_text().strip() and not self.other.get_active():
            self.other.set_active(True)
        self._on_change()

    def _focus_entry(self):
        # Without-selecting, or the first keystroke after the click would
        # select what it just typed and the second would replace it.
        self.entry.grab_focus_without_selecting()
        return GLib.SOURCE_REMOVE

    def answer(self):
        """What has been chosen here, and anything worth noting about it.

        The tool wants one string per question, so several boxes ticked come
        back comma-separated — which is what it does with a list of its own
        accord. Nothing chosen returns an empty string, and the question is
        then left out of the answers entirely rather than answered blankly.
        """
        picked = [option for option, check in self._choices if check.get_active()]
        values = [option["label"] for option in picked]
        own = self.entry.get_text().strip() if self.other.get_active() else ""
        if own:
            values.append(own)
        if not values:
            return "", None
        annotation = {}
        if len(picked) == 1 and picked[0].get("preview"):
            annotation["preview"] = picked[0]["preview"]
        return ", ".join(values), annotation or None

    def focus(self):
        widgets.focus_soon(self._choices[0][1] if self._choices else self.entry)


def _language_of(request):
    """Label the preview by what is being written, not by the tool doing it."""
    if request.get("tool_name") == "Bash":
        return "bash"
    path = (request.get("input") or {}).get("file_path") or ""
    extension = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return extension if 0 < len(extension) <= 5 else ""


class QueueStrip(Gtk.Box):
    """Messages typed ahead, sitting between the thread and the composer.

    They are deliberately not in the transcript: nothing has been said yet.
    Each is a button — click to pull it back into the composer and edit it,
    or use the × to drop it.
    """

    SHOWN = 8

    def __init__(self, on_edit, on_remove):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2, visible=False,
                         css_classes=["queue-strip"])
        self.on_edit = on_edit
        self.on_remove = on_remove

    def set_messages(self, messages):
        widgets.clear_box(self)
        if not messages:
            self.set_visible(False)
            return
        count = len(messages)
        self.append(Gtk.Label(
            label=f"{count} queued · sent one per turn as Claude finishes",
            xalign=0, css_classes=["queue-head"],
        ))
        for index, text in enumerate(messages[: self.SHOWN]):
            self.append(self._row(index, text))
        if count > self.SHOWN:
            self.append(Gtk.Label(label=f"… and {count - self.SHOWN} more", xalign=0,
                                  css_classes=["queue-head"]))
        self.set_visible(True)

    def _row(self, index, text):
        row = Gtk.Box(spacing=2, css_classes=["queue-row"])

        line = Gtk.Box(spacing=8)
        line.append(Gtk.Label(label=f"{index + 1}", css_classes=["dim", "queue-index"]))
        line.append(Gtk.Label(label=" ".join(text.split()), xalign=0, hexpand=True,
                              ellipsize=Pango.EllipsizeMode.END))
        edit = Gtk.Button(child=line, hexpand=True,
                          css_classes=["flat", "queue-edit"],
                          tooltip_text=f"{text}\n\nClick to edit — puts it back in the composer")
        edit.connect("clicked", lambda _b, i=index: self.on_edit(i))
        row.append(edit)
        row.append(widgets.icon_button("window-close-symbolic", "Remove from the queue",
                                       lambda i=index: self.on_remove(i)))
        return row


def command_prefix(text):
    """The half-typed command name, if that is all there is in the box.

    Only while the slash word is still the whole of it: once there is a space
    or a second line, the command has been chosen and what follows is its
    argument, which the menu has nothing to say about.
    """
    if not text.startswith("/") or text.startswith("//"):
        return None
    body = text[1:]
    if not all(char.isalnum() or char in "-_" for char in body):
        return None
    return body


class CommandStrip(Gtk.Box):
    """The command menu the terminal shows as you type a slash.

    Inline above the composer rather than a popover: this column already
    carries permissions and questions, and a popover would take the focus out
    of the box being typed in — which is exactly where Up, Down and Tab have
    to keep arriving for the menu to be usable at all.
    """

    SHOWN = 7

    def __init__(self, on_pick):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=1, visible=False,
                         css_classes=["cmd-strip"])
        self.on_pick = on_pick
        self.matches = []
        self.index = 0
        self._rows = []

    @property
    def open(self):
        return self.get_visible() and bool(self.matches)

    def update(self, prefix, commands):
        """Show what the typed prefix could still become."""
        matches = widgets.match_commands(prefix, commands)[: self.SHOWN]
        if not matches:
            self.close()
            return
        # Keep the highlight on the same command while it is still offered:
        # typing another letter should narrow the list, not move the choice.
        chosen = self.selected()
        self.matches = matches
        self.index = next((i for i, item in enumerate(matches)
                           if chosen and item.get("name") == chosen.get("name")), 0)
        self._build()
        self.set_visible(True)

    def close(self):
        """Put the menu away; True if it was open."""
        if not self.get_visible():
            return False
        self.set_visible(False)
        self.matches = []
        self.index = 0
        return True

    def move(self, step):
        if not self.open:
            return False
        self.index = (self.index + step) % len(self.matches)
        self._highlight()
        return True

    def selected(self):
        return self.matches[self.index] if self.open and self.matches else None

    def _build(self):
        widgets.clear_box(self)
        self._rows = []
        for index, command in enumerate(self.matches):
            self.append(self._row(index, command))
        self.append(Gtk.Label(
            label="↑↓ choose · Tab completes · Esc dismisses", xalign=0,
            css_classes=["cmd-hint"],
        ))
        self._highlight()

    def _row(self, index, command):
        line = Gtk.Box(spacing=8)
        line.append(Gtk.Label(label=f"/{command.get('name', '')}", css_classes=["cmd-name"]))
        argument = " ".join(str(command.get("argumentHint") or "").split())
        if argument:
            line.append(Gtk.Label(label=argument, css_classes=["cmd-args"],
                                  ellipsize=Pango.EllipsizeMode.END))
        description = widgets.command_hint(command)
        if description:
            line.append(Gtk.Label(label=description, xalign=0, hexpand=True,
                                  ellipsize=Pango.EllipsizeMode.END, css_classes=["cmd-desc"]))
        else:
            line.append(Gtk.Box(hexpand=True))
        button = Gtk.Button(child=line, css_classes=["flat", "cmd-row"])
        button.connect("clicked", lambda _b, item=command: self.on_pick(item))
        self._rows.append(button)
        return button

    def _highlight(self):
        for index, row in enumerate(self._rows):
            row.set_css_classes(["flat", "cmd-row"]
                                + (["cmd-row-active"] if index == self.index else []))


class ReportCard(Gtk.Box):
    """What a report command answered — to read, not to keep.

    `/usage` and its like say something about the tool rather than anything to
    the model, so the answer belongs in front of you and nowhere else: it is
    never written to the transcript, and closing the card is the end of it.
    """

    def __init__(self, name, on_close):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         css_classes=["report-card"], margin_top=6, margin_bottom=6)
        self.name = name
        head = Gtk.Box(spacing=8)
        head.append(Gtk.Label(label=f"/{name}", xalign=0, hexpand=True,
                              css_classes=["report-title"]))
        head.append(widgets.icon_button("window-close-symbolic", "Close", on_close))
        self.append(head)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.append(self.body)

    def show(self, text, pending=False, failed=False):
        widgets.clear_box(self.body)
        if pending:
            row = Gtk.Box(spacing=8)
            row.append(Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER))
            row.append(Gtk.Label(label="Asking Claude Code…", xalign=0, css_classes=["dim"]))
            self.body.append(row)
        elif failed:
            self.body.append(_label(markdown.esc(text), css=("error-card",)))
        else:
            render_markdown(self.body, text)


class Composer(Gtk.Box):
    """Multi-line prompt box. Enter sends, Shift+Enter makes a new line.

    While a turn is running Enter still takes the message — it joins the
    queue instead of being refused. The button is Stop only when there is
    nothing typed, so the same key never means two things at once.

    A leading slash opens the command menu above the box, which is the one
    thing that changes what the keys do: while it is open Up and Down move
    through it, Tab completes, and Escape puts it away rather than stopping
    the run.
    """

    def __init__(self, on_send, on_stop, commands=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.on_send = on_send
        self.on_stop = on_stop
        self.commands = commands or (lambda: [])
        self._busy = False
        self._completing = False

        self.strip = CommandStrip(self._complete)
        self.append(self.strip)

        shell = Gtk.Box(spacing=6, css_classes=["composer"])
        self.view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True,
            top_margin=8, bottom_margin=8, css_classes=["composer-entry"],
        )
        scroller = Gtk.ScrolledWindow(propagate_natural_height=True, max_content_height=200,
                                      hexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.view)
        shell.append(scroller)

        self.button = Gtk.Button(icon_name="go-up-symbolic", valign=Gtk.Align.END,
                                 css_classes=["suggested-action", "send-button"],
                                 tooltip_text="Send (Enter)")
        self.button.connect("clicked", lambda _b: self._fire())
        shell.append(self.button)
        self.append(shell)

        self.hint = Gtk.Label(label="Enter to send · Shift+Enter for a new line",
                              xalign=0, css_classes=["composer-hint"])
        self.append(self.hint)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.view.add_controller(keys)

        self.view.get_buffer().connect("changed", self._on_changed)

    # ------------------------------------------------------------- the menu --

    def _on_changed(self, _buffer):
        self._refresh_button()
        if self._completing:
            return
        prefix = command_prefix(self._raw())
        if prefix is None:
            self.strip.close()
        else:
            self.strip.update(prefix, self.commands())

    def _complete(self, command=None):
        """Put the highlighted command in the box, ready for its arguments."""
        command = command or self.strip.selected()
        if command is None:
            return False
        text = f"/{command.get('name', '')}"
        if command.get("argumentHint"):
            text += " "
        self._completing = True
        buffer = self.view.get_buffer()
        buffer.set_text(text)
        buffer.place_cursor(buffer.get_end_iter())
        self._completing = False
        self.strip.close()
        self._refresh_button()
        self.focus()
        return True

    def close_commands(self):
        """Escape's first job, when there is a menu to put away."""
        return self.strip.close()

    def _whole_command(self):
        """True when what is typed already names a command in full.

        Enter then means send, not complete — finishing a name you have
        already finished is the one thing it must not do.
        """
        name, _ = split_command(self._text())
        if not name:
            return False
        return any(name == item.get("name") or name in (item.get("aliases") or [])
                   for item in self.commands())

    # ----------------------------------------------------------------- keys --

    def _on_key(self, _controller, keyval, _code, state):
        if self.strip.open:
            if keyval == Gdk.KEY_Down:
                self.strip.move(1)
                return Gdk.EVENT_STOP
            if keyval == Gdk.KEY_Up:
                self.strip.move(-1)
                return Gdk.EVENT_STOP
            if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
                self._complete()
                return Gdk.EVENT_STOP
            if keyval == Gdk.KEY_Escape:
                self.strip.close()
                return Gdk.EVENT_STOP
            if (keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
                    and not state & Gdk.ModifierType.SHIFT_MASK
                    and not self._whole_command()):
                self._complete()
                return Gdk.EVENT_STOP
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if state & Gdk.ModifierType.SHIFT_MASK:
                return Gdk.EVENT_PROPAGATE
            self._fire()
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _raw(self):
        buffer = self.view.get_buffer()
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, False)

    def _text(self):
        return self._raw().strip()

    def _fire(self):
        text = self._text()
        self.strip.close()
        if not text:
            if self._busy:
                self.on_stop()
            return
        self.view.get_buffer().set_text("")
        self.on_send(text)

    def prefill(self, text):
        """Put text back in the box, ahead of whatever is already typed."""
        buffer = self.view.get_buffer()
        existing = self._text()
        buffer.set_text(f"{text}\n\n{existing}" if existing else text)
        buffer.place_cursor(buffer.get_end_iter())
        self.focus()

    def set_busy(self, busy, note=""):
        self._busy = busy
        self._refresh_button()
        self.hint.set_label(note or ("Working… Enter queues your next message · Esc stops"
                                     if busy else "Enter to send · Shift+Enter for a new line"))

    def _refresh_button(self):
        # Stop only when the box is empty: with something typed, the button
        # does what Enter does, so neither can be pressed by mistake.
        stopping = self._busy and not self._text()
        self.button.set_icon_name("media-playback-stop-symbolic" if stopping
                                  else "go-up-symbolic")
        self.button.set_tooltip_text(
            "Stop (Esc)" if stopping else
            "Queue for when this turn ends (Enter)" if self._busy else "Send (Enter)"
        )
        self.button.set_css_classes(
            ["destructive-action", "send-button"] if stopping
            else ["suggested-action", "send-button"]
        )

    def focus(self):
        self.view.grab_focus()


class ChatView(Gtk.Box):
    """Binds one Session to the widgets that show it."""

    def __init__(self, session):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.session = session
        self._rows = {}
        self._prompt_card = None
        self._report_card = None
        self._empty = None
        self._stuck = True
        self._scroll_queued = False

        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                            margin_top=16, margin_bottom=12)
        clamp = Adw.Clamp(maximum_size=widgets.READING_WIDTH, child=self.list,
                          margin_start=12, margin_end=12)

        self.scroller = Gtk.ScrolledWindow(vexpand=True)
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_child(clamp)

        # The view follows the conversation until you scroll away from the
        # bottom, and follows it again the moment you come back. Two signals
        # say which is which: `value-changed` is the view moving — you — and
        # `changed` is the content growing under it.
        adjustment = self.scroller.get_vadjustment()
        adjustment.connect("value-changed", self._on_view_moved)
        adjustment.connect("changed", self._on_content_grew)

        self.jump = Gtk.Button(
            icon_name="go-bottom-symbolic", visible=False,
            halign=Gtk.Align.END, valign=Gtk.Align.END,
            css_classes=["osd", "circular", "jump-button"],
            tooltip_text="Jump to the latest message",
        )
        self.jump.connect("clicked", lambda _b: self._scroll_soon(force=True))

        overlay = Gtk.Overlay(child=self.scroller, vexpand=True)
        overlay.add_overlay(self.jump)
        self.append(overlay)

        self.streaming = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False)
        self._stream_label = _label("", css=("msg-text",))
        self.streaming.append(self._stream_label)
        self.list.append(self.streaming)

        self.queue_strip = QueueStrip(self._edit_queued, self._remove_queued)
        self.composer = Composer(self._send, self.stop,
                                 commands=self.session.command_list)
        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        stack.append(self.queue_strip)
        stack.append(self.composer)
        bottom = Adw.Clamp(maximum_size=widgets.READING_WIDTH, child=stack,
                           margin_start=12, margin_end=12, margin_top=4, margin_bottom=12)
        self.append(bottom)

        self.session.subscribe(self._on_session_event)
        self.reload()

    # ------------------------------------------------------------ rendering --

    def reload(self):
        # Every message label is selectable, so one of them may hold the
        # window's focus. Destroying the focused widget leaves the scrolled
        # window checking the ancestry of something that no longer exists, so
        # hand focus back to the composer first — which is where it belongs
        # after a reload anyway.
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        if focus is not None and focus.is_ancestor(self.list):
            self.composer.focus()

        widgets.clear_box(self.list)
        self._rows.clear()
        self._prompt_card = None
        # A report was answered to the window, not to the conversation, so a
        # rebuilt view has nothing to put back.
        self._report_card = None
        self._empty = None

        entries = self.session.entries
        start = max(0, len(entries) - RENDER_LIMIT)
        if start:
            self.list.append(Gtk.Label(
                label=f"{start} earlier messages are on disk but not shown",
                css_classes=["dim", "turn-meta"],
            ))
        if not entries:
            self._empty = self._empty_state()
            self.list.append(self._empty)
        for index in range(start, len(entries)):
            self._add_row(index, entries[index])

        self.list.append(self.streaming)
        if self.session.pending_permission:
            self._show_prompt(self.session.pending_permission)
        self.queue_strip.set_messages(self.session.outbox)
        self._sync_state()
        self._scroll_soon(force=True)

    def _empty_state(self):
        page = Adw.StatusPage(
            icon_name="mail-send-symbolic",
            title="Ready when you are",
            description=f"Working in {widgets.shorten_path(self.session.meta.cwd)}",
            vexpand=True,
        )
        page.add_css_class("compact")
        return page

    def _add_row(self, index, entry):
        widget = self._build_row(entry)
        if widget is None:
            return
        self._rows[index] = widget
        self.list.append(widget)

    def _build_row(self, entry):
        role = entry.get("role")
        if role == "user":
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                          css_classes=["msg-user"], margin_start=48)
            render_markdown(box, entry.get("text", ""))
            return box
        if role == "assistant":
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            render_markdown(box, entry.get("text", ""))
            return box
        if role == "thinking":
            return ThinkingRow(entry.get("text", ""))
        if role == "tool":
            return ToolCard(entry)
        if role == "notice":
            return _label(markdown.esc(entry.get("text", "")), css=("notice-card",))
        if role == "error":
            return _label(markdown.esc(entry.get("text", "")), css=("error-card",))
        if role == "turn":
            return self._turn_meta(entry)
        return None

    def _turn_meta(self, entry):
        bits = []
        duration = entry.get("duration_ms") or 0
        if duration:
            bits.append(f"{duration / 1000:.1f}s")
        cost = entry.get("cost") or 0
        if cost:
            bits.append(f"${cost:.4f}")
        return Gtk.Label(label=" · ".join(bits), xalign=0, css_classes=["turn-meta"])

    # --------------------------------------------------------------- events --

    def _on_session_event(self, what, data):
        if what == "entry_added":
            self._drop_empty_state()
            index = data["index"]
            widget = self._build_row(data["entry"])
            if widget is not None:
                self._rows[index] = widget
                self.list.insert_child_after(widget, self._before_streaming())
            self._scroll_soon()
        elif what == "entry_updated":
            widget = self._rows.get(data["index"])
            if isinstance(widget, ToolCard):
                widget.refresh(data["entry"])
        elif what == "delta":
            self._show_delta(data.get("text", ""), data.get("kind", "text"))
        elif what == "state":
            self._sync_state()
        elif what == "permission":
            self._show_prompt(data["request"])
        elif what == "permission_cleared":
            self._clear_prompt()
        elif what == "report":
            self._show_report(data.get("name", ""), data.get("text", ""),
                              pending=data.get("pending", False),
                              failed=data.get("failed", False))
        elif what == "outbox":
            self.queue_strip.set_messages(self.session.outbox)
            self._sync_state()
        elif what == "reloaded":
            self.reload()
        elif what == "rate_limit":
            widgets.toast(self, f"Usage at {data.get('percent', 0)}% of your limit", timeout=6)

    def _drop_empty_state(self):
        if self._empty is not None:
            self.list.remove(self._empty)
            self._empty = None

    def _before_streaming(self):
        sibling = self.list.get_first_child()
        previous = None
        while sibling is not None and sibling is not self.streaming:
            previous = sibling
            sibling = sibling.get_next_sibling()
        return previous

    def _show_delta(self, text, kind):
        if not text:
            self.streaming.set_visible(False)
            self._stream_label.set_text("")
            return
        prefix = "" if kind == "text" else "<i>thinking… </i>"
        try:
            self._stream_label.set_markup(prefix + markdown.esc(text[-4000:]))
        except GLib.GError:
            self._stream_label.set_text(text[-4000:])
        self.streaming.set_visible(True)
        self._scroll_soon()

    def _show_prompt(self, request):
        """Whatever the run has stopped to wait for, at the foot of the thread.

        A permission and a question arrive the same way and both end the same
        way — with the turn carrying on — so they share this one place, and
        differ only in what they put in it.
        """
        self._drop_empty_state()
        self._clear_prompt()
        if request.get("questions"):
            card = QuestionCard(request, self._answer_question)
        else:
            card = PermissionCard(request, self._answer_permission)
        self._prompt_card = card
        self.list.append(card)
        self._sync_state()
        self._scroll_soon()

    def _clear_prompt(self):
        if self._prompt_card is not None:
            self.list.remove(self._prompt_card)
            self._prompt_card = None
            self._sync_state()

    def _answer_permission(self, allow, always):
        self._clear_prompt()
        self.session.answer_permission(allow, always)

    def _answer_question(self, answers, annotations, response=""):
        self._clear_prompt()
        self.session.answer_question(answers, annotations, response)

    def _show_report(self, name, text, pending=False, failed=False):
        """One card per report, filled in when the answer arrives."""
        self._drop_empty_state()
        if self._report_card is None or self._report_card.name != name:
            self._clear_report()
            self._report_card = ReportCard(name, self._clear_report)
            self.list.append(self._report_card)
        self._report_card.show(text, pending=pending, failed=failed)
        self._scroll_soon()

    def _clear_report(self):
        if self._report_card is not None:
            self.list.remove(self._report_card)
            self._report_card = None

    def _sync_state(self):
        state = self.session.state
        busy = self.session.busy
        note = self.session.status_note
        if state == "starting" and not note:
            note = "Starting Claude Code…"
        if busy and not note:
            note = "Working… Enter queues your next message · Esc stops"
        if isinstance(self._prompt_card, QuestionCard):
            note = "Waiting on your answer · Enter replies in your own words"
        queued = len(self.session.outbox)
        if queued:
            note = f"{note} · {_plural(queued, 'message')} queued"
        self.composer.set_busy(busy, note)

    def _send(self, text):
        # A question on screen is what the turn is waiting for, so what you
        # type is an answer to it, not the next thing you want to say. Queued,
        # it would sit there for a turn that cannot end until you answer.
        if isinstance(self._prompt_card, QuestionCard):
            self._answer_question({}, {}, text)
        elif not self._run_here(text):
            name, _ = split_command(text)
            if name in SESSION_COMMANDS and self.session.busy:
                widgets.toast(self, f"/{name} answers when this turn ends — only this "
                                    "conversation's own session can tell you")
            self.session.send(text)
        self._scroll_soon(force=True)

    def _run_here(self, text):
        """The commands the window already owns; True if this was one.

        Passing these to the child would work and leave the window wrong:
        what this conversation is called, what is in its transcript and which
        model the next child is launched with are the app's to change, and it
        would never hear that they had.
        """
        name, argument = split_command(text)
        known = self.session.command_names()
        if not name or (known and name not in known):
            # With a list to check against, an unknown name is someone typing
            # a path; with no list yet — nothing launched, nothing cached —
            # these three are the app's own either way.
            return False
        if name in ("clear", "reset", "new"):
            self.activate_action("win.clear", None)
            return True
        if name in ("rename", "name"):
            if argument:
                self.session.rename(argument)
                widgets.toast(self, f"Renamed to “{self.session.meta.name}”")
            else:
                self.activate_action("win.rename", None)
            return True
        if name == "model":
            return self._set_model(argument)
        return False

    def _set_model(self, argument):
        labels = dict(MODELS)
        names = ", ".join(key for key, _ in MODELS)
        wanted = " ".join(argument.split()).lower()
        if not wanted:
            widgets.toast(self, f"Model is {labels.get(self.session.meta.model, '?')} "
                                f"· /model {names}")
            return True
        match = next((key for key, label in MODELS
                      if wanted in (key.lower(), label.lower())), "")
        if not match:
            widgets.toast(self, f"No model called “{wanted}” — try {names}")
            return True
        if match == self.session.meta.model:
            widgets.toast(self, f"Already on {labels[match]}")
            return True
        # Fixed at spawn, so the change lands with the next message — which is
        # what the header dropdown does too.
        self.session.set_model(match)
        widgets.toast(self, f"{labels[match]} from your next message")
        return True

    # ---------------------------------------------------------------- queue --

    def escape(self):
        """What Escape does here, in the order the eye expects.

        A menu on screen is the nearest thing to dismiss, and dismissing it
        must not also stop the run — reaching for Escape to close a list you
        opened by typing a slash is not asking to cancel the turn.
        """
        if self.composer.close_commands():
            return
        self.stop()

    def stop(self):
        """Esc, or the stop button. Ends the turn and hands back the queue.

        Stopping usually means the reply went somewhere you did not want, and
        the follow-ups you typed were written for the version of events you
        have just cancelled. Sending them anyway would restart the run you
        just stopped, so they come back to the composer instead — edit or
        re-send, but on purpose.
        """
        if self.session.busy:
            self.session.interrupt()
        self.take_back_queue()

    def take_back_queue(self):
        queued = self.session.clear_outbox()
        if not queued:
            return
        self.composer.prefill("\n\n".join(queued))
        widgets.toast(self, f"{_plural(len(queued), 'queued message')} back in the composer")

    def _edit_queued(self, index):
        text = self.session.take_queued(index)
        if text:
            self.composer.prefill(text)

    def _remove_queued(self, index):
        if self.session.take_queued(index):
            widgets.toast(self, "Removed from the queue")

    # --------------------------------------------------------------- scroll --

    def _on_view_moved(self, adjustment):
        """You moved the view: whether it keeps following is now your call."""
        self._set_stuck(_at_bottom(adjustment))

    def _on_content_grew(self, adjustment):
        """The conversation got longer. Follow it only if we were following.

        A streaming reply resizes the view many times a second, and almost
        always the view is already where it needs to be — so check before
        queueing work, rather than scheduling a scroll per frame.
        """
        if self._stuck and not _at_bottom(adjustment):
            self._scroll_soon()

    def _set_stuck(self, stuck):
        if stuck == self._stuck:
            return
        self._stuck = stuck
        self.jump.set_visible(not stuck)

    def _scroll_soon(self, force=False):
        """Go to the bottom once the new content has been laid out.

        `force` is for the two moments where following is not in question —
        opening a conversation, and sending a message. Everything else defers
        to where you left the view.
        """
        if force:
            self._set_stuck(True)
        if not self._stuck or self._scroll_queued:
            return
        self._scroll_queued = True
        GLib.idle_add(self._scroll_to_end, priority=GLib.PRIORITY_LOW)

    def _scroll_to_end(self):
        self._scroll_queued = False
        adjustment = self.scroller.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        return GLib.SOURCE_REMOVE

    def detach(self):
        self.session.unsubscribe(self._on_session_event)
