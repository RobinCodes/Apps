"""The Branches tab: local and remote branches, plus the stash."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from . import gitcmd, jobs, widgets  # noqa: E402


class BranchesTab(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.loaded_for = None

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                         margin_top=10, margin_bottom=4, margin_start=12, margin_end=12)
        title = Gtk.Label(label="Branches", xalign=0, hexpand=True, css_classes=["title-4"])
        header.append(title)
        new_btn = Gtk.Button(label="New branch", css_classes=["suggested-action"])
        new_btn.connect("clicked", lambda _b: self._new_branch())
        header.append(new_btn)
        self.append(header)

        self.local_group = Adw.PreferencesGroup(title="Local")
        self.remote_group = Adw.PreferencesGroup(title="Remote", margin_top=12)
        self.stash_group = Adw.PreferencesGroup(title="Stash", margin_top=12)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                       margin_top=6, margin_bottom=16, margin_start=12, margin_end=12)
        page.append(self.local_group)
        page.append(self.remote_group)
        page.append(self.stash_group)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(page)
        self.append(scroller)

        self._rows = []

    # ----------------------------------------------------------- loading ---

    def refresh(self):
        state = self.win.selected
        if not state:
            return
        path = state.path

        def work():
            return gitcmd.branches(path), gitcmd.stash_list(path)

        def done(result):
            if not self.win.selected or self.win.selected.path != path:
                return
            self._render(*result)

        jobs.run(work, done, lambda e: widgets.error_toast(self, e))

    def _clear(self, group):
        for row in list(self._rows):
            if row.get_parent() is not None and getattr(row, "_group", None) is group:
                group.remove(row)
                self._rows.remove(row)

    def _add(self, group, row):
        row._group = group
        group.add(row)
        self._rows.append(row)

    def _render(self, branches, stashes):
        for group in (self.local_group, self.remote_group, self.stash_group):
            self._clear(group)

        local = [b for b in branches if not b.remote]
        remote = [b for b in branches if b.remote]

        self.local_group.set_description(f"{len(local)} branch{'es' if len(local) != 1 else ''}")
        for b in local:
            self._add(self.local_group, self._branch_row(b, local=True))
        if not local:
            self._add(self.local_group, Adw.ActionRow(title="No local branches yet"))

        self.remote_group.set_description(f"{len(remote)} tracked remotely")
        for b in remote[:60]:
            self._add(self.remote_group, self._branch_row(b, local=False))
        if not remote:
            self._add(self.remote_group, Adw.ActionRow(title="No remote branches"))

        self.stash_group.set_description(
            f"{len(stashes)} entr{'ies' if len(stashes) != 1 else 'y'}" if stashes else "Nothing stashed"
        )
        for s in stashes:
            self._add(self.stash_group, self._stash_row(s))

    # -------------------------------------------------------------- rows ---

    def _branch_row(self, branch, local):
        subtitle_bits = []
        if branch.current:
            subtitle_bits.append("checked out")
        if branch.upstream:
            track = []
            if branch.ahead:
                track.append(f"↑{branch.ahead}")
            if branch.behind:
                track.append(f"↓{branch.behind}")
            subtitle_bits.append(f"{branch.upstream} {' '.join(track)}".strip())
        if branch.when:
            subtitle_bits.append(branch.when)
        if branch.subject:
            subtitle_bits.append(branch.subject[:60])

        row = Adw.ActionRow(title=branch.name, subtitle="  ·  ".join(subtitle_bits))
        row.set_title_lines(1)
        row.set_subtitle_lines(1)

        if branch.current:
            marker = Gtk.Label(label="●", css_classes=["badge-ahead"], valign=Gtk.Align.CENTER)
            marker.set_tooltip_text("Current branch")
            row.add_prefix(marker)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, valign=Gtk.Align.CENTER)
        if not branch.current:
            label = "Check out" if local else "Check out locally"
            switch_btn = Gtk.Button(label=label, css_classes=["flat"])
            switch_btn.connect("clicked", lambda _b, n=branch.name, l=local: self._checkout(n, l))
            box.append(switch_btn)

            merge_btn = Gtk.Button(label="Merge", css_classes=["flat"],
                                   tooltip_text="Merge into the current branch")
            merge_btn.connect("clicked", lambda _b, n=branch.name: self._merge(n))
            box.append(merge_btn)

        if not branch.current:
            del_btn = Gtk.Button(label="Delete", css_classes=["flat"],
                                 tooltip_text="Delete this branch")
            del_btn.connect("clicked", lambda _b, n=branch.name, l=local: self._delete(n, l))
            box.append(del_btn)

        row.add_suffix(box)
        return row

    def _stash_row(self, stash):
        row = Adw.ActionRow(title=stash.subject, subtitle=f"{stash.ref}  ·  {stash.when}")
        row.set_title_lines(1)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, valign=Gtk.Align.CENTER)
        pop = Gtk.Button(label="Pop", css_classes=["flat"], tooltip_text="Restore and remove from the stash")
        pop.connect("clicked", lambda _b, r=stash.ref: self._stash_action(r, pop=True))
        apply_btn = Gtk.Button(label="Apply", css_classes=["flat"], tooltip_text="Restore but keep in the stash")
        apply_btn.connect("clicked", lambda _b, r=stash.ref: self._stash_action(r, pop=False))
        drop = Gtk.Button(label="Drop", css_classes=["flat"], tooltip_text="Delete this stash entry")
        drop.connect("clicked", lambda _b, r=stash.ref, s=stash.subject: self._stash_drop(r, s))
        for b in (pop, apply_btn, drop):
            box.append(b)
        row.add_suffix(box)
        return row

    # ----------------------------------------------------------- actions ---

    def _run(self, fn, message):
        def done(_result):
            widgets.toast(self, message)
            self.win.refresh_selected(force=True)
            self.refresh()

        jobs.run(fn, done, lambda e: widgets.error_toast(self, e))

    def _checkout(self, name, local):
        state = self.win.selected
        if not state:
            return
        path = state.path
        dirty = bool(state.status and state.status.entries)

        def go():
            fn = (lambda: gitcmd.checkout(path, name)) if local else (lambda: gitcmd.checkout_remote(path, name))
            self._run(fn, f"Now on {name.split('/', 1)[-1] if not local else name}")

        if dirty:
            widgets.confirm(
                self.win, f"Switch to {name}?",
                "You have uncommitted changes. Git will carry them across if it can, "
                "and refuse the switch if they would conflict — nothing is discarded either way.",
                "Switch", go, destructive=False,
            )
        else:
            go()

    def _merge(self, name):
        state = self.win.selected
        if not state:
            return
        path = state.path
        current = state.status.branch if state.status else "the current branch"

        widgets.confirm(
            self.win, f"Merge {name} into {current}?",
            "A merge commit is created if the branches have diverged. "
            "Conflicts stop the merge and leave the files for you to resolve.",
            "Merge",
            lambda: self._run(lambda: gitcmd.merge(path, name), f"Merged {name}"),
            destructive=False,
        )

    def _delete(self, name, local):
        state = self.win.selected
        if not state:
            return
        path = state.path
        if local:
            def force_delete():
                self._run(lambda: gitcmd.delete_branch(path, name, force=True), f"Deleted {name}")

            def try_delete():
                def work():
                    return gitcmd.delete_branch(path, name, force=False)

                def failed(_exc):
                    # git refuses when the branch holds unmerged commits — say so
                    # plainly rather than surfacing the raw hint text.
                    widgets.confirm(
                        self.win, f"“{name}” is not fully merged",
                        "It holds commits that aren't on any other branch. Deleting it "
                        "loses them for good.",
                        "Delete anyway", force_delete,
                    )

                jobs.run(work,
                         lambda _r: (widgets.toast(self, f"Deleted {name}"), self.refresh()),
                         failed)

            widgets.confirm(
                self.win, f"Delete branch {name}?",
                "The branch label is removed from your machine. The remote copy, if any, stays.",
                "Delete", try_delete,
            )
        else:
            widgets.confirm(
                self.win, f"Delete {name} from the remote?",
                "This deletes the branch on GitHub for everyone, not just here.",
                "Delete on remote",
                lambda: self._run(lambda: gitcmd.delete_branch(path, name, remote=True),
                                  f"Deleted {name} on the remote"),
            )

    def _new_branch(self):
        state = self.win.selected
        if not state:
            return
        path = state.path
        base = state.status.branch if state.status and state.status.branch else "HEAD"

        widgets.prompt(
            self.win, "New branch",
            f"Created from {base} and checked out immediately.",
            "Create", lambda name: self._run(lambda: gitcmd.create_branch(path, name), f"Switched to {name}"),
            placeholder="feature/my-change",
        )

    def _stash_action(self, ref, pop):
        state = self.win.selected
        if not state:
            return
        path = state.path
        self._run(lambda: gitcmd.stash_apply(path, ref, pop=pop),
                  "Stash restored" + (" and removed" if pop else ""))

    def _stash_drop(self, ref, subject):
        state = self.win.selected
        if not state:
            return
        path = state.path
        widgets.confirm(
            self.win, "Drop this stash?",
            f"“{subject}” is deleted. The changes in it are not recoverable from this window.",
            "Drop",
            lambda: self._run(lambda: gitcmd.stash_drop(path, ref), "Stash dropped"),
        )
