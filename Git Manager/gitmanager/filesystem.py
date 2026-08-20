"""The file system view: every repository where it actually sits on disk.

The sidebar answers "which repositories do I have"; this answers "where are
they, and what down there isn't safe yet". It lays the repositories out as the
directory tree they really form, and audits one chosen folder for work that no
remote has a copy of.

The tree is built from leaf paths rather than by walking the filesystem, so it
only ever contains directories that lead somewhere worth showing. Chains of
single directories are collapsed into one row -- without that, a repository
eight levels below home costs eight rows of nothing.
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import gitcmd, jobs, scanner, widgets  # noqa: E402

# Why a folder counts as not backed up, worst first. The order matters: it is
# also the order the summary line reports them in.
NO_GIT = "no git"
NO_REMOTE = "no remote"
UNPUSHED = "unpushed"
BACKED_UP = "backed up"

_BADGE_CSS = {
    NO_GIT: "badge-behind",
    NO_REMOTE: "badge-behind",
    UNPUSHED: "badge-ahead",
    BACKED_UP: "badge-clean",
}


def tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


class Node(GObject.Object):
    """One row: a directory on the way down, a repository, or a loose folder."""

    __gtype_name__ = "GitManagerFsNode"

    def __init__(self, path, label, kind="dir"):
        super().__init__()
        self.path = path
        self.label = label
        self.kind = kind            # dir | repo | unbacked
        self.reason = ""            # why it isn't backed up
        self.detail = ""            # branch, or a count of what's at risk
        self.dirty = False
        self.kids = []

    @property
    def is_container(self):
        return bool(self.kids)


# ---------------------------------------------------------------- the audit --


def _is_repo_root(path):
    """True only for a repository's own top level.

    gitcmd.is_repo answers "is this inside a work tree", which is true of every
    subdirectory of every repository -- useless for finding nested ones.
    """
    is_repo, _bare = scanner._is_repo_dir(path)
    return is_repo


def _has_repo_below(path, depth, prune):
    """True if anything at or below `path` is a git repository."""
    if depth < 0:
        return False
    try:
        with os.scandir(path) as it:
            entries = [e for e in it if e.is_dir(follow_symlinks=False) and e.name not in prune]
    except OSError:
        return False
    for e in entries:
        if _is_repo_root(e.path) or _has_repo_below(e.path, depth - 1, prune):
            return True
    return False


def classify_repo(path):
    """(reason, detail, dirty) for one repository."""
    try:
        if not gitcmd.remotes(path):
            return NO_REMOTE, "no remote to push to", False
        st = gitcmd.status(path)
    except Exception:
        return NO_REMOTE, "unreadable", False
    dirty = bool(st.entries)
    if not st.upstream:
        return UNPUSHED, f"{st.branch or 'branch'} has no upstream", dirty
    if st.ahead:
        return UNPUSHED, f"{st.ahead} commit{'s' if st.ahead != 1 else ''} not pushed", dirty
    return BACKED_UP, st.branch or "", dirty


def audit(root, max_depth, prune=None):
    """Walk `root` and report what is and isn't backed up.

    A folder is only called out when nothing beneath it is a repository --
    a directory that merely holds repositories is scaffolding, not a risk.
    """
    # ".git" is excluded explicitly: this walk descends into working trees to
    # find nested repositories, and a .git directory looks exactly like a bare
    # repo (HEAD + objects + refs) to the root test.
    prune = set(prune or scanner.PRUNE_NAMES) | {".git"}
    root = os.path.abspath(os.path.expanduser(root))
    found = []
    if not os.path.isdir(root):
        return found

    def walk(path, depth, inside_repo=False):
        try:
            with os.scandir(path) as it:
                entries = sorted(
                    (e for e in it if e.name not in prune),
                    key=lambda e: e.name.lower(),
                )
        except OSError:
            return
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if _is_repo_root(entry.path):
                reason, detail, dirty = classify_repo(entry.path)
                found.append((entry.path, "repo", reason, detail, dirty))
                # Keep descending. A repository nested inside another one is
                # invisible to the scan, so this is the only place it surfaces.
                if depth > 0:
                    walk(entry.path, depth - 1, inside_repo=True)
                continue
            if depth <= 0:
                continue
            if inside_repo:
                # Inside a working tree the parent repo already covers loose
                # folders; only a nested repository is worth reporting.
                if _has_repo_below(entry.path, depth - 1, prune):
                    walk(entry.path, depth - 1, inside_repo=True)
                continue
            if _has_repo_below(entry.path, depth - 1, prune):
                walk(entry.path, depth - 1)
            else:
                found.append((entry.path, "unbacked", NO_GIT, "not a repository", False))

    walk(root, max_depth)
    return found


# ----------------------------------------------------------- tree building --


def build_tree(leaves):
    """Fold (path, kind, reason, detail, dirty) leaves into a collapsed tree."""
    root = {}
    meta = {}
    for path, kind, reason, detail, dirty in leaves:
        parts = [p for p in path.strip("/").split("/") if p]
        node = root
        for i, part in enumerate(parts):
            node = node.setdefault(part, {})
            if i == len(parts) - 1:
                meta["/" + "/".join(parts)] = (kind, reason, detail, dirty)

    def to_nodes(mapping, prefix, top=False):
        out = []
        for name in sorted(mapping, key=str.lower):
            path = f"{prefix}/{name}"
            info = meta.get(path)
            node = Node(path, name, info[0] if info else "dir")
            if info:
                _, node.reason, node.detail, node.dirty = info
            node.kids = to_nodes(mapping[name], path)
            # Collapse a directory that only leads somewhere else into its
            # child, so "Projects/Programming/Apps" is one row, not three.
            while (node.kind == "dir" and len(node.kids) == 1
                   and node.kids[0].kind == "dir" and node.kids[0].kids):
                only = node.kids[0]
                node = Node(only.path, f"{node.label}/{only.label}", "dir")
                node.kids = only.kids
            if top:
                # The outermost row carries the whole path, so it reads as a
                # location rather than as a bare "home".
                node.label = tilde(node.path)
            out.append(node)
        return out

    return to_nodes(root, "", top=True)


# ------------------------------------------------------------------ widget --


class FilesystemView(Gtk.Box):
    """Repositories in their real directory layout, plus the backup audit."""

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.cfg = window.cfg
        self._only_risky = False
        self._leaves = []
        self.on_repo_chosen = None

        self.summary = Gtk.Label(
            label="Scanning…", xalign=0, css_classes=["dim-label", "repo-row-path"],
            margin_top=6, margin_bottom=6, margin_start=12, margin_end=12,
        )
        self.summary.set_ellipsize(Pango.EllipsizeMode.END)

        self.store = Gio.ListStore(item_type=Node)
        tree = Gtk.TreeListModel.new(self.store, False, True, self._children_of)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)

        self.list_view = Gtk.ListView(
            model=Gtk.NoSelection(model=tree), factory=factory,
            css_classes=["navigation-sidebar"],
        )
        self.list_view.connect("activate", self._on_activate)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.list_view)

        self.append(scroller)
        self.append(self.summary)

    # -- model ------------------------------------------------------------

    @staticmethod
    def _children_of(node):
        if not node.kids:
            return None
        store = Gio.ListStore(item_type=Node)
        for kid in node.kids:
            store.append(kid)
        return store

    def set_only_risky(self, only):
        self._only_risky = only
        self._rebuild()

    def refresh(self):
        """Re-run the audit off the main thread."""
        self.summary.set_label("Scanning…")
        root = self.cfg["backup_root"]
        depth = self.cfg["backup_max_depth"]
        repo_paths = [s.path for s in self.window.states]

        def work():
            leaves = audit(root, depth)
            seen = {p for p, *_ in leaves}
            # Repositories the sidebar knows about but that sit outside the
            # audited folder still belong on the map.
            for path in repo_paths:
                if path not in seen:
                    reason, detail, dirty = classify_repo(path)
                    leaves.append((path, "repo", reason, detail, dirty))
            return leaves

        def done(leaves):
            self._leaves = leaves
            self._rebuild()

        jobs.run(work, done, lambda e: widgets.error_toast(self.window, e))

    def _rebuild(self):
        leaves = self._leaves
        if self._only_risky:
            leaves = [l for l in leaves if l[2] != BACKED_UP]
        self.store.remove_all()
        for node in build_tree(leaves):
            self.store.append(node)

        repos = [l for l in self._leaves if l[1] == "repo"]
        risky = [l for l in self._leaves if l[2] != BACKED_UP]
        counts = {}
        for l in risky:
            counts[l[2]] = counts.get(l[2], 0) + 1
        parts = [f"{counts[k]} {k}" for k in (NO_GIT, NO_REMOTE, UNPUSHED) if k in counts]
        root = tilde(self.cfg["backup_root"])
        if parts:
            self.summary.set_label(
                f"{len(repos)} repositories · {', '.join(parts)} under {root}"
            )
        else:
            self.summary.set_label(f"{len(repos)} repositories · all backed up under {root}")

    # -- rows -------------------------------------------------------------

    def _setup_row(self, _factory, item):
        expander = Gtk.TreeExpander()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      margin_top=3, margin_bottom=3)
        icon = Gtk.Image(icon_name="folder-symbolic")
        name = Gtk.Label(xalign=0)
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        detail = Gtk.Label(xalign=0, hexpand=True, css_classes=["dim-label", "repo-row-path"])
        detail.set_ellipsize(Pango.EllipsizeMode.END)
        badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.append(icon)
        row.append(name)
        row.append(detail)
        row.append(badges)
        expander.set_child(row)
        item.set_child(expander)
        item.row_widgets = (icon, name, detail, badges)

    def _bind_row(self, _factory, item):
        expander = item.get_child()
        list_row = item.get_item()
        expander.set_list_row(list_row)
        node = list_row.get_item()
        icon, name, detail, badges = item.row_widgets

        name.set_label(node.label)
        name.set_css_classes(["heading"] if node.kind == "repo" else [])
        detail.set_label(node.detail)

        child = badges.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            badges.remove(child)
            child = nxt
        if node.kind in ("repo", "unbacked") and node.reason:
            badges.append(widgets.pill(node.reason, _BADGE_CSS.get(node.reason)))
        if node.dirty:
            badges.append(widgets.pill("uncommitted", "badge-dirty"))

    def _on_activate(self, _view, position):
        model = self.list_view.get_model()
        node = model.get_item(position).get_item()
        if node.kind == "repo":
            self.window.select_path_arg(node.path)
            if self.on_repo_chosen:
                self.on_repo_chosen()
        else:
            widgets.open_url(self.window, GLib.filename_to_uri(node.path, None))
