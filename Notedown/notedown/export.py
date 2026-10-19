"""Getting the document out again: HTML, plain text, and whatever pandoc has.

The HTML is written from the same tree the preview is built from, so what
comes out of Export is what was on screen. Writing a second parser for export
is how the file you send someone stops matching the file you were looking at.

One page, everything inside it. A stylesheet in a separate file is one more
thing to lose when the document is emailed, put in a Zip, or opened from a USB
stick, so the CSS goes in a `<style>` block and images can be inlined as data
URIs on request. The result opens in any browser with no network and no server.

Anything beyond HTML — PDF, Word, ODT — is pandoc's job, and pandoc is not
assumed to be here. When it isn't, the menu says so rather than offering an
export that fails.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import os

from . import highlight, latex, markdown, winenv

STYLES = {
    "clean": "Clean — a reading measure, system fonts",
    "github": "GitHub — what a README looks like there",
    "plain": "Plain — no styling at all",
}


# ------------------------------------------------------------------ HTML ----


def to_html(document: markdown.Document, title: str = "", *, style: str = "clean",
            standalone: bool = True, base_dir: str = "",
            embed_images: bool = False) -> str:
    """The document as one HTML page."""
    body = _blocks_html(document.blocks, document, base_dir, embed_images)
    if document.footnotes:
        body += _footnotes_html(document, base_dir, embed_images)

    if not standalone:
        return body

    name = html.escape(title or markdown.title_of(document) or "Document")
    sheet = _STYLESHEETS.get(style, "") if style != "plain" else ""
    head = f"<style>\n{sheet}\n</style>" if sheet else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="Notedown">
<title>{name}</title>
{head}
</head>
<body>
<main>
{body}</main>
</body>
</html>
"""


def _blocks_html(blocks, document, base_dir, embed):
    return "".join(_block_html(block, document, base_dir, embed) for block in blocks)


def _block_html(block, document, base_dir, embed):
    kind = block.kind
    if kind == "heading":
        anchor = html.escape(block.anchor or "")
        inner = _spans_html(block.spans, base_dir, embed)
        return f'<h{block.level} id="{anchor}">{inner}</h{block.level}>\n'
    if kind == "para":
        return f"<p>{_spans_html(block.spans, base_dir, embed)}</p>\n"
    if kind == "code":
        language = highlight.language_of(block.language)
        attribute = f' class="language-{html.escape(language)}"' if language else ""
        return (f"<pre><code{attribute}>"
                f"{html.escape(block.text)}</code></pre>\n")
    if kind == "quote":
        return f"<blockquote>\n{_blocks_html(block.children, document, base_dir, embed)}</blockquote>\n"
    if kind == "list":
        return _list_html(block, document, base_dir, embed)
    if kind == "table":
        return _table_html(block, base_dir, embed)
    if kind == "rule":
        return "<hr>\n"
    if kind == "math":
        # No maths renderer in a bare HTML file, so the formula is preserved
        # as its source inside a block a MathJax-equipped page can still pick
        # up, with the Unicode rendering as the visible fallback.
        rendered = html.escape(latex.strip_tags(latex.markup(latex.parse(block.text))))
        return (f'<div class="math" data-tex="{html.escape(block.text)}">'
                f"{rendered}</div>\n")
    if kind == "html":
        return block.text + "\n"
    if kind == "frontmatter":
        rows = []
        for line in block.text.split("\n"):
            key, sep, value = line.partition(":")
            if sep and key.strip():
                shown = value.strip().strip("\"'")
                rows.append(f"<dt>{html.escape(key.strip())}</dt>"
                            f"<dd>{html.escape(shown)}</dd>")
        return f'<dl class="frontmatter">{"".join(rows)}</dl>\n' if rows else ""
    return ""


def _list_html(block, document, base_dir, embed):
    tag = "ol" if block.ordered else "ul"
    start = f' start="{block.start}"' if block.ordered and block.start != 1 else ""
    classes = []
    if any(item.task is not None for item in block.items):
        classes.append("task-list")
    attribute = f' class="{" ".join(classes)}"' if classes else ""

    out = [f"<{tag}{start}{attribute}>\n"]
    for item in block.items:
        inner = _blocks_html(item.children, document, base_dir, embed)
        if block.tight:
            inner = inner.replace("<p>", "").replace("</p>\n", "").replace("</p>", "")
        box = ""
        if item.task is not None:
            checked = " checked" if item.task else ""
            box = f'<input type="checkbox" disabled{checked}> '
        out.append(f"<li>{box}{inner}</li>\n")
    out.append(f"</{tag}>\n")
    return "".join(out)


def _table_html(block, base_dir, embed):
    out = ["<table>\n<thead>\n<tr>"]
    for align, cell in zip(block.aligns, block.header):
        out.append(f'<th style="text-align:{align}">{_spans_html(cell, base_dir, embed)}</th>')
    out.append("</tr>\n</thead>\n<tbody>\n")
    for row in block.rows:
        out.append("<tr>")
        for align, cell in zip(block.aligns, row):
            out.append(f'<td style="text-align:{align}">'
                       f"{_spans_html(cell, base_dir, embed)}</td>")
        out.append("</tr>\n")
    out.append("</tbody>\n</table>\n")
    return "".join(out)


def _footnotes_html(document, base_dir, embed):
    out = ['<section class="footnotes">\n<hr>\n<ol>\n']
    for label, block in document.footnotes.items():
        inner = _blocks_html(block.children, document, base_dir, embed)
        out.append(f'<li id="fn-{html.escape(label)}">{inner}</li>\n')
    out.append("</ol>\n</section>\n")
    return "".join(out)


def _spans_html(spans, base_dir, embed):
    return "".join(_span_html(node, base_dir, embed) for node in spans or [])


def _span_html(node, base_dir, embed):
    kind = node.kind
    if kind == "text":
        return html.escape(node.text)
    if kind == "code":
        return f"<code>{html.escape(node.text)}</code>"
    if kind == "math":
        rendered = html.escape(latex.strip_tags(latex.inline_markup(node.text)))
        return f'<span class="math" data-tex="{html.escape(node.text)}">{rendered}</span>'
    if kind == "break":
        return "<br>\n"
    if kind == "emph":
        return f"<em>{_spans_html(node.children, base_dir, embed)}</em>"
    if kind == "strong":
        return f"<strong>{_spans_html(node.children, base_dir, embed)}</strong>"
    if kind == "strike":
        return f"<del>{_spans_html(node.children, base_dir, embed)}</del>"
    if kind == "mark":
        return f"<mark>{_spans_html(node.children, base_dir, embed)}</mark>"
    if kind == "underline":
        return f"<u>{_spans_html(node.children, base_dir, embed)}</u>"
    if kind in ("sub", "sup"):
        return f"<{kind}>{_spans_html(node.children, base_dir, embed)}</{kind}>"
    if kind == "small":
        return f"<small>{_spans_html(node.children, base_dir, embed)}</small>"
    if kind == "kbd":
        return f"<kbd>{html.escape(markdown.node_text(node))}</kbd>"
    if kind == "link":
        title = f' title="{html.escape(node.title)}"' if node.title else ""
        return (f'<a href="{html.escape(node.url)}"{title}>'
                f"{_spans_html(node.children, base_dir, embed)}</a>")
    if kind == "image":
        source = _image_source(node.url, base_dir, embed)
        title = f' title="{html.escape(node.title)}"' if node.title else ""
        return (f'<img src="{html.escape(source)}" '
                f'alt="{html.escape(node.text)}"{title}>')
    if kind == "footref":
        label = html.escape(node.text)
        return f'<sup><a href="#fn-{label}" id="fnref-{label}">{label}</a></sup>'
    return html.escape(markdown.node_text(node))


def _image_source(url, base_dir, embed):
    """The src for an image: the original, or the file itself as a data URI."""
    if not embed or not url or "://" in url:
        return url
    path = url if os.path.isabs(url) else os.path.join(base_dir or "", url)
    try:
        if os.path.getsize(path) > 8 * 1024 * 1024:
            return url
        with open(path, "rb") as fh:
            blob = fh.read()
    except OSError:
        return url
    kind = mimetypes.guess_type(path)[0] or "image/png"
    return f"data:{kind};base64,{base64.b64encode(blob).decode('ascii')}"


# ------------------------------------------------------------ plain text ----


def to_text(document: markdown.Document) -> str:
    """The document with the syntax taken out — for pasting into a plain box."""
    lines = _text_blocks(document.blocks)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def _text_blocks(blocks) -> list[str]:
    """Each block as lines. Returning them rather than appending to one list
    is what lets a blockquote indent what its children produced."""
    out: list[str] = []
    for block in blocks:
        if block.kind in ("heading", "para"):
            # Breaks become real newlines here and nowhere else: this is the
            # one caller for which a line the author broke is content rather
            # than an accident of wrapping.
            text = markdown.spans_text(block.spans, "\n").strip()
            out.extend(text.split("\n"))
            out.append("")
        elif block.kind == "code":
            out.extend("    " + line for line in block.text.split("\n"))
            out.append("")
        elif block.kind == "quote":
            inner = _text_blocks(block.children)
            out.extend(("| " + line) if line.strip() else "|" for line in inner)
            out.append("")
        elif block.kind == "list":
            out.extend(_text_list(block))
            out.append("")
        elif block.kind == "table":
            out.extend(_text_table(block))
            out.append("")
        elif block.kind == "rule":
            out.append("-" * 40)
            out.append("")
        elif block.kind == "math":
            out.append(block.text)
            out.append("")
        elif block.kind == "frontmatter":
            out.extend(block.text.split("\n"))
            out.append("")
    return out


def _text_list(block) -> list[str]:
    out: list[str] = []
    for offset, item in enumerate(block.items):
        marker = f"{block.start + offset}." if block.ordered else "-"
        if item.task is not None:
            marker += " [x]" if item.task else " [ ]"
        inner = _text_blocks(item.children)
        if block.tight:
            # A tight list is set as single lines; keeping the blank line that
            # separates two blocks would space it out like a loose one.
            inner = [line for line in inner if line.strip()]
        while inner and not inner[-1].strip():
            inner.pop()
        if not inner:
            out.append(marker)
            continue
        pad = " " * (len(marker) + 1)
        out.append(marker + " " + inner[0])
        out.extend(pad + line if line.strip() else "" for line in inner[1:])
    return out


def _text_table(block) -> list[str]:
    rows = [[markdown.spans_text(cell) for cell in block.header]]
    rows += [[markdown.spans_text(cell) for cell in row] for row in block.rows]
    widths = [max((len(row[column]) for row in rows if column < len(row)), default=0)
              for column in range(len(block.aligns))]
    out = []
    for index, row in enumerate(rows):
        out.append("  ".join(cell.ljust(widths[column])
                             for column, cell in enumerate(row)).rstrip())
        if index == 0:
            out.append("  ".join("-" * width for width in widths))
    return out


# ---------------------------------------------------------------- pandoc ----


def pandoc_path() -> str | None:
    return winenv.which("pandoc")


PANDOC_FORMATS = {
    ".pdf": "PDF",
    ".docx": "Word",
    ".odt": "OpenDocument",
    ".epub": "EPUB",
    ".rst": "reStructuredText",
    ".org": "Org",
    ".tex": "LaTeX",
}


def pandoc_convert(source_markdown: str, out_path: str, base_dir: str = "") -> None:
    """Hand the text to pandoc. Raises OSError or RuntimeError on failure."""
    import subprocess

    pandoc = pandoc_path()
    if not pandoc:
        raise RuntimeError("pandoc is not installed")
    argv = [pandoc, "-f", "gfm+tex_math_dollars+footnotes", "-o", out_path, "--standalone"]
    if out_path.lower().endswith(".pdf"):
        # A PDF via pandoc needs a TeX engine; say which one is missing rather
        # than passing its error on raw.
        argv += ["--pdf-engine=" + (winenv.which("xelatex") or winenv.which("pdflatex")
                                    or "pdflatex")]
    done = subprocess.run(
        argv, input=source_markdown, cwd=base_dir or None, encoding="utf-8",
        capture_output=True, creationflags=winenv.NO_WINDOW, timeout=180)
    if done.returncode != 0:
        message = (done.stderr or "").strip().split("\n")
        raise RuntimeError(message[-1] if message else "pandoc failed")


# ------------------------------------------------------------ stylesheets ----

_COMMON = """
*, *::before, *::after { box-sizing: border-box; }
body {
  margin: 0;
  background: #ffffff;
  color: #1c1c1c;
  font-family: -apple-system, "Segoe UI", Cantarell, "Noto Sans", sans-serif;
  font-size: 17px;
  line-height: 1.65;
  -webkit-text-size-adjust: 100%;
}
main { max-width: 46rem; margin: 0 auto; padding: 3rem 1.25rem 6rem; }
h1, h2, h3, h4, h5, h6 { line-height: 1.25; margin: 2em 0 0.6em; font-weight: 700; }
h1 { font-size: 2.1em; margin-top: 0; letter-spacing: -0.02em; }
h2 { font-size: 1.55em; letter-spacing: -0.01em; }
h3 { font-size: 1.25em; }
h4 { font-size: 1.08em; }
h5 { font-size: 1em; }
h6 { font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.06em; opacity: 0.7; }
p { margin: 0 0 1.1em; }
a { color: #0b57d0; text-decoration-thickness: 1px; text-underline-offset: 2px; }
a:hover { text-decoration-thickness: 2px; }
img { max-width: 100%; height: auto; border-radius: 6px; }
hr { border: none; border-top: 1px solid rgba(0,0,0,0.14); margin: 2.4em 0; }
blockquote {
  margin: 1.2em 0; padding: 0.2em 0 0.2em 1.1em;
  border-left: 3px solid rgba(11,87,208,0.5); color: rgba(0,0,0,0.75);
}
blockquote > :last-child { margin-bottom: 0; }
ul, ol { margin: 0 0 1.1em; padding-left: 1.6em; }
li { margin: 0.2em 0; }
li > p { margin: 0.3em 0; }
ul.task-list { list-style: none; padding-left: 0.3em; }
ul.task-list input { margin-right: 0.5em; }
code {
  font-family: ui-monospace, "Cascadia Code", "Source Code Pro", Menlo, Consolas, monospace;
  font-size: 0.88em;
  background: rgba(0,0,0,0.055); padding: 0.15em 0.4em; border-radius: 4px;
}
pre {
  background: rgba(0,0,0,0.045); border: 1px solid rgba(0,0,0,0.09);
  border-radius: 8px; padding: 0.9em 1.05em; overflow-x: auto; line-height: 1.5;
  margin: 0 0 1.4em;
}
pre code { background: none; padding: 0; font-size: 0.86em; }
table { border-collapse: collapse; width: 100%; margin: 0 0 1.4em; font-size: 0.95em; }
th, td { border: 1px solid rgba(0,0,0,0.13); padding: 0.45em 0.75em; }
th { background: rgba(0,0,0,0.045); font-weight: 700; }
mark { background: #fff3a3; padding: 0.05em 0.15em; border-radius: 3px; }
kbd {
  font-family: ui-monospace, monospace; font-size: 0.85em;
  border: 1px solid rgba(0,0,0,0.25); border-bottom-width: 2px;
  border-radius: 4px; padding: 0.1em 0.4em; background: rgba(0,0,0,0.03);
}
.math { font-size: 1.05em; }
div.math { text-align: center; margin: 1.4em 0; }
dl.frontmatter {
  display: grid; grid-template-columns: max-content 1fr; gap: 0.2em 1em;
  margin: 0 0 2em; padding: 0.9em 1.1em;
  background: rgba(11,87,208,0.05); border: 1px solid rgba(11,87,208,0.15);
  border-radius: 8px; font-size: 0.93em;
}
dl.frontmatter dt { font-weight: 700; opacity: 0.6; text-transform: uppercase;
  font-size: 0.8em; letter-spacing: 0.04em; align-self: center; }
dl.frontmatter dd { margin: 0; }
.footnotes { margin-top: 3em; font-size: 0.92em; opacity: 0.85; }
@media (prefers-color-scheme: dark) {
  body { background: #16181c; color: #e3e3e3; }
  a { color: #8ab4f8; }
  hr { border-top-color: rgba(255,255,255,0.15); }
  blockquote { border-left-color: rgba(138,180,248,0.5); color: rgba(255,255,255,0.78); }
  code { background: rgba(255,255,255,0.09); }
  pre { background: rgba(255,255,255,0.05); border-color: rgba(255,255,255,0.1); }
  th, td { border-color: rgba(255,255,255,0.14); }
  th { background: rgba(255,255,255,0.06); }
  mark { background: #6b5a12; color: #fff; }
  kbd { border-color: rgba(255,255,255,0.3); background: rgba(255,255,255,0.05); }
  dl.frontmatter { background: rgba(138,180,248,0.08); border-color: rgba(138,180,248,0.2); }
}
@media print {
  body { background: #fff; color: #000; font-size: 11pt; }
  main { max-width: none; padding: 0; }
  pre, blockquote, table { break-inside: avoid; }
  h1, h2, h3 { break-after: avoid; }
  a { color: inherit; text-decoration: none; }
}
"""

_GITHUB_EXTRA = """
main { max-width: 56rem; }
body { font-size: 16px; line-height: 1.6; }
h1, h2 { padding-bottom: 0.3em; border-bottom: 1px solid rgba(0,0,0,0.12); }
@media (prefers-color-scheme: dark) {
  h1, h2 { border-bottom-color: rgba(255,255,255,0.15); }
}
"""

_STYLESHEETS = {
    "clean": _COMMON,
    "github": _COMMON + _GITHUB_EXTRA,
}
