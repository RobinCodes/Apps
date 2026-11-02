"""A thought's text as it reads: emphasis, code and links, ready for a Pango layout.

A thought on the canvas is a few lines of Markdown, and what matters in it is
the emphasis the writer chose and the `[[links]]` they made — not tables or
headings. So this reads only the inline marks, and answers with the plain
text and the runs over it: which bytes are bold, which are a link and where to.
Bytes, because that is how Pango counts, and a run that is off by an accent
draws its underline under the wrong letters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, unquote
from xml.sax.saxutils import escape, quoteattr

TOKEN = re.compile(
    r"(?P<code>`[^`\n]+`)"
    r"|(?P<wiki>\[\[([^\[\]\n|#^]+)(#[^\[\]\n|]*)?(?:\|([^\[\]\n]*))?\]\])"
    r"|(?P<link>\[([^\]\n]+)\]\(([^)\s]+)\))"
    r"|(?P<strong>\*\*(?=\S)(?:[^*\n]|\*(?!\*))+?(?<=\S)\*\*)"
    r"|(?P<em>(?<![\w*])\*(?=\S)[^*\n]+?(?<=\S)\*(?![*\w])"
    r"|(?<![\w_])_(?=\S)[^_\n]+?(?<=\S)_(?![\w_]))"
)

WIKI = "wiki:"


@dataclass
class Run:
    start: int            # bytes into the plain text
    end: int
    kind: str             # strong, em, code, link
    target: str = ""      # for a link: the [[target]] or the URL
    wiki: bool = False


def render(text: str) -> tuple[str, list[Run]]:
    """`(plain text, runs)` for a thought's Markdown."""
    out: list[str] = []
    runs: list[Run] = []
    size = 0

    def emit(piece):
        nonlocal size
        out.append(piece)
        size += len(piece.encode("utf-8"))

    def walk(chunk):
        index = 0
        for match in TOKEN.finditer(chunk):
            emit(chunk[index:match.start()])
            start = size
            kind = match.lastgroup
            if kind == "code":
                emit(match.group(0)[1:-1])
                runs.append(Run(start, size, "code"))
            elif kind == "wiki":
                target = match.group(3).strip()
                heading = (match.group(4) or "")[1:].strip()
                label = (match.group(5) or "").strip()
                emit(label or (f"{target} › {heading}" if heading else target))
                runs.append(Run(start, size, "link", target, wiki=True))
            elif kind == "link":
                walk(match.group(7))
                runs.append(Run(start, size, "link", match.group(8)))
            elif kind == "strong":
                walk(match.group(0)[2:-2])
                runs.append(Run(start, size, "strong"))
            else:
                walk(match.group(0)[1:-1])
                runs.append(Run(start, size, "em"))
            index = match.end()
        emit(chunk[index:])

    for number, line in enumerate((text or "").split("\n")):
        if number:
            emit("\n")
        heading = re.match(r"^#{1,6}\s+(.*)$", line)
        start = size
        walk(heading.group(1) if heading else line)
        if heading:
            runs.append(Run(start, size, "strong"))
    return "".join(out), runs


def link_at(runs: list[Run], index: int) -> Run | None:
    """The link run covering byte `index`, if any."""
    return next((run for run in runs if run.kind == "link" and run.start <= index < run.end),
                None)


def markup(text: str) -> str:
    """The same marks as Pango markup for a label, links as `<a href>`."""
    plain, runs = render(text)
    data = plain.encode("utf-8")
    opens: dict[int, list[str]] = {}
    closes: dict[int, list[str]] = {}
    tags = {"strong": ("<b>", "</b>"), "em": ("<i>", "</i>"), "code": ("<tt>", "</tt>")}
    for run in sorted(runs, key=lambda r: (r.start, -r.end)):
        if run.kind == "link":
            href = WIKI + quote(run.target) if run.wiki else run.target
            start, end = f"<a href={quoteattr(href)}>", "</a>"
        elif run.kind in tags:
            start, end = tags[run.kind]
        else:
            continue
        opens.setdefault(run.start, []).append(start)
        closes.setdefault(run.end, []).insert(0, end)
    out, last = [], 0
    for index in sorted(set(opens) | set(closes) | {len(data)}):
        out.append(escape(data[last:index].decode("utf-8", "replace")))
        out += closes.get(index, [])
        out += opens.get(index, [])
        last = index
    return "".join(out)


def parse_href(uri: str) -> tuple[str, bool]:
    """`(target, wiki?)` from a link `markup` made."""
    if uri.startswith(WIKI):
        return unquote(uri[len(WIKI):]), True
    return uri, False
