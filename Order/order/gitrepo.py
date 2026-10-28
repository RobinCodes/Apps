"""What git says about a checkout, and the only three things the Order does to one.

Clone it when it is missing; fast-forward it when it is clean and behind; for
the brain, commit-rebase-push. Anything else — local edits, unpushed commits,
a diverged branch, a folder that is not a repository — is *reported*, never
resolved by force. A sync that destroyed work to make two machines agree would
be worse than two machines that disagree.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def git(args: list[str], cwd: Path | None = None, timeout: float = 300) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
           "LC_ALL": "C"}
    try:
        return subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
                              creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git", *args], 124, "", str(exc))


@dataclass
class State:
    exists: bool = False
    is_repo: bool = False
    remote: str = ""
    branch: str = ""
    dirty: int = 0              # changed or untracked files
    ahead: int = 0
    behind: int = 0
    upstream: bool = False
    detail: str = ""

    @property
    def clean(self) -> bool:
        return self.is_repo and not self.dirty


def same_remote(a: str, b: str) -> bool:
    def norm(u: str) -> str:
        u = u.strip().rstrip("/")
        if u.endswith(".git"):
            u = u[:-4]
        u = u.replace("git@github.com:", "https://github.com/")
        return u.lower()
    return norm(a) == norm(b)


def state(where: Path, fetch: bool = False) -> State:
    s = State(exists=where.exists())
    if not s.exists:
        return s
    top = git(["rev-parse", "--show-toplevel"], where)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != where.resolve():
        # A folder inside another repository is not a repository of its own.
        return s
    s.is_repo = True
    s.remote = git(["remote", "get-url", "origin"], where).stdout.strip()
    s.branch = git(["branch", "--show-current"], where).stdout.strip()
    if fetch and s.remote:
        done = git(["fetch", "--quiet", "origin"], where, timeout=180)
        if done.returncode:
            s.detail = (done.stderr.strip().splitlines() or ["fetch failed"])[-1]
    s.dirty = len([line for line in git(["status", "--porcelain"], where).stdout.splitlines()
                   if line.strip()])
    counts = git(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], where)
    if counts.returncode == 0 and counts.stdout.split():
        ahead, behind = counts.stdout.split()[:2]
        s.ahead, s.behind, s.upstream = int(ahead), int(behind), True
    return s


def clone(url: str, where: Path) -> subprocess.CompletedProcess:
    where.parent.mkdir(parents=True, exist_ok=True)
    return git(["clone", "--quiet", url, str(where)], timeout=1800)


def fast_forward(where: Path) -> subprocess.CompletedProcess:
    return git(["merge", "--ff-only", "--quiet", "@{upstream}"], where)


def adopt(where: Path, url: str) -> tuple[bool, str]:
    """Make an existing folder the manifest's repository without losing a byte of it.

    The folder's files stay exactly as they are. If it is not a repository yet,
    it becomes one whose history is the remote's, with the folder's differences
    as uncommitted changes on top — to look at and commit. If it is one with no
    remote, the remote is added and fetched; histories that share an ancestor
    then merge as usual, and ones that do not are left for a human.
    """
    s = state(where)
    if not s.exists:
        return False, "nothing there to adopt"
    if s.is_repo:
        if s.remote and not same_remote(s.remote, url):
            return False, f"already a repository of {s.remote}"
        if not s.remote:
            git(["remote", "add", "origin", url], where)
        done = git(["fetch", "--quiet", "origin"], where, timeout=600)
        if done.returncode:
            return False, done.stderr.strip() or "fetch failed"
        head = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], where).stdout.strip() \
            or "origin/main"
        branch = s.branch or "main"
        git(["branch", "--set-upstream-to", head, branch], where)
        return True, f"remote set to {url}; now {state(where).ahead} ahead, {state(where).behind} behind"
    if git(["init", "--quiet", "-b", "main"], where).returncode:
        return False, "git init failed"
    git(["remote", "add", "origin", url], where)
    done = git(["fetch", "--quiet", "origin"], where, timeout=600)
    if done.returncode:
        return False, done.stderr.strip() or "fetch failed"
    head = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], where).stdout.strip()
    if not head:
        git(["remote", "set-head", "origin", "--auto"], where)
        head = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], where).stdout.strip() \
            or "origin/main"
    # A mixed reset moves HEAD and the index, never the working tree.
    git(["reset", "--quiet", head], where)
    git(["branch", "--set-upstream-to", head], where)
    left = state(where)
    return True, f"adopted; {left.dirty} local difference(s) to review and commit"
