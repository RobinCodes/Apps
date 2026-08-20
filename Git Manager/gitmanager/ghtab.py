"""The GitHub tab: pull requests, issues, CI runs, cloning and repo creation."""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from . import github, gitcmd, jobs, widgets  # noqa: E402

TTL = 120  # seconds before a tab switch re-hits the API


class GitHubTab(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.account = None
        self.showing = None      # nwo currently rendered
        self._rows = {}

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                         margin_top=10, margin_bottom=4, margin_start=12, margin_end=12)
        self.account_label = Gtk.Label(xalign=0, hexpand=True, css_classes=["dim-label"])
        header.append(self.account_label)
        self.reload_btn = Gtk.Button(icon_name="view-refresh-symbolic", css_classes=["flat"],
                                     tooltip_text="Reload from GitHub")
        self.reload_btn.connect("clicked", lambda _b: self.refresh(force=True))
        header.append(self.reload_btn)
        self.append(header)

        self.prs = Adw.PreferencesGroup(title="Pull requests")
        self.issues = Adw.PreferencesGroup(title="Issues", margin_top=12)
        self.runs = Adw.PreferencesGroup(title="Recent workflow runs", margin_top=12)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin_bottom=10)
        for label, handler, css in (
            ("New pull request", self._new_pr, "suggested-action"),
            ("Open repository", self._open_repo, None),
            ("Clone from GitHub…", self.clone_dialog, None),
        ):
            btn = Gtk.Button(label=label, css_classes=(["flat"] if not css else [css]))
            btn.connect("clicked", lambda _b, h=handler: h())
            actions.append(btn)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                            margin_top=6, margin_bottom=16, margin_start=12, margin_end=12)
        self.body.append(actions)
        self.body.append(self.prs)
        self.body.append(self.issues)
        self.body.append(self.runs)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.body)

        self.unlinked = Adw.StatusPage(
            icon_name="network-offline-symbolic",
            title="No GitHub remote",
            description="This folder has no github.com remote. Make a new repository out of "
                        "it, or connect it to one that already exists — pull requests, issues "
                        "and CI appear here either way.",
        )
        choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.CENTER)
        publish = Gtk.Button(label="Publish as a new repository…",
                             css_classes=["suggested-action", "pill"])
        publish.connect("clicked", lambda _b: self._publish_dialog())
        choices.append(publish)
        connect = Gtk.Button(label="Connect to an existing repository…", css_classes=["pill"])
        connect.connect("clicked", lambda _b: self._connect_dialog())
        choices.append(connect)
        self.unlinked.set_child(choices)

        self.no_auth = Adw.StatusPage(
            icon_name="dialog-password-symbolic",
            title="GitHub CLI not ready",
            description="Run gh auth login in a terminal, then reload.",
        )

        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(scroller, "repo")
        self.stack.add_named(self.unlinked, "unlinked")
        self.stack.add_named(self.no_auth, "noauth")
        self.append(self.stack)

    # ----------------------------------------------------------- loading ---

    def refresh(self, force=False):
        state = self.win.selected
        if not state:
            return

        if self.account is None:
            jobs.run(github.auth_status, self._got_auth, lambda e: widgets.error_toast(self, e))
            return
        if not self.account.ok:
            self.no_auth.set_description(self.account.message or "Run gh auth login, then reload.")
            self.stack.set_visible_child_name("noauth")
            return

        nwo = state.nwo
        if not nwo:
            self.stack.set_visible_child_name("unlinked")
            return

        self.stack.set_visible_child_name("repo")
        if nwo == self.showing and not force:
            return
        self.showing = nwo
        if force:
            github.invalidate(nwo)
        self._set_group_loading()

        def work():
            return (
                github.cached((nwo, "pr"), TTL, lambda: github.pull_requests(nwo)),
                github.cached((nwo, "issue"), TTL, lambda: github.issues(nwo)),
                github.cached((nwo, "run"), TTL, lambda: github.runs(nwo)),
            )

        def done(result):
            if self.showing != nwo:
                return
            self._render(*result)

        def failed(exc):
            if self.showing == nwo:
                self._render_error(exc)

        jobs.run(work, done, failed)

    def _got_auth(self, account):
        self.account = account
        if account.ok:
            self.account_label.set_label(f"Signed in as {account.login}  ·  {', '.join(account.scopes)}")
        else:
            self.account_label.set_label(account.message)
        self.refresh()

    def _set_group_loading(self):
        for group, title in ((self.prs, "Pull requests"), (self.issues, "Issues"), (self.runs, "Recent workflow runs")):
            self._reset(group)
            group.set_description("Loading…")

    def _reset(self, group):
        for row in self._rows.get(id(group), []):
            group.remove(row)
        self._rows[id(group)] = []

    def _add(self, group, row):
        group.add(row)
        self._rows.setdefault(id(group), []).append(row)

    def _render_error(self, exc):
        for group in (self.prs, self.issues, self.runs):
            self._reset(group)
            group.set_description("")
        self._add(self.prs, Adw.ActionRow(title="Couldn't reach GitHub", subtitle=str(exc).splitlines()[0][:120]))

    def _render(self, prs, issues, runs):
        self._reset(self.prs)
        self.prs.set_description(f"{len(prs)} open" if prs else "None open")
        for pr in prs:
            bits = [f"#{pr.number}", pr.author, f"{pr.head} → {pr.base}", pr.updated]
            if pr.review:
                bits.append(pr.review)
            row = Adw.ActionRow(title=("Draft: " if pr.draft else "") + pr.title,
                                subtitle="  ·  ".join(b for b in bits if b))
            row.set_title_lines(1)
            row.add_suffix(self._open_button(pr.url))
            row.set_activatable(True)
            row.connect("activated", lambda _r, u=pr.url: widgets.open_url(self, u))
            self._add(self.prs, row)

        self._reset(self.issues)
        self.issues.set_description(f"{len(issues)} open" if issues else "None open")
        for issue in issues:
            bits = [f"#{issue.number}", issue.author, issue.updated]
            if issue.labels:
                bits.append(", ".join(issue.labels[:3]))
            if issue.comments:
                bits.append(f"{issue.comments} comment{'s' if issue.comments != 1 else ''}")
            row = Adw.ActionRow(title=issue.title, subtitle="  ·  ".join(b for b in bits if b))
            row.set_title_lines(1)
            row.add_suffix(self._open_button(issue.url))
            row.set_activatable(True)
            row.connect("activated", lambda _r, u=issue.url: widgets.open_url(self, u))
            self._add(self.issues, row)

        self._reset(self.runs)
        self.runs.set_description(f"{len(runs)} recent" if runs else "No workflow runs")
        for run in runs:
            if run.running:
                mark, css = "○", None
            elif run.ok:
                mark, css = "✓", "badge-ci-ok"
            else:
                mark, css = "✗", "badge-ci-fail"
            state_text = run.status if run.running else (run.conclusion or "unknown")
            row = Adw.ActionRow(title=run.name,
                                subtitle=f"{state_text}  ·  {run.branch}  ·  {run.event}  ·  {run.created}")
            row.set_title_lines(1)
            marker = Gtk.Label(label=mark, css_classes=[css] if css else [], valign=Gtk.Align.CENTER)
            row.add_prefix(marker)
            row.add_suffix(self._open_button(run.url))
            row.set_activatable(True)
            row.connect("activated", lambda _r, u=run.url: widgets.open_url(self, u))
            self._add(self.runs, row)

    def _open_button(self, url):
        btn = Gtk.Button(label="Open", css_classes=["flat"],
                         tooltip_text="Open in browser", valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda _b: widgets.open_url(self, url))
        return btn

    # ----------------------------------------------------------- actions ---

    def _open_repo(self):
        state = self.win.selected
        if state and state.nwo:
            widgets.open_url(self, f"https://github.com/{state.nwo}")

    def _new_pr(self):
        state = self.win.selected
        if not state or not state.nwo:
            widgets.toast(self, "This repository has no GitHub remote")
            return
        st = state.status
        if st and st.ahead == 0 and st.upstream:
            widgets.toast(self, "Nothing to propose — this branch matches its upstream")
            return
        path, nwo = state.path, state.nwo

        def go(title):
            def work():
                return github.create_pr(path, title)

            def done(url):
                github.invalidate(nwo)
                widgets.toast(self, "Pull request opened")
                if url:
                    widgets.open_url(self, url.split()[-1])
                self.refresh(force=True)

            jobs.run(work, done, lambda e: widgets.error_toast(self, e))

        branch = st.branch if st else "this branch"
        widgets.prompt(
            self.win, "Open a pull request",
            f"Proposes {branch} against the repository's default branch. "
            "Push the branch first if you haven't.",
            "Create", go, placeholder="Pull request title",
            text=(gitcmd.log(state.path, limit=1)[0].subject if gitcmd.has_commits(state.path) else ""),
        )

    def _connect_dialog(self):
        state = self.win.selected
        self.win.connect_github(state.path if state else "")

    def _publish_dialog(self):
        state = self.win.selected
        if not state:
            return
        path, name = state.path, state.name

        def go(repo_name):
            def work():
                github.create_repo(repo_name, private=True, source=path, push=True)
                return gitcmd.remote_url(path)

            def done(_url):
                widgets.toast(self, f"Published {repo_name} as a private repository")
                self.showing = None
                self.win.refresh_selected(force=True)

            jobs.run(work, done, lambda e: widgets.error_toast(self, e))

        widgets.prompt(
            self.win, "Publish to GitHub",
            "Creates a private repository under your account, adds it as origin, "
            "and pushes the current branch. Make it public later on github.com.",
            "Publish", go, placeholder="repository-name", text=name,
        )

    def clone_dialog(self):
        """List everything on GitHub, marking what's already on this machine."""
        dialog = Adw.Dialog(title="Clone from GitHub", content_width=560, content_height=620)
        group = Adw.PreferencesGroup()
        page = Adw.PreferencesPage()
        page.add(group)
        toolbar = Adw.ToolbarView(content=page)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Clone from GitHub", subtitle="Loading…"))
        toolbar.add_top_bar(header)
        dialog.set_child(toolbar)
        dialog.present(self.win)

        local_nwos = {s.nwo for s in self.win.states if s.nwo}
        clone_dir = self.win.cfg["clone_dir"]

        def work():
            return github.cached("all-repos", TTL, lambda: github.list_repos())

        def done(repos):
            missing = [r for r in repos if r.nwo not in local_nwos]
            header.get_title_widget().set_subtitle(
                f"{len(missing)} of {len(repos)} not on this machine"
            )
            if not repos:
                group.add(Adw.ActionRow(title="No repositories found"))
                return
            for repo in repos:
                here = repo.nwo in local_nwos
                bits = [repo.visibility, repo.language, f"updated {repo.updated}"]
                row = Adw.ActionRow(title=repo.name,
                                    subtitle="  ·  ".join(b for b in bits if b))
                row.set_title_lines(1)
                if here:
                    tag = Gtk.Label(label="on disk", css_classes=["dim-label"], valign=Gtk.Align.CENTER)
                    row.add_suffix(tag)
                else:
                    btn = Gtk.Button(label="Clone", css_classes=["flat"], valign=Gtk.Align.CENTER)
                    btn.connect("clicked", lambda _b, r=repo, w=row: self._do_clone(r, clone_dir, w))
                    row.add_suffix(btn)
                group.add(row)

        def failed(exc):
            header.get_title_widget().set_subtitle("Couldn't reach GitHub")
            group.add(Adw.ActionRow(title="Error", subtitle=str(exc).splitlines()[0][:120]))

        jobs.run(work, done, failed)

    def _do_clone(self, repo, clone_dir, row):
        dest = os.path.join(os.path.expanduser(clone_dir), repo.name)
        if os.path.exists(dest):
            widgets.toast(self, f"{dest} already exists")
            return
        row.set_sensitive(False)
        row.set_subtitle(f"Cloning into {dest}…")

        def work():
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            github.clone(repo.nwo, dest)
            return dest

        def done(path):
            row.set_subtitle(f"Cloned to {path}")
            widgets.toast(self, f"Cloned {repo.name}")
            self.win.rescan()

        def failed(exc):
            row.set_sensitive(True)
            row.set_subtitle("Clone failed")
            widgets.error_toast(self, exc)

        jobs.run(work, done, failed)
