"""Colour for fenced code, without a syntax engine behind it.

A code block in a note is five lines of shell or a function stub, and it is
read, not edited. That is a much smaller problem than the one a language
server solves, and it is answered here with one tokeniser and a table of
keywords: comments, strings, numbers, keywords, and the name after a `def`,
`class`, `function` or `fn`. Everything else is plain, which is honest about
what this knows.

The output is Pango markup for a label, so it costs one widget per block
rather than one per token, and a document full of code stays cheap to build.

A language this has never heard of still gets strings, numbers and whichever
comment syntax it declares — the defaults are `#` and `//`, which between them
cover nearly everything — so an unknown fence looks like code rather than like
prose.
"""

from __future__ import annotations

import re

from gi.repository import GLib

# Language families, by the names people actually write after a fence.
ALIASES = {
    "py": "python", "python3": "python", "sage": "python",
    "js": "javascript", "jsx": "javascript", "mjs": "javascript", "cjs": "javascript",
    "ts": "typescript", "tsx": "typescript",
    "sh": "shell", "bash": "shell", "zsh": "shell", "console": "shell",
    "shell-session": "shell", "fish": "shell",
    "c++": "cpp", "cxx": "cpp", "cc": "cpp", "h": "c", "hpp": "cpp",
    "cs": "csharp", "golang": "go", "rs": "rust", "rb": "ruby", "kt": "kotlin",
    "yml": "yaml", "htm": "html", "xhtml": "html", "svg": "xml",
    "postgres": "sql", "psql": "sql", "sqlite": "sql", "mysql": "sql",
    "docker": "dockerfile", "makefile": "make", "mk": "make",
    "tex": "latex", "md": "markdown", "patch": "diff",
}

_KEYWORDS = {
    "python": """and as assert async await break class continue def del elif else except
        finally for from global if import in is lambda nonlocal not or pass raise return
        try while with yield match case True False None self cls""",
    "javascript": """async await break case catch class const continue debugger default delete
        do else export extends finally for function if import in instanceof let new of return
        static super switch this throw try typeof var void while with yield true false null
        undefined""",
    "typescript": """abstract any as async await boolean break case catch class const constructor
        continue declare default delete do else enum export extends false finally for from
        function if implements import in instanceof interface let namespace never new null
        number of private protected public readonly return static string super switch this
        throw true try type typeof undefined union unknown var void while yield""",
    "c": """auto break case char const continue default do double else enum extern float for
        goto if inline int long register restrict return short signed sizeof static struct
        switch typedef union unsigned void volatile while bool true false NULL""",
    "cpp": """alignas alignof auto bool break case catch char class const constexpr const_cast
        continue decltype default delete do double dynamic_cast else enum explicit export
        extern false float for friend goto if inline int long mutable namespace new noexcept
        nullptr operator private protected public register return short signed sizeof static
        static_cast struct switch template this throw true try typedef typeid typename union
        unsigned using virtual void volatile while""",
    "java": """abstract assert boolean break byte case catch char class const continue default
        do double else enum extends final finally float for goto if implements import
        instanceof int interface long native new null package private protected public
        return short static strictfp super switch synchronized this throw throws transient
        true false try void volatile while var record sealed""",
    "csharp": """abstract as async await base bool break byte case catch char checked class
        const continue decimal default delegate do double else enum event explicit extern
        false finally fixed float for foreach goto if implicit in int interface internal is
        lock long namespace new null object operator out override params private protected
        public readonly ref return sbyte sealed short sizeof stackalloc static string struct
        switch this throw true try typeof uint ulong unchecked unsafe ushort using var
        virtual void volatile while record""",
    "go": """break case chan const continue default defer else fallthrough for func go goto if
        import interface map package range return select struct switch type var nil true
        false make new len cap append copy delete panic recover""",
    "rust": """as async await break const continue crate dyn else enum extern false fn for if
        impl in let loop match mod move mut pub ref return self Self static struct super
        trait true type unsafe use where while box macro_rules""",
    "ruby": """alias and begin break case class def defined? do else elsif end ensure false for
        if in module next nil not or redo rescue retry return self super then true undef
        unless until when while yield require attr_accessor puts lambda proc""",
    "shell": """if then else elif fi case esac for while until do done in function select time
        return break continue local export readonly declare set unset shift source alias
        echo cd exit test trap eval exec""",
    "sql": """select from where group by having order limit offset insert into values update
        set delete create table drop alter add column primary key foreign references index
        unique not null default join inner left right outer full on as distinct union all
        and or in exists between like is case when then else end begin commit rollback with
        returning""",
    "php": """abstract and array as break callable case catch class clone const continue
        declare default do echo else elseif empty enddeclare endfor endforeach endif endswitch
        endwhile enum extends final finally fn for foreach function global goto if implements
        include include_once instanceof insteadof interface isset list match namespace new or
        print private protected public readonly require require_once return static switch
        throw trait try unset use var while xor yield true false null""",
    "kotlin": """as break by catch class companion const continue crossinline data do dynamic
        else enum external false final finally for fun get if import in infix init inline
        interface internal is lateinit noinline null object open operator out override
        package private protected public reified return sealed set super suspend tailrec
        this throw true try typealias val var vararg when where while""",
    "swift": """associatedtype class deinit enum extension fileprivate func import init inout
        internal let open operator private protocol public rethrows static struct subscript
        typealias var break case continue default defer do else fallthrough for guard if in
        repeat return switch where while as catch is nil super self Self throw throws try
        true false async await""",
    "lua": """and break do else elseif end false for function goto if in local nil not or
        repeat return then true until while""",
    "yaml": """true false null yes no on off""",
    "json": """true false null""",
    "make": """ifeq ifneq ifdef ifndef else endif include define endef export unexport
        override vpath .PHONY""",
    "dockerfile": """FROM RUN CMD LABEL MAINTAINER EXPOSE ENV ADD COPY ENTRYPOINT VOLUME USER
        WORKDIR ARG ONBUILD STOPSIGNAL HEALTHCHECK SHELL AS""",
    "css": """important media supports keyframes import charset font-face namespace from to
        and not only screen print""",
}

# How each family writes a comment, and whether its strings do backslashes.
_SYNTAX = {
    "python": {"line": ("#",), "triple": True},
    "shell": {"line": ("#",)},
    "ruby": {"line": ("#",)},
    "yaml": {"line": ("#",), "plain": True},
    "make": {"line": ("#",)},
    "dockerfile": {"line": ("#",)},
    "r": {"line": ("#",)},
    "perl": {"line": ("#",)},
    "toml": {"line": ("#",), "plain": True},
    "ini": {"line": (";", "#"), "plain": True},
    "sql": {"line": ("--",), "block": ("/*", "*/")},
    "lua": {"line": ("--",), "block": ("--[[", "]]")},
    "haskell": {"line": ("--",), "block": ("{-", "-}")},
    "latex": {"line": ("%",), "plain": True},
    "css": {"block": ("/*", "*/")},
    "html": {"block": ("<!--", "-->"), "markup": True},
    "xml": {"block": ("<!--", "-->"), "markup": True},
    "json": {"plain": True},
    "diff": {"diff": True},
}
_DEFAULT = {"line": ("//", "#"), "block": ("/*", "*/")}

LIGHT = {
    "comment": "#6a737d",
    "string": "#0a7c3e",
    "number": "#8250df",
    "keyword": "#cf222e",
    "name": "#8250df",
    "tag": "#0550ae",
    "attr": "#0a7c3e",
    "added": "#0a7c3e",
    "removed": "#cf222e",
    "meta": "#953800",
}

DARK = {
    "comment": "#8b949e",
    "string": "#7ee787",
    "number": "#d2a8ff",
    "keyword": "#ff7b72",
    "name": "#d2a8ff",
    "tag": "#79c0ff",
    "attr": "#7ee787",
    "added": "#7ee787",
    "removed": "#ff7b72",
    "meta": "#ffa657",
}

_NUMBER = re.compile(r"\b(?:0[xXbBoO][0-9a-fA-F_]+|\d[\d_]*(?:\.[\d_]+)?(?:[eE][-+]?\d+)?)\b")
_WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_DEFINES = re.compile(r"\b(?:def|class|function|fn|func|struct|enum|interface|trait|type)\s+"
                      r"([A-Za-z_][A-Za-z_0-9]*)")
_TAG = re.compile(r"</?([A-Za-z][A-Za-z0-9-]*)((?:\s+[^<>]*?)?)/?>")
_ATTR = re.compile(r"([A-Za-z_:][-\w:.]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")
_YAML_KEY = re.compile(r"^(\s*-?\s*)([A-Za-z_][\w .-]*)(\s*:)(?=\s|$)")
_MAX = 40_000


def language_of(info: str) -> str:
    """The family name for whatever was written after the fence."""
    name = (info or "").strip().lower()
    name = re.split(r"[\s,{:]", name)[0] if name else ""
    return ALIASES.get(name, name)


def known(info: str) -> bool:
    name = language_of(info)
    return name in _KEYWORDS or name in _SYNTAX


def markup(code: str, info: str, dark: bool) -> str:
    """Pango markup for a code block. Falls back to escaped text on anything."""
    try:
        return _markup(code or "", language_of(info), DARK if dark else LIGHT)
    except Exception:  # noqa: BLE001 - a code block must always render
        return esc(code or "")


def _markup(code, language, palette):
    if len(code) > _MAX:
        return esc(code)

    rules = _SYNTAX.get(language, {} if language in _KEYWORDS else _DEFAULT)
    if language in _KEYWORDS and "line" not in rules and "block" not in rules \
            and not rules.get("plain") and not rules.get("markup") and not rules.get("diff"):
        rules = {**_DEFAULT, **rules}

    if rules.get("diff"):
        return _diff(code, palette)
    if rules.get("markup"):
        return _markup_language(code, palette)

    keywords = set((_KEYWORDS.get(language) or "").split())
    out = []
    index = 0
    size = len(code)
    line_starts = tuple(rules.get("line", ()))
    block = rules.get("block")

    while index < size:
        char = code[index]

        # Comments.
        comment = next((start for start in line_starts if code.startswith(start, index)), None)
        if comment:
            end = code.find("\n", index)
            end = size if end == -1 else end
            out.append(_span(palette["comment"], code[index:end], italic=True))
            index = end
            continue
        if block and code.startswith(block[0], index):
            end = code.find(block[1], index + len(block[0]))
            end = size if end == -1 else end + len(block[1])
            out.append(_span(palette["comment"], code[index:end], italic=True))
            index = end
            continue

        # Strings.
        if char in "\"'`":
            if rules.get("triple") and code.startswith(char * 3, index):
                end = code.find(char * 3, index + 3)
                end = size if end == -1 else end + 3
                out.append(_span(palette["string"], code[index:end]))
                index = end
                continue
            end = _string_end(code, index, char, escapes=not rules.get("plain"))
            out.append(_span(palette["string"], code[index:end]))
            index = end
            continue

        # YAML and TOML read as keys and values rather than as code.
        if rules.get("plain") and (index == 0 or code[index - 1] == "\n"):
            line_end = code.find("\n", index)
            line_end = size if line_end == -1 else line_end
            key = _YAML_KEY.match(code, index, line_end)
            if key:
                out.append(esc(key.group(1)))
                out.append(_span(palette["tag"], key.group(2)))
                out.append(esc(key.group(3)))
                index = key.end()
                continue

        number = _NUMBER.match(code, index)
        if number:
            out.append(_span(palette["number"], number.group(0)))
            index = number.end()
            continue

        word = _WORD.match(code, index)
        if word:
            body = word.group(0)
            if body in keywords or (language == "dockerfile" and body.upper() in keywords):
                out.append(_span(palette["keyword"], body, bold=True))
            elif _defines_here(code, index, body):
                out.append(_span(palette["name"], body, bold=True))
            else:
                out.append(esc(body))
            index = word.end()
            continue

        if char == "@" and language in ("python", "java", "typescript", "javascript"):
            decorator = _WORD.match(code, index + 1)
            if decorator:
                out.append(_span(palette["meta"], code[index:decorator.end()]))
                index = decorator.end()
                continue

        out.append(esc(char))
        index += 1

    return "".join(out)


def _defines_here(code, index, word):
    """Is this word the name being introduced by a def/class/fn just before it?"""
    head = code.rfind("\n", 0, index)
    prefix = code[head + 1:index + len(word)]
    match = _DEFINES.search(prefix)
    return bool(match and match.end() == len(prefix))


def _string_end(code, index, quote, escapes=True):
    probe = index + 1
    while probe < len(code):
        if escapes and code[probe] == "\\":
            probe += 2
            continue
        if code[probe] == quote:
            return probe + 1
        if code[probe] == "\n" and quote != "`":
            return probe
        probe += 1
    return len(code)


def _markup_language(code, palette):
    """HTML and XML: tags, attribute names, attribute values, comments."""
    out = []
    index = 0
    while index < len(code):
        if code.startswith("<!--", index):
            end = code.find("-->", index)
            end = len(code) if end == -1 else end + 3
            out.append(_span(palette["comment"], code[index:end], italic=True))
            index = end
            continue
        tag = _TAG.match(code, index)
        if tag:
            out.append(esc(code[index:tag.start(1)]))
            out.append(_span(palette["tag"], tag.group(1), bold=True))
            rest = tag.group(2) or ""
            position = 0
            for attribute in _ATTR.finditer(rest):
                out.append(esc(rest[position:attribute.start()]))
                out.append(_span(palette["name"], attribute.group(1)))
                out.append(esc("="))
                out.append(_span(palette["attr"], attribute.group(2)))
                position = attribute.end()
            out.append(esc(rest[position:]))
            out.append(esc(code[tag.end(2):tag.end()]))
            index = tag.end()
            continue
        out.append(esc(code[index]))
        index += 1
    return "".join(out)


def _diff(code, palette):
    out = []
    for line in code.split("\n"):
        if line.startswith(("+++", "---", "diff ", "index ", "@@")):
            out.append(_span(palette["meta"], line, bold=True))
        elif line.startswith("+"):
            out.append(_span(palette["added"], line))
        elif line.startswith("-"):
            out.append(_span(palette["removed"], line))
        else:
            out.append(esc(line))
    return "\n".join(out)


def _span(colour, text, bold=False, italic=False):
    body = esc(text)
    if bold:
        body = f"<b>{body}</b>"
    if italic:
        body = f"<i>{body}</i>"
    return f'<span foreground="{colour}">{body}</span>'


def esc(text):
    return GLib.markup_escape_text(text or "")
