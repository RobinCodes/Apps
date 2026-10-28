"""The part of YAML that people write by hand, without PyYAML.

Two files in the brain are YAML and read by this app: `config/learning.yaml`
(the learning settings, SYSTEM.md §7) and each curriculum's `drills.yaml`.
PyYAML is used when it is installed, because it is the real thing; it is not
always installed — MSYS2 does not ship it with GTK, and a fresh Arch has it
only as somebody else's dependency — and an app that cannot read its own
settings without an extra package is an app that silently falls back to
defaults on one machine and not on another.

So `load` understands what those files actually contain: block mappings,
block lists (including lists of mappings), `[a, b]` and `{a: 1}` flow
collections, quoted and plain scalars, numbers, booleans and `#` comments.
Anchors, tags and multi-line strings are not here, and nothing raises on
them: an unreadable line is skipped.

`dump` writes the same subset back, always the same way, so that the file
`learning.yaml` follows Robin between machines with diffs a person can read.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- reading --


def load(text: str):
    """Parse YAML text. PyYAML when available, the reader below when not."""
    try:
        import yaml                                         # noqa: PLC0415
    except ImportError:
        return parse(text)
    try:
        return yaml.safe_load(text)
    except Exception:                                       # noqa: BLE001
        return parse(text)


def parse(text: str):
    """The hand-written subset, with no dependency at all."""
    lines = []
    for raw in text.replace("\t", "    ").splitlines():
        content = _strip_comment(raw).rstrip()
        if not content.strip() or content.strip() in ("---", "..."):
            continue
        indent = len(content) - len(content.lstrip(" "))
        lines.append([indent, content.strip()])
    if not lines:
        return None
    value, _ = _block(lines, 0, lines[0][0])
    return value


def _strip_comment(line: str) -> str:
    """The line without a trailing comment — a `#` outside quotes, after a space."""
    quote = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index]
    return line


def _block(lines, index, indent):
    """The block starting at `lines[index]`, at `indent`. Returns (value, next index)."""
    if index >= len(lines):
        return None, index
    if lines[index][1] == "-" or lines[index][1].startswith("- "):
        return _sequence(lines, index, indent)
    return _mapping(lines, index, indent)


def _sequence(lines, index, indent):
    items = []
    while index < len(lines) and lines[index][0] == indent:
        content = lines[index][1]
        if not (content == "-" or content.startswith("- ")):
            break
        rest = content[1:].lstrip()
        if not rest:
            # `-` alone: the item is the indented block below it.
            if index + 1 < len(lines) and lines[index + 1][0] > indent:
                value, index = _block(lines, index + 1, lines[index + 1][0])
            else:
                value, index = None, index + 1
            items.append(value)
            continue
        if _key_value(rest) and not rest.startswith(("[", "{", '"', "'")):
            # `- key: value`: a mapping whose first key shares the dash's line.
            # The rest of its keys are indented to where that key began.
            column = indent + (len(content) - len(rest))
            lines[index] = [column, rest]
            value, index = _mapping(lines, index, column)
            items.append(value)
            continue
        items.append(_scalar(rest))
        index += 1
    return items, index


def _mapping(lines, index, indent):
    data = {}
    while index < len(lines) and lines[index][0] == indent:
        pair = _key_value(lines[index][1])
        if not pair:
            index += 1
            continue
        key, rest = pair
        index += 1
        if rest:
            data[key] = _scalar(rest)
        elif index < len(lines) and (lines[index][0] > indent or (
                lines[index][0] == indent and lines[index][1].startswith("- "))):
            # A block below the key; a list may sit at the key's own indent.
            data[key], index = _block(lines, index, lines[index][0])
        else:
            data[key] = None
    return data, index


_KEY = re.compile(r"""^("(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|[^:#\[\]{},][^:]*?)\s*:(?:\s+(.*))?$""")


def _key_value(text: str):
    m = _KEY.match(text)
    if not m:
        return None
    # A key is the text it is written as, never a boolean or a number: `off:`
    # is a setting here, and `07:` a number of the Major system. (YAML 1.1,
    # and so PyYAML, reads the first as False — config.py allows for that.)
    key = m.group(1).strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "'\"":
        key = str(_scalar(key))
    return key, (m.group(2) or "").strip()


def _scalar(text: str):
    text = text.strip()
    if not text:
        return None
    if text[0] in "[{":
        value, _ = _flow(text, 0)
        return value
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return _unescape(text[1:-1])
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~"):
        return None
    if re.fullmatch(r"[-+]?\d+", text) and not re.fullmatch(r"[-+]?0\d+", text):
        return int(text)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", text) and "." in text:
        return float(text)
    return text


def _unescape(text: str) -> str:
    escapes = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/", "0": "\0"}
    return re.sub(r"\\(.)", lambda m: escapes.get(m.group(1), m.group(1)), text)


def _flow(text: str, index: int):
    """A `[…]` or `{…}` starting at `text[index]`. Returns (value, index after it)."""
    opener = text[index]
    closer = "]" if opener == "[" else "}"
    index += 1
    items: list = []
    data: dict = {}
    while index < len(text):
        while index < len(text) and text[index] in " ,":
            index += 1
        if index >= len(text):
            break
        if text[index] == closer:
            return (items if opener == "[" else data), index + 1
        if text[index] in "[{":
            value, index = _flow(text, index)
            items.append(value)
            continue
        # A plain or quoted run up to the next separator at this depth.
        start = index
        quote = None
        while index < len(text):
            char = text[index]
            if quote:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char in ",]}":
                break
            elif char in "[{" and opener == "{" and text[start:index].rstrip().endswith(":"):
                break
            index += 1
        token = text[start:index].strip()
        if opener == "{":
            key, _, rest = token.partition(":")
            if not rest.strip() and index < len(text) and text[index] in "[{":
                value, index = _flow(text, index)
                data[str(_scalar(key))] = value
            else:
                data[str(_scalar(key))] = _scalar(rest)
        elif token:
            items.append(_scalar(token))
    return (items if opener == "[" else data), index


# ---------------------------------------------------------------- writing --


def dump(data: dict, header: str = "") -> str:
    """A mapping as YAML, in the subset `parse` reads back, keys in their order."""
    out = [f"# {line}".rstrip() for line in header.splitlines()] if header else []
    out += _dump_mapping(data, 0)
    return "\n".join(out) + "\n"


def _dump_mapping(data: dict, indent: int) -> list[str]:
    pad = " " * indent
    out = []
    for key, value in data.items():
        name = _quote(str(key))
        if isinstance(value, dict) and value:
            out.append(f"{pad}{name}:")
            out += _dump_mapping(value, indent + 2)
        elif isinstance(value, (list, tuple)) and value:
            out.append(f"{pad}{name}:")
            out += _dump_sequence(list(value), indent + 2)
        else:
            out.append(f"{pad}{name}: {_quote(value)}")
    return out


def _dump_sequence(items: list, indent: int) -> list[str]:
    pad = " " * indent
    out = []
    for item in items:
        if isinstance(item, dict) and item:
            first, *rest = _dump_mapping(item, indent + 2)
            out.append(f"{pad}- {first.lstrip()}")
            out += rest
        elif isinstance(item, (list, tuple)) and item:
            out.append(f"{pad}-")
            out += _dump_sequence(list(item), indent + 2)
        else:
            out.append(f"{pad}- {_quote(item)}")
    return out


def _quote(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[]"
    if isinstance(value, dict):
        return "{}"
    text = str(value)
    plain = (text and text == text.strip()
             and not re.search(r"[:#\[\]{},&*!|>'\"%@`]", text)
             and not text.startswith(("-", "?"))
             and _scalar(text) == text)
    if plain:
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
