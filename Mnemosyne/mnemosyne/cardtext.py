r"""A card's text as Pango markup: the little Markdown a card has, and its maths.

Cards are written inside notes, so they carry what notes carry: **bold**,
*italic*, `code`, ~~struck~~, a [[link]], a [link](url), and maths between
dollars — `$(p-1)! \equiv -1 \pmod p$`. A card is a line or two, not a page,
so this is not Notedown's renderer: it is one pass that turns those into
Pango's markup for a single label, which is what both the screen and the PDF
draw. The maths goes through `latex.inline_markup`, the same Unicode-and-Pango
typesetting Notedown and Claude Desk use, so a formula looks the same on the
card, on the sheet and in the note.

Nothing here raises: text it does not understand is shown as written.
"""

from __future__ import annotations

import re
from pathlib import Path

_SPAN = re.compile(
    r"(?P<code>(`+)(?P<code_text>.+?)\2)"
    r"|\$\$(?P<display>.+?)\$\$"
    r"|(?<![\\$\w])\$(?!\s)(?P<math>[^$\n]+?)(?<![\s\\])\$(?!\w)"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|__(?P<bold2>.+?)__"
    r"|(?<![*\w])\*(?![\s*])(?P<italic>.+?)(?<![\s*])\*(?!\*)"
    r"|(?<![\w])_(?![\s_])(?P<italic2>.+?)(?<![\s_])_(?![\w])"
    r"|~~(?P<strike>.+?)~~"
    r"|==(?P<mark>.+?)=="
    r"|\[\[(?P<wiki>[^\]|\n]+)(?:\|(?P<wiki_label>[^\]\n]+))?\]\]"
    r"|!\[(?P<image>[^\]]*)\]\([^)\n]*\)"
    r"|!\[\[(?P<embed>[^\]\n]+)\]\]"
    r"|\[(?P<link>[^\]\n]+)\]\([^)\n]+\)",
    re.S)

_LIST = re.compile(r"^(\s*)(?:[-*+])\s+", re.M)


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Number theory is half of what the cards are about, and latex.py (Notedown's
# copy, kept as it is) does not know these two; they are spelt out for it.
_PMOD = re.compile(r"\\pmod\s*(\{[^{}]*\}|\\?\w+)")
_BMOD = re.compile(r"\\bmod\b")


def _maths(tex: str) -> str:
    try:
        from .latex import inline_markup                    # noqa: PLC0415

        tex = _PMOD.sub(lambda m: r"\quad(\mathrm{mod}\ " + m.group(1).strip("{}") + ")", tex)
        tex = _BMOD.sub(r"\\mod", tex)
        return inline_markup(tex)
    except Exception:                                       # noqa: BLE001
        return esc(tex)


def to_markup(text: str) -> str:
    """Card text → Pango markup. Newlines are kept; list markers become bullets."""
    text = _LIST.sub(lambda m: m.group(1) + "• ", text.strip())
    return _render(text)


def _render(text: str) -> str:
    out = []
    cursor = 0
    for m in _SPAN.finditer(text):
        out.append(esc(text[cursor:m.start()]))
        cursor = m.end()
        groups = m.groupdict()
        if groups["code"] is not None:
            out.append(f"<tt>{esc(groups['code_text'])}</tt>")
        elif groups["display"] is not None:
            out.append(_maths(groups["display"]))
        elif groups["math"] is not None:
            out.append(_maths(groups["math"]))
        elif groups["bold"] is not None or groups["bold2"] is not None:
            out.append(f"<b>{_render(groups['bold'] or groups['bold2'])}</b>")
        elif groups["italic"] is not None or groups["italic2"] is not None:
            out.append(f"<i>{_render(groups['italic'] or groups['italic2'])}</i>")
        elif groups["strike"] is not None:
            out.append(f"<s>{_render(groups['strike'])}</s>")
        elif groups["mark"] is not None:
            out.append(f"<u>{_render(groups['mark'])}</u>")
        elif groups["wiki"] is not None:
            shown = groups["wiki_label"] or groups["wiki"]
            out.append(f"<u>{esc(shown.strip())}</u>")
        elif groups["image"] is not None or groups["embed"] is not None:
            pass                     # drawn as the picture itself, under the text
        elif groups["link"] is not None:
            out.append(f"<u>{_render(groups['link'])}</u>")
        else:                                               # pragma: no cover
            out.append(esc(m.group(0)))
    out.append(esc(text[cursor:]))
    return "".join(out)


_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)>\s]+?)>?(?:\s+\"[^\"]*\")?\s*\)|!\[\[([^\]|#]+)")


def images(text: str) -> list[str]:
    """The pictures a card shows, as written: `![](…)`, and `![[…]]` embeds."""
    return [(m.group(1) or m.group(2)).strip() for m in _IMAGE.finditer(text)
            if not (m.group(1) or "").startswith(("http://", "https://"))]


def image_paths(text: str, note: Path, *bases) -> list[Path]:
    """Those pictures as files that exist: beside the note first, then under `bases`."""
    found = []
    for target in images(text):
        for base in (note.parent, *bases):
            candidate = Path(base) / target
            if candidate.is_file():
                found.append(candidate)
                break
    return found


def plain(text: str) -> str:
    """The text a person would read aloud: markup and markers taken off."""
    return re.sub(r"<[^>]+>", "", to_markup(text)).replace("&lt;", "<") \
        .replace("&gt;", ">").replace("&amp;", "&")
