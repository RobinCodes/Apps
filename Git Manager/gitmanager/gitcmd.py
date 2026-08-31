"""Every git invocation the app makes.

The rest of the app never shells out to git itself — it calls in here, gets
dataclasses back, and stays ignorant of porcelain formats. Nothing in this
module touches GTK, so it is safe to call from worker threads (and it must be:
several of these calls block for seconds on a cold cache).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

from . import winenv

# Resolved once. Windows installs git into Program Files and only edits the
# PATH of interactive shells, so a process started from a shortcut has to be
# told where to look; "git" is the answer everywhere else.
GIT = winenv.which("git") or "git"

# Stable output regardless of the user's locale, and no index.lock churn from
# the status polling — status is read-only but git still refreshes the index.
_ENV = {
    **os.environ,
    "LC_ALL": "C",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_TERMINAL_PROMPT": "0",  # never block a worker thread on a credential prompt
}


class GitError(Exception):
    def __init__(self, args_, returncode, stderr):
        self.cmd = "git " + " ".join(args_)
        self.returncode = returncode
        self.stderr = (stderr or "").strip()
        super().__init__(self.stderr or f"{self.cmd} exited {returncode}")


def git(repo, *args, check=True, stdin=None, timeout=120):
    """Run git in `repo` and return stdout. Raises GitError unless check=False."""
    proc = subprocess.run(
        [GIT, *args],
        cwd=repo,
        env=_ENV,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=winenv.NO_WINDOW,
    )
    if check and proc.returncode != 0:
        raise GitError(list(args), proc.returncode, proc.stderr)
    return proc.stdout


def git_bytes(repo, *args, check=True, timeout=120):
    proc = subprocess.run(
        [GIT, *args], cwd=repo, env=_ENV, capture_output=True, timeout=timeout,
        creationflags=winenv.NO_WINDOW,
    )
    if check and proc.returncode != 0:
        raise GitError(list(args), proc.returncode, proc.stderr.decode("utf-8", "replace"))
    return proc.stdout


# ---------------------------------------------------------------- status ----


@dataclass
class FileEntry:
    path: str
    index_status: str = "."  # staged side  (porcelain v2 X)
    work_status: str = "."   # worktree side (porcelain v2 Y)
    orig_path: str | None = None  # rename/copy source
    untracked: bool = False
    unmerged: bool = False

    @property
    def staged(self):
        return self.index_status not in (".", "?") and not self.unmerged

    @property
    def unstaged(self):
        return self.untracked or self.unmerged or self.work_status != "."

    @property
    def label(self):
        """Single-letter badge for the file list."""
        if self.unmerged:
            return "U"
        if self.untracked:
            return "?"
        code = self.index_status if self.staged else self.work_status
        return code if code != "." else "M"

    @property
    def display(self):
        if self.orig_path:
            return f"{self.orig_path} → {self.path}"
        return self.path


@dataclass
class Status:
    branch: str = ""          # branch name, or "" when detached
    oid: str = ""
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    detached: bool = False
    entries: list[FileEntry] = field(default_factory=list)
    state: str | None = None  # "merge" / "rebase" / "cherry-pick" / "bisect"

    @property
    def staged_files(self):
        return [e for e in self.entries if e.staged]

    @property
    def unstaged_files(self):
        return [e for e in self.entries if e.unstaged]

    @property
    def dirty(self):
        return bool(self.entries)


def _in_progress(repo):
    gd = os.path.join(repo, ".git")
    # Worktrees and submodules use a .git *file* pointing elsewhere.
    if os.path.isfile(gd):
        try:
            with open(gd) as fh:
                line = fh.read().strip()
            if line.startswith("gitdir:"):
                gd = os.path.join(repo, line[7:].strip())
        except OSError:
            return None
    for name, state in (
        ("MERGE_HEAD", "merge"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
        ("BISECT_LOG", "bisect"),
    ):
        if os.path.exists(os.path.join(gd, name)):
            return state
    return None


def status(repo):
    """Parse `git status --porcelain=v2 -z` into a Status."""
    out = git(
        repo,
        "status",
        "--porcelain=v2",
        "--branch",
        "--untracked-files=all",
        "-z",
    )
    st = Status()
    # -z separates records by NUL; rename records carry their source path in
    # the following record, so we walk with an index rather than a for-loop.
    records = out.split("\0")
    i = 0
    while i < len(records):
        rec = records[i]
        i += 1
        if not rec:
            continue
        kind = rec[0]
        if kind == "#":
            parts = rec.split(" ", 2)
            if len(parts) < 3:
                continue
            key, val = parts[1], parts[2]
            if key == "branch.oid":
                st.oid = "" if val == "(initial)" else val
            elif key == "branch.head":
                if val == "(detached)":
                    st.detached = True
                else:
                    st.branch = val
            elif key == "branch.upstream":
                st.upstream = val
            elif key == "branch.ab":
                for tok in val.split():
                    if tok.startswith("+"):
                        st.ahead = int(tok[1:])
                    elif tok.startswith("-"):
                        st.behind = int(tok[1:])
        elif kind == "1":
            f = rec.split(" ", 8)
            st.entries.append(FileEntry(path=f[8], index_status=f[1][0], work_status=f[1][1]))
        elif kind == "2":
            f = rec.split(" ", 9)
            orig = records[i] if i < len(records) else None
            i += 1
            st.entries.append(
                FileEntry(path=f[9], index_status=f[1][0], work_status=f[1][1], orig_path=orig)
            )
        elif kind == "u":
            f = rec.split(" ", 10)
            st.entries.append(
                FileEntry(path=f[10], index_status=f[1][0], work_status=f[1][1], unmerged=True)
            )
        elif kind == "?":
            st.entries.append(FileEntry(path=rec[2:], untracked=True))
        # "!" ignored entries never appear: we don't pass --ignored.
    st.entries.sort(key=lambda e: e.path)
    st.state = _in_progress(repo)
    return st


def is_repo(path):
    try:
        return git(path, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except (GitError, OSError, subprocess.TimeoutExpired):
        return False


def in_git_dir(path):
    """True inside .git itself, the one place a repository must never be made."""
    try:
        return git(path, "rev-parse", "--is-inside-git-dir").strip() == "true"
    except (GitError, OSError, subprocess.TimeoutExpired):
        return False


def toplevel(path):
    return git(path, "rev-parse", "--show-toplevel").strip()


# ----------------------------------------------------------------- diffs ----


def diff_file(repo, path, staged=False, untracked=False, context=3):
    """Unified diff for one file, as text. Untracked files diff against /dev/null."""
    if untracked:
        full = os.path.join(repo, path)
        if os.path.isdir(full):
            return ""  # untracked submodule / dir — nothing useful to show
        # --no-index exits 1 when files differ, which is the normal case here.
        return git(
            repo, "diff", "--no-color", f"-U{context}", "--no-index",
            "--", os.devnull, path, check=False,
        )
    args = ["diff", "--no-color", f"-U{context}"]
    if staged:
        args.append("--cached")
    args += ["--", path]
    return git(repo, *args)


def diff_commit(repo, sha, path=None):
    args = ["show", "--no-color", "--format=", "--patch", sha]
    if path:
        args += ["--", path]
    return git(repo, *args)


def commit_files(repo, sha):
    """Files touched by a commit, as (status_letter, path)."""
    out = git(repo, "show", "--name-status", "--format=", "-z", sha)
    fields = [f for f in out.split("\0") if f]
    files, i = [], 0
    while i < len(fields):
        code = fields[i]
        i += 1
        if code[0] in ("R", "C"):  # rename/copy: old path, then new path
            if i + 1 < len(fields):
                files.append((code[0], fields[i + 1]))
            i += 2
        else:
            if i < len(fields):
                files.append((code[0], fields[i]))
            i += 1
    return files


def split_hunks(diff_text):
    """Split a one-file diff into (header, [hunk_text, ...])."""
    lines = diff_text.splitlines(keepends=True)
    start = next((n for n, ln in enumerate(lines) if ln.startswith("@@")), None)
    if start is None:
        return "".join(lines), []
    header = "".join(lines[:start])
    hunks, current = [], []
    for ln in lines[start:]:
        if ln.startswith("@@") and current:
            hunks.append("".join(current))
            current = []
        current.append(ln)
    if current:
        hunks.append("".join(current))
    return header, hunks


def apply_patch(repo, patch, cached=True, reverse=False):
    """Apply a constructed patch — the mechanism behind hunk staging."""
    args = ["apply", "--whitespace=nowarn"]
    if cached:
        args.append("--cached")
    if reverse:
        args.append("--reverse")
    args.append("-")
    if not patch.endswith("\n"):
        patch += "\n"
    git(repo, *args, stdin=patch)


# ------------------------------------------------------------ index / ops ----


def stage(repo, paths):
    if paths:
        git(repo, "add", "--", *paths)


def unstage(repo, paths):
    if paths:
        # `git reset` fails on a repo with no commits yet; rm --cached is the
        # equivalent there.
        try:
            git(repo, "reset", "--quiet", "HEAD", "--", *paths)
        except GitError:
            git(repo, "rm", "--cached", "--quiet", "-r", "--", *paths)


def stage_all(repo):
    git(repo, "add", "-A")


def unstage_all(repo):
    try:
        git(repo, "reset", "--quiet", "HEAD")
    except GitError:
        git(repo, "rm", "--cached", "--quiet", "-r", ".")


def discard(repo, entries):
    """Throw away worktree changes. Destructive — callers must confirm first."""
    tracked = [e.path for e in entries if not e.untracked]
    untracked = [e.path for e in entries if e.untracked]
    if tracked:
        git(repo, "checkout", "--", *tracked)
    for p in untracked:
        full = os.path.join(repo, p)
        if os.path.isdir(full):
            git(repo, "clean", "-fd", "--", p)
        else:
            try:
                os.remove(full)
            except FileNotFoundError:
                pass


def commit(repo, message, amend=False, sign_off=False):
    args = ["commit", "-m", message]
    if amend:
        args.append("--amend")
    if sign_off:
        args.append("--signoff")
    return git(repo, *args)


def has_commits(repo):
    try:
        git(repo, "rev-parse", "HEAD")
        return True
    except GitError:
        return False


# ------------------------------------------------------------- transport ----


def fetch(repo, all_remotes=True, prune=True):
    args = ["fetch"]
    if all_remotes:
        args.append("--all")
    if prune:
        args.append("--prune")
    return git(repo, *args, timeout=300)


def pull(repo, rebase=False):
    args = ["pull"]
    args.append("--rebase" if rebase else "--no-rebase")
    return git(repo, *args, timeout=300)


def push(repo, remote=None, branch=None, force=False, set_upstream=False):
    args = ["push"]
    if force:
        # --force-with-lease refuses when the remote moved under us, which is
        # the difference between "rewrite my branch" and "clobber a teammate".
        args.append("--force-with-lease")
    if set_upstream:
        args.append("--set-upstream")
    if remote:
        args.append(remote)
        if branch:
            args.append(branch)
    return git(repo, *args, timeout=300)


def remotes(repo):
    out = git(repo, "remote", "-v")
    seen = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            seen.setdefault(parts[0], parts[1])
    return seen


def remote_url(repo, remote="origin"):
    return remotes(repo).get(remote)


def add_remote(repo, name, url):
    return git(repo, "remote", "add", name, url)


def set_remote_url(repo, name, url):
    return git(repo, "remote", "set-url", name, url)


def remove_remote(repo, name):
    return git(repo, "remote", "remove", name)


def fetch_remote(repo, remote, prune=True):
    args = ["fetch", remote]
    if prune:
        args.append("--prune")
    return git(repo, *args, timeout=300)


def ls_remote(url, timeout=90):
    """Ask a remote we haven't added yet what it holds.

    Returns (default branch, {branch: sha}). This is the cheapest honest answer
    to "does this repository exist, may I read it, and what is in it" — it uses
    the same credentials the later fetch and push will use, which `gh` does not.
    Runs outside any repository, so no cwd is passed.
    """
    proc = subprocess.run(
        ["git", "ls-remote", "--symref", url],
        env=_ENV, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise GitError(["ls-remote", url], proc.returncode, proc.stderr)
    default, heads = "", {}
    for line in proc.stdout.splitlines():
        if line.startswith("ref: "):
            # "ref: refs/heads/main\tHEAD" — the remote's default branch.
            ref, _, name = line[5:].partition("\t")
            if name.strip() == "HEAD" and ref.startswith("refs/heads/"):
                default = ref[len("refs/heads/"):]
            continue
        sha, _, ref = line.partition("\t")
        if ref.startswith("refs/heads/"):
            heads[ref[len("refs/heads/"):]] = sha
    return default, heads


# ------------------------------------------------------------- new repos ----


def init(path, initial_branch=None):
    """Create a repository in an existing directory. Files there are untouched."""
    args = ["init"]
    if initial_branch:
        args += ["--initial-branch", initial_branch]
    return git(path, *args)


def head_branch(repo):
    """The checked-out branch name, even before the first commit. "" if detached."""
    return git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip()


def set_head_branch(repo, name):
    """Point an unborn HEAD at a different branch name.

    `git branch -m` renames a branch, and before the first commit there is no
    branch to rename — only a HEAD symref to one that was never created.
    Writing the symref is that rename, minus the commit it would need.
    """
    return git(repo, "symbolic-ref", "HEAD", f"refs/heads/{name}")


def set_upstream(repo, branch, upstream):
    return git(repo, "branch", f"--set-upstream-to={upstream}", branch)


def checkout_track(repo, branch, upstream):
    """Create `branch` from a remote-tracking branch and follow it."""
    return git(repo, "checkout", "-b", branch, "--track", upstream)


def ref_exists(repo, ref):
    return bool(git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}",
                    check=False).strip())


def merge_base(repo, a, b):
    """The commit two refs share, or None when their histories are unrelated."""
    return git(repo, "merge-base", a, b, check=False).strip() or None


def ahead_behind(repo, local, remote):
    """(commits `local` has and `remote` doesn't, and the other way round)."""
    out = git(repo, "rev-list", "--left-right", "--count", f"{local}...{remote}",
              check=False).strip().split()
    if len(out) != 2:
        return 0, 0
    return int(out[0]), int(out[1])


def tree_is_empty(path):
    """True when a folder holds nothing but its own .git."""
    try:
        return not [n for n in os.listdir(path) if n != ".git"]
    except OSError:
        return False


# -------------------------------------------------------------- branches ----


@dataclass
class Branch:
    name: str
    current: bool = False
    remote: bool = False
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    subject: str = ""
    when: str = ""


def branches(repo, include_remote=True):
    fmt = "%(refname)%00%(HEAD)%00%(refname:short)%00%(upstream:short)%00%(upstream:track)%00%(contents:subject)%00%(committerdate:relative)"
    args = ["for-each-ref", "--sort=-committerdate", f"--format={fmt}", "refs/heads"]
    if include_remote:
        args.append("refs/remotes")
    out = git(repo, *args)
    result = []
    for line in out.splitlines():
        if not line.strip():
            continue
        f = line.split("\0")
        if len(f) < 7:
            continue
        full, head, name, upstream, track, subject, when = f[:7]
        # refs/remotes/origin/HEAD shortens to plain "origin", which would
        # otherwise show up as a branch of its own.
        if full.endswith("/HEAD"):
            continue
        ahead = behind = 0
        # track looks like "[ahead 2, behind 1]" or "[gone]" or ""
        if "ahead " in track:
            ahead = int(track.split("ahead ")[1].split("]")[0].split(",")[0])
        if "behind " in track:
            behind = int(track.split("behind ")[1].split("]")[0].split(",")[0])
        result.append(
            Branch(
                name=name,
                current=head.strip() == "*",
                upstream=upstream or None,
                ahead=ahead,
                behind=behind,
                subject=subject,
                when=when,
            )
        )
    # A remote branch can't be told from a local one with a "/" in its name by
    # looking at the short refname, so ask which names are actually local.
    local = set(git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").split())
    for b in result:
        b.remote = b.name not in local
    return result


def checkout(repo, name):
    return git(repo, "checkout", name)


def checkout_remote(repo, remote_ref):
    """Create a local branch tracking a remote one (origin/foo -> foo)."""
    local = remote_ref.split("/", 1)[1] if "/" in remote_ref else remote_ref
    return git(repo, "checkout", "-b", local, "--track", remote_ref)


def create_branch(repo, name, start=None, switch=True):
    args = ["switch", "-c", name] if switch else ["branch", name]
    if start:
        args.append(start)
    return git(repo, *args)


def delete_branch(repo, name, force=False, remote=False):
    if remote:
        rem, _, ref = name.partition("/")
        return git(repo, "push", rem, "--delete", ref, timeout=300)
    return git(repo, "branch", "-D" if force else "-d", name)


def merge(repo, name, no_ff=False, allow_unrelated=False):
    args = ["merge"]
    if no_ff:
        args.append("--no-ff")
    if allow_unrelated:
        # git refuses by default, on the assumption that two histories with no
        # commit in common are two different projects. Joining a folder to a
        # repository that was created with its own README is the case where
        # they are not, so the caller has to say so explicitly.
        args.append("--allow-unrelated-histories")
    args.append(name)
    return git(repo, *args)


def rename_branch(repo, old, new):
    return git(repo, "branch", "-m", old, new)


# ----------------------------------------------------------------- stash ----


@dataclass
class Stash:
    ref: str
    subject: str
    when: str


def stash_list(repo):
    out = git(repo, "stash", "list", "--format=%gd%00%s%00%cr")
    result = []
    for line in out.splitlines():
        f = line.split("\0")
        if len(f) >= 3:
            result.append(Stash(ref=f[0], subject=f[1], when=f[2]))
    return result


def stash_push(repo, message=None, include_untracked=True):
    args = ["stash", "push"]
    if include_untracked:
        args.append("--include-untracked")
    if message:
        args += ["-m", message]
    return git(repo, *args)


def stash_apply(repo, ref, pop=True):
    return git(repo, "stash", "pop" if pop else "apply", ref)


def stash_drop(repo, ref):
    return git(repo, "stash", "drop", ref)


# ------------------------------------------------------------------- log ----


@dataclass
class Commit:
    sha: str
    short: str
    author: str
    email: str
    when: str
    rel: str
    refs: str
    parents: list[str]
    subject: str
    body: str

    @property
    def is_merge(self):
        return len(self.parents) > 1


_LOG_FMT = "%H%x00%h%x00%an%x00%ae%x00%ad%x00%ar%x00%D%x00%P%x00%s%x00%b%x1e"


def log(repo, limit=200, skip=0, ref=None, path=None, search=None):
    args = ["log", f"--format={_LOG_FMT}", "--date=short", f"-n{limit}", f"--skip={skip}"]
    if search:
        args += ["--grep", search, "--regexp-ignore-case"]
    if ref:
        args.append(ref)
    if path:
        args += ["--", path]
    try:
        out = git(repo, *args)
    except GitError:
        return []  # empty repo: HEAD doesn't resolve
    commits = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        f = rec.split("\0")
        if len(f) < 10:
            continue
        commits.append(
            Commit(
                sha=f[0], short=f[1], author=f[2], email=f[3], when=f[4], rel=f[5],
                refs=f[6], parents=f[7].split() if f[7] else [], subject=f[8], body=f[9],
            )
        )
    return commits


# ----------------------------------------------------- destructive extras ----


def reset(repo, ref, mode="mixed"):
    """mode: soft | mixed | hard. hard is destructive — confirm before calling."""
    return git(repo, "reset", f"--{mode}", ref)


def revert(repo, sha, no_commit=False):
    args = ["revert", "--no-edit"]
    if no_commit:
        args.append("--no-commit")
    args.append(sha)
    return git(repo, *args)


def clean(repo, directories=True, ignored=False):
    args = ["clean", "-f"]
    if directories:
        args.append("-d")
    if ignored:
        args.append("-x")
    return git(repo, *args)


def abort_state(repo, state):
    """Bail out of an in-progress merge/rebase/cherry-pick."""
    cmd = {"merge": "merge", "rebase": "rebase", "cherry-pick": "cherry-pick", "revert": "revert"}.get(state)
    if cmd:
        return git(repo, cmd, "--abort")
    return ""


# ------------------------------------------------------- index / ignoring ----

# git chokes on a command line built from thousands of paths, and a repo with
# 5000 tracked files is ordinary. Everything below that takes a path list feeds
# it in batches of this size.
_ARG_BATCH = 500


def _batched(paths, size=_ARG_BATCH):
    paths = list(paths)
    for i in range(0, len(paths), size):
        yield paths[i:i + size]


def tracked_files(repo):
    """Every path in the index. These are the ones .gitignore cannot touch."""
    out = git(repo, "ls-files", "-z")
    return [p for p in out.split("\0") if p]


def literal(path):
    """A pathspec matching this exact path, `*`, `?` and `[` included.

    git reads a bare path as a glob, so a folder called `notes[1]` matches
    `notes1` and not itself. Any path taken from disk goes through here before
    it is handed to a command that would act on what it matched.
    """
    return f":(literal){path}"


def tracked_under(repo, rel):
    """Index entries under one directory. Empty when nothing there is tracked."""
    out = git(repo, "ls-files", "-z", "--", literal(rel))
    return [p for p in out.split("\0") if p]


def listed_files(repo):
    """Tracked plus untracked-and-not-ignored — i.e. what the repo really is."""
    out = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted({p for p in out.split("\0") if p})


def check_ignore(repo, paths, no_index=False):
    """Subset of `paths` that the ignore rules apply to.

    Two different questions hide behind one command, and the flag picks which:

      no_index=False — "is git ignoring this path?" check-ignore consults the
        index and stays silent about tracked files, because a tracked file is
        not being ignored no matter what the patterns say.

      no_index=True — "do the patterns match this path?" ignoring the index.
        This is how you find the files that look ignored in the .gitignore but
        keep showing up in status: matched by a pattern, yet still tracked.
    """
    paths = [p for p in paths if p]
    if not paths:
        return set()
    args = ["check-ignore", "-z", "--stdin"]
    if no_index:
        args.append("--no-index")
    found = set()
    for batch in _batched(paths):
        # Exit 1 simply means "none of these matched", not a failure.
        out = git(repo, *args, stdin="\0".join(batch) + "\0", check=False)
        found.update(p for p in out.split("\0") if p)
    return found


def untrack(repo, paths):
    """Drop paths from the index, leaving them on disk.

    The fix for a file that keeps showing up after being added to .gitignore:
    the pattern only ever applied to untracked files, so the path has to leave
    the index before the pattern can take effect.
    """
    for batch in _batched(paths):
        git(repo, "rm", "--cached", "--quiet", "-r", "--ignore-unmatch", "--", *batch)
