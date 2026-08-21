"""The main window: repository sidebar on the left, the four tabs on the right."""

from __future__ import annotations

import os
import shutil
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import config, filesystem, github, gitcmd, ignore, jobs, link, readme, scanner, widgets  # noqa: E402
from .branches import BranchesTab  # noqa: E402
from .changes import ChangesTab  # noqa: E402
from .ghtab import GitHubTab  # noqa: E402
from .history import HistoryTab  # noqa: E402


class RepoState:
    """One repository, plus whatever we last learned about it."""

    def __init__(self, ref):
        self.ref = ref
        self.status = None
        self.remote = None   # the remote the nwo below was read from
        self.nwo = None
        self.error = None
        self.row = None

    @property
    def path(self):
        return self.ref.path

    @property
    def name(self):
        return self.ref.name


class RepoRow(Gtk.ListBoxRow):
    def __init__(self, state):
        super().__init__()
        self.state = state
        state.row = self

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2,
            margin_top=7, margin_bottom=7, margin_start=10, margin_end=10,
        )
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.name_label = Gtk.Label(label=state.name, xalign=0, hexpand=True, css_classes=["heading"])
        self.name_label.set_ellipsize(Pango.EllipsizeMode.END)
        top.append(self.name_label)
        self.badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        top.append(self.badges)
        box.append(top)

        home = os.path.expanduser("~")
        shown = state.path
        if shown.startswith(home):
            shown = "~" + shown[len(home):]
        self.sub_label = Gtk.Label(label=shown, xalign=0, css_classes=["dim-label", "repo-row-path"])
        # END, not START: this line reads "branch · path", and the branch is the
        # half worth keeping when the row runs out of width.
        self.sub_label.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(self.sub_label)
        self.set_child(box)

    def refresh_badges(self):
        child = self.badges.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.badges.remove(child)
            child = nxt

        st = self.state.status
        if self.state.error:
            self.badges.append(widgets.pill("!", "badge-behind"))
            return
        if st is None:
            return
        if st.ahead:
            self.badges.append(widgets.pill(f"↑{st.ahead}", "badge-ahead"))
        if st.behind:
            self.badges.append(widgets.pill(f"↓{st.behind}", "badge-behind"))
        if st.entries:
            self.badges.append(widgets.pill(f"●{len(st.entries)}", "badge-dirty"))
        if st.state:
            self.badges.append(widgets.pill(st.state, "badge-behind"))

        branch = st.branch or "detached"
        home = os.path.expanduser("~")
        shown = self.state.path
        if shown.startswith(home):
            shown = "~" + shown[len(home):]
        self.sub_label.set_label(f"{branch}  ·  {shown}")


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Git Manager")
        self.cfg = config.Config()
        self.set_default_size(self.cfg["window_width"], self.cfg["window_height"])
        self.states = []
        self.selected = None
        self.queue = jobs.Serial()
        self._scan_generation = 0

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)
        self.split = Adw.NavigationSplitView(min_sidebar_width=280, max_sidebar_width=420)
        self.toasts.set_child(self.split)

        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())

        self._install_actions()
        self.connect("close-request", self._on_close)
        self.connect("notify::is-active", self._on_active_changed)

        self._load_repos()
        GLib.timeout_add_seconds(max(5, self.cfg["status_poll_seconds"]), self._poll)

    # ------------------------------------------------------------ sidebar --

    def _build_sidebar(self):
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Repositories"))

        rescan = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Rescan the filesystem for repositories")
        rescan.connect("clicked", lambda _b: self.rescan())
        header.pack_start(rescan)

        menu = Gio.Menu()
        repo_section = Gio.Menu()
        repo_section.append("Clone from GitHub…", "win.clone")
        repo_section.append("Connect a folder to GitHub…", "win.connect-github")
        repo_section.append("Add existing folder…", "win.add-folder")
        repo_section.append("Fetch all repositories", "win.fetch-all")
        menu.append_section(None, repo_section)
        view_section = Gio.Menu()
        view_section.append("File system…", "win.browse")
        menu.append_section(None, view_section)
        app_section = Gio.Menu()
        app_section.append("Scan settings…", "win.settings")
        app_section.append("About Git Manager", "win.about")
        menu.append_section(None, app_section)
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu")
        header.pack_end(menu_button)

        self.search = Gtk.SearchEntry(placeholder_text="Filter repositories")
        self.search.connect("search-changed", lambda _e: self.repo_list.invalidate_filter())
        search_bar = Gtk.Box(margin_top=4, margin_bottom=6, margin_start=8, margin_end=8)
        self.search.set_hexpand(True)
        search_bar.append(self.search)

        self.repo_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.repo_list.set_filter_func(self._filter_repo)
        self.repo_list.connect("row-selected", self._on_repo_selected)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.repo_list)

        self.scan_label = Gtk.Label(
            label="Scanning…", xalign=0, css_classes=["dim-label", "repo-row-path"],
            margin_top=4, margin_bottom=6, margin_start=12, margin_end=12,
        )

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(scroller)
        body.append(self.scan_label)

        toolbar = Adw.ToolbarView(content=body)
        toolbar.add_top_bar(header)
        toolbar.add_top_bar(search_bar)
        return Adw.NavigationPage(title="Repositories", child=toolbar)

    def _filter_repo(self, row):
        needle = self.search.get_text().strip().lower()
        if not needle:
            return True
        return needle in row.state.name.lower() or needle in row.state.path.lower()

    # ------------------------------------------------------------ content --

    def _build_content(self):
        self.title_widget = Adw.WindowTitle(title="Git Manager", subtitle="No repository selected")

        self.header = Adw.HeaderBar()
        self.header.set_title_widget(self.title_widget)

        # Text rather than icons: this machine's icon theme renders several
        # symbolic names blank, and "Push" is worth being unambiguous about.
        self.fetch_btn = Gtk.Button(label="Fetch", tooltip_text="Fetch all remotes (F5)")
        self.fetch_btn.connect("clicked", lambda _b: self.do_fetch())
        self.pull_btn = Gtk.Button(label="Pull", tooltip_text="Pull the current branch")
        self.pull_btn.connect("clicked", lambda _b: self.do_pull())
        self.push_btn = Gtk.Button(label="Push", tooltip_text="Push the current branch (Ctrl+P)")
        self.push_btn.connect("clicked", lambda _b: self.do_push())
        transport = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, css_classes=["linked"])
        for b in (self.fetch_btn, self.pull_btn, self.push_btn):
            transport.append(b)
        self.header.pack_start(transport)

        repo_menu = Gio.Menu()
        work = Gio.Menu()
        work.append("Stash changes…", "win.stash")
        work.append("Discard all changes…", "win.discard-all")
        work.append("Force push (with lease)…", "win.force-push")
        work.append("Hard reset to upstream…", "win.hard-reset")
        repo_menu.append_section("Working tree", work)
        scaffold = Gio.Menu()
        scaffold.append("Gitignore helper…", "win.gitignore")
        scaffold.append("README helper…", "win.readme")
        repo_menu.append_section("Project files", scaffold)
        remote_section = Gio.Menu()
        remote_section.append("Connect to GitHub…", "win.connect-github")
        repo_menu.append_section("Remote", remote_section)
        openers = Gio.Menu()
        openers.append("Open on GitHub", "win.open-github")
        openers.append("Open folder", "win.open-folder")
        openers.append("Open in terminal", "win.open-terminal")
        openers.append("Copy path", "win.copy-path")
        openers.append("Forget this folder", "win.forget-folder")
        repo_menu.append_section(None, openers)
        self.repo_menu_button = Gtk.MenuButton(label="Actions", menu_model=repo_menu, tooltip_text="Repository actions")
        self.header.pack_end(self.repo_menu_button)

        # Gtk.Stack + StackSwitcher rather than the Adw pair: Adw.ViewSwitcher
        # always reserves an icon slot, and every tab would carry a
        # missing-image glyph under this machine's icon theme.
        self.stack = Gtk.Stack()
        self.changes = ChangesTab(self)
        self.history = HistoryTab(self)
        self.branches = BranchesTab(self)
        self.ghtab = GitHubTab(self)
        self.stack.add_titled(self.changes, "changes", "Changes")
        self.stack.add_titled(self.history, "history", "History")
        self.stack.add_titled(self.branches, "branches", "Branches")
        self.stack.add_titled(self.ghtab, "github", "GitHub")
        self.stack.connect("notify::visible-child", lambda *_: self.refresh_tabs())

        switcher = Gtk.StackSwitcher(stack=self.stack)
        switcher_bar = Gtk.Box(halign=Gtk.Align.CENTER, margin_top=2, margin_bottom=2)
        switcher_bar.append(switcher)

        self.empty = Adw.StatusPage(
            icon_name="folder-symbolic",
            title="No repository selected",
            description="Pick one from the list, or scan again if it's missing.",
        )
        self.content_stack = Gtk.Stack()
        self.content_stack.add_named(self.empty, "empty")
        self.content_stack.add_named(self.stack, "repo")
        self.content_stack.set_visible_child_name("empty")

        toolbar = Adw.ToolbarView(content=self.content_stack)
        toolbar.add_top_bar(self.header)
        toolbar.add_top_bar(switcher_bar)
        return Adw.NavigationPage(title="Repository", child=toolbar)

    # ------------------------------------------------------------ actions --

    def _install_actions(self):
        for name, handler in (
            ("clone", lambda *_: self.ghtab.clone_dialog()),
            ("add-folder", lambda *_: self._add_folder()),
            ("connect-github", lambda *_: self.connect_github()),
            ("fetch-all", lambda *_: self.fetch_all()),
            ("settings", lambda *_: self._settings_dialog()),
            ("about", lambda *_: self._about()),
            ("stash", lambda *_: self.changes.stash_dialog()),
            ("discard-all", lambda *_: self.changes.discard_all()),
            ("force-push", lambda *_: self.do_push(force=True)),
            ("hard-reset", lambda *_: self.hard_reset_upstream()),
            ("gitignore", lambda *_: ignore.open_dialog(self, self.selected)),
            ("readme", lambda *_: readme.open_dialog(self, self.selected)),
            ("open-github", lambda *_: self._open_github()),
            ("open-folder", lambda *_: self._open_folder()),
            ("open-terminal", lambda *_: self._open_terminal()),
            ("copy-path", lambda *_: self._copy_path()),
            ("forget-folder", lambda *_: self._forget_folder()),
            ("browse", lambda *_: self.show_filesystem()),
            ("refresh", lambda *_: self.refresh_selected(force=True)),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

        app = self.get_application()
        app.set_accels_for_action("win.refresh", ["F5"])
        app.set_accels_for_action("win.push", ["<Control>p"])
        push = Gio.SimpleAction.new("push", None)
        push.connect("activate", lambda *_: self.do_push())
        self.add_action(push)

    # -------------------------------------------------------------- repos --

    def _extra_refs(self):
        """Hand-added repositories, dropped from the list once they disappear."""
        refs, keep = [], []
        for path in self.cfg["extra_repos"]:
            if not gitcmd.is_repo(path):
                continue
            keep.append(path)
            refs.append(scanner.RepoRef(path=path, name=os.path.basename(path.rstrip("/")) or path))
        if keep != self.cfg["extra_repos"]:
            self.cfg["extra_repos"] = keep
            self.cfg.save()
        return refs

    def _with_extras(self, refs):
        """Scan results plus the hand-added repos the scan cannot reach."""
        known = {r.path for r in refs}
        merged = list(refs) + [r for r in self._extra_refs() if r.path not in known]
        merged.sort(key=lambda r: r.name.lower())
        return merged

    def _load_repos(self):
        cached = scanner.load_cache()
        if cached:
            self._apply_repos(self._with_extras(cached), from_cache=True)
        self.rescan()

    def rescan(self):
        self._scan_generation += 1
        generation = self._scan_generation
        self.scan_label.set_label("Scanning…")
        roots = self.cfg["roots"]
        depth = self.cfg["max_depth"]

        def work():
            return scanner.scan(roots, max_depth=depth,
                                should_stop=lambda: generation != self._scan_generation)

        def done(found):
            if generation != self._scan_generation:
                return
            scanner.save_cache(found)
            self._apply_repos(self._with_extras(found))

        jobs.run(work, done, lambda e: widgets.error_toast(self, e))

    def _apply_repos(self, refs, from_cache=False):
        existing = {s.path: s for s in self.states}
        states = []
        for ref in refs:
            states.append(existing.get(ref.path) or RepoState(ref))
        states.sort(key=lambda s: s.name.lower())
        self.states = states

        self.repo_list.remove_all()
        for state in states:
            state.row = None
            row = RepoRow(state)
            row.refresh_badges()
            self.repo_list.append(row)

        roots = ", ".join(r.replace(os.path.expanduser("~"), "~") for r in self.cfg["roots"])
        suffix = " (cached)" if from_cache else ""
        self.scan_label.set_label(f"{len(states)} repositories in {roots}{suffix}")

        # Reselect whatever was open before the rescan, else the last session's.
        want = (self.selected.path if self.selected else "") or self.cfg["last_repo"]
        target = next((s for s in states if s.path == want), None)
        if target and target.row:
            self.repo_list.select_row(target.row)
        elif states and not self.selected:
            self.repo_list.select_row(states[0].row)

        self.queue.clear()
        for state in states:
            self._queue_status(state)

    def _queue_status(self, state):
        def work():
            st = gitcmd.status(state.path)
            remote, nwo = github.nwo_from_remotes(gitcmd.remotes(state.path))
            return st, remote, nwo

        def done(result):
            state.status, state.remote, state.nwo = result
            state.error = None
            if state.row:
                state.row.refresh_badges()
            if state is self.selected:
                self._sync_header()

        def failed(exc):
            state.error = str(exc)
            if state.row:
                state.row.refresh_badges()

        self.queue.submit(work, done, failed)

    def select_path_arg(self, path):
        """Select the repo containing `path` — used by `git-manager .`."""
        try:
            top = gitcmd.toplevel(os.path.abspath(path))
        except (gitcmd.GitError, OSError):
            widgets.toast(self, f"{path} is not inside a git repository")
            return
        state = next((s for s in self.states if s.path == top), None)
        if state and state.row:
            self.repo_list.select_row(state.row)
        else:
            # Outside every scan root — add it for this session.
            state = RepoState(scanner.RepoRef(path=top, name=os.path.basename(top)))
            self.states.append(state)
            self._apply_repos([s.ref for s in sorted(self.states, key=lambda s: s.name.lower())])
            if state.row:
                self.repo_list.select_row(state.row)

    def _on_repo_selected(self, _list, row):
        if row is None:
            return
        self.selected = row.state
        self.cfg["last_repo"] = row.state.path
        self.content_stack.set_visible_child_name("repo")
        self._sync_header()
        self.refresh_selected(force=True)

    def refresh_selected(self, force=False):
        if not self.selected:
            return
        state = self.selected

        def work():
            st = gitcmd.status(state.path)
            remote, nwo = github.nwo_from_remotes(gitcmd.remotes(state.path))
            return st, remote, nwo

        previous_oid = state.status.oid if state.status else None

        def done(result):
            state.status, state.remote, state.nwo = result
            state.error = None
            if state.row:
                state.row.refresh_badges()
            self._sync_header()
            # A new HEAD means the log on screen is stale — commit, merge, pull
            # and reset all land here.
            if state.status.oid != previous_oid:
                self.history.invalidate()
            self.refresh_tabs()

        def failed(exc):
            state.error = str(exc)
            self._sync_header()
            widgets.error_toast(self, exc)

        jobs.run(work, done, failed)

    def refresh_tabs(self):
        page = self.stack.get_visible_child()
        if page and hasattr(page, "refresh"):
            page.refresh()

    def _sync_header(self):
        state = self.selected
        if not state:
            self.title_widget.set_title("Git Manager")
            self.title_widget.set_subtitle("No repository selected")
            return
        self.title_widget.set_title(state.name)
        st = state.status
        if state.error:
            self.title_widget.set_subtitle("unreadable — " + state.error.splitlines()[0][:60])
            return
        if not st:
            self.title_widget.set_subtitle("reading…")
            return
        bits = [st.branch or "detached HEAD"]
        if st.upstream:
            track = []
            if st.ahead:
                track.append(f"↑{st.ahead}")
            if st.behind:
                track.append(f"↓{st.behind}")
            bits.append(" ".join(track) if track else "in sync")
        else:
            bits.append("no upstream")
        if st.entries:
            bits.append(f"{len(st.entries)} changed")
        if st.state:
            bits.append(f"{st.state} in progress")
        self.title_widget.set_subtitle("  ·  ".join(bits))

    def _poll(self):
        if self.is_active() and self.selected:
            self.refresh_selected()
        return GLib.SOURCE_CONTINUE

    def _on_active_changed(self, *_):
        # Files change in an editor while this window sits in the background.
        if self.is_active() and self.selected:
            self.refresh_selected()

    # ---------------------------------------------------------- transport --

    def _run_git(self, label, fn, then=None):
        """Run a git operation with a busy header and toast on completion."""
        if not self.selected:
            return
        state = self.selected
        self.fetch_btn.set_sensitive(False)
        self.pull_btn.set_sensitive(False)
        self.push_btn.set_sensitive(False)
        self.title_widget.set_subtitle(f"{label}…")

        def restore():
            self.fetch_btn.set_sensitive(True)
            self.pull_btn.set_sensitive(True)
            self.push_btn.set_sensitive(True)

        def done(output):
            restore()
            widgets.toast(self, f"{label}: {state.name} — done")
            github.invalidate(state.nwo or "")
            self.refresh_selected(force=True)
            if then:
                then(output)

        def failed(exc):
            restore()
            self._sync_header()
            widgets.error_toast(self, exc)

        jobs.run(fn, done, failed)

    def do_fetch(self):
        if not self.selected:
            return
        path = self.selected.path
        self._run_git("Fetch", lambda: gitcmd.fetch(path))

    def do_pull(self):
        if not self.selected:
            return
        path = self.selected.path
        self._run_git("Pull", lambda: gitcmd.pull(path))

    def do_push(self, force=False):
        state = self.selected
        if not state:
            return
        st = state.status
        path = state.path
        if st and not st.upstream and st.branch:
            branch = st.branch

            def push_upstream():
                self._run_git(
                    "Push",
                    lambda: gitcmd.push(path, "origin", branch, set_upstream=True),
                )

            widgets.confirm(
                self, "Publish this branch?",
                f"“{branch}” has no upstream yet. Push it to origin and start tracking it?",
                "Publish", push_upstream, destructive=False,
            )
            return

        if force:
            widgets.confirm(
                self, "Force push?",
                f"This rewrites “{st.branch if st else 'the branch'}” on the remote. "
                "It uses --force-with-lease, so it will refuse if someone else has "
                "pushed since your last fetch — but any of your own commits that "
                "aren't in this branch will be gone from the remote.",
                "Force push",
                lambda: self._run_git("Force push", lambda: gitcmd.push(path, force=True)),
            )
            return
        self._run_git("Push", lambda: gitcmd.push(path))

    def fetch_all(self):
        targets = list(self.states)
        if not targets:
            return
        widgets.toast(self, f"Fetching {len(targets)} repositories…")
        done_count = {"n": 0}

        def after(state):
            def _(_result):
                done_count["n"] += 1
                self._queue_status(state)
                if done_count["n"] == len(targets):
                    widgets.toast(self, f"Fetched {len(targets)} repositories")
            return _

        for state in targets:
            path = state.path
            self.queue.submit(lambda p=path: gitcmd.fetch(p), after(state), lambda _e: None)

    def hard_reset_upstream(self):
        state = self.selected
        if not state or not state.status:
            return
        st = state.status
        if not st.upstream:
            widgets.toast(self, "This branch has no upstream to reset to")
            return
        path, upstream = state.path, st.upstream
        lost = []
        if st.ahead:
            lost.append(f"{st.ahead} unpushed commit{'s' if st.ahead != 1 else ''}")
        if st.entries:
            lost.append(f"{len(st.entries)} uncommitted file{'s' if len(st.entries) != 1 else ''}")
        detail = " and ".join(lost) if lost else "nothing — you are already in sync"
        widgets.confirm(
            self, f"Hard reset to {upstream}?",
            f"This throws away {detail}. There is no undo for the uncommitted part.",
            "Reset hard",
            lambda: self._run_git("Hard reset", lambda: gitcmd.reset(path, upstream, "hard")),
        )

    # -------------------------------------------------------------- misc ---

    def _open_github(self):
        state = self.selected
        if state and state.nwo:
            widgets.open_url(self, f"https://github.com/{state.nwo}")
        else:
            widgets.toast(self, "No GitHub remote on this repository")

    def _open_folder(self):
        if self.selected:
            widgets.open_url(self, GLib.filename_to_uri(self.selected.path, None))

    def _open_terminal(self):
        if not self.selected:
            return
        for term, args in (
            ("xfce4-terminal", ["--working-directory"]),
            ("gnome-terminal", ["--working-directory"]),
            ("alacritty", ["--working-directory"]),
            ("kitty", ["--directory"]),
            ("foot", ["--working-directory"]),
        ):
            exe = shutil.which(term)
            if exe:
                subprocess.Popen([exe, *args, self.selected.path])
                return
        widgets.toast(self, "No supported terminal emulator found")

    def _copy_path(self):
        if self.selected:
            self.get_clipboard().set(self.selected.path)
            widgets.toast(self, "Path copied")

    def show_filesystem(self):
        """Open the file system view: repositories where they really live."""
        dialog = Adw.Dialog(title="File system", content_width=920, content_height=720)
        view = filesystem.FilesystemView(self)

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(
            title="File system",
            subtitle=f"Backup audit of {filesystem.tilde(self.cfg['backup_root'])}",
        ))
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Audit again")
        refresh.connect("clicked", lambda _b: view.refresh())
        header.pack_start(refresh)

        risky = Gtk.ToggleButton(label="Only what isn't backed up")
        risky.connect("toggled", lambda b: view.set_only_risky(b.get_active()))
        header.pack_end(risky)

        toolbar = Adw.ToolbarView(content=view)
        toolbar.add_top_bar(header)
        dialog.set_child(toolbar)
        # Selecting a repository from the tree should reveal it in the window
        # behind, so close on activation rather than stacking two views.
        view.on_repo_chosen = lambda: dialog.close()
        dialog.present(self)
        view.refresh()

    def _forget_folder(self):
        """Drop a hand-added repository from the list. Nothing is deleted."""
        state = self.selected
        if not state:
            return
        if state.path not in self.cfg["extra_repos"]:
            widgets.toast(self, "This one comes from the scan — change the scan roots instead")
            return
        self.cfg["extra_repos"] = [p for p in self.cfg["extra_repos"] if p != state.path]
        self.cfg.save()
        self.selected = None
        self._apply_repos([s.ref for s in self.states if s.path != state.path])
        widgets.toast(self, f"Forgot {state.name}")

    def adopt_path(self, path, select=True):
        """Make sure a repository is in the sidebar, and select it.

        Remembering it matters: the next scan would drop a repository outside
        every root, and one nested inside another working tree is never found
        by scanning at all.
        """
        if not path or not gitcmd.is_repo(path):
            return None
        try:
            top = gitcmd.toplevel(path)
        except (gitcmd.GitError, OSError):
            return None
        state = next((s for s in self.states if s.path == top), None)
        if state is None:
            if top not in self.cfg["extra_repos"]:
                self.cfg["extra_repos"] = self.cfg["extra_repos"] + [top]
                self.cfg.save()
            state = RepoState(scanner.RepoRef(path=top, name=os.path.basename(top)))
            self.states.append(state)
            self.states.sort(key=lambda s: s.name.lower())
            self._apply_repos([s.ref for s in self.states])
        if select and state.row:
            if self.repo_list.get_selected_row() is state.row:
                self.refresh_selected(force=True)
            else:
                self.repo_list.select_row(state.row)  # selecting refreshes it
        return state

    def connect_github(self, path=None):
        """Point a folder that already exists at a repository that already does."""
        if path is None:
            state = self.selected
            # Only prefill a repository with nothing to lose: offering to
            # repoint a remote that already works is how accidents happen.
            path = state.path if state and not state.nwo else ""
        link.open_dialog(self, path)

    def _add_folder(self):
        chooser = Gtk.FileDialog(title="Choose a repository or a folder to scan")

        def picked(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            path = folder.get_path()
            if not path:
                return
            if gitcmd.is_repo(path):
                top = gitcmd.toplevel(path)
                if os.path.realpath(top) != os.path.realpath(path):
                    self._folder_inside_repo(path, top)
                    return
                if any(s.path == top for s in self.states):
                    widgets.toast(self, "Already in the list")
                    self.adopt_path(top)
                    return
                state = self.adopt_path(top)
                if state:
                    widgets.toast(self, f"Added {state.name}")
            else:
                self._not_a_repo(path)

        chooser.select_folder(self, None, picked)

    def _folder_inside_repo(self, path, top):
        """A folder that is part of a repository, rather than being one.

        Adding it adds the repository around it, under a name the user did
        not pick — which looks like the app ignoring the folder they chose.
        The other reading, that this folder is about to become a project of
        its own, is just as ordinary, so it is a question rather than a guess.
        """
        name = os.path.basename(top.rstrip("/")) or top
        dialog = Adw.AlertDialog(
            heading="Inside another repository",
            body=f"{filesystem.tilde(path)} is part of the repository at "
                 f"{filesystem.tilde(top)}, so that is what adding it would add.\n\n"
                 "It can become a repository in its own right instead — git allows "
                 "one inside another — and Connect to GitHub… writes out what that "
                 f"leaves {name} tracking before anything is created.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("parent", f"Add {name}")
        dialog.add_response("connect", "Connect it to GitHub…")
        dialog.set_response_appearance("connect", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("connect")
        dialog.set_close_response("cancel")

        def answered(dlg, result):
            answer = dlg.choose_finish(result)
            if answer == "connect":
                self.connect_github(path)
            elif answer == "parent":
                if any(s.path == top for s in self.states):
                    widgets.toast(self, "Already in the list")
                    self.adopt_path(top)
                    return
                state = self.adopt_path(top)
                if state:
                    widgets.toast(self, f"Added {state.name}")

        dialog.choose(self, None, answered)

    def _not_a_repo(self, path):
        """A folder with no .git in it. Ask which of the three things was meant.

        Silently turning it into a scan root — the old behaviour — is right
        for ~/Projects and wrong for a project folder, and the two are
        indistinguishable from here.
        """
        dialog = Adw.AlertDialog(
            heading="Not a repository yet",
            body=f"{filesystem.tilde(path)} has no .git in it, so there is nothing "
                 "to show about it yet.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("root", "Scan it for repositories")
        dialog.add_response("init", "Start a repository here")
        dialog.add_response("connect", "Connect it to GitHub…")
        dialog.set_response_appearance("connect", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("connect")
        dialog.set_close_response("cancel")

        def answered(dlg, result):
            answer = dlg.choose_finish(result)
            if answer == "connect":
                self.connect_github(path)
            elif answer == "init":
                self._init_repo(path)
            elif answer == "root":
                roots = list(self.cfg["roots"])
                if path not in roots:
                    roots.append(path)
                    self.cfg["roots"] = roots
                    self.cfg.save()
                widgets.toast(self, f"Scanning {filesystem.tilde(path)} from now on")
                self.rescan()

        dialog.choose(self, None, answered)

    def _init_repo(self, path):
        def work():
            gitcmd.init(path)
            return path

        def done(_p):
            state = self.adopt_path(path)
            widgets.toast(self, f"Started a repository in {filesystem.tilde(path)}")
            if state:
                self.stack.set_visible_child_name("changes")

        jobs.run(work, done, lambda e: widgets.error_toast(self, e))

    def _settings_dialog(self):
        dialog = Adw.PreferencesDialog(title="Scan settings")
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title="Scan roots",
            description="Folders walked when looking for repositories, one per line.",
        )
        buf = Gtk.TextView(monospace=True, top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        buf.get_buffer().set_text("\n".join(self.cfg["roots"]))
        frame = Gtk.Frame(child=buf, height_request=120)
        group.add(frame)

        depth = Adw.SpinRow.new_with_range(1, 20, 1)
        depth.set_title("Maximum depth")
        depth.set_subtitle("How deep below each root to look")
        depth.set_value(self.cfg["max_depth"])
        group.add(depth)
        page.add(group)

        safety = Adw.PreferencesGroup(title="Safety")
        poll = Adw.SpinRow.new_with_range(5, 300, 5)
        poll.set_title("Status refresh (seconds)")
        poll.set_value(self.cfg["status_poll_seconds"])
        safety.add(poll)
        clone_row = Adw.EntryRow(title="Default clone folder", text=self.cfg["clone_dir"])
        safety.add(clone_row)
        page.add(safety)

        backup = Adw.PreferencesGroup(
            title="Backup audit",
            description="The file system view flags folders here that no remote has a copy of.",
        )
        backup_row = Adw.EntryRow(title="Folder to audit", text=self.cfg["backup_root"])
        pick = Gtk.Button(icon_name="folder-symbolic", valign=Gtk.Align.CENTER,
                          tooltip_text="Choose a folder")

        def choose_backup(_button):
            chooser = Gtk.FileDialog(title="Folder to audit for backups")

            def picked(d, result):
                try:
                    folder = d.select_folder_finish(result)
                except GLib.Error:
                    return
                if folder and folder.get_path():
                    backup_row.set_text(folder.get_path())

            chooser.select_folder(self, None, picked)

        pick.connect("clicked", choose_backup)
        backup_row.add_suffix(pick)
        backup.add(backup_row)
        backup_depth = Adw.SpinRow.new_with_range(1, 10, 1)
        backup_depth.set_title("Audit depth")
        backup_depth.set_subtitle("How deep below that folder to look")
        backup_depth.set_value(self.cfg["backup_max_depth"])
        backup.add(backup_depth)
        page.add(backup)
        dialog.add(page)

        def closed(*_):
            text_buf = buf.get_buffer()
            raw = text_buf.get_text(text_buf.get_start_iter(), text_buf.get_end_iter(), False)
            roots = [r.strip() for r in raw.splitlines() if r.strip()]
            changed = roots != self.cfg["roots"] or int(depth.get_value()) != self.cfg["max_depth"]
            if roots:
                self.cfg["roots"] = roots
            self.cfg["max_depth"] = int(depth.get_value())
            self.cfg["status_poll_seconds"] = int(poll.get_value())
            self.cfg["clone_dir"] = clone_row.get_text().strip() or self.cfg["clone_dir"]
            chosen = os.path.expanduser(backup_row.get_text().strip())
            if chosen and os.path.isdir(chosen):
                self.cfg["backup_root"] = chosen
            elif chosen:
                widgets.toast(self, f"{chosen} is not a folder — audit folder unchanged")
            self.cfg["backup_max_depth"] = int(backup_depth.get_value())
            self.cfg.save()
            if changed:
                self.rescan()

        dialog.connect("closed", closed)
        dialog.present(self)

    def _about(self):
        account = "not signed in"
        about = Adw.AboutDialog(
            application_name="Git Manager",
            developer_name="Built for this machine",
            version="1.0",
            comments=(
                "Finds every git repository on disk, shows what changed, and drives "
                "git and GitHub from one window.\n\n"
                f"GitHub access comes from the gh CLI ({account})."
            ),
            license_type=Gtk.License.MIT_X11,
        )
        about.present(self)

    def _on_close(self, *_):
        width, height = self.get_default_size()
        self.cfg["window_width"] = width
        self.cfg["window_height"] = height
        self.cfg.save()
        return False
