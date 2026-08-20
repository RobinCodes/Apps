r"""LaTeX for people who did not install TeX.

Claude writes maths the way it writes prose — `$O(n \log n)$` in a sentence,
a displayed derivation between `$$`. A browser would hand this to KaTeX; the
whole point of this app is not to ship a browser, so the formulas are parsed
here into a small tree and drawn with the two things GTK already has: Unicode
and Pango markup.

The split that makes it work: a formula is mostly a *line* — symbols, italic
variables, superscripts — and only occasionally something two-dimensional. So
`markup()` renders the linear part into one label, and the handful of nodes
that genuinely stack (fractions, roots, big operators with limits, matrices)
stay in the tree for `chat.MathView` to build out of boxes. Inline maths uses
the linear form throughout, because a fraction inside a sentence reads better
as `a/b` than as something that pushes the line apart.

Nothing here raises. Unknown commands render as their own name, which is what
you want when the alternative is a blank window.
"""

from __future__ import annotations

import re

from gi.repository import GLib

# --------------------------------------------------------------- symbols ---

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "omicron": "ο", "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ",
    "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "ϕ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ",
    "Omega": "Ω",
}

# Symbol, and how it spaces: rel and bin get thin space either side.
RELATIONS = {
    "=": "=", "<": "<", ">": ">", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "ne": "≠", "equiv": "≡", "approx": "≈", "cong": "≅", "sim": "∼",
    "simeq": "≃", "propto": "∝", "ll": "≪", "gg": "≫", "prec": "≺", "succ": "≻",
    "subset": "⊂", "supset": "⊃", "subseteq": "⊆", "supseteq": "⊇",
    "sqsubseteq": "⊑", "in": "∈", "notin": "∉", "ni": "∋", "vdash": "⊢",
    "models": "⊨", "perp": "⊥", "parallel": "∥", "mid": "∣", "colon": ":",
    "to": "→", "rightarrow": "→", "longrightarrow": "⟶", "leftarrow": "←",
    "longleftarrow": "⟵", "leftrightarrow": "↔", "Rightarrow": "⇒",
    "implies": "⟹", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "iff": "⟺",
    "mapsto": "↦", "hookrightarrow": "↪", "uparrow": "↑", "downarrow": "↓",
    "nearrow": "↗", "searrow": "↘", "doteq": "≐", "asymp": "≍",
}

BINARIES = {
    "+": "+", "-": "−", "*": "∗", "times": "×", "div": "÷", "pm": "±",
    "mp": "∓", "cdot": "⋅", "ast": "∗", "star": "⋆", "circ": "∘",
    "bullet": "∙", "oplus": "⊕", "ominus": "⊖", "otimes": "⊗", "oslash": "⊘",
    "odot": "⊙", "cup": "∪", "cap": "∩", "sqcup": "⊔", "sqcap": "⊓",
    "vee": "∨", "wedge": "∧", "lor": "∨", "land": "∧", "setminus": "∖",
    "smallsetminus": "∖", "triangleleft": "◁", "triangleright": "▷",
    "bigtriangleup": "△", "amalg": "⨿", "uplus": "⊎", "dagger": "†",
}

ORDINARY = {
    "infty": "∞", "partial": "∂", "nabla": "∇", "emptyset": "∅",
    "varnothing": "∅", "forall": "∀", "exists": "∃", "nexists": "∄",
    "neg": "¬", "lnot": "¬", "top": "⊤", "bot": "⊥", "angle": "∠",
    "measuredangle": "∡", "triangle": "△", "square": "□", "blacksquare": "■",
    "diamond": "⋄", "hbar": "ℏ", "ell": "ℓ", "wp": "℘", "aleph": "ℵ",
    "Re": "ℜ", "Im": "ℑ", "prime": "′", "degree": "°", "circ_deg": "°",
    "ldots": "…", "dots": "…", "cdots": "⋯", "vdots": "⋮", "ddots": "⋱",
    "checkmark": "✓", "clubsuit": "♣", "spadesuit": "♠", "heartsuit": "♡",
    "flat": "♭", "sharp": "♯", "natural": "♮", "surd": "√", "backslash": "\\",
    "quad": "\u2003", "qquad": "\u2003\u2003", "space": " ", "!": "",
    ",": "\u2009", ";": "\u2005", ":": "\u2005", " ": " ",
    "#": "#", "%": "%", "&": "&", "_": "_", "{": "{", "}": "}", "$": "$",
    "|": "‖", "lbrace": "{", "rbrace": "}", "langle": "⟨", "rangle": "⟩",
    "lceil": "⌈", "rceil": "⌉", "lfloor": "⌊", "rfloor": "⌋",
}

# Big operators take their scripts above and below when displayed.
BIG_OPS = {
    "sum": "∑", "prod": "∏", "coprod": "∐", "bigcup": "⋃", "bigcap": "⋂",
    "bigoplus": "⨁", "bigotimes": "⨂", "bigvee": "⋁", "bigwedge": "⋀",
    "biguplus": "⨄", "lim": "lim", "limsup": "lim sup", "liminf": "lim inf",
    "max": "max", "min": "min", "sup": "sup", "inf": "inf", "argmax": "arg max",
    "argmin": "arg min", "gcd": "gcd", "det": "det", "Pr": "Pr",
}

# Integrals stack their limits only in the tallest layouts; keep them inline.
INTEGRALS = {
    "int": "∫", "iint": "∬", "iiint": "∭", "oint": "∮", "oiint": "∯",
}

# Upright even without a backslash: nobody means f·u·n·c when they type "sin".
FUNCTIONS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "sinh", "cosh", "tanh", "coth",
    "arcsin", "arccos", "arctan", "log", "ln", "lg", "exp", "deg", "dim",
    "ker", "hom", "arg", "mod", "bmod", "pmod", "erf", "sgn", "tr", "rank",
    "diag", "var", "cov", "std", "median", "mean",
}

BLACKBOARD = {
    "R": "ℝ", "C": "ℂ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "H": "ℍ", "P": "ℙ",
    "F": "𝔽", "E": "𝔼", "1": "𝟙",
}

ACCENTS = {
    "hat": "\u0302", "widehat": "\u0302", "bar": "\u0304", "overline": "\u0304",
    "vec": "\u20d7", "tilde": "\u0303", "widetilde": "\u0303", "dot": "\u0307",
    "ddot": "\u0308", "acute": "\u0301", "grave": "\u0300", "check": "\u030c",
    "breve": "\u0306", "mathring": "\u030a",
}

DELIMITERS = {
    "(": "(", ")": ")", "[": "[", "]": "]", "\\{": "{", "\\}": "}",
    "|": "|", "\\|": "‖", "\\langle": "⟨", "\\rangle": "⟩",
    "\\lceil": "⌈", "\\rceil": "⌉", "\\lfloor": "⌊", "\\rfloor": "⌋",
    "/": "/", ".": "", "\\vert": "|", "\\Vert": "‖",
}

ENV_DELIMS = {
    "matrix": ("", ""), "pmatrix": ("(", ")"), "bmatrix": ("[", "]"),
    "Bmatrix": ("{", "}"), "vmatrix": ("|", "|"), "Vmatrix": ("‖", "‖"),
    "smallmatrix": ("", ""), "cases": ("{", ""), "array": ("", ""),
}

_LINE_ENVS = {"aligned", "align", "align*", "alignat", "alignat*", "gather",
              "gather*", "gathered", "split", "eqnarray", "multline"}

_TOKEN = re.compile(
    r"\\begin\s*\{\s*([A-Za-z*]+)\s*\}"
    r"|\\end\s*\{\s*([A-Za-z*]+)\s*\}"
    r"|(\\[A-Za-z]+)"
    r"|(\\.)"
    r"|(\s+)"
    r"|(.)",
    re.S,
)


def _tokenize(tex):
    out = []
    for match in _TOKEN.finditer(tex or ""):
        begin, end, command, escaped, space, char = match.groups()
        if begin is not None:
            out.append(("begin", begin))
        elif end is not None:
            out.append(("end", end))
        elif command is not None:
            out.append(("cmd", command[1:]))
        elif escaped is not None:
            out.append(("esc", escaped[1:]))
        elif space is not None:
            out.append(("space", " "))
        else:
            out.append(("char", char))
    return out


# ---------------------------------------------------------------- parsing ---

def parse(tex):
    """LaTeX source to a node list. Never raises."""
    try:
        nodes, _ = _sequence(_tokenize(tex), 0)
        return nodes
    except Exception:  # noqa: BLE001 - a formula must not take the window with it
        return [("mk", esc(tex or ""))]


def _sequence(tokens, index, stop=None):
    """Nodes until a closing brace, an \\end, or the end of the tokens."""
    nodes = []
    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "char" and value == "}":
            return _tidy(nodes), (index + 1) if stop == "}" else index
        if kind == "end":
            return _tidy(nodes), index

        node, index = _atom(tokens, index)
        if node is None:
            continue
        node, index = _scripts(tokens, index, node)
        nodes.append(node)
    return _tidy(nodes), index


def _tidy(nodes):
    """Drop the source's own spaces where our spacing rules take over.

    TeX ignores whitespace in maths, and we insert thin spaces around
    operators ourselves — keeping both is how `a + b` ends up airy.
    """
    out = []
    for node in nodes:
        blank = node[0] == "mk" and node[1] == " "
        if blank and (not out or out[-1][0] == "op" or
                      (out[-1][0] == "mk" and out[-1][1] == " ")):
            continue
        if node[0] == "op" and out and out[-1][0] == "mk" and out[-1][1] == " ":
            out.pop()
        out.append(node)
    while out and out[-1][0] == "mk" and out[-1][1] == " ":
        out.pop()
    return out


def _atom(tokens, index):
    kind, value = tokens[index]

    if kind == "space":
        return ("mk", " "), index + 1

    if kind == "char":
        if value == "{":
            inner, index = _sequence(tokens, index + 1, stop="}")
            return ("group", inner), index
        if value in "^_":
            # A script with nothing to attach to; treat the mark as text.
            return ("mk", esc(value)), index + 1
        return _char_atom(tokens, index)

    if kind == "esc":
        if value == "\\":
            return ("break",), index + 1
        return ("mk", esc(ORDINARY.get(value, value))), index + 1

    if kind == "begin":
        return _environment(tokens, index + 1, value)

    if kind == "end":
        return None, index + 1

    return _command(tokens, index, value)


def _char_atom(tokens, index):
    """Letters clump into words so functions stay upright and names stay italic."""
    _, value = tokens[index]
    if value.isalpha():
        word = ""
        while index < len(tokens) and tokens[index][0] == "char" and tokens[index][1].isalpha():
            word += tokens[index][1]
            index += 1
        # `sin x` is a function, `sin` inside `nsinx` is not worth guessing at.
        if word in FUNCTIONS:
            return ("mk", esc(word)), index
        if len(word) == 1:
            return ("mk", f"<i>{esc(word)}</i>"), index
        for name in sorted(FUNCTIONS, key=len, reverse=True):
            if word.startswith(name):
                rest = word[len(name):]
                tail = f"<i>{esc(rest)}</i>" if rest else ""
                return ("mk", esc(name) + tail), index
        return ("mk", f"<i>{esc(word)}</i>"), index

    if value.isdigit():
        number = ""
        while index < len(tokens) and tokens[index][0] == "char" and (
            tokens[index][1].isdigit() or (tokens[index][1] == "." and number)
        ):
            number += tokens[index][1]
            index += 1
        return ("mk", esc(number)), index

    if value in RELATIONS:
        return ("op", RELATIONS[value], "rel"), index + 1
    if value in BINARIES:
        return ("op", BINARIES[value], "bin"), index + 1
    if value in "([{":
        return ("op", value, "open"), index + 1
    if value in ")]}":
        return ("op", value, "close"), index + 1
    if value in ",;":
        return ("op", value, "punct"), index + 1
    if value == "'":
        return ("mk", "′"), index + 1
    return ("mk", esc(value)), index + 1


def _command(tokens, index, name):
    index += 1

    if name == "frac" or name == "dfrac" or name == "tfrac" or name == "cfrac":
        numerator, index = _argument(tokens, index)
        denominator, index = _argument(tokens, index)
        return ("frac", numerator, denominator), index

    if name == "binom" or name == "dbinom":
        top, index = _argument(tokens, index)
        bottom, index = _argument(tokens, index)
        return ("matrix", [[top], [bottom]], "(", ")", "matrix"), index

    if name == "sqrt":
        degree = None
        if index < len(tokens) and tokens[index] == ("char", "["):
            degree, index = _bracket_argument(tokens, index + 1)
        body, index = _argument(tokens, index)
        return ("sqrt", degree, body), index

    if name in ("text", "textrm", "mathrm", "mathsf", "mathtt", "operatorname",
                "textnormal", "textsf", "texttt", "mbox", "hbox"):
        body, index = _raw_argument(tokens, index)
        return ("mk", esc(body)), index

    if name in ("textbf", "mathbf", "bm", "boldsymbol", "pmb"):
        body, index = _argument(tokens, index)
        return ("style", "b", body), index

    if name in ("textit", "mathit", "emph", "mathcal", "mathscr", "mathfrak"):
        body, index = _argument(tokens, index)
        return ("style", "i", body), index

    if name == "mathbb":
        body, index = _raw_argument(tokens, index)
        return ("mk", esc("".join(BLACKBOARD.get(ch, ch) for ch in body))), index

    if name in ACCENTS:
        body, index = _argument(tokens, index)
        return ("accent", ACCENTS[name], body), index

    if name == "left":
        opener, index = _delimiter(tokens, index)
        inner, closer, index = _fenced(tokens, index)
        return ("fenced", opener, inner, closer), index

    if name == "right":
        # Unbalanced: render it as the delimiter it names and carry on.
        delimiter, index = _delimiter(tokens, index)
        return (("op", delimiter, "close") if delimiter else None), index

    if name in ("big", "bigl", "bigr", "Big", "Bigl", "Bigr",
                "bigg", "biggl", "biggr", "Bigg", "Biggl", "Biggr"):
        delimiter, index = _delimiter(tokens, index)
        return (("op", delimiter, "open") if delimiter else None), index

    if name in ("displaystyle", "textstyle", "scriptstyle", "limits",
                "nolimits", "notag", "nonumber", "protect", "!"):
        return None, index

    if name in ("hspace", "vspace", "label", "tag", "ref"):
        _, index = _raw_argument(tokens, index)
        return None, index

    if name == "boxed" or name == "underline" or name == "mathord":
        body, index = _argument(tokens, index)
        return ("group", body), index

    if name in BIG_OPS:
        return ("bigop", BIG_OPS[name], name in ("lim", "limsup", "liminf")), index
    if name in INTEGRALS:
        return ("mk", big_symbol(INTEGRALS[name])), index
    if name in GREEK:
        return ("mk", esc(GREEK[name])), index
    if name in RELATIONS:
        return ("op", RELATIONS[name], "rel"), index
    if name in BINARIES:
        return ("op", BINARIES[name], "bin"), index
    if name in ORDINARY:
        return ("mk", esc(ORDINARY[name])), index
    if name in FUNCTIONS:
        return ("mk", esc(name)), index

    # Unknown: show the name rather than swallowing it silently.
    return ("mk", f"<i>{esc(name)}</i>"), index


def _fenced(tokens, index):
    """Everything up to the matching \\right, and the delimiter that closed it."""
    nodes = []
    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "cmd" and value == "right":
            closer, index = _delimiter(tokens, index + 1)
            return _tidy(nodes), closer, index
        if kind == "char" and value == "}":
            break  # the group ended first; let the caller close it
        if kind == "end":
            break
        node, index = _atom(tokens, index)
        if node is None:
            continue
        node, index = _scripts(tokens, index, node)
        nodes.append(node)
    return _tidy(nodes), "", index


def _delimiter(tokens, index):
    if index >= len(tokens):
        return "", index
    kind, value = tokens[index]
    if kind == "cmd":
        return DELIMITERS.get("\\" + value, value), index + 1
    if kind == "esc":
        return DELIMITERS.get("\\" + value, value), index + 1
    return DELIMITERS.get(value, value), index + 1


def _argument(tokens, index):
    """One brace group, or the single token that follows."""
    while index < len(tokens) and tokens[index][0] == "space":
        index += 1
    if index >= len(tokens):
        return [], index
    if tokens[index] == ("char", "{"):
        return _sequence(tokens, index + 1, stop="}")
    node, index = _atom(tokens, index)
    return ([node] if node else []), index


def _bracket_argument(tokens, index):
    nodes = []
    while index < len(tokens) and tokens[index] != ("char", "]"):
        node, index = _atom(tokens, index)
        if node:
            nodes.append(node)
    return nodes, index + 1


def _raw_argument(tokens, index):
    """The literal text of a group, for \\text and friends."""
    while index < len(tokens) and tokens[index][0] == "space":
        index += 1
    if index >= len(tokens) or tokens[index] != ("char", "{"):
        if index < len(tokens):
            return tokens[index][1], index + 1
        return "", index
    index += 1
    depth, out = 1, []
    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "char" and value == "{":
            depth += 1
        elif kind == "char" and value == "}":
            depth -= 1
            if depth == 0:
                return "".join(out), index + 1
        if kind == "cmd":
            out.append(GREEK.get(value) or ORDINARY.get(value) or value)
        else:
            out.append(value)
        index += 1
    return "".join(out), index


def _scripts(tokens, index, base):
    """Attach any ^ and _ that follow, in either order."""
    superscript = subscript = None
    while index < len(tokens) and tokens[index][0] == "char" and tokens[index][1] in "^_":
        mark = tokens[index][1]
        body, index = _argument(tokens, index + 1)
        if mark == "^":
            superscript = body
        else:
            subscript = body
    if superscript is None and subscript is None:
        return base, index
    limits = base[0] == "bigop"
    return ("script", base, superscript, subscript, limits), index


def _environment(tokens, index, name):
    rows, row, cell = [], [], []
    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "end":
            index += 1
            break
        if kind == "char" and value == "&":
            row.append(_tidy(cell))
            cell = []
            index += 1
            continue
        if kind == "esc" and value == "\\":
            row.append(_tidy(cell))
            rows.append(row)
            row, cell = [], []
            index += 1
            continue
        node, index = _atom(tokens, index)
        if node is None:
            continue
        node, index = _scripts(tokens, index, node)
        cell.append(node)
    row.append(_tidy(cell))
    rows.append(row)
    rows = [r for r in rows if any(c for c in r)]
    for row in rows:
        for cell in row[1:]:
            if cell and cell[0][0] == "op":
                cell.insert(0, ("mk", ""))

    if name in _LINE_ENVS:
        return ("matrix", rows, "", "", "lines"), index
    left, right = ENV_DELIMS.get(name, ("", ""))
    style = "cases" if name == "cases" else "matrix"
    return ("matrix", rows, left, right, style), index


# -------------------------------------------------------------- rendering ---

_SPACED = {"rel", "bin"}


def markup(nodes):
    """The whole tree on one line, as Pango markup."""
    try:
        return _markup(nodes)
    except Exception:  # noqa: BLE001
        return ""


def _markup(nodes):
    out = []
    for position, node in enumerate(nodes):
        kind = node[0]
        if kind == "mk":
            out.append(node[1])
        elif kind == "op":
            _, symbol, role = node
            text = esc(symbol)
            if role in _SPACED and _binds(nodes, position):
                text = f"\u2009{text}\u2009"
            out.append(text)
        elif kind == "group":
            out.append(_markup(node[1]))
        elif kind == "style":
            inner = _markup(node[2])
            if node[1] == "b":
                inner = inner.replace("<i>", "").replace("</i>", "")
            elif inner.startswith("<i>") and inner.endswith("</i>"):
                out.append(inner)
                continue
            out.append(f"<{node[1]}>{inner}</{node[1]}>")
        elif kind == "accent":
            inner = _markup(node[2])
            if inner.endswith(">") and "</" in inner:
                head, _, tail = inner.rpartition("</")
                inner = f"{head}{node[1]}</{tail}"
            else:
                inner += node[1]
            out.append(inner)
        elif kind == "bigop":
            out.append(big_symbol(node[1]))
        elif kind == "frac":
            out.append(f"{_wrapped(node[1])}\u2044{_wrapped(node[2])}")
        elif kind == "sqrt":
            root = "√"
            if node[1]:
                root = {"2": "√", "3": "∛", "4": "∜"}.get(_plain(node[1]), "√")
            out.append(f"{root}{_wrapped(node[2], always=len(node[2]) > 1)}")
        elif kind == "script":
            out.append(_script_markup(node))
        elif kind == "fenced":
            out.append(f"{esc(node[1])}{_markup(node[2])}{esc(node[3])}")
        elif kind == "matrix":
            out.append(_matrix_markup(node))
        elif kind == "break":
            out.append("  ")
    return "".join(out)


def _binds(nodes, position):
    """A minus with nothing to its left is a sign, not a subtraction."""
    if position in (0, len(nodes) - 1):
        return False
    previous = nodes[position - 1]
    return not (previous[0] == "op" and previous[2] in ("open", "bin", "rel", "punct"))


def big_symbol(symbol):
    """Sums and integrals are drawn larger than the terms they gather up."""
    if len(symbol) == 1 and not symbol.isascii():
        return f'<span size="135%">{esc(symbol)}</span>'
    return esc(symbol)


def _script_markup(node):
    _, base, superscript, subscript, _limits = node
    text = _markup([base])
    if subscript:
        text += f"<sub>{_markup(subscript)}</sub>"
    if superscript:
        text += f"<sup>{_markup(superscript)}</sup>"
    return text


def _matrix_markup(node):
    _, rows, left, right, style = node
    if style == "lines":
        return "  ".join(" ".join(_markup(cell) for cell in row) for row in rows)
    body = "; ".join(", ".join(_markup(cell) for cell in row) for row in rows)
    return f"{esc(left)}{body}{esc(right)}"


def _wrapped(nodes, always=False):
    """Parenthesise a part that would otherwise re-associate on one line."""
    text = _markup(nodes)
    if not always and len(nodes) <= 1 and not any(n[0] == "op" for n in nodes):
        return text
    if len(nodes) == 1 and nodes[0][0] in ("group", "frac", "script", "matrix", "fenced"):
        return text
    return f"({text})"


def _plain(nodes):
    """The bare text of a small node list, for degree lookups."""
    return strip_tags(_markup(nodes)).strip()


def strip_tags(text):
    """Markup back to plain text, for tooltips and last-resort fallbacks."""
    return re.sub(r"<[^>]+>", "", text or "")


def is_stacked(node):
    """True when a node genuinely needs two dimensions to look right."""
    kind = node[0]
    if kind in ("frac", "sqrt", "matrix"):
        return True
    if kind == "fenced":
        # Only worth drawing the delimiters when they have to grow.
        return any(is_stacked(child) for child in node[2])
    if kind == "script":
        if node[4] and (node[2] or node[3]):
            return True
        return is_stacked(node[1])
    if kind == "group":
        return any(is_stacked(child) for child in node[1])
    if kind == "style":
        return any(is_stacked(child) for child in node[2])
    return False


def has_stacked(nodes):
    return any(is_stacked(node) for node in nodes)


def inline_markup(tex):
    """Straight from LaTeX source to markup, for maths inside a sentence."""
    return markup(parse(tex))


def esc(text):
    return GLib.markup_escape_text(text or "")
