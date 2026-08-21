"""Connecting a folder that already exists to a repository that already exists.

Publishing covers the case where only the folder is there; cloning covers the
case where only the repository is. The fourth quadrant — both already exist,
made separately — is the one that turns into six commands typed in the right
order, and it is where getting it wrong costs something:

  * a `git remote add` pointed at a history that has nothing to do with the
    folder, after which every push is rejected and every pull refuses; or
  * a `git checkout` / `git pull` that overwrites the very files you were
    trying to connect.

So nothing here is guessed. The folder is read, the repository is asked what it
holds — with `git ls-remote`, which uses the same credentials the later fetch
and push will use, rather than taking `gh`'s word for it — and the exact
sequence is written out before any of it runs.

No step in this module overwrites a file. When both sides have content, HEAD is
moved to the remote's tip and the working tree is left exactly as it was, so
the difference between the folder and GitHub arrives in the Changes tab, which
is the part of this app that already knows how to show it.

A folder inside another repository's working tree is allowed to become one in
its own right: git nests them, and it is how one folder of a pile of projects
turns into a project. What the surrounding repository tracks it goes on
tracking, though, so the plan says that out loud and offers to drop the folder
from its index and ignore it instead — an offer that is off unless it is
turned on, and that stops short of committing anything there either.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import github, gitcmd, ignore, jobs, widgets  # noqa: E402
from .filesystem import tilde  # noqa: E402

TTL = 120           # shares the repo list with the clone dialog
INVALID_REMOTE = set(" \t/\\~^:?*[")


# ----------------------------------------------------------------- facts ----


@dataclass
class Local:
    """What the folder is before anything is done to it."""

    path: str = ""
    exists: bool = False
    in_git_dir: bool = False  # inside .git itself, where nothing may be created
    is_repo: bool = False
    toplevel: str = ""        # the repo this folder sits inside, if any
    branch: str = ""
    has_commits: bool = False
    tree_empty: bool = True
    remotes: dict = field(default_factory=dict)
    staged: int = 0
    changes: int = 0
    # Filled in only when the folder sits inside a working tree without being
    # the top of it. See _survey_parent.
    rel: str = ""             # where in that repository the folder is
    parent_tracked: int = 0   # files of ours in its index
    parent_ignores: bool = False

    @property
    def nested(self):
        """Inside another working tree, and not the top of it."""
        return bool(self.toplevel) and not self.is_repo


def survey(path):
    local = Local(path=path)
    if not path or not os.path.isdir(path):
        return local
    local.exists = True
    local.tree_empty = gitcmd.tree_is_empty(path)
    if gitcmd.in_git_dir(path):
        local.in_git_dir = True
        return local
    if not gitcmd.is_repo(path):
        return local
    try:
        top = gitcmd.toplevel(path)
    except (gitcmd.GitError, OSError):
        return local
    local.toplevel = top
    # The top of a working tree is a repository. Anything under it is a folder
    # inside one, which can still become a repository -- but not without the
    # one around it having something to say about the files.
    local.is_repo = os.path.realpath(top) == os.path.realpath(path)
    if not local.is_repo:
        _survey_parent(local)
        return local
    local.branch = gitcmd.head_branch(path)
    local.has_commits = gitcmd.has_commits(path)
    local.remotes = gitcmd.remotes(path)
    try:
        st = gitcmd.status(path)
        local.changes = len(st.entries)
        local.staged = len(st.staged_files)
    except (gitcmd.GitError, OSError, subprocess.TimeoutExpired):
        pass
    return local


def _survey_parent(local):
    """What the repository around this folder makes of it.

    Not "can this be done" -- git nests working trees without complaint -- but
    what the two would then disagree about. Everything the outer repository
    already tracks in here it keeps tracking, and the same files committed in
    two places that know nothing of each other is the failure mode worth
    naming before a .git is created, not after.
    """
    local.rel = os.path.relpath(local.path, local.toplevel).replace(os.sep, "/")
    try:
        local.parent_tracked = len(gitcmd.tracked_under(local.toplevel, local.rel))
    except (gitcmd.GitError, OSError, subprocess.TimeoutExpired):
        pass
    try:
        # --no-index, because the question is whether the patterns cover this
        # folder -- which check-ignore otherwise declines to answer about a
        # path that is tracked, and a tracked path is exactly this case.
        local.parent_ignores = bool(
            gitcmd.check_ignore(local.toplevel, [local.rel + "/"], no_index=True))
    except (gitcmd.GitError, OSError, subprocess.TimeoutExpired):
        pass


def _parent_name(local):
    return os.path.basename(local.toplevel.rstrip("/")) or local.toplevel


def _possessive(name):
    """`Apps'` rather than `Apps's`: the name is whatever the folder is called."""
    return name + ("'" if name.endswith("s") else "'s")


@dataclass
class Remote:
    """What the repository on GitHub actually holds."""

    nwo: str = ""
    url: str = ""
    default_branch: str = ""
    heads: dict = field(default_factory=dict)
    info: object = None       # github.Repo, when gh could tell us
    reach_error: str = ""

    @property
    def empty(self):
        return not self.heads


def inspect_remote(nwo, protocol="https"):
    """Ask GitHub and git, in that order, what we are connecting to."""
    info = None
    try:
        info = github.repo_info(nwo)
    except (github.GhError, subprocess.TimeoutExpired, OSError):
        pass  # gh may be missing or the repo may live on another host
    if info:
        url = info.clone_url(protocol)
        nwo = info.nwo
    elif protocol == "ssh":
        url = f"git@github.com:{nwo}.git"
    else:
        url = f"https://github.com/{nwo}.git"

    remote = Remote(nwo=nwo, url=url, info=info)
    try:
        remote.default_branch, remote.heads = gitcmd.ls_remote(url)
    except (gitcmd.GitError, OSError, subprocess.TimeoutExpired) as exc:
        first = str(exc).strip().splitlines()
        remote.reach_error = first[-1][:200] if first else "the remote did not answer"
        if info:
            remote.default_branch = info.default_branch
    if not remote.default_branch and remote.heads:
        remote.default_branch = "main" if "main" in remote.heads else sorted(remote.heads)[0]
    return remote


# ------------------------------------------------------------------ plan ----


@dataclass
class Step:
    text: str
    detail: str = ""
    kind: str = "do"     # do | note | warn


@dataclass
class Plan:
    local: Local
    remote: Remote
    remote_name: str = "origin"
    steps: list = field(default_factory=list)
    blocked: str = ""
    adopt: str = ""          # "" | "checkout" | "reset"
    push_branch: str = ""    # branch that can be pushed once connected
    push_default: bool = False
    detach: bool = False     # also take the folder out of the repo around it

    @property
    def ready(self):
        return not self.blocked

    @property
    def can_detach(self):
        """Whether there is anything to take out of the surrounding repository."""
        local = self.local
        return bool(local.nested and local.rel) and (
            bool(local.parent_tracked) or not local.parent_ignores)


def build_plan(local, remote, remote_name="origin"):
    """Work out the exact sequence, or why there isn't one. No side effects."""
    plan = Plan(local=local, remote=remote, remote_name=remote_name)

    if not local.path:
        plan.blocked = "Choose the folder to connect."
        return plan
    if not local.exists:
        plan.blocked = f"{tilde(local.path)} is not a folder."
        return plan
    if local.in_git_dir:
        plan.blocked = (
            f"{tilde(local.path)} is inside git's own storage. Pick the folder the "
            "repository is checked out into instead."
        )
        return plan
    if not remote.nwo:
        plan.blocked = "Choose the GitHub repository to connect it to."
        return plan
    if not remote_name or INVALID_REMOTE & set(remote_name):
        plan.blocked = "A remote name can't contain a space or any of / \\ ~ ^ : ? * [."
        return plan

    info = remote.info
    if info is not None and info.archived:
        plan.steps.append(Step(
            f"{remote.nwo} is archived on GitHub",
            "Archived repositories are read-only until someone unarchives them.",
            kind="warn",
        ))
    elif info is not None and not info.writable:
        plan.steps.append(Step(
            f"You have read-only access to {remote.nwo}",
            "Fetching and pulling will work; pushing will be rejected.",
            kind="warn",
        ))

    if local.nested:
        _plan_nested(plan)

    if not local.is_repo:
        start = remote.default_branch or "main"
        plan.steps.append(Step(
            f"Create a repository in {tilde(local.path)}",
            f"git init on the folder as it stands, starting on {start}. "
            "Nothing already in the folder is moved, changed or removed.",
        ))

    existing = local.remotes.get(remote_name)
    if existing == remote.url:
        plan.steps.append(Step(f"{remote_name} already points at {remote.url}", kind="note"))
    elif existing:
        plan.steps.append(Step(
            f"Point {remote_name} at {remote.url}",
            f"It currently points at {existing}, which this forgets. "
            "Nothing on either server changes.",
            kind="warn",
        ))
    else:
        plan.steps.append(Step(f"Add {remote_name} → {remote.url}"))
        twins = [n for n, u in local.remotes.items() if u == remote.url]
        if twins:
            plan.steps.append(Step(
                f"This repository is already a remote here, as {twins[0]}",
                "Two names for one URL is legal, if rarely what you meant.",
                kind="note",
            ))

    if remote.reach_error:
        plan.steps.append(Step(
            "git could not reach this repository", remote.reach_error, kind="warn"))
        plan.steps.append(Step(
            "The remote is set up anyway",
            "Fetching and tracking are left for when it can be reached.", kind="note"))
        return plan

    plan.steps.append(Step(
        f"Fetch {remote_name}",
        "Downloads the history. Files in the folder are not touched.",
    ))

    if remote.empty:
        _plan_empty_remote(plan)
    elif not local.has_commits:
        _plan_adopt(plan)
    else:
        _plan_two_histories(plan)
    return plan


def _plan_nested(plan):
    """The folder is inside another working tree. Say what that costs.

    Nothing here blocks. Two repositories can hold the same files quite
    happily as far as git is concerned; it is the person who has to keep
    track of which one a commit went to, and that is only fair if they were
    told.
    """
    local = plan.local
    parent = tilde(local.toplevel)
    if local.parent_tracked:
        many = local.parent_tracked != 1
        plan.steps.append(Step(
            f"{parent} tracks {local.parent_tracked} file{'s' if many else ''} "
            f"inside this folder",
            f"It keeps tracking {'them' if many else 'it'}: the same "
            f"file{'s' if many else ''} would then sit in two repositories, "
            f"each blind to what the other commits. “Take it out of "
            f"{_parent_name(local)}” above settles that.",
            kind="warn",
        ))
    elif local.parent_ignores:
        plan.steps.append(Step(
            f"This folder is inside {parent}, which already ignores it",
            "So the two repositories will not be holding the same files.",
            kind="note",
        ))
    else:
        plan.steps.append(Step(
            f"This folder is inside {parent}, which tracks nothing in it",
            "Committing everything there would record this folder as a bare "
            "pointer to a commit — a submodule without any of the wiring. An "
            "ignore rule there is what stops that happening by accident.",
            kind="note",
        ))


def _detach_steps(plan):
    """What taking the folder out of the surrounding repository adds to the plan."""
    local = plan.local
    name = _parent_name(local)
    steps = []
    if local.parent_tracked:
        many = local.parent_tracked != 1
        steps.append(Step(
            f"Stop {name} tracking {local.rel}",
            f"git rm -r --cached there: {local.parent_tracked} "
            f"path{'s' if many else ''} leave{'' if many else 's'} its index and "
            f"stay exactly where {'they are' if many else 'it is'} on disk. The "
            f"removal is staged in {name} and committed by you, there, when you "
            f"want its copy on GitHub to lose them too.",
            kind="warn",
        ))
    if not local.parent_ignores:
        steps.append(Step(
            f"Add {ignore.pattern_for(local.rel, True)} to {_possessive(name)} .gitignore",
            f"So committing everything in {name} doesn't pick this folder up again.",
        ))
    return steps


def _detach_summary(local):
    """The switch's subtitle: what it does, in the two shapes it comes in."""
    name = _parent_name(local)
    if local.parent_tracked:
        return (f"Drop this folder from {_possessive(name)} index and ignore it there instead. "
                f"Every file stays on disk, and nothing in {name} is committed — "
                f"the removal waits in its Changes tab.")
    return (f"Add an ignore rule for this folder to {name}, so committing "
            f"everything there doesn't swallow it.")


def _plan_empty_remote(plan):
    """Nothing on GitHub yet — the usual "I made the repo in the browser" case."""
    local, plan_branch = plan.local, plan.local.branch or "the current branch"
    if local.has_commits:
        plan.push_branch = local.branch
        plan.push_default = True
        plan.steps.append(Step(
            f"{plan.remote.nwo} is empty",
            f"Pushing {plan_branch} fills it and starts tracking it.", kind="note"))
    elif local.tree_empty:
        plan.steps.append(Step(
            "Both sides are empty", "Add a file and commit it, then push.", kind="note"))
    else:
        plan.steps.append(Step(
            f"{plan.remote.nwo} is empty, and nothing here is committed yet",
            "Commit the folder in the Changes tab, then push it.", kind="note"))


def _plan_adopt(plan):
    """The folder has no commits and GitHub does — take its history over."""
    remote, local = plan.remote, plan.local
    target = remote.default_branch
    upstream = f"{plan.remote_name}/{target}"
    if local.tree_empty:
        plan.adopt = "checkout"
        plan.steps.append(Step(
            f"Check {target} out into the folder",
            f"The folder is empty, so the repository's files land in it and "
            f"{target} tracks {upstream}.",
        ))
        return
    plan.adopt = "reset"
    if local.branch and local.branch != target:
        plan.steps.append(Step(
            f"Rename the unborn branch {local.branch} to {target}",
            "It has no commits yet, so this is only which name HEAD points at.",
            kind="note",
        ))
    plan.steps.append(Step(
        f"Point {target} at {upstream} and track it",
        "Your files stay exactly as they are on disk — none is overwritten, "
        "renamed or deleted.",
    ))
    plan.steps.append(Step(
        "The difference then shows up in the Changes tab",
        "Files here that differ from GitHub appear as edits; files on GitHub "
        "that aren't here appear as deletions until you restore or commit them.",
        kind="note",
    ))
    if local.staged:
        plan.steps.append(Step(
            f"{local.staged} staged file{'s' if local.staged != 1 else ''} will be unstaged",
            "The index is rebuilt from GitHub's copy. The files themselves stay put.",
            kind="note",
        ))


def _plan_two_histories(plan):
    """Both sides have commits — track, or offer to push a branch GitHub lacks."""
    remote, local = plan.remote, plan.local
    branch = local.branch
    if branch and branch in remote.heads:
        upstream = f"{plan.remote_name}/{branch}"
        plan.steps.append(Step(
            f"Make {upstream} the upstream of {branch}",
            "Ahead and behind counts, Pull and Push all start working from that.",
        ))
        plan.steps.append(Step(
            "Then compare the two histories",
            "Nothing is merged or rebased here — you'll be told what the "
            "relationship is and can decide.",
            kind="note",
        ))
        return
    if not branch:
        plan.steps.append(Step(
            "This repository has a detached HEAD",
            "Check a branch out before anything can track a remote one.", kind="warn"))
        return
    plan.push_branch = branch
    plan.steps.append(Step(
        f"GitHub has no branch called {branch}",
        "Its branches are: " + ", ".join(sorted(remote.heads)[:8]) +
        ("…" if len(remote.heads) > 8 else ""),
        kind="note",
    ))


# --------------------------------------------------------------- execute ----


@dataclass
class Result:
    path: str = ""
    nwo: str = ""
    remote_name: str = "origin"
    branch: str = ""
    upstream: str = ""
    ahead: int = 0
    behind: int = 0
    unrelated: bool = False
    pushed: bool = False
    deletions: int = 0
    done: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def needs_attention(self):
        return bool(self.warnings)


def execute(plan, push=False):
    """Run the plan. Called on a worker thread; every step is a git command.

    The connecting comes first and the surrounding repository, if there is one
    and it was asked about, is dealt with after: a fetch that fails leaves
    nothing done to a repository the user did not pick.
    """
    res = Result(path=plan.local.path, nwo=plan.remote.nwo, remote_name=plan.remote_name)
    _link(plan, res, push)
    if plan.detach:
        _detach_from_parent(plan, res)
    return res


def _link(plan, res, push):
    """Point the folder at the repository.

    The folder is surveyed again rather than taken from the plan: an attempt
    that failed part way through — a fetch that timed out after the remote was
    added — leaves a folder the plan no longer describes, and pressing Connect
    a second time has to pick up from where that one stopped instead of trying
    to add a remote that is already there.
    """
    remote = plan.remote
    path = plan.local.path
    local = survey(path)

    if not local.is_repo:
        gitcmd.init(path, initial_branch=remote.default_branch or None)
        res.done.append(f"Created a repository in {tilde(path)}")

    existing = local.remotes.get(plan.remote_name)
    if existing is None:
        gitcmd.add_remote(path, plan.remote_name, remote.url)
        res.done.append(f"Added {plan.remote_name} → {remote.url}")
    elif existing != remote.url:
        gitcmd.set_remote_url(path, plan.remote_name, remote.url)
        res.done.append(f"Pointed {plan.remote_name} at {remote.url}")

    if remote.reach_error:
        res.warnings.append(
            f"git could not reach {remote.url}: {remote.reach_error}. The remote is "
            "set up, so Fetch will work once it can be reached."
        )
        res.branch = gitcmd.head_branch(path)
        return

    gitcmd.fetch_remote(path, plan.remote_name)
    res.done.append(f"Fetched {plan.remote_name}")

    # Re-read rather than trust the survey: it is seconds old, and the init and
    # fetch above have both moved things since.
    branch = gitcmd.head_branch(path)
    has_commits = gitcmd.has_commits(path)

    if remote.empty:
        res.branch = branch
        if has_commits and push and branch:
            gitcmd.push(path, plan.remote_name, branch, set_upstream=True)
            res.pushed = True
            res.upstream = f"{plan.remote_name}/{branch}"
            res.done.append(f"Pushed {branch} and set it to track {res.upstream}")
        elif has_commits:
            res.done.append(f"{remote.nwo} is still empty — push {branch} when you're ready")
        return

    if not has_commits:
        branch = _adopt(plan, res, branch)
    elif branch and branch in remote.heads:
        upstream = f"{plan.remote_name}/{branch}"
        gitcmd.set_upstream(path, branch, upstream)
        res.upstream = upstream
        res.done.append(f"{branch} now tracks {upstream}")
    elif branch and push:
        gitcmd.push(path, plan.remote_name, branch, set_upstream=True)
        res.pushed = True
        res.upstream = f"{plan.remote_name}/{branch}"
        res.done.append(f"Pushed {branch} and set it to track {res.upstream}")
    elif branch:
        res.warnings.append(
            f"GitHub has no branch called {branch}, so nothing is tracking it yet. "
            "Pushing creates it — the Push button will offer to."
        )

    res.branch = branch
    if res.upstream and gitcmd.ref_exists(path, res.upstream) and gitcmd.has_commits(path):
        res.ahead, res.behind = gitcmd.ahead_behind(path, branch, res.upstream)
        res.unrelated = gitcmd.merge_base(path, branch, res.upstream) is None
        if res.unrelated:
            res.warnings.append(
                f"{branch} and {res.upstream} have no commit in common — they were "
                "started separately. Until they are joined, pulling refuses and "
                "pushing is rejected."
            )


def _detach_from_parent(plan, res):
    """Take the folder out of the repository it sits inside.

    Neither half of this is committed there. The removal is staged and the
    ignore rule is an edited file, both left in that repository's own Changes
    tab, because what it records about this folder is a commit its owner
    makes — not something a connect slips in on the way past.
    """
    local = plan.local
    parent, rel = local.toplevel, local.rel
    if not parent or not rel or not os.path.isdir(parent):
        return
    name = _parent_name(local)
    notes = []

    try:
        tracked = gitcmd.tracked_under(parent, rel)
        if tracked:
            gitcmd.untrack(parent, [gitcmd.literal(rel)])
            many = len(tracked) != 1
            res.done.append(
                f"Removed {len(tracked)} path{'s' if many else ''} under {rel} "
                f"from {_possessive(name)} index")
            notes.append(
                f"It shows {len(tracked)} staged deletion{'s' if many else ''}, "
                f"and every one of those files is still on disk — committing that "
                f"there is what takes {'them' if many else 'it'} off GitHub's copy "
                f"of {name}.")
    except (gitcmd.GitError, OSError, subprocess.TimeoutExpired) as exc:
        res.warnings.append(
            f"{name} could not be made to let go of this folder: {exc}. It tracks "
            f"what it tracked before, and this folder is connected all the same.")
        return

    pattern = _ignore_in_parent(plan, res, name)
    if pattern:
        notes.append(f"Its .gitignore has gained {pattern}.")
    if notes:
        res.warnings.append(f"Nothing in {name} is committed by this. " + " ".join(notes))


def _ignore_in_parent(plan, res, name):
    """Add the folder to the surrounding repository's .gitignore, if it isn't."""
    local = plan.local
    if local.parent_ignores:
        return ""
    pattern = ignore.pattern_for(local.rel, True)
    try:
        lines = ignore.read_gitignore(local.toplevel).splitlines()
        if pattern in (line.strip() for line in lines):
            return ""
        lines.append(pattern)
        ignore.write_gitignore(local.toplevel, "\n".join(lines))
    except OSError as exc:
        res.warnings.append(f"Couldn't add {pattern} to {_possessive(name)} .gitignore: {exc}")
        return ""
    res.done.append(f"Added {pattern} to {_possessive(name)} .gitignore")
    return pattern


def _adopt(plan, res, branch):
    """Take on the remote's history in a folder that has none of its own."""
    path, remote = plan.local.path, plan.remote
    target = remote.default_branch
    upstream = f"{plan.remote_name}/{target}"

    if plan.adopt == "checkout":
        gitcmd.checkout_track(path, target, upstream)
        res.upstream = upstream
        res.done.append(f"Checked {target} out into the folder, tracking {upstream}")
        return target

    if branch != target:
        gitcmd.set_head_branch(path, target)
    # --mixed: HEAD and the index take the remote's tip, the working tree is
    # not read or written at all. This is the whole reason the folder's files
    # survive being connected to a repository that already has content.
    gitcmd.reset(path, upstream, "mixed")
    gitcmd.set_upstream(path, target, upstream)
    res.upstream = upstream
    res.done.append(f"{target} now tracks {upstream}, with your files left as they were")
    try:
        st = gitcmd.status(path)
        res.deletions = sum(1 for e in st.entries if "D" in (e.index_status + e.work_status))
        if res.deletions:
            many = res.deletions != 1
            res.warnings.append(
                f"{res.deletions} path{'s' if many else ''} in {res.upstream} "
                f"{'are' if many else 'is'} missing from this folder, so Changes "
                f"shows {'them' if many else 'it'} as deleted. Discarding that "
                f"change brings {'them' if many else 'it'} back; committing it "
                f"removes {'them' if many else 'it'} from GitHub."
            )
    except (gitcmd.GitError, OSError, subprocess.TimeoutExpired):
        pass
    return target


# ---------------------------------------------------------------- dialog ----


class ConnectDialog(Adw.Dialog):
    """Pick a folder, pick a repository, read what will happen, then do it."""

    def __init__(self, window, path=""):
        super().__init__(title="Connect to GitHub", content_width=760, content_height=800)
        self.window = window
        self.path = path or ""
        self.manual_nwo = ""
        self.list_nwo = ""
        self.repos = []
        self.plan = None
        self.protocol = github.git_protocol()
        self._generation = 0
        self._debounce = 0
        self._syncing = False
        self._radio = None
        self._plan_rows = []
        self._remote_cache = {}

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(
            title="Connect to GitHub",
            subtitle="Point a folder you already have at a repository that already exists",
        ))
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel)
        self.connect_btn = Gtk.Button(label="Connect", css_classes=["suggested-action"], sensitive=False)
        self.connect_btn.connect("clicked", lambda _b: self._connect())
        header.pack_end(self.connect_btn)

        page = Adw.PreferencesPage()
        page.add(self._folder_group())
        page.add(self._repo_group())
        page.add(self._options_group())
        page.add(self._plan_group())

        toolbar = Adw.ToolbarView(content=page)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

        self.connect("closed", self._on_closed)
        self._load_repos()
        self._sync_folder_row()
        self._schedule()

    # -- the three inputs

    def _folder_group(self):
        group = Adw.PreferencesGroup(title="Local folder")
        self.folder_row = Adw.ActionRow(title="No folder chosen", subtitle="Pick the folder to connect")
        choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        choose.connect("clicked", lambda _b: self._choose_folder())
        self.folder_row.add_suffix(choose)
        self.folder_row.set_activatable_widget(choose)
        group.add(self.folder_row)
        return group

    def _repo_group(self):
        group = Adw.PreferencesGroup(
            title="GitHub repository",
            description="Your account's repositories. Anything else — an "
                        "organisation's, a fork, a repository you were given "
                        "access to — goes in the box underneath.",
        )
        self.search = Gtk.SearchEntry(placeholder_text="Filter", width_request=220)
        self.search.connect("search-changed", lambda _e: self.repo_list.invalidate_filter())
        group.set_header_suffix(self.search)

        self.repo_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                     css_classes=["boxed-list"])
        self.repo_list.set_filter_func(self._filter_row)
        self.repo_list.append(Adw.ActionRow(title="Loading your repositories…"))
        scroller = Gtk.ScrolledWindow(height_request=260)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self.repo_list)
        group.add(scroller)

        self.manual_row = Adw.EntryRow(title="Or a URL, owner/name, or a name")
        self.manual_row.connect("changed", lambda _r: self._on_manual())
        group.add(self.manual_row)
        return group

    def _options_group(self):
        group = Adw.PreferencesGroup(title="How")
        self.remote_row = Adw.EntryRow(title="Remote name", text="origin")
        self.remote_row.connect("changed", lambda _r: self._schedule())
        group.add(self.remote_row)

        self.protocol_row = Adw.ComboRow(
            title="Protocol",
            subtitle="Whichever one your credentials are set up for — gh's own default is pre-selected",
            model=Gtk.StringList.new(["HTTPS", "SSH"]),
            selected=1 if self.protocol == "ssh" else 0,
        )
        self.protocol_row.connect("notify::selected", lambda *_: self._on_protocol())
        group.add(self.protocol_row)

        self.push_row = Adw.SwitchRow(
            title="Push this branch afterwards", subtitle="", visible=False,
        )
        self.push_row.connect("notify::active", lambda *_: self._render_steps())
        group.add(self.push_row)

        # Off unless it is asked for: it stages a removal in a repository the
        # user picked nothing about, which is not a thing to default to.
        self.detach_row = Adw.SwitchRow(
            title="Take it out of the repository around it", subtitle="", visible=False,
        )
        self.detach_row.connect("notify::active", lambda *_: self._render_steps())
        group.add(self.detach_row)
        return group

    def _plan_group(self):
        self.plan_group = Adw.PreferencesGroup(title="What will happen")
        return self.plan_group

    # -- input handling

    def _choose_folder(self):
        chooser = Gtk.FileDialog(title="Folder to connect")
        start = self.path if os.path.isdir(self.path) else self.window.cfg["clone_dir"]
        if start and os.path.isdir(os.path.expanduser(start)):
            chooser.set_initial_folder(Gio.File.new_for_path(os.path.expanduser(start)))

        def picked(dialog, result):
            try:
                folder = dialog.select_folder_finish(result)
            except GLib.Error:
                return
            if folder and folder.get_path():
                self.path = folder.get_path()
                self._syncing = True
                self.detach_row.set_active(False)   # a different folder, a different question
                self._syncing = False
                self._sync_folder_row()
                self._schedule()

        chooser.select_folder(self.window, None, picked)

    def _sync_folder_row(self):
        if self.path:
            self.folder_row.set_title(os.path.basename(self.path.rstrip("/")) or self.path)
            self.folder_row.set_subtitle(tilde(self.path))
        else:
            self.folder_row.set_title("No folder chosen")
            self.folder_row.set_subtitle("Pick the folder to connect")

    def _on_manual(self):
        owner = self.window.ghtab.account.login if getattr(self.window.ghtab, "account", None) else ""
        self.manual_nwo = github.parse_repo_input(self.manual_row.get_text(), owner)
        if self.manual_nwo and self._radio is not None and not self._syncing:
            # Typing wins over the list, and the list should say so.
            self._syncing = True
            self._radio.set_active(True)   # the hidden group leader: clears every row
            self._syncing = False
            self.list_nwo = ""
        self._schedule()

    def _on_protocol(self):
        self.protocol = "ssh" if self.protocol_row.get_selected() == 1 else "https"
        self._schedule()

    def _filter_row(self, row):
        needle = self.search.get_text().strip().lower()
        nwo = getattr(row, "nwo", "")
        return not needle or needle in nwo.lower() or needle in getattr(row, "haystack", "")

    def _load_repos(self):
        def work():
            return github.cached("all-repos", TTL, github.list_repos)

        def done(repos):
            self.repos = repos
            self._fill_repos(repos)

        def failed(exc):
            self.repo_list.remove_all()
            row = Adw.ActionRow(title="Couldn't list your repositories",
                                subtitle=str(exc).splitlines()[0][:120])
            self.repo_list.append(row)

        jobs.run(work, done, failed)

    def _fill_repos(self, repos):
        self.repo_list.remove_all()
        # The group's leader is never shown: activating it is how "none of
        # these" is expressed when the user types a URL instead.
        self._radio = Gtk.CheckButton()
        on_disk = {s.nwo: s.path for s in self.window.states if s.nwo}
        if not repos:
            self.repo_list.append(Adw.ActionRow(
                title="No repositories on this account",
                subtitle="Use the box below for one belonging to somebody else."))
            return
        for repo in repos:
            bits = [repo.visibility, repo.language, f"updated {repo.updated}"]
            row = Adw.ActionRow(title=repo.nwo, subtitle="  ·  ".join(b for b in bits if b))
            row.set_title_lines(1)
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, group=self._radio)
            check.connect("toggled", lambda b, r=repo: self._on_repo_toggled(b, r))
            row.add_prefix(check)
            row.set_activatable_widget(check)
            here = on_disk.get(repo.nwo)
            if here:
                row.add_suffix(Gtk.Label(label=f"on disk at {tilde(here)}",
                                         css_classes=["dim-label", "repo-row-path"],
                                         valign=Gtk.Align.CENTER))
            row.nwo = repo.nwo
            row.haystack = f"{repo.nwo} {repo.description} {repo.language}".lower()
            self.repo_list.append(row)

    def _on_repo_toggled(self, check, repo):
        if self._syncing or not check.get_active():
            return
        self.list_nwo = repo.nwo
        if self.manual_row.get_text():
            self._syncing = True
            self.manual_row.set_text("")
            self.manual_nwo = ""
            self._syncing = False
        self._schedule()

    # -- planning

    def _target_nwo(self):
        return self.manual_nwo or self.list_nwo

    def _schedule(self, delay=350):
        if self._debounce:
            GLib.source_remove(self._debounce)
        self._debounce = GLib.timeout_add(delay, self._recompute)

    def _recompute(self):
        self._debounce = 0
        self._generation += 1
        generation = self._generation
        self.plan = None
        self.connect_btn.set_sensitive(False)

        path = self.path
        nwo = self._target_nwo()
        remote_name = self.remote_row.get_text().strip()
        protocol = self.protocol
        key = (nwo, protocol)

        self._show_loading(
            f"Reading {tilde(path)} and {nwo}…" if path and nwo else "Reading…")

        def work():
            # Both halves block — a status walk on one side, the network on the
            # other — so neither runs anywhere near the main loop.
            local = survey(path) if path else Local()
            if not nwo:
                return local, Remote()
            remote = self._remote_cache.get(key) or inspect_remote(nwo, protocol)
            return local, remote

        def done(result):
            if generation != self._generation:
                return
            local, remote = result
            if not remote.reach_error:
                self._remote_cache[key] = remote
            self._show(build_plan(local, remote, remote_name))

        def failed(exc):
            if generation != self._generation:
                return
            plan = Plan(local=Local(path=path), remote=Remote(nwo=nwo))
            plan.blocked = str(exc).strip().splitlines()[0][:200] or "Couldn't look that repository up"
            self._show(plan)

        jobs.run(work, done, failed)
        return GLib.SOURCE_REMOVE

    def _clear_plan_rows(self):
        for row in self._plan_rows:
            self.plan_group.remove(row)
        self._plan_rows = []

    def _add_plan_row(self, row):
        self.plan_group.add(row)
        self._plan_rows.append(row)

    def _show_loading(self, message):
        self._clear_plan_rows()
        self.plan_group.set_description("")
        row = Adw.ActionRow(title=message)
        row.add_prefix(Adw.Spinner(valign=Gtk.Align.CENTER))
        self._add_plan_row(row)

    def _show(self, plan):
        self.plan = plan
        self._syncing = True
        self.push_row.set_visible(bool(plan.push_branch))
        if plan.push_branch:
            self.push_row.set_subtitle(
                f"git push --set-upstream {plan.remote_name} {plan.push_branch} — "
                "sends your commits to GitHub once the remote is wired up"
            )
            self.push_row.set_title(f"Push {plan.push_branch} afterwards")
            self.push_row.set_active(plan.push_default)
        self.detach_row.set_visible(plan.can_detach)
        if plan.can_detach:
            # Whatever the switch is already set to is left alone: the plan is
            # rebuilt on every keystroke in the remote name, and an answer the
            # user gave should not be taken back by one of those.
            self.detach_row.set_title(f"Take it out of {_parent_name(plan.local)}")
            self.detach_row.set_subtitle(_detach_summary(plan.local))
        self._syncing = False
        self._render_steps()

    def _render_steps(self):
        if self._syncing:
            return
        plan = self.plan
        self._clear_plan_rows()
        self.connect_btn.set_sensitive(bool(plan and plan.ready))
        if plan is None:
            return
        if plan.blocked:
            self.plan_group.set_description("")
            row = Adw.ActionRow(title=plan.blocked, css_classes=["lint-warning"])
            row.add_prefix(Gtk.Label(label="!", css_classes=["lint-warning"], valign=Gtk.Align.CENTER))
            self._add_plan_row(row)
            return

        steps = list(plan.steps)
        if plan.push_branch and self.push_row.get_active():
            steps.append(Step(
                f"Push {plan.push_branch} to {plan.remote_name}",
                "Your commits go to GitHub, and the branch starts tracking.",
            ))
        if plan.can_detach and self.detach_row.get_active():
            steps.extend(_detach_steps(plan))
        self.plan_group.set_description(
            f"{tilde(plan.local.path)}  ↔  {plan.remote.nwo}"
        )
        for step in steps:
            mark = {"do": "→", "note": "·", "warn": "!"}[step.kind]
            row = Adw.ActionRow(title=step.text, subtitle=step.detail)
            label = Gtk.Label(label=mark, valign=Gtk.Align.START, margin_top=2,
                              css_classes=["lint-warning"] if step.kind == "warn" else ["dim-label"])
            label.set_size_request(14, -1)
            row.add_prefix(label)
            self._add_plan_row(row)

    # -- doing it

    def _connect(self):
        plan = self.plan
        if not plan or not plan.ready:
            return
        push = bool(plan.push_branch) and self.push_row.get_active()
        plan.detach = plan.can_detach and self.detach_row.get_active()
        self.connect_btn.set_sensitive(False)
        self.connect_btn.set_label("Connecting…")
        self.set_can_close(False)
        self._show_loading(f"Connecting {tilde(plan.local.path)} to {plan.remote.nwo}…")

        def failed(exc):
            self.set_can_close(True)
            self.connect_btn.set_label("Connect")
            self.connect_btn.set_sensitive(True)
            self._clear_plan_rows()
            row = Adw.ActionRow(title="That didn't work", subtitle=str(exc).strip().splitlines()[0][:200])
            row.add_prefix(Gtk.Label(label="!", css_classes=["lint-warning"], valign=Gtk.Align.CENTER))
            self._add_plan_row(row)
            widgets.error_toast(self.window, exc)

        def done(result):
            self.set_can_close(True)
            self.close()
            finish(self.window, result)

        jobs.run(lambda: execute(plan, push=push), done, failed)

    def _on_closed(self, *_):
        if self._debounce:
            GLib.source_remove(self._debounce)
            self._debounce = 0
        self._generation += 1


# --------------------------------------------------------------- landing ----


def finish(window, result):
    """Put the newly connected folder in front of the user, and explain."""
    github.invalidate(result.nwo)
    window.ghtab.showing = None
    window.adopt_path(result.path)
    name = os.path.basename(result.path.rstrip("/")) or result.path
    if not result.needs_attention:
        widgets.toast(window, f"Connected {name} to {result.nwo}")
        return
    _report(window, result)


def _report(window, result):
    body = "\n\n".join(result.warnings)
    dialog = Adw.AlertDialog(heading=f"Connected to {result.nwo}", body=body)

    steps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=8)
    for line in result.done:
        steps.append(Gtk.Label(label="· " + line, xalign=0, wrap=True,
                               css_classes=["dim-label", "repo-row-path"]))
    dialog.set_extra_child(steps)

    dialog.add_response("close", "Done")
    if result.unrelated and result.upstream:
        dialog.add_response("merge", f"Merge {result.upstream}")
        dialog.set_response_appearance("merge", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("close")
    dialog.set_close_response("close")

    def answered(dlg, res):
        if dlg.choose_finish(res) == "merge":
            _merge_unrelated(window, result)

    dialog.choose(window, None, answered)


def _merge_unrelated(window, result):
    """Join two histories that were started apart. The only way to reconcile them."""
    path, upstream, branch = result.path, result.upstream, result.branch

    def go():
        def work():
            return gitcmd.merge(path, upstream, allow_unrelated=True)

        def done(_out):
            widgets.toast(window, f"Merged {upstream} into {branch}")
            window.refresh_selected(force=True)

        jobs.run(work, done, lambda e: widgets.error_toast(window, e))

    widgets.confirm(
        window, f"Merge {upstream} into {branch}?",
        "This keeps both sets of commits: GitHub's history is joined onto yours "
        "with a merge commit, and nothing is thrown away. Where both sides "
        "changed the same file the merge stops with conflicts, which show up in "
        "the Changes tab.",
        "Merge", go, destructive=False,
    )


def open_dialog(window, path=""):
    ConnectDialog(window, path).present(window)
