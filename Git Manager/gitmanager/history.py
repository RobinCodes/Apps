"""The History tab: the commit log, and what each commit changed."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gio, GObject, Gtk, Pango  # noqa: E402

from . import gitcmd, jobs, widgets  # noqa: E402

PAGE = 150


class CommitItem(GObject.Object):
    def __init__(self, commit):
        super().__init__()
        self.commit = commit


class HistoryTab(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.loaded_for = None      # (repo path, search text) currently in the list
        self.current = None         # Commit on screen
        self.files = []             # (letter, path) of the current commit

        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=430)
        split.set_start_child(self._build_list())
        split.set_end_child(self._build_detail())
        split.set_resize_start_child(False)
        split.set_resize_end_child(True)
        split.set_shrink_start_child(False)
        self.append(split)

    def _build_list(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.search = Gtk.SearchEntry(placeholder_text="Search commit messages")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", lambda _e: self._reload())
        bar = Gtk.Box(margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
        bar.append(self.search)
        box.append(bar)

        self.store = Gio.ListStore(item_type=CommitItem)
        self.selection = Gtk.SingleSelection(model=self.store, autoselect=False, can_unselect=True)
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self.selection.connect("notify::selected", lambda *_: self._on_select())

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        self.view = Gtk.ListView(model=self.selection, factory=factory, css_classes=["navigation-sidebar"])

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.view)
        box.append(scroller)

        self.more_btn = Gtk.Button(label="Load older commits", css_classes=["flat"],
                                   margin_top=4, margin_bottom=6, margin_start=8, margin_end=8)
        self.more_btn.connect("clicked", lambda _b: self._load(append=True))
        box.append(self.more_btn)
        return box

    def _setup_row(self, _f, list_item):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                        margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
        subject = Gtk.Label(xalign=0, hexpand=True)
        subject.set_ellipsize(Pango.EllipsizeMode.END)
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        sha = Gtk.Label(xalign=0, css_classes=["commit-sha", "dim-label"])
        who = Gtk.Label(xalign=0, hexpand=True, css_classes=["dim-label", "repo-row-path"])
        who.set_ellipsize(Pango.EllipsizeMode.END)
        refs = Gtk.Label(xalign=1, css_classes=["dim-label", "repo-row-path"])
        refs.set_ellipsize(Pango.EllipsizeMode.END)
        meta.append(sha)
        meta.append(who)
        meta.append(refs)
        outer.append(subject)
        outer.append(meta)
        outer.subject, outer.sha, outer.who, outer.refs = subject, sha, who, refs
        list_item.set_child(outer)

    def _bind_row(self, _f, list_item):
        box = list_item.get_child()
        c = list_item.get_item().commit
        box.subject.set_label(c.subject or "(no message)")
        box.sha.set_label(c.short)
        box.who.set_label(f"{c.author} · {c.rel}")
        box.refs.set_label(c.refs.split(",")[0].strip() if c.refs else "")

    def _build_detail(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                       margin_top=10, margin_bottom=6, margin_start=12, margin_end=12)
        self.detail_subject = Gtk.Label(xalign=0, wrap=True, css_classes=["title-4"])
        self.detail_meta = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
        self.detail_body = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.detail_body.set_visible(False)
        info.append(self.detail_subject)
        info.append(self.detail_meta)
        info.append(self.detail_body)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_top=6)
        self.file_picker = Gtk.DropDown.new_from_strings(["All files"])
        self.file_picker.connect("notify::selected", lambda *_: self._render_diff())
        self.file_picker.set_hexpand(True)
        actions.append(self.file_picker)
        for label, handler, css in (
            ("Copy SHA", self._copy_sha, None),
            ("Revert", self._revert, None),
            ("Reset here", self._reset_here, "destructive-action"),
        ):
            btn = Gtk.Button(label=label, css_classes=["flat"] + ([css] if css else []))
            btn.connect("clicked", lambda _b, h=handler: h())
            actions.append(btn)
        info.append(actions)
        box.append(info)

        self.diff = widgets.DiffView()
        box.append(self.diff)
        return box

    # ----------------------------------------------------------- loading ---

    def invalidate(self):
        """Force the next refresh to re-read the log — HEAD moved."""
        self.loaded_for = None

    def refresh(self):
        state = self.win.selected
        if not state:
            return
        key = (state.path, self.search.get_text().strip())
        if key != self.loaded_for:
            self._reload()

    def _reload(self):
        self.store.remove_all()
        self.current = None
        self.diff.placeholder("document-open-recent-symbolic", "No commit selected",
                              "Pick a commit to see what it changed.")
        self.detail_subject.set_label("")
        self.detail_meta.set_label("")
        self.detail_body.set_visible(False)
        self._load(append=False)

    def _load(self, append):
        state = self.win.selected
        if not state:
            return
        path = state.path
        search = self.search.get_text().strip()
        skip = self.store.get_n_items() if append else 0
        self.loaded_for = (path, search)
        self.more_btn.set_sensitive(False)

        def work():
            return gitcmd.log(path, limit=PAGE, skip=skip, search=search or None)

        def done(commits):
            if self.loaded_for != (path, search):
                return
            for c in commits:
                self.store.append(CommitItem(c))
            self.more_btn.set_sensitive(len(commits) == PAGE)
            self.more_btn.set_visible(len(commits) == PAGE)
            if not append and self.store.get_n_items():
                self.selection.set_selected(0)

        jobs.run(work, done, lambda e: widgets.error_toast(self, e))

    def _on_select(self):
        pos = self.selection.get_selected()
        if pos == Gtk.INVALID_LIST_POSITION:
            return
        item = self.store.get_item(pos)
        if item is None:
            return
        self.current = item.commit
        self._show(item.commit)

    def _show(self, commit):
        state = self.win.selected
        if not state:
            return
        self.detail_subject.set_label(commit.subject or "(no message)")
        refs = f"  ·  {commit.refs}" if commit.refs else ""
        merge = "  ·  merge" if commit.is_merge else ""
        self.detail_meta.set_label(
            f"{commit.short}  ·  {commit.author} <{commit.email}>  ·  {commit.when} ({commit.rel}){merge}{refs}"
        )
        body = commit.body.strip()
        self.detail_body.set_label(body)
        self.detail_body.set_visible(bool(body))

        path, sha = state.path, commit.sha

        def work():
            return gitcmd.commit_files(path, sha)

        def done(files):
            if not self.current or self.current.sha != sha:
                return
            self.files = files
            model = Gtk.StringList.new(["All files"] + [f"{letter}  {p}" for letter, p in files])
            self.file_picker.set_model(model)
            self.file_picker.set_selected(0)
            self._render_diff()

        jobs.run(work, done, lambda e: widgets.error_toast(self, e))

    def _render_diff(self):
        state = self.win.selected
        commit = self.current
        if not state or not commit:
            return
        index = self.file_picker.get_selected()
        only = self.files[index - 1][1] if 0 < index <= len(self.files) else None
        path, sha = state.path, commit.sha

        def work():
            return gitcmd.diff_commit(path, sha, path=only)

        def done(text):
            if self.current and self.current.sha == sha:
                self.diff.set_plain(text)

        jobs.run(work, done, lambda e: widgets.error_toast(self, e))

    # ----------------------------------------------------------- actions ---

    def _copy_sha(self):
        if self.current:
            self.win.get_clipboard().set(self.current.sha)
            widgets.toast(self, f"Copied {self.current.short}")

    def _revert(self):
        state, commit = self.win.selected, self.current
        if not state or not commit:
            return
        path, sha, short = state.path, commit.sha, commit.short

        def go():
            def work():
                return gitcmd.revert(path, sha)

            jobs.run(work,
                     lambda _r: (widgets.toast(self, f"Reverted {short}"), self.win.refresh_selected(True)),
                     lambda e: widgets.error_toast(self, e))

        widgets.confirm(
            self.win, f"Revert {short}?",
            "A new commit is added that undoes this one. Your history is kept intact.",
            "Revert", go, destructive=False,
        )

    def _reset_here(self):
        state, commit = self.win.selected, self.current
        if not state or not commit:
            return
        path, sha, short = state.path, commit.sha, commit.short
        ahead = ""
        if state.status and state.status.entries:
            ahead = f" Your {len(state.status.entries)} uncommitted file(s) are kept."

        def go():
            def work():
                return gitcmd.reset(path, sha, "mixed")

            jobs.run(work,
                     lambda _r: (widgets.toast(self, f"Branch reset to {short}"), self.win.refresh_selected(True)),
                     lambda e: widgets.error_toast(self, e))

        widgets.confirm(
            self.win, f"Reset the branch to {short}?",
            f"Commits after this one are dropped from the branch, and their changes "
            f"come back as uncommitted edits.{ahead} You can undo this with the "
            f"reflog, but not from this window.",
            "Reset branch", go,
        )
