"""The Changes tab: what's staged, what isn't, the diff, and the commit box."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GObject, Gtk, Pango  # noqa: E402

from . import gitcmd, jobs, widgets  # noqa: E402


class FileItem(GObject.Object):
    """A row in one of the two file lists."""

    def __init__(self, entry, staged):
        super().__init__()
        self.entry = entry
        self.staged = staged


class FileList(Gtk.Box):
    """A titled, virtualised list of changed files with a bulk action button."""

    def __init__(self, title, bulk_label, on_bulk, on_row_action, on_select, row_label, row_tooltip):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_row_action = on_row_action
        self.row_label = row_label
        self.row_tooltip = row_tooltip
        self.base_title = title

        header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
            margin_top=6, margin_bottom=4, margin_start=10, margin_end=8,
        )
        self.title_label = Gtk.Label(label=title, xalign=0, hexpand=True, css_classes=["heading"])
        header.append(self.title_label)
        self.bulk = Gtk.Button(label=bulk_label, css_classes=["flat", "small-button"])
        self.bulk.connect("clicked", lambda _b: on_bulk())
        header.append(self.bulk)
        self.append(header)

        self.store = Gio.ListStore(item_type=FileItem)
        self.selection = Gtk.SingleSelection(model=self.store, autoselect=False, can_unselect=True)
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self.selection.connect("notify::selected", lambda *_: on_select(self))

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup)
        factory.connect("bind", self._bind)
        self.view = Gtk.ListView(model=self.selection, factory=factory, css_classes=["navigation-sidebar"])

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.view)
        self.append(scroller)

    def _setup(self, _factory, list_item):
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
            margin_top=2, margin_bottom=2, margin_start=6, margin_end=4,
        )
        status = Gtk.Label(css_classes=["file-status"], xalign=0)
        name = Gtk.Label(xalign=0, hexpand=True)
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        button = Gtk.Button(label=self.row_label, css_classes=["flat", "stage-button"],
                            tooltip_text=self.row_tooltip)
        button.set_valign(Gtk.Align.CENTER)
        button.connect("clicked", lambda _b, b=box: self.on_row_action(getattr(b, "item", None)))
        box.append(status)
        box.append(name)
        box.append(button)
        box.status, box.name_label = status, name
        list_item.set_child(box)

    def _bind(self, _factory, list_item):
        box = list_item.get_child()
        item = list_item.get_item()
        box.item = item
        entry = item.entry
        box.status.set_label(entry.label)
        # "?" can't be a CSS class name; untracked reads as new, so colour it green.
        colour = "A" if entry.label == "?" else (entry.label if entry.label in "MADRU" else "M")
        box.status.set_css_classes(["file-status", f"st-{colour}"])
        box.name_label.set_label(entry.display)
        box.name_label.set_tooltip_text(entry.display)

    def set_entries(self, entries, staged):
        self.store.remove_all()
        for entry in entries:
            self.store.append(FileItem(entry, staged))
        self.title_label.set_label(f"{self.base_title}  ·  {len(entries)}" if entries else self.base_title)
        self.bulk.set_sensitive(bool(entries))

    def selected_item(self):
        pos = self.selection.get_selected()
        if pos == Gtk.INVALID_LIST_POSITION:
            return None
        return self.store.get_item(pos)

    def select_path(self, path):
        for i in range(self.store.get_n_items()):
            if self.store.get_item(i).entry.path == path:
                self.selection.set_selected(i)
                return True
        return False

    def clear_selection(self):
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)


class ChangesTab(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.current = None       # FileItem being diffed
        self._diff_cache = ("", [])  # (header, hunks) of what's on screen

        self.staged_list = FileList(
            "Staged", "Unstage all", self._unstage_all, self._row_unstage,
            self._on_select, "−", "Unstage this file",
        )
        self.unstaged_list = FileList(
            "Changes", "Stage all", self._stage_all, self._row_stage,
            self._on_select, "+", "Stage this file",
        )

        lists = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, position=240)
        lists.set_start_child(self.staged_list)
        lists.set_end_child(self.unstaged_list)
        lists.set_resize_start_child(True)
        lists.set_resize_end_child(True)
        lists.set_shrink_start_child(False)
        lists.set_shrink_end_child(False)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.append(lists)
        left.append(self._build_commit_box())

        self.diff = widgets.DiffView()
        self.diff.on_hunk_action = self._hunk_action

        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, position=430)
        split.set_start_child(left)
        split.set_end_child(self.diff)
        split.set_resize_start_child(False)
        split.set_resize_end_child(True)
        split.set_shrink_start_child(False)
        self.append(split)

    def _build_commit_box(self):
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6,
            margin_top=8, margin_bottom=8, margin_start=8, margin_end=8,
        )
        self.message = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
            top_margin=6, bottom_margin=6, left_margin=6, right_margin=6,
        )
        self.message.get_buffer().connect("changed", lambda *_: self._sync_commit_button())
        frame = Gtk.Frame(child=self.message, height_request=84)
        box.append(frame)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.amend = Gtk.CheckButton(label="Amend last commit")
        self.amend.connect("toggled", lambda *_: self._on_amend_toggled())
        row.append(self.amend)
        spacer = Gtk.Box(hexpand=True)
        row.append(spacer)
        self.commit_btn = Gtk.Button(label="Commit", css_classes=["suggested-action"])
        self.commit_btn.connect("clicked", lambda _b: self._commit())
        row.append(self.commit_btn)
        box.append(row)
        return box

    # ------------------------------------------------------------ refresh --

    def refresh(self):
        state = self.win.selected
        if not state or not state.status:
            self.staged_list.set_entries([], True)
            self.unstaged_list.set_entries([], False)
            self.diff.placeholder("folder-symbolic", "Nothing loaded", "Select a repository.")
            return

        st = state.status
        keep = (self.current.entry.path, self.current.staged) if self.current else None

        self.staged_list.set_entries(st.staged_files, True)
        self.unstaged_list.set_entries(st.unstaged_files, False)

        # Keep the same file on screen across a refresh so a status poll
        # doesn't yank the diff away mid-read.
        if keep:
            path, was_staged = keep
            target = self.staged_list if was_staged else self.unstaged_list
            other = self.unstaged_list if was_staged else self.staged_list
            if target.select_path(path):
                other.clear_selection()
            elif other.select_path(path):
                target.clear_selection()
            else:
                self.current = None
                self.diff.placeholder("dialog-information-symbolic", "No file selected",
                                      "Pick a file to see its diff.")
        self._sync_commit_button()

    def _sync_commit_button(self):
        state = self.win.selected
        buf = self.message.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        has_staged = bool(state and state.status and state.status.staged_files)
        self.commit_btn.set_sensitive(bool(text) and (has_staged or self.amend.get_active()))

    def _on_amend_toggled(self):
        state = self.win.selected
        buf = self.message.get_buffer()
        current = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        if self.amend.get_active() and not current and state:
            commits = gitcmd.log(state.path, limit=1)
            if commits:
                body = commits[0].body.strip()
                buf.set_text(commits[0].subject + ("\n\n" + body if body else ""))
        self._sync_commit_button()

    # ------------------------------------------------------------- diffs ---

    def _on_select(self, which):
        item = which.selected_item()
        if item is None:
            if self.current and self.current.staged == (which is self.staged_list):
                self.current = None
            return
        # Only one of the two lists holds a selection at a time.
        other = self.unstaged_list if which is self.staged_list else self.staged_list
        if other.selected_item() is not None:
            other.clear_selection()
        self.current = item
        self._load_diff(item)

    def _load_diff(self, item):
        state = self.win.selected
        if not state:
            return
        entry, staged = item.entry, item.staged
        path = state.path
        context = self.win.cfg["diff_context"]

        def work():
            return gitcmd.diff_file(path, entry.path, staged=staged,
                                    untracked=entry.untracked, context=context)

        def done(text):
            if self.current is not item:
                return  # selection moved on while we were reading
            header, hunks = gitcmd.split_hunks(text)
            self._diff_cache = (header, hunks)
            if entry.untracked:
                self.diff.set_diff(header, hunks, actions=(),
                                   note="Untracked file — stage the whole file to add it.")
            elif entry.unmerged:
                self.diff.set_diff(header, hunks, actions=(),
                                   note="Conflicted file — resolve it in your editor, then stage it.")
            elif staged:
                self.diff.set_diff(header, hunks, actions=[("Unstage hunk", "unstage", None)])
            else:
                self.diff.set_diff(header, hunks, actions=[
                    ("Stage hunk", "stage", "suggested-action"),
                    ("Discard", "discard", "destructive-action"),
                ])

        jobs.run(work, done, lambda e: widgets.error_toast(self, e))

    def _hunk_action(self, action, index):
        state = self.win.selected
        item = self.current
        if not state or not item:
            return
        header, hunks = self._diff_cache
        if index >= len(hunks):
            return
        patch = header + hunks[index]
        path = state.path

        def apply(reverse, cached):
            def work():
                gitcmd.apply_patch(path, patch, cached=cached, reverse=reverse)
            self._after(work, {"stage": "Hunk staged", "unstage": "Hunk unstaged",
                               "discard": "Hunk discarded"}[action])

        if action == "stage":
            apply(reverse=False, cached=True)
        elif action == "unstage":
            apply(reverse=True, cached=True)
        elif action == "discard":
            widgets.confirm(
                self.win, "Discard this hunk?",
                "These lines go back to their last committed state. This cannot be undone.",
                "Discard", lambda: apply(reverse=True, cached=False),
            )

    # ----------------------------------------------------------- staging ---

    def _after(self, work, message):
        """Run a mutating git call, then refresh everything that shows state."""

        def done(_result):
            widgets.toast(self, message)
            self.win.refresh_selected(force=True)

        jobs.run(work, done, lambda e: widgets.error_toast(self, e))

    def _row_stage(self, item):
        state = self.win.selected
        if state and item:
            path, target = state.path, item.entry.path
            self._after(lambda: gitcmd.stage(path, [target]), f"Staged {target}")

    def _row_unstage(self, item):
        state = self.win.selected
        if state and item:
            path, target = state.path, item.entry.path
            self._after(lambda: gitcmd.unstage(path, [target]), f"Unstaged {target}")

    def _stage_all(self):
        state = self.win.selected
        if state:
            path = state.path
            self._after(lambda: gitcmd.stage_all(path), "Everything staged")

    def _unstage_all(self):
        state = self.win.selected
        if state:
            path = state.path
            self._after(lambda: gitcmd.unstage_all(path), "Everything unstaged")

    def discard_all(self):
        state = self.win.selected
        if not state or not state.status or not state.status.entries:
            widgets.toast(self, "Nothing to discard")
            return
        entries = list(state.status.entries)
        path = state.path
        tracked = sum(1 for e in entries if not e.untracked)
        untracked = len(entries) - tracked
        parts = []
        if tracked:
            parts.append(f"{tracked} modified file{'s' if tracked != 1 else ''}")
        if untracked:
            parts.append(f"{untracked} untracked file{'s' if untracked != 1 else ''} (deleted from disk)")
        widgets.confirm(
            self.win, "Discard all changes?",
            "This throws away " + " and ".join(parts) + ". There is no undo.",
            "Discard everything",
            lambda: self._after(lambda: gitcmd.discard(path, entries), "All changes discarded"),
        )

    # ------------------------------------------------------------ commit ---

    def _commit(self):
        state = self.win.selected
        if not state:
            return
        buf = self.message.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        if not text:
            return
        path, amend = state.path, self.amend.get_active()

        def go():
            def work():
                return gitcmd.commit(path, text, amend=amend)

            def done(_out):
                buf.set_text("")
                self.amend.set_active(False)
                widgets.toast(self, "Committed" + (" (amended)" if amend else ""))
                self.win.refresh_selected(force=True)

            jobs.run(work, done, lambda e: widgets.error_toast(self, e))

        if amend:
            widgets.confirm(
                self.win, "Amend the last commit?",
                "The previous commit is replaced. If you already pushed it, you'll "
                "need a force push to update the remote.",
                "Amend", go,
            )
        else:
            go()

    # ------------------------------------------------------------- stash ---

    def stash_dialog(self):
        state = self.win.selected
        if not state or not state.status or not state.status.entries:
            widgets.toast(self, "Nothing to stash")
            return
        path = state.path

        def go(message):
            self._after(lambda: gitcmd.stash_push(path, message), "Changes stashed")

        widgets.prompt(
            self.win, "Stash changes",
            "Your changes are set aside and the working tree goes clean. "
            "Restore them from the Branches tab.",
            "Stash", go, placeholder="Optional description",
        )
