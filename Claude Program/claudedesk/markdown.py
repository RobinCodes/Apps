r"""Just enough Markdown to render a Claude reply as native GTK.

A web view would give us a full Markdown renderer for free, and cost 300 MB
to do it. Instead the model's text is split into blocks here, and each block
becomes an ordinary GTK widget: paragraphs and headings are Pango markup on a
label, fenced code becomes its own monospace card with a copy button, a table
becomes a grid, and maths goes through `latex.py`.

Two rules matter throughout. Everything that reaches Pango is escaped exactly
once — inline spans are built from escaped fragments, never by escaping the
finished markup. And anything unparseable degrades to plain text rather than
raising, because a stray backtick in a model reply must not blank the window.

The dollar sign is the one place this guesses. `$x$` is maths and `$5 and $6`
is money, and only the shape of what is between them tells you which: no
space against the delimiters, something operator-like inside, no digit
straight after the close. Anything that fails those tests stays as text,
because turning prices into italics is a worse failure than missing a formula.
"""

from __future__ import annotations

import re

from gi.repository import GLib

from . import latex

_FENCE = re.compile(r"^\s*(?:```+|~~~+)\s*([\w+-]*)\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
_TASK = re.compile(r"^\[([ xX])\]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?(?:\s*:?-{2,}:?\s*\|){1,}\s*:?-{2,}:?\s*\|?\s*$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")

_MATH_OPEN = re.compile(r"^\s*(\$\$|\\\[)")
_MATH_ENV = re.compile(r"^\s*\\begin\{(equation\*?|align\*?|gather\*?|aligned|multline\*?)\}")

_INLINE = re.compile(
    r"(?P<code>`+[^`\n]+?`+)"
    r"|(?P<mathp>\\\((?:[^\\]|\\(?!\)))*?\\\))"
    r"|(?P<math>\$(?!\s)(?:[^$\n\\]|\\.)+?(?<!\s)\$(?!\d))"
    r"|(?P<link>\[[^\]\n]*\]\([^)\s]+\))"
    r"|(?P<bold>\*\*(?:[^*\n]|\*(?!\*))+\*\*|__[^_\n]+__)"
    r"|(?P<italic>\*[^*\n]+\*|(?<![\w_])_[^_\n]+_(?![\w_]))"
    r"|(?P<strike>~~[^~\n]+~~)"
    r"|(?P<url>https?://[^\s<>()\[\]]+)"
)

# What makes a run of text between dollars look like maths rather than money.
_MATHY = re.compile(r"[\\^_{}=<>]|[A-Za-z0-9]\s*[-+*/]\s*[A-Za-z0-9(\\]|[A-Za-z]\s*\(")
_LONE_TOKEN = re.compile(r"[A-Za-z]\w{0,2}|\d+(?:\.\d+)?")

HEADING_SCALE = {1: "x-large", 2: "large", 3: "large", 4: "medium", 5: "medium", 6: "medium"}


def blocks(text):
    """Split Markdown into blocks: para, code, heading, bullet, quote, rule,
    table and math, each a tuple whose first item names the kind."""
    out = []
    lines = (text or "").replace("\r\n", "\n").split("\n")
    paragraph = []
    quote = []
    index = 0

    def flush_paragraph():
        if paragraph:
            out.append(("para", soft_wrap("\n".join(paragraph))))
            paragraph.clear()

    def flush_quote():
        if quote:
            out.append(("quote", soft_wrap("\n".join(quote))))
            quote.clear()

    def flush():
        flush_paragraph()
        flush_quote()

    while index < len(lines):
        line = lines[index]

        fence = _FENCE.match(line)
        if fence:
            flush()
            language = fence.group(1) or ""
            body = []
            index += 1
            while index < len(lines) and not _FENCE.match(lines[index]):
                body.append(lines[index])
                index += 1
            index += 1  # step past the closing fence, or off the end
            out.append(("code", language, "\n".join(body)))
            continue

        if _MATH_OPEN.match(line) or _MATH_ENV.match(line):
            flush()
            formula, index = _math_block(lines, index)
            if formula:
                out.append(("math", formula))
            continue

        if "|" in line and index + 1 < len(lines) and _TABLE_SEP.match(lines[index + 1]):
            flush()
            table, index = _table(lines, index)
            out.append(table)
            continue

        quoted = _QUOTE.match(line)
        if quoted:
            flush_paragraph()
            quote.append(quoted.group(1))
            index += 1
            continue
        flush_quote()

        if not line.strip():
            flush_paragraph()
            index += 1
            continue

        if _RULE.match(line):
            flush_paragraph()
            out.append(("rule",))
            index += 1
            continue

        heading = _HEADING.match(line)
        if heading:
            flush_paragraph()
            out.append(("heading", len(heading.group(1)), heading.group(2).strip()))
            index += 1
            continue

        bullet = _BULLET.match(line)
        numbered = None if bullet else _NUMBERED.match(line)
        if bullet or numbered:
            flush_paragraph()
            if bullet:
                depth, marker, body = len(bullet.group(1)) // 2, "•", bullet.group(2)
                task = _TASK.match(body)
                if task:
                    marker = "☑" if task.group(1) in "xX" else "☐"
                    body = task.group(2)
            else:
                depth = len(numbered.group(1)) // 2
                marker, body = numbered.group(2) + ".", numbered.group(3)
            body, index = _continued(lines, index + 1, body)
            body = soft_wrap(body)
            out.append(("bullet", depth, marker, body))
            continue

        paragraph.append(line)
        index += 1

    flush()
    return out


def _continued(lines, index, body):
    """Absorb the wrapped remainder of a list item.

    A model writing to 80 columns breaks one bullet across several lines, and
    rendering each as its own bullet is the tell of a naive parser.
    """
    while index < len(lines):
        line = lines[index]
        if not line.strip() or _starts_block(line):
            break
        body += "\n" + line.strip()
        index += 1
    return body, index


def soft_wrap(text):
    """One newline is a space; Markdown only breaks the line if you ask twice.

    The model writes to 80 columns, so honouring its newlines would ragged
    every paragraph at whatever width it happened to wrap at, inside a window
    of an entirely different width.
    """
    out = []
    for line in (text or "").split("\n"):
        hard = line.endswith("  ") or line.rstrip().endswith("\\")
        line = line.strip().rstrip("\\").strip()
        out.append(line + ("\n" if hard else " "))
    return "".join(out).strip()


def _starts_block(line):
    return bool(
        _BULLET.match(line) or _NUMBERED.match(line) or _HEADING.match(line)
        or _FENCE.match(line) or _QUOTE.match(line) or _RULE.match(line)
        or _MATH_OPEN.match(line) or _MATH_ENV.match(line) or _TABLE_SEP.match(line)
    )


def _math_block(lines, index):
    """A displayed formula: $$…$$, \\[…\\], or a maths environment."""
    first = lines[index].strip()
    if _MATH_ENV.match(first):
        opener, closer = "", r"\end{"
        body = [first]
        index += 1
        while index < len(lines) and closer not in lines[index]:
            body.append(lines[index])
            index += 1
        if index < len(lines):
            body.append(lines[index])
            index += 1
        return "\n".join(body), index

    opener = "$$" if first.startswith("$$") else "\\["
    closer = "$$" if opener == "$$" else "\\]"
    rest = first[len(opener):]
    if rest.strip().endswith(closer) and rest.strip() != closer:
        return rest.strip()[: -len(closer)].strip(), index + 1

    body = [rest] if rest.strip() else []
    index += 1
    while index < len(lines) and closer not in lines[index]:
        body.append(lines[index])
        index += 1
    if index < len(lines):
        tail = lines[index].split(closer)[0]
        if tail.strip():
            body.append(tail)
        index += 1
    return "\n".join(body).strip(), index


def _table(lines, index):
    """A pipe table: header row, alignment row, then body rows."""
    header = _cells(lines[index])
    aligns = []
    for cell in _cells(lines[index + 1]):
        left, right = cell.startswith(":"), cell.endswith(":")
        aligns.append("center" if left and right else "right" if right else "left")
    index += 2

    rows = []
    while index < len(lines) and "|" in lines[index] and lines[index].strip():
        rows.append(_cells(lines[index]))
        index += 1

    width = max([len(header)] + [len(row) for row in rows] or [0])
    aligns += ["left"] * (width - len(aligns))
    header += [""] * (width - len(header))
    rows = [row + [""] * (width - len(row)) for row in rows]
    return ("table", aligns[:width], header[:width], [row[:width] for row in rows]), index


def _cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip().replace("\\|", "|") for cell in _CELL_SPLIT.split(line)]


def inline(text):
    """Markdown inline spans to Pango markup, escaping as we go."""
    try:
        return _inline(text, allow_bold=True)
    except Exception:  # noqa: BLE001 - a label must never fail to render
        return esc(text)


def _inline(text, allow_bold=True):
    out = []
    position = 0
    for match in _INLINE.finditer(text):
        if match.start() < position:
            continue
        out.append(esc(text[position:match.start()]))
        kind = match.lastgroup
        body = match.group()

        if kind == "code":
            inner = body.strip("`")
            out.append(f'<tt><span background="#80808026">{esc(" " + inner + " ")}</span></tt>')
        elif kind == "mathp":
            out.append(latex.inline_markup(body[2:-2]))
        elif kind == "math":
            inner = body[1:-1]
            out.append(latex.inline_markup(inner) if is_math(inner) else esc(body))
        elif kind == "link":
            label, _, target = body[1:].partition("](")
            url = target.rstrip(")")
            out.append(f'<a href="{esc(url)}">{esc(label) or esc(url)}</a>')
        elif kind == "url":
            out.append(f'<a href="{esc(body)}">{esc(body)}</a>')
        elif kind == "bold" and allow_bold:
            out.append(f"<b>{_inline(body.strip('*_'), allow_bold=False)}</b>")
        elif kind == "italic":
            out.append(f"<i>{esc(body.strip('*_'))}</i>")
        elif kind == "strike":
            out.append(f"<s>{esc(body.strip('~'))}</s>")
        else:
            out.append(esc(body))
        position = match.end()

    out.append(esc(text[position:]))
    return "".join(out)


def is_math(body):
    """Does what is between two dollar signs look like maths, or like money?"""
    stripped = body.strip()
    if not stripped or len(stripped) > 200:
        return False
    if _MATHY.search(stripped):
        return True
    return bool(_LONE_TOKEN.fullmatch(stripped))


def heading_markup(level, text):
    size = HEADING_SCALE.get(level, "medium")
    return f'<span size="{size}" weight="bold">{inline(text)}</span>'


def esc(text):
    return GLib.markup_escape_text(text or "")


def first_line(text, limit=90):
    """A one-line gist of a longer string, for collapsed rows."""
    line = " ".join((text or "").split())
    return (line[:limit] + "…") if len(line) > limit + 1 else line
