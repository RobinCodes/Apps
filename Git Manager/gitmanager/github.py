"""The GitHub half, built on the `gh` CLI.

Using gh rather than the REST API directly means the app inherits the
credentials already stored in ~/.config/gh — there is no token for this app to
prompt for, store, or leak. Every call here does network I/O, so all of it
belongs on a worker thread.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

GH = shutil.which("gh")
NET_TIMEOUT = 60


class GhError(Exception):
    pass


def available():
    return GH is not None


def _gh(*args, timeout=NET_TIMEOUT, check=True):
    if not GH:
        raise GhError("The GitHub CLI (gh) is not installed — pacman -S github-cli")
    proc = subprocess.run(
        [GH, *args], capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"},
    )
    if check and proc.returncode != 0:
        raise GhError((proc.stderr or proc.stdout or "").strip() or f"gh {args[0]} failed")
    return proc.stdout


def _gh_json(*args, timeout=NET_TIMEOUT):
    out = _gh(*args, timeout=timeout)
    try:
        return json.loads(out or "[]")
    except ValueError:
        return []


# ------------------------------------------------------------------ auth ----


@dataclass
class Account:
    login: str = ""
    scopes: list[str] = field(default_factory=list)
    ok: bool = False
    message: str = ""


def auth_status():
    if not GH:
        return Account(message="The GitHub CLI (gh) is not installed — pacman -S github-cli")
    try:
        out = _gh("auth", "status", timeout=20, check=False)
    except (GhError, subprocess.TimeoutExpired) as e:
        return Account(message=str(e))
    login = ""
    scopes = []
    m = re.search(r"Logged in to \S+ account (\S+)", out)
    if m:
        login = m.group(1)
    m = re.search(r"Token scopes:\s*(.+)", out)
    if m:
        scopes = [s.strip().strip("'\"") for s in m.group(1).split(",")]
    if not login:
        return Account(message="Not logged in to GitHub — run: gh auth login")
    return Account(login=login, scopes=scopes, ok=True)


# ----------------------------------------------------------- remote → nwo ----

_SSH = re.compile(r"^(?:ssh://)?(?:[\w.-]+@)?([\w.-]+)[:/](.+?)(?:\.git)?/?$")
_HTTP = re.compile(r"^https?://(?:[^@/]+@)?([\w.-]+)/(.+?)(?:\.git)?/?$")


def parse_remote(url):
    """Turn a git remote URL into (host, 'owner/name'), or (None, None)."""
    if not url:
        return None, None
    url = url.strip()
    for rx in (_HTTP, _SSH):
        m = rx.match(url)
        if m:
            host, path = m.group(1), m.group(2)
            if path.count("/") >= 1:
                return host, "/".join(path.split("/")[:2])
    return None, None


def nwo_for(remote_url):
    host, nwo = parse_remote(remote_url)
    return nwo if host and "github" in host else None


def nwo_from_remotes(remotes):
    """Pick the remote that stands for this repository on GitHub.

    `origin` by convention, but a folder connected under another name — a fork
    with `upstream`, a `github` remote beside a company one — is still a GitHub
    repository and the tab should show it.
    """
    for name in ["origin", *sorted(remotes)]:
        nwo = nwo_for(remotes.get(name))
        if nwo:
            return name, nwo
    return None, None


def parse_repo_input(text, default_owner=""):
    """Read whatever the user pasted as "owner/name", or "" if it isn't one.

    Accepts a browser URL, a clone URL of either protocol, `owner/name`, and a
    bare name when we know whose account to assume.
    """
    text = (text or "").strip()
    if not text:
        return ""
    first = text.split("/")[0]
    if "://" in text or text.startswith("git@") or ":" in first:
        return nwo_for(text) or (parse_remote(text)[1] or "")
    text = text.strip("/")
    if text.endswith(".git"):
        text = text[:-4]
    parts = [p for p in text.split("/") if p]
    if len(parts) >= 2:
        return "/".join(parts[:2])
    if len(parts) == 1 and default_owner:
        return f"{default_owner}/{parts[0]}"
    return ""


# ------------------------------------------------------------------ data ----


@dataclass
class Repo:
    name: str = ""
    nwo: str = ""
    visibility: str = ""
    description: str = ""
    url: str = ""
    updated: str = ""
    fork: bool = False
    default_branch: str = ""
    stars: int = 0
    language: str = ""
    ssh_url: str = ""
    empty: bool = False
    archived: bool = False
    permission: str = ""   # READ / TRIAGE / WRITE / MAINTAIN / ADMIN

    @property
    def owner(self):
        return self.nwo.split("/")[0] if "/" in self.nwo else ""

    @property
    def writable(self):
        # Empty means we never asked; only a definite READ is read-only.
        return self.permission not in ("READ", "TRIAGE")

    def clone_url(self, protocol="https"):
        if protocol == "ssh":
            return self.ssh_url or f"git@github.com:{self.nwo}.git"
        return (self.url or f"https://github.com/{self.nwo}") + ".git"


def list_repos(limit=200):
    data = _gh_json(
        "repo", "list", "--limit", str(limit), "--json",
        "name,nameWithOwner,visibility,description,url,updatedAt,isFork,"
        "defaultBranchRef,stargazerCount,primaryLanguage",
        timeout=90,
    )
    repos = []
    for r in data:
        repos.append(
            Repo(
                name=r.get("name", ""),
                nwo=r.get("nameWithOwner", ""),
                visibility=(r.get("visibility") or "").lower(),
                description=r.get("description") or "",
                url=r.get("url", ""),
                updated=(r.get("updatedAt") or "")[:10],
                fork=bool(r.get("isFork")),
                default_branch=(r.get("defaultBranchRef") or {}).get("name", "") if r.get("defaultBranchRef") else "",
                stars=r.get("stargazerCount") or 0,
                language=(r.get("primaryLanguage") or {}).get("name", "") if r.get("primaryLanguage") else "",
            )
        )
    return repos


REPO_FIELDS = (
    "name,nameWithOwner,visibility,description,url,sshUrl,updatedAt,isFork,"
    "isArchived,isEmpty,defaultBranchRef,stargazerCount,primaryLanguage,viewerPermission"
)


def repo_info(nwo):
    """Everything about one repository, including whether we may write to it."""
    data = _gh_json("repo", "view", nwo, "--json", REPO_FIELDS, timeout=45)
    if not isinstance(data, dict) or not data.get("nameWithOwner"):
        raise GhError(f"GitHub returned nothing for {nwo}")
    return Repo(
        name=data.get("name", ""),
        nwo=data.get("nameWithOwner", ""),
        visibility=(data.get("visibility") or "").lower(),
        description=data.get("description") or "",
        url=data.get("url", ""),
        updated=(data.get("updatedAt") or "")[:10],
        fork=bool(data.get("isFork")),
        default_branch=(data.get("defaultBranchRef") or {}).get("name", ""),
        stars=data.get("stargazerCount") or 0,
        language=(data.get("primaryLanguage") or {}).get("name", "") if data.get("primaryLanguage") else "",
        ssh_url=data.get("sshUrl", ""),
        empty=bool(data.get("isEmpty")),
        archived=bool(data.get("isArchived")),
        permission=(data.get("viewerPermission") or "").upper(),
    )


def git_protocol():
    """Whichever protocol `gh` was told to clone with — https unless changed."""
    try:
        out = _gh("config", "get", "git_protocol", timeout=15, check=False).strip()
    except (GhError, subprocess.TimeoutExpired, OSError):
        return "https"
    return out if out in ("https", "ssh") else "https"


@dataclass
class PullRequest:
    number: int
    title: str
    state: str
    draft: bool
    head: str
    base: str
    author: str
    url: str
    updated: str
    review: str = ""


def pull_requests(nwo, limit=30, state="open"):
    data = _gh_json(
        "pr", "list", "--repo", nwo, "--state", state, "--limit", str(limit),
        "--json", "number,title,state,isDraft,headRefName,baseRefName,author,url,updatedAt,reviewDecision",
    )
    return [
        PullRequest(
            number=p.get("number", 0), title=p.get("title", ""),
            state=(p.get("state") or "").lower(), draft=bool(p.get("isDraft")),
            head=p.get("headRefName", ""), base=p.get("baseRefName", ""),
            author=(p.get("author") or {}).get("login", ""), url=p.get("url", ""),
            updated=(p.get("updatedAt") or "")[:10],
            review=(p.get("reviewDecision") or "").replace("_", " ").lower(),
        )
        for p in data
    ]


@dataclass
class Issue:
    number: int
    title: str
    state: str
    author: str
    url: str
    updated: str
    labels: list[str] = field(default_factory=list)
    comments: int = 0


def issues(nwo, limit=30, state="open"):
    data = _gh_json(
        "issue", "list", "--repo", nwo, "--state", state, "--limit", str(limit),
        "--json", "number,title,state,author,url,updatedAt,labels,comments",
    )
    out = []
    for i in data:
        comments = i.get("comments")
        out.append(
            Issue(
                number=i.get("number", 0), title=i.get("title", ""),
                state=(i.get("state") or "").lower(),
                author=(i.get("author") or {}).get("login", ""),
                url=i.get("url", ""), updated=(i.get("updatedAt") or "")[:10],
                labels=[l.get("name", "") for l in (i.get("labels") or [])],
                comments=len(comments) if isinstance(comments, list) else (comments or 0),
            )
        )
    return out


@dataclass
class Run:
    name: str
    status: str
    conclusion: str
    branch: str
    event: str
    url: str
    created: str

    @property
    def ok(self):
        return self.conclusion == "success"

    @property
    def running(self):
        return self.status in ("in_progress", "queued", "requested", "waiting")


def runs(nwo, limit=15):
    data = _gh_json(
        "run", "list", "--repo", nwo, "--limit", str(limit),
        "--json", "displayTitle,workflowName,status,conclusion,headBranch,event,url,createdAt",
    )
    return [
        Run(
            name=r.get("displayTitle") or r.get("workflowName") or "run",
            status=r.get("status") or "", conclusion=r.get("conclusion") or "",
            branch=r.get("headBranch") or "", event=r.get("event") or "",
            url=r.get("url") or "", created=(r.get("createdAt") or "")[:10],
        )
        for r in data
    ]


# --------------------------------------------------------------- actions ----


def clone(nwo, dest):
    return _gh("repo", "clone", nwo, dest, timeout=900)


def create_repo(name, private=True, description="", source=None, push=False, remote="origin"):
    args = ["repo", "create", name, "--private" if private else "--public"]
    if description:
        args += ["--description", description]
    if source:
        args += ["--source", source, "--remote", remote]
        if push:
            args.append("--push")
    return _gh(*args, timeout=300)


def create_pr(repo_path, title, body="", base=None, draft=False):
    """Open a PR from the checked-out branch. Runs inside the repo directory."""
    if not GH:
        raise GhError("The GitHub CLI (gh) is not installed — pacman -S github-cli")
    args = [GH, "pr", "create", "--title", title, "--body", body or ""]
    if base:
        args += ["--base", base]
    if draft:
        args.append("--draft")
    proc = subprocess.run(args, cwd=repo_path, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise GhError((proc.stderr or proc.stdout).strip())
    return proc.stdout.strip()


# --------------------------------------------------------------- caching ----

_cache = {}


def cached(key, ttl, producer):
    """Memoise a network call so switching tabs doesn't re-hit the API."""
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = producer()
    _cache[key] = (now, value)
    return value


def invalidate(prefix=""):
    for k in [k for k in _cache if not prefix or str(k).startswith(prefix)]:
        _cache.pop(k, None)
