"""The Gitignore helper: tick files and folders, get a correct .gitignore.

Hand-written ignore files fail in two ways that look identical from outside —
the file keeps showing up — and neither one produces an error message:

  1. Shell habits leak into the patterns. `piac/"Options Trading"` looks like
     it quotes a path containing a space, but .gitignore is not a shell: the
     quote marks become part of the name, so the pattern matches a directory
     literally called `"Options Trading"` and the real one stays visible.
     Spaces need no quoting at all; `*?[]\\` are the characters that do need
     escaping, and a trailing space needs a backslash to survive.

  2. The path is already tracked. Ignore rules are consulted only for
     untracked files, so a pattern added after the first `git add` changes
     nothing at all until the path leaves the index.

So the patterns here are generated rather than typed (1 cannot happen), and
the index is re-checked after writing (2 gets caught and offered as a fix).
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import gitcmd, jobs, widgets  # noqa: E402

# Characters that mean something to git's matcher and so must be escaped to
# stand for themselves. A space is deliberately not on this list.
_META = "\\*?[]"

MAX_ENTRIES = 2000  # per directory; past this a picker is the wrong tool anyway


def escape_segment(name):
    """Escape one path component so it matches itself and nothing else."""
    return "".join("\\" + c if c in _META else c for c in name)


def pattern_for(rel_path, is_dir):
    """The .gitignore line that matches exactly `rel_path` and nothing else.

    Anchored with a leading slash so `build/` under the repo root doesn't also
    swallow `src/vendor/build/`, and so a leading `#` or `!` in a filename can
    never be read as a comment or a negation.
    """
    escaped = "/".join(escape_segment(part) for part in rel_path.split("/") if part)
    line = "/" + escaped + ("/" if is_dir else "")
    # git strips trailing whitespace from a pattern unless it is escaped.
    stripped = line.rstrip(" ")
    return stripped + "\\ " * (len(line) - len(stripped))


# Patterns worth suggesting, and the evidence that makes each one relevant.
# (pattern, description, [marker paths or *.extensions that imply it])
SUGGESTIONS = [
    ("__pycache__/", "Python bytecode caches", ["__pycache__", "*.py"]),
    ("*.py[codz]", "Compiled Python files", ["*.pyc", "*.py"]),
    (".venv/", "Virtualenv in the project folder", [".venv"]),
    ("venv/", "Virtualenv in the project folder", ["venv"]),
    ("*.egg-info/", "Python packaging metadata", ["setup.py", "pyproject.toml"]),
    (".pytest_cache/", "pytest cache", [".pytest_cache", "pytest.ini"]),
    (".mypy_cache/", "mypy cache", [".mypy_cache"]),
    (".ruff_cache/", "ruff cache", [".ruff_cache"]),
    ("node_modules/", "Installed npm packages", ["node_modules", "package.json"]),
    ("dist/", "Build output", ["dist"]),
    ("build/", "Build output", ["build"]),
    (".next/", "Next.js build output", [".next"]),
    ("target/", "Cargo build output", ["Cargo.toml"]),
    ("*.class", "Compiled Java classes", ["*.java", "pom.xml"]),
    ("bin/", "Build output", ["*.csproj", "*.sln"]),
    ("obj/", "Build output", ["*.csproj", "*.sln"]),
    (".env", "Local secrets and credentials", [".env", ".env.example"]),
    ("*.local", "Machine-local overrides", ["*.local"]),
    ("*.log", "Log files", ["*.log"]),
    ("*.sqlite3", "Local databases", ["*.sqlite3", "*.db"]),
    (".DS_Store", "macOS folder metadata", None),
    ("Thumbs.db", "Windows thumbnail cache", None),
    (".idea/", "JetBrains project settings", [".idea"]),
    (".vscode/", "VS Code workspace settings", [".vscode"]),
    ("*.swp", "Vim swap files", ["*.swp"]),
    ("*~", "Editor backup files", ["*~"]),
]


def relevant_suggestions(repo):
    """Filter the catalogue down to what this repo shows evidence of.

    Only the top two levels are sampled: deep enough to notice `src/dist` or a
    nested `node_modules`, shallow enough not to walk a 40k-file tree.
    """
    names, extensions = set(), set()

    def sample(path, depth):
        try:
            with os.scandir(path) as it:
                for n, entry in enumerate(it):
                    if n > MAX_ENTRIES:
                        return
                    if entry.name == ".git":
                        continue
                    names.add(entry.name)
                    ext = os.path.splitext(entry.name)[1]
                    if ext:
                        extensions.add("*" + ext)
                    if depth and entry.is_dir(follow_symlinks=False):
                        sample(entry.path, depth - 1)
        except OSError:
            pass

    sample(repo, 2)

    out = []
    for pattern, description, markers in SUGGESTIONS:
        if markers is None:  # always offered — OS litter shows up everywhere
            out.append((pattern, description, False))
            continue
        hit = any(m in names or m in extensions for m in markers)
        if hit:
            out.append((pattern, description, True))
    return out


def suspicious_lines(text):
    """Lines that look like they were meant to match something and don't.

    Only mistakes that are unambiguous from the text alone are reported —
    anything requiring a guess about intent is left alone, because a false
    warning about a working pattern is worse than no warning.
    """
    problems = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if '"' in line or "'" in line:
            problems.append((number, line,
                             "quotes are literal here — .gitignore is not a shell, "
                             "so this matches a name with quote marks in it"))
        elif raw.endswith(" ") and not raw.endswith("\\ "):
            problems.append((number, line,
                             "trailing space is stripped — escape it as “\\ ” if the "
                             "name really ends in one"))
    return problems


def read_gitignore(repo):
    path = os.path.join(repo, ".gitignore")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def write_gitignore(repo, text):
    if text and not text.endswith("\n"):
        text += "\n"
    with open(os.path.join(repo, ".gitignore"), "w", encoding="utf-8") as fh:
        fh.write(text)


# ------------------------------------------------------------------ tree ----


class Node(GObject.Object):
    """One file or folder in the picker."""

    __gtype_name__ = "GitignoreNode"

    def __init__(self, repo, rel, name, is_dir, parent=None):
        super().__init__()
        self.repo = repo
        self.rel = rel
        self.name = name
        self.is_dir = is_dir
        self.parent = parent
        self.ignored = False   # matched by the rules already on disk
        self._children = None

    @property
    def pattern(self):
        return pattern_for(self.rel, self.is_dir)

    def covered_by_ancestor(self, active):
        """True when some parent folder is already ticked, making this moot."""
        node = self.parent
        while node is not None:
            # The synthetic root stands for the repo itself and has no pattern
            # of its own to match against.
            if node.rel and node.pattern in active:
                return True
            node = node.parent
        return False

    def children(self):
        if self._children is None:
            self._children = Gio.ListStore(item_type=Node)
            for child in self._scan():
                self._children.append(child)
        return self._children

    def _scan(self):
        full = os.path.join(self.repo, self.rel) if self.rel else self.repo
        try:
            with os.scandir(full) as it:
                entries = [e for e in it if e.name != ".git"][:MAX_ENTRIES]
        except OSError:
            return []
        entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
        kids = [
            Node(
                self.repo,
                f"{self.rel}/{e.name}" if self.rel else e.name,
                e.name,
                e.is_dir(follow_symlinks=False),
                parent=self,
            )
            for e in entries
        ]
        # One check-ignore per expanded folder, so rows can say which entries
        # the existing rules already cover.
        try:
            already = gitcmd.check_ignore(self.repo, [k.rel for k in kids])
        except (gitcmd.GitError, OSError):
            already = set()
        for kid in kids:
            kid.ignored = kid.rel in already or self.ignored
        return kids


# ---------------------------------------------------------------- dialog ----


class GitignoreDialog(Adw.Dialog):
    """Pick paths on the left, watch the file being written on the right.

    The text view is the single source of truth: ticking a box inserts or
    removes one line, and hand-edits are never overwritten by the picker.
    """

    def __init__(self, window, state):
        super().__init__(title="Gitignore helper", content_width=980, content_height=720)
        self.window = window
        self.state = state
        self.repo = state.path
        self._syncing = False
        self._bound = {}  # live row widgets, so ticks can repaint their siblings
        self._shadowed = []

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Gitignore helper", subtitle=state.name))
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        save = Gtk.Button(label="Write .gitignore", css_classes=["suggested-action"])
        save.connect("clicked", lambda _b: self._save())
        header.pack_end(save)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=520,
                          shrink_start_child=False, shrink_end_child=False)
        # Preview first: it creates the buffer, which the picker's rows read to
        # decide whether their box is ticked.
        preview = self._build_preview()
        paned.set_start_child(self._build_picker())
        paned.set_end_child(preview)

        self.banner = Adw.Banner(revealed=False, button_label="Stop tracking")
        self.banner.connect("button-clicked", lambda _b: self._untrack_shadowed())

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.banner)
        paned.set_vexpand(True)
        body.append(paned)

        toolbar = Adw.ToolbarView(content=body)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

        self.buffer.set_text(read_gitignore(self.repo))
        self.buffer.connect("changed", self._on_buffer_changed)
        self._refresh_rows()
        self._check_tracked()

    # -- left: suggestions above, file tree below

    def _build_picker(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        intro = Gtk.Label(
            label="Tick anything you don't want in the repository. Patterns are "
                  "written for you, so spaces and other awkward characters are "
                  "handled correctly.",
            xalign=0, wrap=True, css_classes=["dim-label"],
            margin_top=10, margin_bottom=6, margin_start=12, margin_end=12,
        )
        box.append(intro)

        self.suggestion_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"],
            margin_start=12, margin_end=12, margin_bottom=10,
        )
        suggestions = relevant_suggestions(self.repo)
        for pattern, description, _detected in suggestions:
            self.suggestion_box.append(self._suggestion_row(pattern, description))

        expander = Gtk.Expander(
            label=f"Common patterns for this project ({len(suggestions)})",
            expanded=True, margin_start=12, margin_end=12,
        )
        wrap = Gtk.ScrolledWindow(height_request=190, propagate_natural_height=True)
        wrap.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        wrap.set_child(self.suggestion_box)
        expander.set_child(wrap)
        box.append(expander)

        heading = Gtk.Label(
            label="Files and folders", xalign=0, css_classes=["heading"],
            margin_top=10, margin_bottom=4, margin_start=12,
        )
        box.append(heading)
        box.append(self._build_tree())
        return box

    def _suggestion_row(self, pattern, description):
        row = Adw.ActionRow(title=GLib.markup_escape_text(pattern), subtitle=description)
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        check.connect("toggled", lambda b, p=pattern: self._toggle_pattern(p, b.get_active()))
        row.add_prefix(check)
        row.set_activatable_widget(check)
        row.check, row.pattern = check, pattern
        return row

    def _build_tree(self):
        roots = Gio.ListStore(item_type=Node)
        top = Node(self.repo, "", os.path.basename(self.repo), True)
        for child in top.children():
            roots.append(child)

        tree = Gtk.TreeListModel.new(roots, False, False, self._children_of)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        factory.connect("unbind", self._unbind_row)

        self.tree_view = Gtk.ListView(
            model=Gtk.NoSelection(model=tree), factory=factory,
            css_classes=["navigation-sidebar"],
        )
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.tree_view)
        return scroller

    @staticmethod
    def _children_of(node):
        return node.children() if node.is_dir else None

    def _setup_row(self, _factory, item):
        expander = Gtk.TreeExpander()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_top=2, margin_bottom=2)
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        icon = Gtk.Image()
        name = Gtk.Label(xalign=0, hexpand=True)
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        note = Gtk.Label(xalign=1, css_classes=["dim-label", "repo-row-path"])
        row.append(check)
        row.append(icon)
        row.append(name)
        row.append(note)
        expander.set_child(row)
        item.set_child(expander)
        check.connect("toggled", self._on_check_toggled)
        expander.parts = (check, icon, name, note)

    def _on_check_toggled(self, check):
        if self._syncing:
            return
        node = getattr(check, "node", None)
        if node is not None:
            self._toggle_pattern(node.pattern, check.get_active())

    def _bind_row(self, _factory, item):
        row = item.get_item()
        expander = item.get_child()
        expander.set_list_row(row)
        node = row.get_item()
        check, icon, name, _note = expander.parts
        check.node = node
        icon.set_from_icon_name("folder-symbolic" if node.is_dir else "text-x-generic-symbolic")
        name.set_label(node.name)
        name.set_tooltip_text(node.rel)
        self._bound[id(expander)] = (expander, node)
        self._paint(expander, node, self._active_patterns())

    def _unbind_row(self, _factory, item):
        expander = item.get_child()
        self._bound.pop(id(expander), None)
        expander.parts[0].node = None

    def _paint(self, expander, node, active):
        check, _icon, name, note = expander.parts
        covered = node.covered_by_ancestor(active)
        ticked = node.pattern in active

        self._syncing = True
        check.set_active(ticked or covered)
        self._syncing = False
        check.set_sensitive(not covered)

        if covered:
            note.set_label("in an ignored folder")
        elif ticked:
            note.set_label("")
        elif node.ignored:
            note.set_label("already ignored")
        else:
            note.set_label("")
        dim = covered or (node.ignored and not ticked)
        name.set_css_classes(["dim-label"] if dim else [])

    def _refresh_rows(self):
        self._update_lint()
        active = self._active_patterns()
        for expander, node in list(self._bound.values()):
            self._paint(expander, node, active)
        self._syncing = True
        row = self.suggestion_box.get_first_child()
        while row is not None:
            row.check.set_active(row.pattern in active)
            row = row.get_next_sibling()
        self._syncing = False

    # -- right: the file itself

    def _build_preview(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        heading = Gtk.Label(
            label=".gitignore", xalign=0, css_classes=["heading"],
            margin_top=10, margin_bottom=2, margin_start=12,
        )
        box.append(heading)
        hint = Gtk.Label(
            label="Editable — ticking a box adds or removes a line here.",
            xalign=0, css_classes=["dim-label", "repo-row-path"],
            margin_bottom=6, margin_start=12, margin_end=12, wrap=True,
        )
        box.append(hint)

        view = Gtk.TextView(
            monospace=True, top_margin=8, bottom_margin=8, left_margin=10, right_margin=10,
            wrap_mode=Gtk.WrapMode.NONE,
        )
        self.buffer = view.get_buffer()
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(view)
        frame = Gtk.Frame(
            child=scroller, vexpand=True,
            margin_start=12, margin_end=12, margin_bottom=8,
        )
        box.append(frame)

        self.lint = Gtk.Label(
            xalign=0, wrap=True, visible=False, css_classes=["lint-warning"],
            margin_start=12, margin_end=12, margin_bottom=12,
        )
        box.append(self.lint)
        return box

    def _update_lint(self):
        problems = suspicious_lines(self._text())
        if not problems:
            self.lint.set_visible(False)
            return
        lines = [f"Line {n}: <tt>{GLib.markup_escape_text(line)}</tt> — {why}"
                 for n, line, why in problems[:4]]
        if len(problems) > 4:
            lines.append(f"… and {len(problems) - 4} more")
        self.lint.set_markup("\n".join(lines))
        self.lint.set_visible(True)

    def _text(self):
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)

    def _active_patterns(self):
        return {ln.strip() for ln in self._text().splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")}

    def _toggle_pattern(self, pattern, wanted):
        lines = self._text().splitlines()
        present = [i for i, ln in enumerate(lines) if ln.strip() == pattern]
        if wanted and not present:
            lines.append(pattern)
        elif not wanted and present:
            for i in reversed(present):
                del lines[i]
        else:
            return
        self._syncing = True
        self.buffer.set_text("\n".join(lines) + ("\n" if lines else ""))
        self._syncing = False
        self._refresh_rows()

    def _on_buffer_changed(self, _buffer):
        if self._syncing:
            return
        self._refresh_rows()  # hand-edits move the checkboxes, not the reverse

    # -- writing, and the tracked-file trap

    def _save(self):
        try:
            write_gitignore(self.repo, self._text())
        except OSError as exc:
            widgets.error_toast(self, exc)
            return
        widgets.toast(self.window, "Wrote .gitignore")
        self._check_tracked(after_save=True)
        self.window.refresh_selected(force=True)

    def _check_tracked(self, after_save=False):
        """Warn about paths the new rules match but git is still tracking."""
        repo = self.repo

        def work():
            tracked = gitcmd.tracked_files(repo)
            # no_index: ask what the patterns match, not what git is ignoring —
            # the whole point is to find paths where those two disagree.
            return sorted(gitcmd.check_ignore(repo, tracked, no_index=True))

        def done(shadowed):
            self._shadowed = shadowed
            if not shadowed:
                self.banner.set_revealed(False)
                if after_save:
                    self.close()
                return
            count = len(shadowed)
            self.banner.set_title(
                f"{count} tracked file{'s' if count != 1 else ''} match these rules. "
                "Ignore rules don't apply to files git already tracks — until they "
                "leave the index, they keep showing up."
            )
            self.banner.set_revealed(True)

        jobs.run(work, done, lambda e: widgets.error_toast(self, e))

    def _untrack_shadowed(self):
        paths = list(getattr(self, "_shadowed", []))
        if not paths:
            return
        repo = self.repo
        sample = "\n".join("  " + p for p in paths[:8])
        if len(paths) > 8:
            sample += f"\n  … and {len(paths) - 8} more"

        def go():
            def work():
                gitcmd.untrack(repo, paths)
                return len(paths)

            def done(count):
                widgets.toast(self.window, f"Stopped tracking {count} files")
                self._check_tracked()
                self.window.refresh_selected(force=True)

            jobs.run(work, done, lambda e: widgets.error_toast(self, e))

        widgets.confirm(
            self,
            f"Stop tracking {len(paths)} files?",
            "They stay on disk exactly as they are — this only removes them from "
            "git's index, which is what lets the ignore rules take effect. The "
            "removal is staged, so it lands in your next commit (and the files "
            "will disappear for anyone who pulls it).\n\n" + sample,
            "Stop tracking", go, destructive=False,
        )


def open_dialog(window, state):
    if not state:
        return
    GitignoreDialog(window, state).present(window)
