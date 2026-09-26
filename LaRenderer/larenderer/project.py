"""What an open folder means: which file is the document, and what else it holds.

A single .tex file is its own document. A folder usually isn't one document
but one document *and its parts* — main.tex with \\input{chapters/intro}, a
refs.bib, a figures/ directory — and editing a part has to rebuild the whole,
the way Overleaf does it. So the first question for any file is which
document it belongs to, and it is answered here, in order:

1. A `% !TEX root = ../main.tex` line at the top says so outright. It is the
   convention TeXShop, TeXstudio and VS Code's LaTeX Workshop all read.
2. A file with its own \\documentclass is its own document.
3. Inside an open folder, anything else belongs to the folder's main document.
4. Outside one, a nearby .tex that \\inputs this file is taken as its root.

Knows no GTK, so it runs on a worker thread and in the tests as it is.
"""

from __future__ import annotations

import os
import re

TEX_EXTENSIONS = (".tex", ".ltx")

# Big enough for any source file a person writes; small enough that a stray
# log or dataset isn't loaded into an editor tab by accident.
TEXT_LIMIT = 8 * 1024 * 1024

# Places a project keeps nothing worth looking at for its main document.
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
             "_minted", ".texpadtmp", "auto"}

_ROOT_DIRECTIVE = re.compile(r"^\s*%\s*!\s*TeX\s+root\s*=\s*(.+?)\s*$", re.I | re.M)
_DOCUMENTCLASS = re.compile(r"^[^%\n]*\\document(?:class|style)\b", re.M)
_BEGIN_DOCUMENT = re.compile(r"^[^%\n]*\\begin\s*\{document\}", re.M)

# Every way a document names another file it is made of. `\subimport{dir}{file}`
# and `\import{dir}{file}` carry their directory separately.
_PULLS_IN = re.compile(
    r"^[^%\n]*?\\(input|include|subfile|bibliography|addbibresource)\*?"
    r"\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}",
    re.M,
)
_IMPORTS = re.compile(r"^[^%\n]*?\\(?:sub)?import\*?\s*\{([^}]*)\}\s*\{([^}]*)\}", re.M)
_INCLUDE_ONLY = re.compile(r"\\include\s*\{([^}]*)\}")


# ------------------------------------------------------------------ files --


def is_tex(path: str | None) -> bool:
    return bool(path) and path.lower().endswith(TEX_EXTENSIONS)


def same(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return (os.path.normcase(os.path.normpath(os.path.abspath(a)))
            == os.path.normcase(os.path.normpath(os.path.abspath(b))))


def inside(path: str | None, folder: str | None) -> bool:
    """True when `path` is `folder` or anywhere below it."""
    if not path or not folder:
        return False
    path = os.path.normcase(os.path.abspath(path))
    folder = os.path.normcase(os.path.abspath(folder))
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def read(path: str, limit: int = TEXT_LIMIT) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read(limit)


def looks_like_text(path: str) -> bool:
    """Whether a file can go in an editor tab rather than to another app.

    The extension is not asked, because a project is full of text files with
    names no list would think of — .latexmkrc, .cls, .bbx, a Makefile. A NUL
    byte in the first 8 KiB means binary; so does a sample that is neither
    UTF-8 nor mostly printable.
    """
    try:
        if os.path.getsize(path) > TEXT_LIMIT:
            return False
        with open(path, "rb") as fh:
            sample = fh.read(8192)
    except OSError:
        return False
    if b"\0" in sample:
        return False
    for trim in range(4):   # a sample can end half-way through a character
        try:
            sample[:len(sample) - trim].decode("utf-8")
            return True
        except UnicodeDecodeError:
            continue
    printable = sum(1 for b in sample if b >= 32 or b in (9, 10, 12, 13))
    return printable >= 0.95 * max(1, len(sample))


# ---------------------------------------------------------------- the root --


def root_directive(text: str) -> str | None:
    """The path a `% !TEX root = …` line names, if the file starts with one."""
    match = _ROOT_DIRECTIVE.search(text[:4000])
    return match.group(1).strip().strip('"') if match else None


def is_standalone(text: str) -> bool:
    """True when the text is a whole document rather than a part of one."""
    return bool(_DOCUMENTCLASS.search(text))


def included_dirs(text: str) -> list[str]:
    """Subdirectories \\include writes .aux files into, relative to the root.

    `\\include{chapters/intro}` makes TeX write chapters/intro.aux next to the
    main .aux, and it stops with "I can't write on file" when that directory
    is not there. The build directory has to have them made in advance.
    """
    dirs = []
    for arg in _INCLUDE_ONLY.findall(text):
        folder = os.path.dirname(arg.strip())
        if folder and not os.path.isabs(folder) and not folder.startswith(os.pardir):
            if folder not in dirs:
                dirs.append(folder)
    return dirs


def _named_files(text: str) -> list[str]:
    """Every file the text pulls in, as written — no extension added yet."""
    names = []
    for command, arg in _PULLS_IN.findall(text):
        parts = arg.split(",") if command == "bibliography" else [arg]
        names.extend(p.strip() for p in parts if p.strip())
    for folder, name in _IMPORTS.findall(text):
        names.append(os.path.join(folder.strip(), name.strip()))
    return names


def _pulls_in(root: str, text: str, target: str) -> bool:
    """True when the document at `root` names `target` as one of its parts."""
    here = os.path.dirname(os.path.abspath(root))
    target_abs = os.path.normcase(os.path.abspath(target))
    stem, _ = os.path.splitext(target_abs)
    base = os.path.basename(stem)
    loose = False
    for name in _named_files(text):
        candidate = os.path.normcase(os.path.normpath(os.path.join(here, name)))
        if candidate in (target_abs, stem):
            return True
        # TEXINPUTS searches the folder recursively, so \input{intro} finds
        # chapters/intro.tex. Remember a bare name match as a weaker answer.
        if os.path.basename(os.path.splitext(candidate)[0]) == base:
            loose = True
    return loose


def _tex_files(folder: str, depth: int = 0, limit: int = 400) -> list[str]:
    """The .tex files at most `depth` directories below `folder`."""
    found = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
    except OSError:
        return found
    for entry in entries:
        if len(found) >= limit:
            break
        try:
            if entry.is_file() and is_tex(entry.name):
                found.append(entry.path)
            elif (depth > 0 and entry.is_dir() and not entry.name.startswith(".")
                  and entry.name not in SKIP_DIRS):
                found.extend(_tex_files(entry.path, depth - 1, limit - len(found)))
        except OSError:
            continue
    return found


def find_main(folder: str) -> str | None:
    """The folder's main document: the .tex a person would call "the paper".

    Overleaf names it main.tex unless told otherwise, and so do most
    templates, so that name wins. After it: a file named after the folder,
    one at the top rather than in a subfolder, one that pulls in more parts.
    Only whole documents — \\documentclass and \\begin{document} — qualify.
    """
    if not folder or not os.path.isdir(folder):
        return None
    folder_name = os.path.basename(os.path.normpath(folder)).lower()
    best, best_score = None, None
    for path in _tex_files(folder, depth=1):
        try:
            text = read(path, 256 * 1024)
        except OSError:
            continue
        if not (_DOCUMENTCLASS.search(text) and _BEGIN_DOCUMENT.search(text)):
            continue
        name = os.path.basename(path).lower()
        stem = os.path.splitext(name)[0]
        score = 0
        if name == "main.tex":
            score += 100
        if stem == folder_name:
            score += 40
        if os.path.dirname(path) == os.path.normpath(folder):
            score += 30
        score += min(20, 2 * len(_named_files(text)))
        if "\\title" in text:
            score += 5
        key = (score, -len(path))
        if best_score is None or key > best_score:
            best, best_score = path, key
    return best


def find_including_root(path: str, levels: int = 2) -> str | None:
    """A whole document near `path` that pulls it in, for files opened alone.

    Looks in the file's own folder and up to `levels` above it, since a
    chapter is usually one level below the main file and a figure's .tex two.
    Stops at the home directory rather than trawling everything above it.
    """
    if not path:
        return None
    home = os.path.normcase(os.path.expanduser("~"))
    folder = os.path.dirname(os.path.abspath(path))
    loose = None
    for _ in range(levels + 1):
        for candidate in _tex_files(folder, depth=0, limit=60):
            if same(candidate, path):
                continue
            try:
                text = read(candidate, 256 * 1024)
            except OSError:
                continue
            if not is_standalone(text):
                continue
            here = os.path.dirname(os.path.abspath(candidate))
            target_abs = os.path.normcase(os.path.abspath(path))
            exact = any(
                os.path.normcase(os.path.normpath(os.path.join(here, n))) in
                (target_abs, os.path.splitext(target_abs)[0])
                for n in _named_files(text))
            if exact:
                return candidate
            if loose is None and _pulls_in(candidate, text, path):
                loose = candidate
        parent = os.path.dirname(folder)
        if parent == folder or os.path.normcase(folder) == home:
            break
        folder = parent
    return loose


# --------------------------------------------------------------- overleaf --

_OVERLEAF_ID = re.compile(r"\b([0-9a-f]{24})\b")


def overleaf_git_url(link: str) -> tuple[str, str] | None:
    """The git address of an Overleaf project, from however it was pasted.

    Accepts the editor's address (overleaf.com/project/<id>), the git one
    (git.overleaf.com/<id>), or the bare 24-character id. A self-hosted
    Overleaf's git address (…/git/<id>) is used as it is. Returns
    (git_url, project_id), or None when nothing in it looks like a project.
    """
    link = (link or "").strip().rstrip("/")
    if not link:
        return None
    match = _OVERLEAF_ID.search(link.lower())
    if not match:
        return None
    project_id = match.group(1)
    if "://" in link and "/git/" in link and "overleaf.com" not in link:
        return link, project_id
    return f"https://git.overleaf.com/{project_id}", project_id


def git_top(path: str | None) -> str | None:
    """The working tree around `path`, found by looking for .git — no git run."""
    if not path:
        return None
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    folder = os.path.abspath(folder)
    while True:
        if os.path.exists(os.path.join(folder, ".git")):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def remote_urls(top: str) -> list[str]:
    """The remote URLs in a repository's own config, read without git."""
    config = os.path.join(top, ".git", "config")
    try:
        text = read(config, 256 * 1024)
    except OSError:
        return []
    return re.findall(r"^\s*url\s*=\s*(\S+)\s*$", text, re.M)


def overleaf_remote(path: str | None) -> str | None:
    """The Overleaf address the repository around `path` syncs with, if any."""
    top = git_top(path)
    if not top:
        return None
    for url in remote_urls(top):
        if "overleaf" in url.lower():
            return url
    return None
