r"""Markdown, parsed once into a tree that three different things can read.

The preview builds GTK widgets from it, the HTML export writes tags from it,
and the outline sidebar reads the headings out of it. Parsing it three times,
once per consumer, is how the three of them quietly stop agreeing with each
other — so there is one parser here and it is the only one.

Two properties matter more than completeness.

*Every block knows where it came from.* `block.line` is the zero-based line in
the source that the block starts on, which is what makes the preview scroll
with the editor, what makes clicking a rendered paragraph put the cursor in
the text that produced it, and what lets a checkbox in the preview tick the
`[ ]` in the file. A parser that throws away positions can render a document
but it cannot edit one.

*Nothing raises.* A half-typed table, an unclosed fence, a stray backtick —
these are what a document looks like for most of the time it is being written,
and the preview must keep rendering through all of them. Anything unparseable
degrades to the text it was written as.

This is CommonMark's shape with GitHub's additions (tables, task lists,
strikethrough, footnotes, bare URLs), plus the three conventions that are not
in either specification but are in everyone's notes: `$…$` maths, `==marked==`
text, and YAML front matter.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------- the tree --


class Node:
    """An inline span: text, or something wrapping more spans.

    kind is one of text, code, math, link, image, footref, break, emph,
    strong, strike, mark, sub, sup, kbd, raw.
    """

    __slots__ = ("kind", "text", "url", "title", "children")

    def __init__(self, kind, text="", url="", title="", children=None):
        self.kind = kind
        self.text = text
        self.url = url
        self.title = title
        self.children = children if children is not None else []

    def __repr__(self):  # pragma: no cover - debugging only
        inner = self.children or self.text
        return f"<{self.kind} {inner!r}>"


class Block:
    """A block-level element, and the line of the source it starts on.

    Fields depend on the kind and are set by the parser:

      heading    level, spans, anchor
      para       spans
      code       text, language
      quote      children
      list       ordered, start, tight, items (each a ListItem)
      table      aligns, header (list of span lists), rows
      rule       —
      math       text
      html       text
      frontmatter text
      footnote   label, children
      image      url, title, spans (the alt text)
    """

    __slots__ = ("kind", "line", "end_line", "level", "spans", "text", "language",
                 "children", "ordered", "start", "tight", "items", "aligns",
                 "header", "rows", "anchor", "label", "url", "title")

    def __init__(self, kind, line, **fields):
        self.kind = kind
        self.line = line
        self.end_line = fields.pop("end_line", line)
        for slot in self.__slots__:
            if slot not in ("kind", "line", "end_line"):
                setattr(self, slot, None)
        for name, value in fields.items():
            setattr(self, name, value)

    def __repr__(self):  # pragma: no cover - debugging only
        return f"<{self.kind}@{self.line}>"


class ListItem:
    """One item of a list: its own blocks, and where its checkbox is.

    `task` is None for an ordinary item, True or False for `[x]` and `[ ]`.
    `task_line` and `task_column` locate that bracket in the source so the
    preview can toggle it by editing two characters rather than re-writing
    the line.
    """

    __slots__ = ("children", "task", "task_line", "task_column", "line")

    def __init__(self, children, task=None, task_line=-1, task_column=-1, line=0):
        self.children = children
        self.task = task
        self.task_line = task_line
        self.task_column = task_column
        self.line = line


class Document:
    """Everything a parse produced: the blocks, plus what they refer to."""

    __slots__ = ("blocks", "refs", "footnotes", "frontmatter", "source_lines")

    def __init__(self, blocks, refs, footnotes, frontmatter, source_lines):
        self.blocks = blocks
        self.refs = refs                # label (folded) -> (url, title)
        self.footnotes = footnotes      # label -> Block of kind "footnote"
        self.frontmatter = frontmatter  # raw YAML text, or ""
        self.source_lines = source_lines


# ------------------------------------------------------------- block rules --

_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_ATX_CLOSE = re.compile(r"[ \t]+#+[ \t]*$")
_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})[ \t]*([^`\n]*?)[ \t]*$")
_RULE = re.compile(r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
_BULLET = re.compile(r"^( *)([-*+])([ \t]+(.*)|)$")
_ORDERED = re.compile(r"^( *)(\d{1,9})([.)])([ \t]+(.*)|)$")
_TASK = re.compile(r"^(\[([ xX])\])(?:[ \t]+(.*)|)$")
_TABLE_DELIM = re.compile(r"^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
_REF_DEF = re.compile(
    r"""^[ ]{0,3}\[((?:[^\]\\]|\\.)+)\]:[ \t]*
        (<[^>\n]*>|\S+)
        (?:[ \t]+(?:"([^"]*)"|'([^']*)'|\(([^)]*)\)))?[ \t]*$""",
    re.VERBOSE,
)
_FOOTNOTE_DEF = re.compile(r"^ {0,3}\[\^((?:[^\]\\]|\\.)+)\]:[ \t]*(.*)$")
_MATH_FENCE = re.compile(r"^ {0,3}\$\$(.*)$")
_HTML_BLOCK = re.compile(
    r"^ {0,3}(?:</?(?:address|article|aside|blockquote|details|div|dl|dd|dt|"
    r"fieldset|figcaption|figure|footer|form|h[1-6]|header|hr|iframe|li|main|"
    r"nav|ol|p|pre|section|summary|table|tbody|td|tfoot|th|thead|tr|ul|video|"
    r"audio|picture|source|img|br|center|font|style|script)\b|<!--)",
    re.I,
)
_FRONT_OPEN = re.compile(r"^(---|\+\+\+)[ \t]*$")

# Four spaces, or one tab, with something after it.
_INDENTED = re.compile(r"^(?: {4}|\t)(.*)$")

_TABS = 4


def _expand(line: str) -> str:
    """Tabs to spaces, so indentation arithmetic can be done in characters."""
    if "\t" not in line:
        return line
    out = []
    width = 0
    for char in line:
        if char == "\t":
            step = _TABS - (width % _TABS)
            out.append(" " * step)
            width += step
        else:
            out.append(char)
            width += 1
    return "".join(out)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _blank(line: str) -> bool:
    return not line.strip()


# ------------------------------------------------------------ the parser ----


def parse(text: str) -> Document:
    """Markdown source to a Document. Never raises."""
    try:
        return _parse(text)
    except Exception:  # noqa: BLE001 - a preview must never fail to render
        import traceback

        traceback.print_exc()
        lines = (text or "").replace("\r\n", "\n").split("\n")
        return Document([Block("para", 0, spans=[Node("text", text or "")])],
                        {}, {}, "", lines)


def _parse(text: str) -> Document:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_expand(line) for line in raw.split("\n")]

    start = 0
    frontmatter = ""
    if lines and _FRONT_OPEN.match(lines[0]):
        closer = lines[0].strip()
        for index in range(1, len(lines)):
            if lines[index].strip() in (closer, "..."):
                frontmatter = "\n".join(lines[1:index])
                start = index + 1
                break

    refs: dict[str, tuple[str, str]] = {}
    footnotes: dict[str, Block] = {}
    blocks = _blocks(lines, start, len(lines), refs, footnotes, base=start)

    if frontmatter:
        blocks.insert(0, Block("frontmatter", 0, text=frontmatter, end_line=start - 1))
    return Document(blocks, refs, footnotes, frontmatter, lines)


def _blocks(lines, start, stop, refs, footnotes, base=0, offsets=None):
    """Parse lines[start:stop] into blocks.

    `offsets` maps an index in `lines` to where that line really came from, as
    (source line, source column), for content that has been lifted out of a
    container — a blockquote's `>` stripped, a list item's indentation removed.
    Without the line a quoted heading would report the line of the quote; and
    without the *column*, a task nested two levels down would say its checkbox
    is at column 2 of a line where it is at column 6, and ticking it in the
    preview would edit the wrong two characters.
    """
    def source_line(index):
        return offsets[index][0] if offsets is not None else index

    def source_column(index):
        return offsets[index][1] if offsets is not None else 0

    out: list[Block] = []
    index = start
    para: list[str] = []
    para_line = 0

    def flush_para():
        nonlocal para
        if not para:
            return
        joined = "\n".join(para)
        # A paragraph can be nothing but link definitions, in which case it is
        # not a paragraph at all.
        joined = _take_ref_defs(joined, refs)
        if joined.strip():
            out.append(Block("para", para_line, spans=inline(joined, refs),
                             end_line=para_line + joined.count("\n")))
        para = []

    while index < stop:
        line = lines[index]

        if _blank(line):
            flush_para()
            index += 1
            continue

        # -- fenced code ---------------------------------------------------
        fence = _FENCE.match(line)
        if fence:
            flush_para()
            pad, marker, info = len(fence.group(1)), fence.group(2), fence.group(3)
            body = []
            opened = index
            index += 1
            while index < stop:
                closing = re.match(r"^ {0,3}(" + marker[0] + "{" + str(len(marker)) + r",})[ \t]*$",
                                   lines[index])
                if closing:
                    index += 1
                    break
                body.append(lines[index][pad:] if lines[index][:pad].strip() == "" else lines[index])
                index += 1
            while body and not body[-1].strip():
                body.pop()          # an unclosed fence swallows the blank tail
            language = info.split()[0] if info.split() else ""
            out.append(Block("code", source_line(opened), text="\n".join(body),
                             language=language,
                             end_line=source_line(min(index, stop) - 1)))
            continue

        # -- display maths -------------------------------------------------
        math = _MATH_FENCE.match(line)
        if math:
            flush_para()
            first = math.group(1).strip()
            if first.endswith("$$") and len(first) > 2:
                out.append(Block("math", source_line(index), text=first[:-2].strip()))
                index += 1
                continue
            body = [first] if first else []
            opened = index
            index += 1
            while index < stop and "$$" not in lines[index]:
                body.append(lines[index])
                index += 1
            if index < stop:
                head = lines[index].split("$$")[0]
                if head.strip():
                    body.append(head)
                index += 1
            out.append(Block("math", source_line(opened), text="\n".join(body).strip(),
                             end_line=source_line(min(index, stop) - 1)))
            continue

        # -- ATX heading ---------------------------------------------------
        atx = _ATX.match(line)
        if atx:
            flush_para()
            body = _ATX_CLOSE.sub("", atx.group(2) or "").strip()
            level = len(atx.group(1))
            out.append(Block("heading", source_line(index), level=level,
                             spans=inline(body, refs), anchor=slug(body)))
            index += 1
            continue

        # -- thematic break ------------------------------------------------
        # Checked before the list rules, so `---` and `***` are rules rather
        # than empty bullets; and before setext, because a `---` under a
        # paragraph is only an underline when the paragraph is still open.
        if _RULE.match(line) and not (para and line.lstrip().startswith("-")):
            flush_para()
            out.append(Block("rule", source_line(index)))
            index += 1
            continue

        # -- setext heading ------------------------------------------------
        setext = _SETEXT.match(line)
        if setext and para:
            body = " ".join(part.strip() for part in para)
            level = 1 if setext.group(1)[0] == "=" else 2
            para = []
            out.append(Block("heading", para_line, level=level,
                             spans=inline(body, refs), anchor=slug(body)))
            index += 1
            continue

        # -- blockquote ----------------------------------------------------
        if _QUOTE.match(line):
            flush_para()
            inner, inner_offsets, index = _gather_quote(
                lines, index, stop, source_line, source_column)
            children = _blocks(inner, 0, len(inner), refs, footnotes,
                               offsets=inner_offsets)
            out.append(Block("quote",
                             inner_offsets[0][0] if inner_offsets else source_line(index),
                             children=children,
                             end_line=inner_offsets[-1][0] if inner_offsets else 0))
            continue

        # -- table ---------------------------------------------------------
        if "|" in line and index + 1 < stop and _TABLE_DELIM.match(lines[index + 1]):
            header = _cells(line)
            aligns = _alignments(lines[index + 1])
            if len(header) == len(aligns):
                flush_para()
                table, index = _table(lines, index, stop, aligns, header, refs, source_line)
                out.append(table)
                continue

        # -- footnote definition -------------------------------------------
        footnote = _FOOTNOTE_DEF.match(line)
        if footnote:
            flush_para()
            label = footnote.group(1)
            body = [footnote.group(2)]
            body_offsets = [(source_line(index),
                             source_column(index)
                             + len(footnote.group(0)) - len(footnote.group(2)))]
            index += 1
            while index < stop and (_blank(lines[index]) or _indent(lines[index]) >= 4):
                if _blank(lines[index]) and not (
                        index + 1 < stop and _indent(lines[index + 1]) >= 4):
                    break
                indented = _indent(lines[index]) >= 4
                body.append(lines[index][4:] if indented else "")
                body_offsets.append((source_line(index),
                                     source_column(index) + (4 if indented else 0)))
                index += 1
            block = Block("footnote", body_offsets[0][0], label=label,
                          children=_blocks(body, 0, len(body), refs, footnotes,
                                           offsets=body_offsets))
            footnotes[label] = block
            continue

        # -- lists ---------------------------------------------------------
        if _BULLET.match(line) or _ORDERED.match(line):
            flush_para()
            block, index = _list(lines, index, stop, refs, footnotes,
                                 source_line, source_column)
            out.append(block)
            continue

        # -- indented code -------------------------------------------------
        if not para and _INDENTED.match(line):
            flush_para()
            body = []
            first = index
            while index < stop and (_INDENTED.match(lines[index]) or _blank(lines[index])):
                if _blank(lines[index]):
                    # A trailing blank line belongs to the document, not to
                    # the block; one in the middle belongs to the block.
                    look = index
                    while look < stop and _blank(lines[look]):
                        look += 1
                    if look >= stop or not _INDENTED.match(lines[look]):
                        break
                    body.extend([""] * (look - index))
                    index = look
                    continue
                body.append(_INDENTED.match(lines[index]).group(1))
                index += 1
            out.append(Block("code", source_line(first), text="\n".join(body),
                             language="", end_line=source_line(index - 1)))
            continue

        # -- HTML block ----------------------------------------------------
        if not para and _HTML_BLOCK.match(line):
            flush_para()
            body = []
            first = index
            while index < stop and not _blank(lines[index]):
                body.append(lines[index])
                index += 1
            out.append(Block("html", source_line(first), text="\n".join(body),
                             end_line=source_line(index - 1)))
            continue

        # -- paragraph -----------------------------------------------------
        if not para:
            para_line = source_line(index)
        para.append(line)
        index += 1

    flush_para()
    return out


def _take_ref_defs(text: str, refs: dict) -> str:
    """Pull `[label]: url "title"` lines off the front of a paragraph."""
    lines = text.split("\n")
    kept = []
    taking = True
    for line in lines:
        match = _REF_DEF.match(line) if taking else None
        if match:
            url = match.group(2)
            if url.startswith("<") and url.endswith(">"):
                url = url[1:-1]
            title = match.group(3) or match.group(4) or match.group(5) or ""
            refs.setdefault(_fold(match.group(1)), (url, title))
            continue
        taking = False
        kept.append(line)
    return "\n".join(kept)


def _fold(label: str) -> str:
    return " ".join(label.split()).lower()


def _gather_quote(lines, index, stop, source_line, source_column):
    """The body of a blockquote, `>` removed, with its real line numbers.

    Lazy continuation is the awkward part: a line with no `>` that follows a
    quoted paragraph is still inside the quote, and a line with no `>` that
    follows a quoted blank line is not.
    """
    inner, offsets = [], []
    open_para = False
    while index < stop:
        quoted = _QUOTE.match(lines[index])
        if quoted:
            body = quoted.group(1)
            inner.append(body)
            offsets.append((source_line(index),
                            source_column(index) + len(lines[index]) - len(body)))
            open_para = bool(body.strip())
            index += 1
            continue
        if open_para and not _blank(lines[index]) and not _starts_block(lines[index]):
            inner.append(lines[index])
            offsets.append((source_line(index), source_column(index)))
            index += 1
            continue
        break
    return inner, offsets, index


def _starts_block(line: str) -> bool:
    """Whether a line interrupts a paragraph rather than continuing it."""
    return bool(
        _ATX.match(line) or _FENCE.match(line) or _RULE.match(line)
        or _QUOTE.match(line) or _BULLET.match(line) or _ORDERED.match(line)
        or _MATH_FENCE.match(line) or _HTML_BLOCK.match(line)
    )


def _cells(line: str) -> list[str]:
    """Split a table row on unescaped pipes, dropping the outer pair."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    out, cell, index = [], [], 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            cell.append(body[index + 1] if body[index + 1] == "|" else char + body[index + 1])
            index += 2
            continue
        if char == "|":
            out.append("".join(cell).strip())
            cell = []
            index += 1
            continue
        cell.append(char)
        index += 1
    out.append("".join(cell).strip())
    return out


def _alignments(line: str) -> list[str]:
    out = []
    for cell in _cells(line):
        left, right = cell.startswith(":"), cell.endswith(":")
        out.append("center" if left and right else "right" if right else "left")
    return out


def _table(lines, index, stop, aligns, header, refs, source_line):
    first = index
    index += 2
    rows = []
    while index < stop and lines[index].strip() and "|" in lines[index]:
        if _starts_block(lines[index]) and not _BULLET.match(lines[index]):
            break
        rows.append(_cells(lines[index]))
        index += 1

    width = len(aligns)
    header = [inline(cell, refs) for cell in _fit(header, width)]
    body = [[inline(cell, refs) for cell in _fit(row, width)] for row in rows]
    return Block("table", source_line(first), aligns=aligns, header=header,
                 rows=body, end_line=source_line(index - 1)), index


def _fit(cells, width):
    cells = list(cells[:width])
    return cells + [""] * (width - len(cells))


def _list(lines, index, stop, refs, footnotes, source_line, source_column):
    """A whole list: its items, and whether it is tight or loose.

    An item's content is everything indented past its marker, plus lazily
    continued paragraph lines. A blank line inside the list makes it loose,
    which is the difference between items set as paragraphs and items set as
    single lines — the only visible consequence of getting this wrong.
    """
    first = index
    bullet = _BULLET.match(lines[index])
    ordered = not bullet
    start_number = 1
    if ordered:
        start_number = int(_ORDERED.match(lines[index]).group(2))

    marker_char = bullet.group(2) if bullet else _ORDERED.match(lines[index]).group(3)
    items: list[ListItem] = []
    tight = True
    saw_blank = False

    while index < stop:
        match = _BULLET.match(lines[index]) if not ordered else _ORDERED.match(lines[index])
        if not match:
            break
        # A different marker starts a different list, which is how you get two
        # lists in a row without a paragraph between them.
        this_marker = match.group(2) if not ordered else match.group(3)
        if this_marker != marker_char:
            break
        marker_indent = len(match.group(1))
        if marker_indent >= 4 and items:
            break

        rest = (match.group(4) if not ordered else match.group(5)) or ""
        marker_width = len(match.group(0)) - len(rest)
        content_indent = marker_width
        item_lines = [rest]
        item_offsets = [(source_line(index), source_column(index) + marker_width)]
        index += 1

        blank_run = 0
        while index < stop:
            line = lines[index]
            if _blank(line):
                blank_run += 1
                index += 1
                continue
            if _indent(line) >= content_indent:
                if blank_run:
                    item_lines.extend([""] * blank_run)
                    item_offsets.extend(
                        [(source_line(index), source_column(index))] * blank_run)
                    saw_blank = True
                    blank_run = 0
                item_lines.append(line[content_indent:])
                item_offsets.append((source_line(index),
                                     source_column(index) + content_indent))
                index += 1
                continue
            if blank_run:
                break
            # Lazy continuation of the item's last paragraph.
            if (not _starts_block(line) and item_lines and item_lines[-1].strip()
                    and not _TABLE_DELIM.match(line)):
                item_lines.append(line.strip())
                item_offsets.append((source_line(index),
                                     source_column(index) + _indent(line)))
                index += 1
                continue
            break

        task = None
        task_line = task_column = -1
        check = _TASK.match(item_lines[0].lstrip()) if item_lines else None
        if check:
            task = check.group(2) in "xX"
            task_line, task_column = item_offsets[0]
            task_column += len(item_lines[0]) - len(item_lines[0].lstrip())
            item_lines[0] = check.group(3) or ""

        children = _blocks(item_lines, 0, len(item_lines), refs, footnotes,
                           offsets=item_offsets)
        items.append(ListItem(children, task=task, task_line=task_line,
                              task_column=task_column, line=item_offsets[0][0]))

        if blank_run and index < stop:
            following = (_BULLET.match(lines[index]) if not ordered
                         else _ORDERED.match(lines[index]))
            if not following:
                break
            # A blank line *between* two items is what makes a list loose.
            # One after the last item merely ends it.
            saw_blank = True

    if saw_blank:
        tight = False

    return Block("list", source_line(first), ordered=ordered, start=start_number,
                 tight=tight, items=items,
                 end_line=source_line(min(index, stop) - 1)), index


# ----------------------------------------------------------------- inline --

_PUNCT = set("\\`*_{}[]()#+-.!|~<>\"'$=:^&@/")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\[\]{}\"'`]+[^\s<>\[\]{}\"'`.,:;!?)]")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_AUTOLINK = re.compile(r"<([a-zA-Z][a-zA-Z0-9+.-]{1,31}:[^<>\s]*)>")
_AUTOEMAIL = re.compile(r"<([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})>")
_ENTITY = re.compile(r"&(?:#x[0-9a-fA-F]{1,6}|#\d{1,7}|[a-zA-Z][a-zA-Z0-9]{1,31});")

# Inline HTML this renderer understands, because Pango has the same idea.
_HTML_INLINE = {
    "b": "strong", "strong": "strong", "i": "emph", "em": "emph",
    "u": "underline", "s": "strike", "del": "strike", "strike": "strike",
    "code": "codetag", "tt": "codetag", "kbd": "kbd", "mark": "mark",
    "sub": "sub", "sup": "sup", "small": "small",
}
_HTML_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\s*/?>")

# What makes a run of text between dollars look like maths rather than money.
_MATHY = re.compile(r"[\\^_{}=<>]|[A-Za-z0-9]\s*[-+*/]\s*[A-Za-z0-9(\\]|[A-Za-z]\s*\(")
_LONE_TOKEN = re.compile(r"[A-Za-z]\w{0,2}|\d+(?:\.\d+)?")


def inline(text: str, refs: dict | None = None) -> list[Node]:
    """Inline Markdown to a list of Nodes. Never raises."""
    try:
        return _inline(text or "", refs or {})
    except Exception:  # noqa: BLE001 - see the module docstring
        return [Node("text", text or "")]


def _inline(text, refs, depth=0):
    if depth > 12:            # pathological nesting; stop pretending
        return [Node("text", text)]

    out: list[Node] = []
    buf: list[str] = []
    index = 0
    size = len(text)

    def flush():
        if buf:
            out.append(Node("text", "".join(buf)))
            buf.clear()

    while index < size:
        char = text[index]

        # -- escapes and hard breaks ---------------------------------------
        if char == "\\" and index + 1 < size:
            following = text[index + 1]
            if following == "\n":
                flush()
                out.append(Node("break"))
                index += 2
                continue
            if following in _PUNCT:
                buf.append(following)
                index += 2
                continue

        if char == "\n":
            trailing = len("".join(buf)) - len("".join(buf).rstrip(" "))
            if trailing >= 2:
                while buf and buf[-1] == " ":
                    buf.pop()
                if buf and buf[-1].endswith(" "):
                    buf[-1] = buf[-1].rstrip(" ")
                flush()
                out.append(Node("break"))
            else:
                while buf and buf[-1].strip() == "" and buf[-1] != "":
                    buf.pop()
                buf.append(" ")
            index += 1
            continue

        # -- code spans ----------------------------------------------------
        if char == "`":
            run = _run_length(text, index, "`")
            close = _find_literal(text, index + run, "`" * run)
            if close != -1:
                flush()
                body = text[index + run:close]
                # One space either side is stripped, so ``a ` b`` works.
                if len(body) > 2 and body[0] == " " and body[-1] == " " and body.strip():
                    body = body[1:-1]
                out.append(Node("code", body.replace("\n", " ")))
                index = close + run
                continue
            buf.append("`" * run)
            index += run
            continue

        # -- maths ---------------------------------------------------------
        if char == "$":
            end = _find_math(text, index)
            if end != -1:
                body = text[index + 1:end]
                if is_math(body):
                    flush()
                    out.append(Node("math", body))
                    index = end + 1
                    continue

        # -- images and links ----------------------------------------------
        if char == "!" and index + 1 < size and text[index + 1] == "[":
            node, after = _link(text, index + 1, refs, depth, image=True)
            if node is not None:
                flush()
                out.append(node)
                index = after
                continue

        if char == "[":
            footnote = re.match(r"\[\^((?:[^\]\\]|\\.)+)\]", text[index:])
            if footnote:
                flush()
                out.append(Node("footref", footnote.group(1)))
                index += footnote.end()
                continue
            node, after = _link(text, index, refs, depth, image=False)
            if node is not None:
                flush()
                out.append(node)
                index = after
                continue

        # -- autolinks and raw HTML ----------------------------------------
        if char == "<":
            auto = _AUTOLINK.match(text, index)
            if auto:
                flush()
                out.append(Node("link", url=auto.group(1),
                                children=[Node("text", auto.group(1))]))
                index = auto.end()
                continue
            mail = _AUTOEMAIL.match(text, index)
            if mail:
                flush()
                out.append(Node("link", url="mailto:" + mail.group(1),
                                children=[Node("text", mail.group(1))]))
                index = mail.end()
                continue
            tag = _HTML_TAG.match(text, index)
            if tag:
                name = tag.group(2).lower()
                if name == "br":
                    flush()
                    out.append(Node("break"))
                    index = tag.end()
                    continue
                kind = _HTML_INLINE.get(name)
                if kind and not tag.group(1):
                    close = _find_literal(text, tag.end(), f"</{tag.group(2)}>")
                    if close == -1:
                        close = _find_literal(text, tag.end(), f"</{name}>")
                    if close != -1:
                        flush()
                        inner = text[tag.end():close]
                        if kind == "codetag":
                            out.append(Node("code", inner))
                        else:
                            out.append(Node(kind, children=_inline(inner, refs, depth + 1)))
                        index = text.index(">", close) + 1
                        continue

        # -- emphasis ------------------------------------------------------
        if char in "*_~=":
            node, after = _emphasis(text, index, refs, depth)
            if node is not None:
                flush()
                out.append(node)
                index = after
                continue

        # -- bare URLs and addresses ---------------------------------------
        if char in "hwHW" and (index == 0 or not text[index - 1].isalnum()):
            url = _URL.match(text, index)
            if url:
                flush()
                target = url.group(0)
                out.append(Node("link", url=target if "://" in target else "https://" + target,
                                children=[Node("text", target)]))
                index = url.end()
                continue
        if char == "&":
            entity = _ENTITY.match(text, index)
            if entity:
                buf.append(entity.group(0))
                index = entity.end()
                continue

        buf.append(char)
        index += 1

    flush()
    return _merge_text(out)


def _merge_text(nodes):
    out = []
    for node in nodes:
        if node.kind == "text" and out and out[-1].kind == "text":
            out[-1].text += node.text
        else:
            out.append(node)
    return out


def _run_length(text, index, char):
    run = 0
    while index + run < len(text) and text[index + run] == char:
        run += 1
    return run


def _find_literal(text, start, needle):
    """Find `needle` at or after `start`, ignoring backslash-escaped copies."""
    index = start
    while True:
        found = text.find(needle, index)
        if found == -1:
            return -1
        backslashes = 0
        probe = found - 1
        while probe >= 0 and text[probe] == "\\":
            backslashes += 1
            probe -= 1
        if backslashes % 2 == 0:
            # A longer run of the same character is not a match for a shorter
            # one — ``` does not close ``.
            if needle[0] in "`~" and found + len(needle) < len(text) \
                    and text[found + len(needle)] == needle[0]:
                index = found + len(needle)
                continue
            return found
        index = found + 1


def _find_math(text, start):
    """The closing `$` of an inline formula, or -1."""
    if start + 1 >= len(text) or text[start + 1].isspace():
        return -1
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "\n":
            return -1
        if char == "$":
            if text[index - 1].isspace():
                return -1
            if index + 1 < len(text) and text[index + 1].isdigit():
                return -1
            return index
        index += 1
    return -1


def is_math(body: str) -> bool:
    """Does what is between two dollar signs look like maths, or like money?

    `$x$` is maths and `$5 and $6` is money, and only the shape of what is
    between them tells you which. Anything that fails these tests stays as
    text, because turning prices into italics is a worse failure than missing
    a formula.
    """
    stripped = (body or "").strip()
    if not stripped or len(stripped) > 400:
        return False
    if _MATHY.search(stripped):
        return True
    return bool(_LONE_TOKEN.fullmatch(stripped))


def _emphasis(text, index, refs, depth):
    """A run of `*`, `_`, `~` or `=` that opens a span, and its contents."""
    char = text[index]
    run = _run_length(text, index, char)

    if char == "~" and run != 2:
        return None, index
    if char == "=" and run != 2:
        return None, index
    if char in "*_" and run > 3:
        return None, index

    after = text[index + run] if index + run < len(text) else ""
    if not after or after.isspace():
        return None, index
    # Intraword `_` is a word, not emphasis: file_name_here.
    if char == "_" and index > 0 and (text[index - 1].isalnum() or text[index - 1] == "_"):
        return None, index

    close = _find_close(text, index + run, char, run)
    if close == -1:
        return None, index

    inner = text[index + run:close]
    if not inner.strip():
        return None, index
    children = _inline(inner, refs, depth + 1)

    if char == "~":
        return Node("strike", children=children), close + run
    if char == "=":
        return Node("mark", children=children), close + run
    if run == 1:
        return Node("emph", children=children), close + run
    if run == 2:
        return Node("strong", children=children), close + run
    return Node("strong", children=[Node("emph", children=children)]), close + run


def _find_close(text, start, char, run):
    """The matching closing run, skipping code spans and escapes."""
    index = start
    while index < len(text):
        current = text[index]
        if current == "\\":
            index += 2
            continue
        if current == "`":
            length = _run_length(text, index, "`")
            close = _find_literal(text, index + length, "`" * length)
            index = (close + length) if close != -1 else (index + length)
            continue
        if current == char:
            length = _run_length(text, index, char)
            if length >= run and not text[index - 1].isspace():
                if char == "_" and index + length < len(text) \
                        and (text[index + length].isalnum() or text[index + length] == "_"):
                    index += length
                    continue
                return index
            index += length
            continue
        index += 1
    return -1


def _link(text, index, refs, depth, image):
    """A link or image starting at `[`. Returns (node, index after) or (None, _)."""
    close = _matching_bracket(text, index)
    if close == -1:
        return None, index
    label = text[index + 1:close]
    after = close + 1

    url = title = ""
    if after < len(text) and text[after] == "(":
        end = _matching_paren(text, after)
        if end == -1:
            return None, index
        url, title = _split_destination(text[after + 1:end])
        after = end + 1
    else:
        ref_label = label
        if after < len(text) and text[after] == "[":
            ref_close = _matching_bracket(text, after)
            if ref_close == -1:
                return None, index
            inner = text[after + 1:ref_close]
            if inner.strip():
                ref_label = inner
            after = ref_close + 1
        found = refs.get(_fold(ref_label))
        if not found:
            return None, index
        url, title = found

    if image:
        return Node("image", text=label, url=url, title=title,
                    children=_inline(label, refs, depth + 1)), after
    return Node("link", url=url, title=title,
                children=_inline(label, refs, depth + 1)), after


def _matching_bracket(text, start):
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "`":
            run = _run_length(text, index, "`")
            found = _find_literal(text, index + run, "`" * run)
            index = (found + run) if found != -1 else (index + run)
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
        elif char == "\n" and text[index:index + 2] == "\n\n":
            return -1
        index += 1
    return -1


def _matching_paren(text, start):
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
        elif char == "\n":
            return -1
        index += 1
    return -1


def _split_destination(body: str) -> tuple[str, str]:
    """`url "title"` — and the url may be wrapped in <> and contain spaces."""
    body = body.strip()
    if body.startswith("<"):
        end = body.find(">")
        if end != -1:
            url = body[1:end]
            rest = body[end + 1:].strip()
            return url, _strip_quotes(rest)
    match = re.match(r'^(\S+)(?:\s+(.*))?$', body, re.S)
    if not match:
        return "", ""
    return match.group(1), _strip_quotes((match.group(2) or "").strip())


def _strip_quotes(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if len(text) >= 2 and text[0] == "(" and text[-1] == ")":
        return text[1:-1]
    return text


# -------------------------------------------------------------- utilities --


def slug(text: str) -> str:
    """A heading's anchor, the way GitHub makes them."""
    plain = "".join(node_text(node) for node in inline(text or ""))
    plain = unicodedata.normalize("NFKD", plain)
    plain = re.sub(r"[^\w\s-]", "", plain.lower())
    return re.sub(r"[\s_]+", "-", plain).strip("-") or "section"


def node_text(node: Node) -> str:
    """The plain text of a span, for anchors, titles and the word count."""
    if node.kind in ("text", "code", "math"):
        return node.text
    if node.kind == "break":
        return " "
    if node.kind == "image":
        return node.text
    return "".join(node_text(child) for child in node.children)


def spans_text(spans) -> str:
    return "".join(node_text(node) for node in spans or [])


def outline(document: Document) -> list[tuple[int, str, int, str]]:
    """(level, title, line, anchor) for every heading, in document order."""
    found = []

    def walk(blocks):
        for block in blocks:
            if block.kind == "heading":
                found.append((block.level, spans_text(block.spans).strip() or "Untitled",
                              block.line, block.anchor or ""))
            elif block.kind == "quote":
                walk(block.children)
            elif block.kind == "list":
                for item in block.items:
                    walk(item.children)

    walk(document.blocks)
    return found


_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*|\d[\d.,]*", re.UNICODE)


def statistics(text: str) -> dict:
    """Words, characters, sentences and reading time for the status strip."""
    return statistics_of(parse(text or ""), text or "")


def statistics_of(document: Document, text: str = "") -> dict:
    """The same, from a document already parsed.

    Counted on the rendered text rather than the source, because "**word**"
    is one word and counting it as three is the sort of thing that makes a
    word count useless for the one job it has. Taking an existing parse
    matters too: the status strip asks for this on every render, and parsing
    the document a second time to count it would double the cost of typing.
    """
    plain = []

    def walk(blocks):
        for block in blocks:
            if block.kind in ("heading", "para"):
                plain.append(spans_text(block.spans))
            elif block.kind == "quote":
                walk(block.children)
            elif block.kind == "list":
                for item in block.items:
                    walk(item.children)
            elif block.kind == "table":
                for cell in block.header:
                    plain.append(spans_text(cell))
                for row in block.rows:
                    for cell in row:
                        plain.append(spans_text(cell))

    walk(document.blocks)
    body = "\n".join(plain)
    words = len(_WORD.findall(body))
    sentences = len([part for part in re.split(r"[.!?]+[\s\n]|[.!?]+$", body) if part.strip()])
    return {
        "words": words,
        "characters": len(body),
        "characters_with_spaces": len(text or ""),
        "sentences": sentences,
        "lines": len((text or "").split("\n")),
        # 200 words a minute, which is the middle of every published estimate.
        "reading_minutes": max(1, round(words / 200)) if words else 0,
    }


def title_of(document: Document) -> str:
    """The document's own name for itself: front matter title, or first H1."""
    if document.frontmatter:
        for line in document.frontmatter.split("\n"):
            match = re.match(r"^title\s*:\s*(.+?)\s*$", line, re.I)
            if match:
                return _strip_quotes(match.group(1).strip())
    for block in document.blocks:
        if block.kind == "heading":
            return spans_text(block.spans).strip()
    return ""
