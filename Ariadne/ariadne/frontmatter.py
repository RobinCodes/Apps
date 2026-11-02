"""Front matter, read the way it was written and written back the same way.

Every file of the Order opens with a block of YAML, and more than one program
edits each of them — an app, Anamnesis, a text editor, Robin. Each owes the
next one the file it found, changed only where it was asked to change it. A
YAML library cannot promise that: it reads the block into values and writes
the values back in its own spelling, and the comment after `status: open`,
the order of the keys and the quotes someone chose are gone.

So a `FrontMatter` keeps its lines. Reading a key parses the lines that hold
it; setting a key replaces those lines and no others; a key nobody touched is
written back byte for byte, comments and all. The values understood are the
ones the Order writes: scalars, quoted or not, `[flow, lists]`,
`{flow: maps}`, and block lists and maps one level deep.

Standard library only; vendored by `house/sync.py` with the other brain files.
"""

from __future__ import annotations

import re

KEY = re.compile(r"^([A-Za-z_][\w-]*)[ \t]*:(.*)$")


def split(text: str) -> tuple["FrontMatter | None", str]:
    """`(front matter, body)` of a text whose newlines are "\\n"."""
    if not text.startswith("---"):
        return None, text
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].rstrip() in ("---", "..."):
            return (FrontMatter(lines[1:index], closer=lines[index]),
                    "\n".join(lines[index + 1:]))
    return None, text


class FrontMatter:
    """The lines between the two `---`, and what they say."""

    def __init__(self, lines=None, closer: str = "---", present: bool = True):
        self.lines: list[str] = list(lines or [])
        self.closer = closer
        self.present = present      # False: the file had none, and gets one only if set
        self.dirty = False

    # -- finding a key ------------------------------------------------------

    def _span(self, key: str) -> tuple[int, int] | None:
        """Where `key`'s lines are: its own line and the indented ones under it."""
        for index, line in enumerate(self.lines):
            match = KEY.match(line)
            if match and match.group(1) == key:
                end = index + 1
                while end < len(self.lines) and _continues(self.lines[end]):
                    end += 1
                return index, end
        return None

    def keys(self) -> list[str]:
        return [m.group(1) for m in map(KEY.match, self.lines) if m]

    def __contains__(self, key: str) -> bool:
        return self._span(key) is not None

    def get(self, key: str, default=None):
        """The value, as str, list or dict — or `default` when absent or empty."""
        span = self._span(key)
        if span is None:
            return default
        first = KEY.match(self.lines[span[0]]).group(2)
        value = parse_value(first, self.lines[span[0] + 1:span[1]])
        return default if value in ("", None) else value

    # -- changing one -------------------------------------------------------

    def set(self, key: str, value) -> None:
        """Write `key`, replacing only its own lines; None removes it.

        A scalar or a flow collection is one line, `key: value`; a dict that
        should be a block (`colours:` with a line per colour) is passed as
        `Block(dict)`. Setting a key to what it already says changes nothing,
        so a file is not rewritten for an edit that was undone.
        """
        if value is not None and self.get(key) == _plain(value):
            return
        span = self._span(key)
        new = [] if value is None else render(key, value)
        if span is None:
            if not new:
                return
            self.lines.extend(new)
        else:
            self.lines[span[0]:span[1]] = new
        self.dirty = True

    def text(self) -> str:
        return "\n".join(["---", *self.lines, self.closer])


class Block(dict):
    """A dict to be written one key to a line, under its own key."""


def _continues(line: str) -> bool:
    return bool(line.strip()) and (line[0] in " \t" or line.startswith("- "))


def _plain(value):
    """What `get` would answer for `value`, to see whether setting it changes anything."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ------------------------------------------------------------------ reading --

def strip_comment(text: str) -> str:
    """`value  # comment` without the comment, leaving a `#` inside quotes alone."""
    quote_char = None
    for index, char in enumerate(text):
        if quote_char:
            if char == quote_char:
                quote_char = None
        elif char in "\"'":
            quote_char = char
        elif char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index].rstrip()
    return text.rstrip()


def parse_value(first: str, more: list[str]):
    text = strip_comment(first).strip()
    if text:
        if text[0] in "[{":
            value, _ = _flow(text, 0)
            return value
        return scalar(text)
    rows = [strip_comment(line) for line in more if strip_comment(line).strip()]
    if not rows:
        return ""
    if all(row.lstrip().startswith("-") for row in rows):
        return [parse_value(row.lstrip()[1:], []) for row in rows]
    out = {}
    for row in rows:
        match = re.match(r"^\s*([^:]+?)\s*:(.*)$", row)
        if match:
            out[scalar(match.group(1))] = parse_value(match.group(2), [])
    return out


def scalar(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return re.sub(r'\\(.)', lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)),
                      text[1:-1])
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def _flow(text: str, index: int):
    """A flow value starting at `index`: `(value, index after it)`."""
    while index < len(text) and text[index] in " \t":
        index += 1
    if index >= len(text):
        return "", index
    opener = text[index]
    if opener in "[{":
        closer = "]" if opener == "[" else "}"
        items, mapping = [], {}
        index += 1
        while True:
            while index < len(text) and text[index] in " \t,":
                index += 1
            if index >= len(text) or text[index] == closer:
                return (items if opener == "[" else mapping), index + 1
            if opener == "[":
                value, index = _flow(text, index)
                items.append(value)
            else:
                name, index = _token(text, index, ":" + closer)
                if index < len(text) and text[index] == ":":
                    value, index = _flow(text, index + 1)
                else:
                    value = ""
                mapping[scalar(name)] = value
    token, index = _token(text, index, ",]}")
    return scalar(token), index


def _token(text: str, index: int, stops: str):
    """A plain or quoted scalar, up to one of `stops`."""
    start = index
    if index < len(text) and text[index] in "\"'":
        quote_char = text[index]
        index += 1
        while index < len(text):
            if text[index] == "\\" and quote_char == '"':
                index += 2
            elif text[index] == quote_char and quote_char == "'" \
                    and text[index + 1:index + 2] == "'":
                index += 2                       # '' is a quote inside '…'
            elif text[index] == quote_char:
                break
            else:
                index += 1
        index += 1
        return text[start:index], index
    while index < len(text) and text[index] not in stops:
        index += 1
    return text[start:index].strip(), index


# ------------------------------------------------------------------ writing --

# What YAML reads back as the same text without quotes: no indicator first,
# no `: ` or ` #` inside, nothing at either end that would be trimmed.
_INDICATORS = "-?:,[]{}#&*!|>'\"%@`"


def quote(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def flow(value) -> str:
    """A value as one line of YAML."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{name}: {flow(item)}" for name, item in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(flow(item) for item in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(int(round(value))) if float(value).is_integer() else f"{value:g}"
    return quote(value)


def bare(value) -> str:
    """A scalar unquoted where YAML would read it back as the same text."""
    text = str(value)
    if (text and text == text.strip() and text[0] not in _INDICATORS
            and "\n" not in text and ": " not in text and " #" not in text
            and not text.endswith(":")
            and text.lower() not in ("true", "false", "null", "yes", "no", "on", "off", "~")):
        return text
    return quote(text)


def render(key: str, value) -> list[str]:
    if isinstance(value, Block):
        if not value:
            return []
        return [f"{key}:"] + [f"  {name}: {flow(item)}" for name, item in value.items()]
    if isinstance(value, (list, tuple, dict, int, float)):
        return [f"{key}: {flow(value)}"]
    return [f"{key}: {bare(value)}"]
