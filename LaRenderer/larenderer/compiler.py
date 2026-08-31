"""Every TeX invocation, returning dataclasses. Knows no GTK.

The document is never compiled in place. The buffer is written to a build
directory under ~/.cache/larenderer and the engine runs *there*, with TEXINPUTS
pointing back at the source folder so `\\input`, `\\includegraphics` and friends
still resolve. Nothing — no .aux, no .log, no .pdf — is written next to the
user's .tex unless they explicitly export.

Because the buffer is written verbatim as the jobname.tex the engine reads,
line numbers in the log are the editor's line numbers. No mapping needed.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from . import winenv

CACHE = winenv.cache_home("larenderer")
BUILD_ROOT = os.path.join(CACHE, "build")

ENGINES = ["pdflatex", "xelatex", "lualatex"]

# poppler ships with the GTK runtime; synctex comes from the TeX distribution
# (MiKTeX puts it in %LOCALAPPDATA%, TeX Live under C:	exlive). Resolved once
# here so a missing one is a clean "feature off" rather than an exception in a
# worker thread.
PDFINFO = winenv.which("pdfinfo") or "pdfinfo"
PDFTOPPM = winenv.which("pdftoppm") or "pdftoppm"
SYNCTEX = winenv.which("synctex")

MAX_PASSES = 3          # a rerun costs ~0.2s; three is enough for any real doc
MAX_LOG_CHARS = 400_000
DEFAULT_TIMEOUT = 40    # \def\x{\x}\x otherwise sits there forever


@dataclass
class Message:
    """One line the engine complained about."""

    severity: str          # "error" | "warning" | "badbox"
    text: str
    line: int = 0          # 0 when the engine didn't say
    file: str = ""         # "" means the document being edited
    package: str = ""      # the package that raised it, when it named itself

    @property
    def is_current_file(self) -> bool:
        return not self.file


@dataclass
class Result:
    ok: bool = False
    pdf: str | None = None
    pages: int = 0
    page_pt: tuple[float, float] = (612.0, 792.0)
    messages: list[Message] = field(default_factory=list)
    log: str = ""
    seconds: float = 0.0
    engine: str = ""
    passes: int = 0
    synctex: str | None = None
    missing_packages: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    cancelled: bool = False
    failure: str = ""      # harness-level: engine absent, timeout, unwritable dir
    # True when this run produced no PDF and `pdf` is the one left behind by an
    # earlier compile. Worth showing — a blank pane while you fix a typo is
    # worse than a slightly old page — but the status strip must say so.
    stale_pdf: bool = False

    @property
    def errors(self) -> list[Message]:
        return [m for m in self.messages if m.severity == "error"]

    @property
    def warnings(self) -> list[Message]:
        return [m for m in self.messages if m.severity == "warning"]

    @property
    def badboxes(self) -> list[Message]:
        return [m for m in self.messages if m.severity == "badbox"]


def available_engines() -> list[str]:
    return [e for e in ENGINES if winenv.which(e)]


def have(tool: str) -> bool:
    return winenv.which(tool) is not None


# --------------------------------------------------------------------------
# where the build happens
# --------------------------------------------------------------------------


def build_dir(source_path: str | None) -> str:
    """A stable private directory per document, so aux files survive edits.

    Keyed by the absolute source path, so reopening a file reuses its .aux and
    cross-references resolve on the first compile rather than the second.
    """
    key = os.path.abspath(source_path) if source_path else "\x00untitled"
    digest = hashlib.sha1(key.encode("utf-8", "surrogateescape")).hexdigest()[:16]
    path = os.path.join(BUILD_ROOT, digest)
    os.makedirs(path, exist_ok=True)
    return path


def job_name(source_path: str | None) -> str:
    if not source_path:
        return "untitled"
    stem = os.path.splitext(os.path.basename(source_path))[0]
    # TeX chokes on spaces and shell-active characters in a jobname.
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._-")
    return safe or "untitled"


def clear_build(source_path: str | None) -> None:
    shutil.rmtree(build_dir(source_path), ignore_errors=True)


def _environment(source_dir: str) -> dict:
    env = dict(os.environ)
    # The build directory must come first. kpathsea searches TEXINPUTS ahead of
    # the working directory, so without the leading "." the engine would find
    # the *saved* file in the source folder and quietly typeset that instead of
    # the buffer we just wrote here — the preview would lag the disk, not the
    # editor. Recursive "//" on the source tree so figures/ and chapters/ still
    # resolve; the trailing separator keeps the distribution's own path.
    # kpathsea splits these on the platform's path separator — ';' on
    # Windows, ':' everywhere else. Hardcoding ':' on Windows turns the whole
    # variable into one nonexistent directory, and every \input silently
    # stops resolving.
    sep = os.pathsep
    tree = f".{sep}{source_dir}//{sep}" if source_dir else f".{sep}"
    for var in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS"):
        env[var] = tree + env.get(var, "")
    # TeX wraps log lines at 79 columns by default, which splits file names and
    # messages mid-word and makes the log unparseable. Widen it.
    env["max_print_line"] = "8192"
    env["error_line"] = "254"
    env["half_error_line"] = "238"
    env["TEXMFOUTPUT"] = ""
    return env


class Job:
    """A compile in flight. Cancellable, because the user keeps typing."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def _run(self, argv, cwd, env, timeout) -> tuple[int, str]:
        if self.cancelled:
            return -1, ""
        try:
            self._proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                creationflags=winenv.NO_WINDOW,
            )
        except OSError as exc:
            return -1, str(exc)
        try:
            out, _ = self._proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            out, _ = self._proc.communicate()
            return -2, out or ""
        return self._proc.returncode, out or ""


def compile_document(
    text: str,
    source_path: str | None,
    engine: str = "pdflatex",
    shell_escape: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    bibliography: bool = True,
    job: Job | None = None,
) -> Result:
    """Write the buffer to its build directory and run the engine over it."""
    job = job or Job()
    started = time.monotonic()
    result = Result(engine=engine)

    if not winenv.which(engine):
        result.failure = f"{engine} is not installed on this machine."
        return result

    bdir = build_dir(source_path)
    name = job_name(source_path)
    tex = os.path.join(bdir, name + ".tex")
    source_dir = os.path.dirname(os.path.abspath(source_path)) if source_path else ""

    try:
        with open(tex, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        result.failure = f"Cannot write the build copy: {exc}"
        return result

    pdf_path = os.path.join(bdir, name + ".pdf")
    try:
        stamp_before = os.path.getmtime(pdf_path)
    except OSError:
        stamp_before = None

    env = _environment(source_dir)
    argv = [
        engine,
        "-interaction=nonstopmode",   # not halt-on-error: we want every error
        "-file-line-error",
        "-synctex=1",
        "-shell-escape" if shell_escape else "-no-shell-escape",
        # An explicit relative path rather than a bare name: a name gets looked
        # up through kpathsea, a path does not.
        os.path.join(os.curdir, name + ".tex"),
    ]

    log = ""
    for attempt in range(MAX_PASSES):
        code, _ = job._run(argv, bdir, env, timeout)
        result.passes = attempt + 1
        if job.cancelled:
            result.cancelled = True
            return result
        if code == -2:
            result.failure = f"{engine} did not finish within {timeout}s and was stopped."
            result.log = _read_log(bdir, name)
            return result

        log = _read_log(bdir, name)

        # BibTeX between the first and second pass, once .aux lists citations.
        if attempt == 0 and bibliography and _needs_bibtex(bdir, name, text):
            job._run(["bibtex", name], bdir, env, timeout)
            if job.cancelled:
                result.cancelled = True
                return result
            continue

        if not _needs_rerun(log):
            break

    result.log = log[-MAX_LOG_CHARS:]
    result.messages = parse_log(log, name)
    result.missing_packages = _missing_packages(log)
    result.missing_files = _missing_files(log)
    result.seconds = time.monotonic() - started

    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
        result.pdf = pdf_path
        result.pages, result.page_pt = pdf_info(pdf_path)
        try:
            result.stale_pdf = os.path.getmtime(pdf_path) == stamp_before
        except OSError:
            result.stale_pdf = False
    synctex = os.path.join(bdir, name + ".synctex.gz")
    result.synctex = synctex if os.path.exists(synctex) else None

    # A PDF that came out despite errors is still worth showing — LaTeX
    # recovers from most mistakes and the page tells you more than the log.
    result.ok = result.pdf is not None and not result.errors and not result.stale_pdf
    return result


def _read_log(bdir: str, name: str) -> str:
    try:
        with open(os.path.join(bdir, name + ".log"), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _needs_rerun(log: str) -> bool:
    return bool(
        re.search(r"Rerun to get|Rerun LaTeX|Label\(s\) may have changed", log)
    )


def _needs_bibtex(bdir: str, name: str, text: str) -> bool:
    if not winenv.which("bibtex"):
        return False
    if not re.search(r"\\(bibliography|addbibresource|nocite)\b", text):
        return False
    try:
        with open(os.path.join(bdir, name + ".aux"), encoding="utf-8", errors="replace") as fh:
            aux = fh.read()
    except OSError:
        return False
    return "\\citation{" in aux or "\\bibdata{" in aux


# --------------------------------------------------------------------------
# log parsing
# --------------------------------------------------------------------------

_FILE_LINE = re.compile(r"^(?:\./)?([^:\n]+?):(\d+):\s*(.*)$")
_BANG = re.compile(r"^!\s+(.*)$")
_PKG_WARN = re.compile(
    r"^(?:(Package|Class|Module)\s+(\S+)\s+)?"
    r"(?:LaTeX|pdfTeX|LuaTeX|XeTeX)?\s*"
    r"(?:Warning|warning):\s*(.*)$"
)
_ON_LINE = re.compile(r"on (?:input )?line (\d+)")
_BADBOX = re.compile(
    r"^(Overfull|Underfull)\s+\\([hv])box\s+\((.*?)\).*?"
    r"(?:at lines? (\d+)(?:--(\d+))?|in paragraph at lines? (\d+)(?:--(\d+))?)"
)
_MISSING_STY = re.compile(r"File [`'\"]([^`'\"]+\.(?:sty|cls))' not found")
_MISSING_ANY = re.compile(r"File [`'\"]([^`'\"]+)' not found")

# Noise that is true of a perfectly healthy document.
_IGNORE_WARNINGS = (
    "Font shape",
    "Some font shapes were not available",
    "Size substitutions with differences",
    "There were undefined references",
    "There were multiply-defined labels",
    "Package hyperref Warning: Token not allowed",
    # epstopdf says this on every document that loads graphicx with shell
    # escape off — which is the default, and the safe setting.
    "Shell escape feature is not enabled",
)


def parse_log(log: str, job: str) -> list[Message]:
    """Turn an engine log into a de-duplicated, ordered list of complaints."""
    out: list[Message] = []
    seen: set[tuple] = set()
    lines = log.splitlines()
    own = {job + ".tex", "./" + job + ".tex", job}

    def add(msg: Message) -> None:
        key = (msg.severity, msg.file, msg.line, msg.text[:120])
        if key in seen:
            return
        seen.add(key)
        out.append(msg)

    for i, raw in enumerate(lines):
        line = raw.rstrip()
        if not line:
            continue

        # "./doc.tex:12: Undefined control sequence."
        m = _FILE_LINE.match(line)
        if m and not line.startswith("!"):
            fname, num, text = m.group(1), int(m.group(2)), m.group(3).strip()
            if text and not text.startswith("=>"):
                lowered = text.lower()
                severity = "warning" if "warning" in lowered else "error"
                add(
                    Message(
                        severity=severity,
                        text=_tidy(text),
                        line=num,
                        file="" if fname in own else fname,
                    )
                )
            continue

        # "! LaTeX Error: ..." — no file:line, so look ahead for "l.42"
        m = _BANG.match(line)
        if m:
            text = m.group(1).strip()
            num = 0
            for ahead in lines[i + 1 : i + 12]:
                lm = re.match(r"^l\.(\d+)", ahead)
                if lm:
                    num = int(lm.group(1))
                    break
            add(Message(severity="error", text=_tidy(text), line=num))
            continue

        m = _BADBOX.match(line)
        if m:
            start = m.group(4) or m.group(6)
            add(
                Message(
                    severity="badbox",
                    text=f"{m.group(1)} \\{m.group(2)}box ({m.group(3)})",
                    line=int(start) if start else 0,
                )
            )
            continue

        if "Warning" in line:
            m = _PKG_WARN.match(line)
            if m:
                text = m.group(3).strip()
                if any(text.startswith(p) or p in line for p in _IGNORE_WARNINGS):
                    continue
                # Warnings often continue onto the following line.
                tail = lines[i + 1].strip() if i + 1 < len(lines) else ""
                probe = text if _ON_LINE.search(text) else f"{text} {tail}"
                lm = _ON_LINE.search(probe)
                add(
                    Message(
                        severity="warning",
                        text=_tidy(text),
                        line=int(lm.group(1)) if lm else 0,
                        package=m.group(2) or "",
                    )
                )

    out.sort(key=lambda m: ({"error": 0, "warning": 1, "badbox": 2}[m.severity], m.line))
    return out


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text[:400]


def _missing_packages(log: str) -> list[str]:
    names = []
    for match in _MISSING_STY.finditer(log):
        stem = os.path.splitext(match.group(1))[0]
        if stem not in names:
            names.append(stem)
    return names


def _missing_files(log: str) -> list[str]:
    names = []
    for match in _MISSING_ANY.finditer(log):
        name = match.group(1)
        if name.endswith((".sty", ".cls")):
            continue
        if name not in names:
            names.append(name)
    return names


# --------------------------------------------------------------------------
# the PDF side
# --------------------------------------------------------------------------


def pdf_info(pdf: str) -> tuple[int, tuple[float, float]]:
    """Page count and first-page size in points. Assumes a uniform page size."""
    try:
        out = subprocess.run(
            [PDFINFO, pdf],
            capture_output=True, text=True, timeout=15, errors="replace",
            creationflags=winenv.NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0, (612.0, 792.0)
    pages = 0
    size = (612.0, 792.0)
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    if m:
        pages = int(m.group(1))
    m = re.search(r"^Page size:\s+([\d.]+) x ([\d.]+)", out, re.M)
    if m:
        size = (float(m.group(1)), float(m.group(2)))
    return pages, size


def render_page(pdf: str, page: int, dpi: int) -> bytes | None:
    """One page as PNG bytes, straight from poppler's rasteriser."""
    try:
        proc = subprocess.run(
            [PDFTOPPM, "-png", "-r", str(dpi), "-f", str(page), "-l", str(page),
             "-aa", "yes", "-aaVector", "yes", pdf],
            capture_output=True, timeout=60,
            creationflags=winenv.NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout or None


def _synctex_names(pdf: str, tex_name: str) -> list[str]:
    """The names synctex might have filed this document's source under.

    `synctex view -i` matches the *string* the engine recorded, not the file.
    TeX Live records the path it was handed — "./paper.tex" — so the bare name
    matches. MiKTeX resolves it first and records the absolute path, and the
    bare name then fails with "No tag for paper.tex" and no forward search at
    all. The buffer is always written beside the PDF, so the absolute form is
    known; try the likelier one for this platform first and keep the other as
    a fallback rather than branching on the distribution.
    """
    absolute = os.path.join(os.path.dirname(os.path.abspath(pdf)), tex_name)
    return [absolute, tex_name] if winenv.WINDOWS else [tex_name, absolute]


def forward_search(pdf: str, tex_name: str, line: int, column: int = 1):
    """Editor line -> (page, x_pt, y_pt). Returns None when synctex can't say."""
    if not SYNCTEX:
        return None
    for name in _synctex_names(pdf, tex_name):
        try:
            out = subprocess.run(
                [SYNCTEX, "view", "-i", f"{line}:{column}:{name}", "-o", pdf],
                capture_output=True, text=True, timeout=15, errors="replace",
                creationflags=winenv.NO_WINDOW,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        page = re.search(r"^Page:(\d+)", out, re.M)
        x = re.search(r"^x:([\d.-]+)", out, re.M)
        y = re.search(r"^y:([\d.-]+)", out, re.M)
        if page and x and y:
            return int(page.group(1)), float(x.group(1)), float(y.group(1))
    return None


def export_pdf(result_pdf: str, destination: str) -> None:
    shutil.copyfile(result_pdf, destination)
