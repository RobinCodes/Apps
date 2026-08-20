"""The README helper: read the repo, propose a README, let it be edited.

Everything here is inferred from files that are already in the repository —
manifests for the name and the commands, the tracked file list for the tree,
the origin remote for the clone line. Nothing is invented: when a section has
no evidence behind it, the generator leaves a short placeholder rather than
writing a confident paragraph about a project it hasn't read.
"""

from __future__ import annotations

import json
import os
import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from . import github, gitcmd, jobs, widgets  # noqa: E402

LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".jsx": "JavaScript", ".rs": "Rust", ".go": "Go",
    ".java": "Java", ".kt": "Kotlin", ".rb": "Ruby", ".php": "PHP", ".cs": "C#",
    ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++", ".swift": "Swift",
    ".sh": "Shell", ".lua": "Lua", ".css": "CSS", ".html": "HTML", ".sql": "SQL",
}

LICENSES = [
    ("MIT", ["MIT License", "Permission is hereby granted, free of charge"]),
    ("Apache-2.0", ["Apache License", "Version 2.0, January 2004"]),
    ("GPL-3.0", ["GNU GENERAL PUBLIC LICENSE", "Version 3"]),
    ("GPL-2.0", ["GNU GENERAL PUBLIC LICENSE", "Version 2"]),
    ("BSD-3-Clause", ["Redistribution and use in source and binary forms",
                      "Neither the name of"]),
    ("BSD-2-Clause", ["Redistribution and use in source and binary forms"]),
    ("MPL-2.0", ["Mozilla Public License"]),
    ("Unlicense", ["This is free and unencumbered software"]),
]

# Files whose presence is worth explaining in the structure listing.
KNOWN_FILES = {
    "pyproject.toml": "packaging and tool configuration",
    "requirements.txt": "Python dependencies",
    "package.json": "npm manifest and scripts",
    "Cargo.toml": "Cargo manifest",
    "go.mod": "Go module definition",
    "Makefile": "build targets",
    "Dockerfile": "container image",
    "docker-compose.yml": "local service stack",
    ".env.example": "template for local configuration",
}

# Files worth listing under "Configuration" — things a user edits to run the
# project, as opposed to manifests, which the Installation section covers.
CONFIG_FILES = {
    "docker-compose.yml": "local service stack",
    "docker-compose.yaml": "local service stack",
    "Dockerfile": "container image",
    "config.toml": "application settings",
    "config.yaml": "application settings",
    "config.yml": "application settings",
    "settings.toml": "application settings",
}


class Facts:
    """What could be read off the repository, with nothing filled in blind."""

    def __init__(self):
        self.name = ""
        self.description = ""
        self.language = ""
        self.languages = []
        self.clone_url = ""
        self.nwo = ""
        self.license = ""
        self.requirements = []
        self.install = []
        self.usage = []
        self.entry_points = []
        self.paths = []
        self.subdir = ""  # where the manifests live, when not at the root
        self.has_tests = False
        self.has_ci = False
        self.config_files = []
        self.test_command = ""


def _read(repo, name, limit=200_000):
    try:
        with open(os.path.join(repo, name), encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _json(repo, name):
    text = _read(repo, name)
    if not text:
        return {}
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {}


def _toml_field(text, section, key):
    """One string field out of a TOML section, without a TOML parser.

    tomllib would be exact, but this only ever needs `name` and `description`
    out of `[project]` or `[package]`, and a regex keeps the module importable
    on a Python without tomllib.
    """
    block = re.search(rf"^\[{re.escape(section)}\]\s*$(.*?)(?=^\[|\Z)",
                      text, re.MULTILINE | re.DOTALL)
    if not block:
        return ""
    match = re.search(rf'^\s*{re.escape(key)}\s*=\s*["\'](.*?)["\']\s*$',
                      block.group(1), re.MULTILINE)
    return match.group(1) if match else ""


MANIFESTS = ("package.json", "pyproject.toml", "requirements.txt", "Cargo.toml",
             "go.mod", "Gemfile", "composer.json", "Makefile", "setup.py")


def _manifest_dir(paths):
    """Where this project actually keeps its manifests.

    The root is the normal answer, but plenty of repos put the code one level
    down (`app/`, `src/`, or a name of their own) and leave the root holding
    only a README and a licence. Looking one level deep costs nothing and
    turns "no install steps found" into real commands for those.
    """
    if any(p in MANIFESTS for p in paths):
        return ""
    depth_one = {}
    for path in paths:
        head, _, rest = path.partition("/")
        if rest and "/" not in rest and rest in MANIFESTS:
            depth_one.setdefault(head, 0)
            depth_one[head] += 1
    if not depth_one:
        return ""
    return max(depth_one.items(), key=lambda kv: kv[1])[0] + "/"


def inspect(repo):
    """Everything the generator knows, gathered in one pass. Thread-safe."""
    f = Facts()
    f.name = os.path.basename(repo.rstrip("/")) or "project"

    try:
        f.paths = gitcmd.listed_files(repo)
    except (gitcmd.GitError, OSError):
        f.paths = []

    lowered = {p.lower() for p in f.paths}
    prefix = _manifest_dir(f.paths)
    f.subdir = prefix.rstrip("/")
    # `names` is the set of filenames to test manifests against, taken from
    # wherever the manifests turned out to live.
    names = {
        p[len(prefix):] for p in f.paths
        if p.startswith(prefix) and "/" not in p[len(prefix):]
    }
    names |= {p.split("/")[0] for p in f.paths}

    # -- language mix, by tracked file count
    counts = {}
    for path in f.paths:
        lang = LANGUAGES.get(os.path.splitext(path)[1].lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    f.languages = [lang for lang, _n in sorted(counts.items(), key=lambda kv: -kv[1])][:3]
    f.language = f.languages[0] if f.languages else ""

    # -- remote, for the clone line
    try:
        url = gitcmd.remote_url(repo)
    except (gitcmd.GitError, OSError):
        url = ""
    f.nwo = github.nwo_for(url) or ""
    f.clone_url = f"https://github.com/{f.nwo}.git" if f.nwo else (url or "")

    # -- manifests: the name, the blurb, and the commands
    pkg = _json(repo, prefix + "package.json")
    if pkg:
        f.name = pkg.get("name") or f.name
        f.description = pkg.get("description") or f.description
        f.requirements.append("Node.js")
        manager, install = "npm", "npm install"
        if "pnpm-lock.yaml" in names:
            manager, install = "pnpm", "pnpm install"
        elif "yarn.lock" in names:
            manager, install = "yarn", "yarn install"
        f.install.append(install)
        scripts = pkg.get("scripts") or {}
        for key in ("dev", "start", "build", "test"):
            if key in scripts:
                run = f"{manager} {key}" if key in ("start", "test") else f"{manager} run {key}"
                f.usage.append((run, f"{key} — `{scripts[key]}`"))

    pyproject = _read(repo, prefix + "pyproject.toml")
    if pyproject:
        f.name = _toml_field(pyproject, "project", "name") or f.name
        f.description = _toml_field(pyproject, "project", "description") or f.description
        f.requirements.append("Python 3")
        f.install.append("pip install -e .")
    if "requirements.txt" in names:
        f.requirements.append("Python 3")
        f.install.append("python3 -m venv .venv && source .venv/bin/activate")
        f.install.append("pip install -r requirements.txt")

    cargo = _read(repo, prefix + "Cargo.toml")
    if cargo:
        f.name = _toml_field(cargo, "package", "name") or f.name
        f.description = _toml_field(cargo, "package", "description") or f.description
        f.requirements.append("Rust and Cargo")
        f.install.append("cargo build --release")
        f.usage.append(("cargo run", "run it from the source tree"))

    if "go.mod" in names:
        f.requirements.append("Go")
        f.install.append("go build ./...")

    if "Gemfile" in names:
        f.requirements.append("Ruby and Bundler")
        f.install.append("bundle install")

    if "composer.json" in names:
        f.requirements.append("PHP and Composer")
        f.install.append("composer install")

    if "Dockerfile" in names:
        f.usage.append((f"docker build -t {f.name} .", "build the container image"))

    makefile = _read(repo, prefix + "Makefile", 20_000)
    if makefile:
        targets = re.findall(r"^([a-zA-Z][\w.-]*)\s*:(?!=)", makefile, re.MULTILINE)
        for target in [t for t in targets if t not in ("PHONY",)][:4]:
            f.usage.append((f"make {target}", ""))

    # -- how you actually start the thing
    for candidate in ("main.py", "app.py", "run.py", "manage.py", "__main__.py"):
        if prefix + candidate in f.paths:
            f.entry_points.append(prefix + candidate)
            f.usage.append((f"python3 {prefix + candidate}", ""))
            break
    # An executable file with no extension in the top two levels is almost
    # always the thing you are meant to run.
    executables = [
        p for p in f.paths
        if p.count("/") <= 1 and "." not in os.path.basename(p)
        and os.path.isfile(os.path.join(repo, p))
        and os.access(os.path.join(repo, p), os.X_OK)
    ]
    for exe in executables[:2]:
        f.entry_points.append(exe)
        f.usage.append((f"./{exe}", ""))

    # -- the rest
    f.has_tests = any(p.startswith(("test/", "tests/", "spec/")) or "/tests/" in p
                      or os.path.basename(p).startswith("test_") for p in f.paths)
    f.has_ci = any(p.startswith((".github/workflows/", ".gitlab-ci")) for p in f.paths)
    f.config_files = sorted(
        {p for p in f.paths
         if p.count("/") <= 1
         and (os.path.basename(p) in CONFIG_FILES or os.path.basename(p).startswith(".env"))}
    )

    # The command that runs the tests, taken from what the project already
    # defines rather than guessed from the language alone.
    scripted = next((cmd for cmd, _n in f.usage if cmd.endswith(" test")), "")
    if scripted:
        f.test_command = scripted
    elif makefile and re.search(r"^tests?\s*:(?!=)", makefile, re.MULTILINE):
        f.test_command = "make test"
    elif f.language == "Python":
        f.test_command = "pytest"
    elif "Cargo.toml" in names:
        f.test_command = "cargo test"
    elif "go.mod" in names:
        f.test_command = "go test ./..."
    else:
        f.test_command = ""

    for path in f.paths:
        if os.path.basename(path).lower().startswith(("license", "licence", "copying")):
            text = _read(repo, path, 4000)
            for name, markers in LICENSES:
                if all(m.lower() in text.lower() for m in markers):
                    f.license = name
                    break
            if not f.license:
                f.license = "see the LICENSE file"
            break

    if "readme.md" in lowered:
        f.description = f.description or ""

    # De-duplicate while keeping the order things were discovered in.
    f.requirements = list(dict.fromkeys(f.requirements))
    f.install = list(dict.fromkeys(f.install))
    seen, usage = set(), []
    for cmd, note in f.usage:
        if cmd not in seen:
            seen.add(cmd)
            usage.append((cmd, note))
    f.usage = usage
    return f


def structure_tree(facts, max_entries=18):
    """Top-level layout, drawn from the files git actually knows about."""
    if not facts.paths:
        return ""
    top = {}
    for path in facts.paths:
        head, _, rest = path.partition("/")
        if rest:
            top.setdefault(head, []).append(rest)
        else:
            top.setdefault(head, [])

    dirs = sorted((k for k, v in top.items() if v), key=str.lower)
    files = sorted((k for k, v in top.items() if not v), key=str.lower)
    lines = []
    for name in dirs[:max_entries]:
        count = len(top[name])
        lines.append((f"{name}/", f"{count} file{'s' if count != 1 else ''}"))
    for name in files[:max_entries]:
        lines.append((name, KNOWN_FILES.get(name, "")))
    if not lines:
        return ""

    width = max(len(n) for n, _ in lines)
    out = [f"{facts.name}/"]
    for i, (name, note) in enumerate(lines):
        stem = "└── " if i == len(lines) - 1 else "├── "
        out.append(f"{stem}{name.ljust(width) if note else name}{'  # ' + note if note else ''}".rstrip())
    return "\n".join(out)


def generate(facts, sections, title, tagline, description):
    """Assemble the markdown. `sections` is the set of enabled section keys."""
    out = [f"# {title or facts.name}", ""]
    if tagline:
        out += [f"> {tagline}", ""]

    if "badges" in sections:
        badges = []
        if facts.license:
            label = facts.license.replace("-", "--")
            badges.append(f"![License](https://img.shields.io/badge/license-{label}-blue)")
        if facts.language:
            badges.append(f"![Language](https://img.shields.io/badge/built%20with-{facts.language}-informational)")
        if badges:
            out += [" ".join(badges), ""]

    if description:
        out += [description.strip(), ""]

    if "features" in sections:
        out += ["## Features", "",
                "- <!-- what it does, one line each -->", "- ", "- ", ""]

    if "requirements" in sections and facts.requirements:
        out += ["## Requirements", ""]
        out += [f"- {r}" for r in facts.requirements]
        out += [""]

    if "install" in sections:
        out += ["## Installation", ""]
        steps = []
        if facts.clone_url:
            steps.append(f"git clone {facts.clone_url}")
            steps.append(f"cd {facts.name}")
        if facts.subdir and facts.install:
            steps.append(f"cd {facts.subdir}")
        steps += facts.install
        if steps:
            out += ["```bash", *steps, "```", ""]
        else:
            out += ["<!-- no manifest found — add the install steps here -->", ""]

    if "usage" in sections:
        out += ["## Usage", ""]
        if facts.usage:
            for cmd, note in facts.usage:
                if note:
                    out += [note[0].upper() + note[1:], ""]
                out += ["```bash", cmd, "```", ""]
        else:
            out += ["<!-- how to run it -->", "", "```bash", "", "```", ""]

    if "structure" in sections:
        tree = structure_tree(facts)
        if tree:
            out += ["## Project structure", "", "```text", tree, "```", ""]

    if "configuration" in sections and facts.config_files:
        out += ["## Configuration", ""]
        for name in facts.config_files:
            base = os.path.basename(name)
            note = CONFIG_FILES.get(base) or KNOWN_FILES.get(base) or "local configuration"
            out += [f"- `{name}` — {note}"]
        out += [""]

    if "tests" in sections and facts.has_tests:
        out += ["## Tests", "", "```bash", facts.test_command or "# add the test command", "```", ""]

    if "contributing" in sections:
        out += ["## Contributing",
                "",
                "Issues and pull requests are welcome. For anything substantial, "
                "open an issue first so the approach can be agreed before the work.",
                ""]

    if "license" in sections:
        out += ["## License", ""]
        out += [f"{facts.license}." if facts.license
                else "<!-- add a LICENSE file, then name it here -->", ""]

    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


SECTIONS = [
    ("badges", "Badges", "Shields for licence and language"),
    ("features", "Features", "A bulleted list for you to fill in"),
    ("requirements", "Requirements", "Detected from the project's manifests"),
    ("install", "Installation", "Clone and setup commands"),
    ("usage", "Usage", "Detected run commands and scripts"),
    ("structure", "Project structure", "A tree of the top level"),
    ("configuration", "Configuration", "Config and environment files"),
    ("tests", "Tests", "How to run the test suite"),
    ("contributing", "Contributing", "A short standard paragraph"),
    ("license", "License", "Detected from the LICENSE file"),
]

DEFAULT_SECTIONS = {"badges", "features", "requirements", "install", "usage",
                    "structure", "configuration", "license"}


class ReadmeDialog(Adw.Dialog):
    """Form and switches on the left, the markdown it produces on the right."""

    def __init__(self, window, state):
        super().__init__(title="README helper", content_width=1000, content_height=740)
        self.window = window
        self.state = state
        self.repo = state.path
        self.facts = Facts()
        self.facts.name = state.name
        self._dirty = False  # the user has hand-edited the markdown

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="README helper", subtitle=state.name))
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        self.save_btn = Gtk.Button(label="Write README.md", css_classes=["suggested-action"],
                                   sensitive=False)
        self.save_btn.connect("clicked", lambda _b: self._save())
        header.pack_end(self.save_btn)
        regen = Gtk.Button(label="Regenerate", tooltip_text="Rebuild the markdown from the fields above")
        regen.connect("clicked", lambda _b: self._regenerate(force=True))
        header.pack_end(regen)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=430,
                          shrink_start_child=False, shrink_end_child=False, vexpand=True)
        paned.set_start_child(self._build_form())
        paned.set_end_child(self._build_preview())

        toolbar = Adw.ToolbarView(content=paned)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

        self._load()

    def _build_form(self):
        page = Adw.PreferencesPage()

        about = Adw.PreferencesGroup(title="About", description="Reading the repository…")
        self.about_group = about
        self.title_row = Adw.EntryRow(title="Project name")
        self.tagline_row = Adw.EntryRow(title="One-line summary")
        about.add(self.title_row)
        about.add(self.tagline_row)

        self.description = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8,
        )
        frame = Gtk.Frame(child=self.description, height_request=110, margin_top=8)
        about.add(frame)
        page.add(about)

        self.section_group = Adw.PreferencesGroup(
            title="Sections", description="Sections with nothing to say are left out."
        )
        self.switches = {}
        for key, title, subtitle in SECTIONS:
            row = Adw.SwitchRow(title=title, subtitle=subtitle, active=key in DEFAULT_SECTIONS)
            row.connect("notify::active", lambda *_: self._regenerate())
            self.switches[key] = row
            self.section_group.add(row)
        page.add(self.section_group)

        for row in (self.title_row, self.tagline_row):
            row.connect("changed", lambda *_: self._regenerate())

        # Adw.PreferencesPage scrolls itself — wrapping it in a ScrolledWindow
        # nests two scrollers and leaves the outer one inert.
        page.set_vexpand(True)
        return page

    def _build_preview(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        heading = Gtk.Label(label="README.md", xalign=0, css_classes=["heading"],
                            margin_top=10, margin_bottom=2, margin_start=12)
        box.append(heading)
        self.hint = Gtk.Label(
            label="Editable. Anything left as an HTML comment is a prompt for you.",
            xalign=0, wrap=True, css_classes=["dim-label", "repo-row-path"],
            margin_bottom=6, margin_start=12, margin_end=12,
        )
        box.append(self.hint)

        view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        self.buffer = view.get_buffer()
        self.buffer.connect("changed", self._on_edited)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(view)
        box.append(Gtk.Frame(child=scroller, vexpand=True,
                             margin_start=12, margin_end=12, margin_bottom=12))
        return box

    # -- lifecycle

    def _load(self):
        repo = self.repo

        def done(facts):
            self.facts = facts
            self.title_row.set_text(facts.name)
            if facts.description:
                self._set_description(facts.description)
            found = []
            if facts.language:
                found.append(facts.language)
            if facts.install:
                found.append(f"{len(facts.install)} install step{'s' if len(facts.install) != 1 else ''}")
            if facts.usage:
                found.append(f"{len(facts.usage)} command{'s' if len(facts.usage) != 1 else ''}")
            if facts.license:
                found.append(facts.license)
            self.about_group.set_description(
                "Detected: " + ", ".join(found) if found
                else "Nothing detectable in this repo — the sections below start empty."
            )
            for key in ("requirements", "configuration", "tests"):
                have = {"requirements": facts.requirements,
                        "configuration": facts.config_files,
                        "tests": facts.has_tests}[key]
                if not have:
                    self.switches[key].set_active(False)
                    self.switches[key].set_sensitive(False)
                    self.switches[key].set_subtitle("Nothing found for this section")
            self.save_btn.set_sensitive(True)
            self._regenerate(force=True)

        jobs.run(lambda: inspect(repo), done, lambda e: widgets.error_toast(self, e))

    def _set_description(self, text):
        self.description.get_buffer().set_text(text)

    def _description_text(self):
        buf = self.description.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()

    def _on_edited(self, _buffer):
        if getattr(self, "_writing", False):
            return
        self._dirty = True
        self.hint.set_label("Edited by hand — “Regenerate” will discard those edits.")

    def _regenerate(self, force=False):
        if self._dirty and not force:
            return  # never clobber hand-edits behind the user's back
        sections = {k for k, row in self.switches.items() if row.get_active()}
        text = generate(
            self.facts, sections,
            self.title_row.get_text().strip(),
            self.tagline_row.get_text().strip(),
            self._description_text(),
        )
        self._writing = True
        self.buffer.set_text(text)
        self._writing = False
        self._dirty = False
        self.hint.set_label("Editable. Anything left as an HTML comment is a prompt for you.")

    def _text(self):
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)

    def _save(self):
        target = os.path.join(self.repo, "README.md")
        # Case matters here: writing README.md next to an existing readme.md
        # would leave the repo with two, and only one of them rendered.
        try:
            siblings = os.listdir(self.repo)
        except OSError:
            siblings = []
        existing = next((n for n in siblings if n.lower() == "readme.md"), None)
        if existing:
            widgets.confirm(
                self, f"Overwrite {existing}?",
                f"“{existing}” already exists in {self.state.name} and will be replaced. "
                "If it is committed you can get it back with git; if it isn't, this "
                "cannot be undone.",
                "Overwrite",
                lambda: self._write(os.path.join(self.repo, existing)),
            )
            return
        self._write(target)

    def _write(self, target):
        try:
            with open(target, "w", encoding="utf-8") as fh:
                text = self._text()
                fh.write(text if text.endswith("\n") else text + "\n")
        except OSError as exc:
            widgets.error_toast(self, exc)
            return
        widgets.toast(self.window, f"Wrote {os.path.basename(target)}")
        self.window.refresh_selected(force=True)
        self.close()


def open_dialog(window, state):
    if not state:
        return
    ReadmeDialog(window, state).present(window)
