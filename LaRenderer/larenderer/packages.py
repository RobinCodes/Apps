"""The curated set of dominant LaTeX packages, and what provides them.

Two jobs. First, a catalogue for the Packages menu: the ~30 packages that
account for nearly every real document, each with the `\\usepackage` line to
insert. Second, working out what to install when one is missing.

That second job is done properly rather than guessed. TeX Live ships its own
package database at /usr/share/tlpkg/texlive.tlpdb, which maps every file to a
TeX Live package and every package to a collection; Arch's texlive-* split
follows those collections exactly. So a missing siunitx.sty resolves to
collection-mathscience resolves to `pacman -S texlive-mathscience` — which is
not the answer you would guess, and is the right one.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

TLPDB = "/usr/share/tlpkg/texlive.tlpdb"

# TeX Live collection -> Arch package. Arch's split is one package per
# collection, with the collection- prefix swapped for texlive-.
_ARCH_PREFIX = "texlive-"


@dataclass(frozen=True)
class Package:
    name: str
    sty: str
    group: str
    blurb: str
    options: str = ""      # inserted as \usepackage[options]{name}
    after: str = ""        # a companion line, e.g. \geometry{...}

    @property
    def directive(self) -> str:
        opts = f"[{self.options}]" if self.options else ""
        line = f"\\usepackage{opts}{{{self.name}}}"
        return f"{line}\n{self.after}" if self.after else line


# Ordered by group, then by how often you actually reach for them.
CATALOGUE: list[Package] = [
    # -- Maths ------------------------------------------------------------
    Package("amsmath", "amsmath.sty", "Maths",
            "align, gather, cases — the standard maths environments"),
    Package("amssymb", "amssymb.sty", "Maths",
            "\\mathbb, \\mathfrak and the rest of the AMS symbols"),
    Package("amsthm", "amsthm.sty", "Maths",
            "\\newtheorem for theorems, lemmas and proofs"),
    Package("mathtools", "mathtools.sty", "Maths",
            "amsmath's rough edges fixed; \\coloneqq, \\DeclarePairedDelimiter"),
    Package("bm", "bm.sty", "Maths",
            "\\bm{v} — bold symbols that stay italic, unlike \\mathbf"),
    Package("braket", "braket.sty", "Maths",
            "\\ket{\\psi}, \\braket{a|b} — Dirac notation that sizes itself"),
    Package("nicematrix", "nicematrix.sty", "Maths",
            "Matrices with rules, dotted lines and labelled rows"),
    Package("thmtools", "thmtools.sty", "Maths",
            "\\declaretheorem, and a \\listoftheorems — amsthm, organised"),
    Package("cancel", "cancel.sty", "Maths",
            "\\cancel and \\cancelto — strike terms out of a derivation"),

    # -- Science and engineering ------------------------------------------
    Package("siunitx", "siunitx.sty", "Science & engineering",
            "\\qty{9.8}{m/s^2} — numbers and units, spaced and aligned correctly"),
    Package("physics", "physics.sty", "Science & engineering",
            "\\dv, \\pdv, \\bra, \\ket — derivative and Dirac shorthands"),
    Package("mhchem", "mhchem.sty", "Science & engineering",
            "\\ce{H2SO4}, \\ce{CO2 + C -> 2CO} — chemical formulae and equations",
            options="version=4"),
    Package("chemfig", "chemfig.sty", "Science & engineering",
            "Structural formulae — benzene rings and skeletal diagrams"),
    Package("circuitikz", "circuitikz.sty", "Science & engineering",
            "Circuit diagrams, drawn in TeX the way TikZ draws everything else"),

    # -- Graphics ---------------------------------------------------------
    Package("graphicx", "graphicx.sty", "Graphics",
            "\\includegraphics — put a picture in the document"),
    Package("tikz", "tikz.sty", "Graphics",
            "Draw diagrams in TeX itself", after="\\usetikzlibrary{arrows.meta,positioning}"),
    Package("pgfplots", "pgfplots.sty", "Graphics",
            "Plots and charts from data, built on TikZ", after="\\pgfplotsset{compat=1.18}"),
    Package("float", "float.sty", "Graphics",
            "The [H] placement specifier — put the figure *here*"),
    Package("subcaption", "subcaption.sty", "Graphics",
            "Side-by-side subfigures, each with its own caption"),
    Package("caption", "caption.sty", "Graphics",
            "Control how captions look", options="font=small,labelfont=bf"),
    Package("tikz-cd", "tikz-cd.sty", "Graphics",
            "Commutative diagrams — arrows between objects in a grid"),
    Package("wrapfig", "wrapfig.sty", "Graphics",
            "Let text flow around a figure instead of breaking for it"),

    # -- Layout -----------------------------------------------------------
    Package("geometry", "geometry.sty", "Layout",
            "Page margins, without counting from the paper edge",
            options="margin=1in"),
    Package("fancyhdr", "fancyhdr.sty", "Layout",
            "Custom headers and footers", after="\\pagestyle{fancy}"),
    Package("titlesec", "titlesec.sty", "Layout",
            "Change how section headings are set"),
    Package("multicol", "multicol.sty", "Layout",
            "The multicols environment — balanced columns mid-page"),
    Package("parskip", "parskip.sty", "Layout",
            "Blank line between paragraphs instead of a first-line indent"),
    Package("microtype", "microtype.sty", "Layout",
            "Micro-typography. Costs nothing, improves every page"),

    # -- Tables and lists -------------------------------------------------
    Package("booktabs", "booktabs.sty", "Tables & lists",
            "\\toprule, \\midrule, \\bottomrule — tables that look professional"),
    Package("tabularx", "tabularx.sty", "Tables & lists",
            "Tables that stretch a column to a fixed total width"),
    Package("longtable", "longtable.sty", "Tables & lists",
            "Tables that break across pages"),
    Package("enumitem", "enumitem.sty", "Tables & lists",
            "Control list spacing and labels: [label=(\\alph*), noitemsep]"),

    # -- Text -------------------------------------------------------------
    Package("xcolor", "xcolor.sty", "Text",
            "Named and mixed colours", options="dvipsnames"),
    Package("hyperref", "hyperref.sty", "Text",
            "Clickable references, and PDF bookmarks",
            options="colorlinks=true,linkcolor=blue,citecolor=teal,urlcolor=magenta"),
    Package("cleveref", "cleveref.sty", "Text",
            "\\cref writes \"Figure 3\" for you — load it after hyperref"),
    Package("csquotes", "csquotes.sty", "Text",
            "\\enquote{} — quotation marks that follow the language"),
    Package("url", "url.sty", "Text",
            "\\url{} — line-breakable URLs"),

    # -- Code -------------------------------------------------------------
    Package("listings", "listings.sty", "Code",
            "Source code with keyword highlighting",
            after="\\lstset{basicstyle=\\ttfamily\\small,breaklines=true}"),
    Package("tcolorbox", "tcolorbox.sty", "Code",
            "Coloured, breakable boxes for notes and examples"),
    Package("algorithm2e", "algorithm2e.sty", "Code",
            "Algorithms as numbered, ruled pseudocode blocks",
            options="ruled,vlined,linesnumbered"),
    Package("algpseudocode", "algpseudocode.sty", "Code",
            "The other pseudocode style — \\State, \\While, \\EndWhile",
            after="\\usepackage{algorithm}"),
    Package("minted", "minted.sty", "Code",
            "Code highlighted by Pygments. Needs shell escape, and python-pygments"),

    # -- References -------------------------------------------------------
    Package("biblatex", "biblatex.sty", "References",
            "The modern bibliography package", options="backend=biber,style=numeric",
            after="\\addbibresource{references.bib}"),
    Package("natbib", "natbib.sty", "References",
            "Author–year citations with the classic BibTeX flow",
            options="numbers,square"),

    # -- Language ---------------------------------------------------------
    Package("babel", "babel.sty", "Language",
            "Hyphenation and names in your language", options="english"),
    Package("fontspec", "fontspec.sty", "Language",
            "System fonts by name — needs XeLaTeX or LuaLaTeX"),
]

GROUPS = ["Maths", "Science & engineering", "Graphics", "Layout",
          "Tables & lists", "Text", "Code", "References", "Language"]

BY_NAME = {p.name: p for p in CATALOGUE}


# --------------------------------------------------------------------------
# what is actually installed
# --------------------------------------------------------------------------

_installed_cache: dict[str, bool] = {}


def installed(sty_files: list[str]) -> dict[str, bool]:
    """Ask kpsewhich about a batch of .sty files in one call."""
    unknown = [s for s in sty_files if s not in _installed_cache]
    if unknown and shutil.which("kpsewhich"):
        try:
            out = subprocess.run(
                ["kpsewhich"] + unknown,
                capture_output=True, text=True, timeout=20, errors="replace",
            ).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        found = {os.path.basename(line.strip()) for line in out.splitlines() if line.strip()}
        for sty in unknown:
            _installed_cache[sty] = sty in found
    for sty in sty_files:
        _installed_cache.setdefault(sty, False)
    return {sty: _installed_cache[sty] for sty in sty_files}


def catalogue_status() -> dict[str, bool]:
    """Installed-or-not for every package in the catalogue, keyed by name."""
    status = installed([p.sty for p in CATALOGUE])
    return {p.name: status[p.sty] for p in CATALOGUE}


def refresh() -> None:
    _installed_cache.clear()
    _tlpdb.clear()


# --------------------------------------------------------------------------
# resolving a missing file to something you can install
# --------------------------------------------------------------------------

_tlpdb: dict[str, str] = {}   # basename -> collection


def _load_tlpdb() -> dict[str, str]:
    """basename -> TeX Live collection, read once from the distribution's own db."""
    if _tlpdb:
        return _tlpdb
    try:
        with open(TLPDB, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        _tlpdb["\x00missing"] = ""
        return _tlpdb

    file_owner: dict[str, str] = {}
    collection_of: dict[str, str] = {}

    for block in raw.split("\n\n"):
        match = re.match(r"name (\S+)", block)
        if not match:
            continue
        name = match.group(1)
        if re.search(r"^category Collection", block, re.M):
            for dep in re.findall(r"^depend (\S+)", block, re.M):
                collection_of.setdefault(dep, name)
            continue
        for path in re.findall(r"^ (texmf-dist/tex/\S+)", block, re.M):
            base = path.rsplit("/", 1)[-1]
            # Prefer the real package over its -dev preview, which ships the
            # same file names and would otherwise win on ordering alone.
            if base not in file_owner or (
                file_owner[base].endswith("-dev") and not name.endswith("-dev")
            ):
                file_owner[base] = name

    for base, owner in file_owner.items():
        collection = collection_of.get(owner)
        if collection:
            _tlpdb[base] = collection
    _tlpdb.setdefault("\x00loaded", "")
    return _tlpdb


def arch_package_for(sty: str) -> str:
    """The pacman package that ships this .sty, or "" when we can't tell."""
    collection = _load_tlpdb().get(sty)
    if not collection or not collection.startswith("collection-"):
        return ""
    return _ARCH_PREFIX + collection[len("collection-"):]


def install_hint(missing_sty: list[str]) -> str:
    """A single pacman command covering everything missing."""
    packages = []
    for sty in missing_sty:
        arch = arch_package_for(sty)
        if arch and arch not in packages:
            packages.append(arch)
    if not packages:
        return ""
    return "sudo pacman -S " + " ".join(sorted(packages))


def describe_missing(name: str) -> tuple[str, str]:
    """(what is missing, how to get it) for a package name from a log."""
    sty = BY_NAME[name].sty if name in BY_NAME else name + ".sty"
    arch = arch_package_for(sty)
    if arch:
        return f"{name} is not installed", f"sudo pacman -S {arch}"
    return f"{name} is not installed", "It is not part of the TeX Live packages Arch ships."


# --------------------------------------------------------------------------
# what a document already loads
# --------------------------------------------------------------------------

_USEPACKAGE = re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_DOCUMENTCLASS = re.compile(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def loaded_by(text: str) -> set[str]:
    """Every package name the document currently loads."""
    names: set[str] = set()
    for match in _USEPACKAGE.finditer(text):
        for part in match.group(1).split(","):
            part = part.strip()
            if part:
                names.add(part)
    return names


def document_class(text: str) -> str:
    match = _DOCUMENTCLASS.search(text)
    return match.group(1).strip() if match else ""
