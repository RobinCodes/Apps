"""Git Manager as a service: the git operations other apps can ask it for.

LaRenderer's Git menu, its shortcuts and its Overleaf sync do not run git
themselves. They ask a Git Manager -- the one already open, or one started
with `git-manager --background` the first time any of them is used -- over
the session bus. Everything git-shaped then happens in one place, with this
app's error handling and credential rules, and a Git Manager window that
happens to be open sees the result straight away.

Two halves:

* `run()` and the operations under it are plain functions over gitcmd. No
  GTK, safe on a worker thread, and importable by a caller that has no
  session bus to reach a service on (Windows), which calls them in-process.
* `Host` puts `run()` on the bus, one worker thread per call, so a push that
  takes ten seconds never stalls the window or the next caller.

The interface is a single method, Call(operation, JSON arguments) -> JSON,
rather than a method per operation, so adding an operation never changes the
interface an older client was built against.
"""

from __future__ import annotations

import inspect
import json
import os
import threading

from . import gitcmd, winenv

# GApplication already owns the application ID on the bus; the service is one
# more object on the connection it made, not a second name to manage.
BUS_NAME = "com.robin.GitManager"
OBJECT_PATH = "/com/robin/GitManager/Service"
INTERFACE = "com.robin.GitManager.Service"
VERSION = 1

ERROR_PREFIX = "com.robin.GitManager.Error."

INTROSPECTION = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Call">
      <arg type="s" name="operation" direction="in"/>
      <arg type="s" name="arguments" direction="in"/>
      <arg type="s" name="result" direction="out"/>
    </method>
    <!-- A caller that wants this instance kept alive while it is. -->
    <method name="Attach"/>
  </interface>
</node>
"""

DEFAULT_SYNC_MESSAGE = "Update from LaRenderer"


class ServiceError(Exception):
    """A failure worth telling the caller about, with a kind it can act on.

    Kinds: "auth" (the remote wants a credential it didn't get), "conflict"
    (a pull stopped half-way through a merge), "nothing" (nothing to commit),
    "norepo", "unknown" (no such operation), and "failed" for the rest.
    """

    def __init__(self, message, kind="failed"):
        super().__init__(message)
        self.kind = kind


# ------------------------------------------------------------ credentials --

# What git says, in the handful of ways it says it, when the remote wanted a
# username and password it didn't get. GIT_TERMINAL_PROMPT=0 turns the prompt
# into "could not read Username", which is the common one here.
_AUTH_SIGNS = (
    "authentication failed",
    "could not read username",
    "could not read password",
    "invalid username or password",
    "http basic: access denied",
    "the requested url returned error: 401",
    "the requested url returned error: 403",
)


def credential_env(token="", username=""):
    """The environment that hands git one credential, without writing it down.

    The credential goes in as a helper that git reaches through its
    environment config (GIT_CONFIG_COUNT, git 2.31+), which reads the token
    from an environment variable -- never the command line, which any process
    can read. Command-scope helpers are asked after the user's own, so one
    that already knows the answer still wins; and when this one's answer
    works, git hands it to the user's configured helpers to keep, which is
    how a token typed once is remembered by whatever the user already trusts
    with credentials (`credential.helper store` on this machine).
    """
    if not token:
        return None
    helper = ('!f() { test "$1" = get || return 0; '
              'printf "username=%s\\npassword=%s\\n" "$GITMANAGER_USER" "$GITMANAGER_TOKEN"; }; f')
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": helper,
        # Overleaf's git bridge, the case this was built for, takes any
        # username with a token as the password and documents "git".
        "GITMANAGER_USER": username or "git",
        "GITMANAGER_TOKEN": token,
    }


def _looks_like_auth(exc):
    text = f"{getattr(exc, 'stderr', '')}\n{exc}".lower()
    return any(sign in text for sign in _AUTH_SIGNS)


def _transport(fn, token="", username="", remote=""):
    """Run a network step, turning a credential failure into kind "auth"."""
    try:
        return fn(credential_env(token, username))
    except gitcmd.GitError as exc:
        if _looks_like_auth(exc):
            where = f" by {remote}" if remote else ""
            if token:
                raise ServiceError(f"The token was not accepted{where}.", kind="auth") from exc
            raise ServiceError(f"A sign-in is needed{where}.", kind="auth") from exc
        raise


# ------------------------------------------------------------------ locks --

# Two callers acting on one repository at once would fight over index.lock --
# a commit landing mid-pull. Each repository gets its own lock; different
# repositories still run side by side.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path):
    key = winenv.path_key(path)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _toplevel(path):
    """The top of the repository around `path`, which may name a file in it."""
    folder = path if os.path.isdir(path or "") else os.path.dirname(path or "")
    if not folder or not os.path.isdir(folder):
        raise ServiceError(f"{path or 'That'} is not a folder on this machine.", kind="norepo")
    try:
        return gitcmd.toplevel(folder)
    except (gitcmd.GitError, OSError) as exc:
        raise ServiceError(f"{folder} is not inside a git repository.", kind="norepo") from exc


def _remote_host(repo):
    """The host of origin, to name in a message ("git.overleaf.com")."""
    url = (gitcmd.remotes(repo) or {}).get("origin", "")
    if "://" in url:
        return url.split("://", 1)[1].split("/", 1)[0].split("@")[-1]
    return ""


def _head(repo):
    return gitcmd.git(repo, "rev-parse", "HEAD", check=False).strip()


# ------------------------------------------------------------- operations --


def op_version():
    return {"version": VERSION}


def op_status(path):
    """Branch, upstream distance and changed files for the repo around `path`.

    A folder outside any repository is an answer, not an error: {"repo": None}.
    """
    try:
        top = _toplevel(path)
    except ServiceError:
        return {"repo": None}
    st = gitcmd.status(top)
    return {
        "repo": top,
        "branch": st.branch,
        "detached": st.detached,
        "upstream": st.upstream,
        "ahead": st.ahead,
        "behind": st.behind,
        "state": st.state,
        "has_commits": bool(st.oid),
        "remotes": gitcmd.remotes(top),
        "files": [
            {
                "path": e.path,
                "label": e.label,
                "staged": e.staged,
                "unstaged": e.unstaged,
                "untracked": e.untracked,
                "unmerged": e.unmerged,
            }
            for e in st.entries
        ],
    }


def op_commit(path, message, stage_all=True):
    """Commit -- everything, by default, the way a "commit all" button means."""
    top = _toplevel(path)
    message = (message or "").strip()
    if not message:
        raise ServiceError("A commit needs a message.")
    with _lock_for(top):
        if stage_all:
            gitcmd.stage_all(top)
        if not gitcmd.status(top).staged_files:
            raise ServiceError("Nothing to commit: every change is already committed.",
                               kind="nothing")
        gitcmd.commit(top, message)
        return {"repo": top, "head": _head(top)}


def _conflicted(top):
    st = gitcmd.status(top)
    return [e.path for e in st.entries if e.unmerged], st.state


def _pull(top, token, username):
    before = _head(top)
    try:
        _transport(lambda env: gitcmd.pull(top, env=env), token, username, _remote_host(top))
    except gitcmd.GitError as exc:
        files, state = _conflicted(top)
        if files or state == "merge":
            names = ", ".join(files[:6]) + ("…" if len(files) > 6 else "")
            raise ServiceError(
                "The pull stopped on a conflict" + (f" in {names}" if names else "") +
                ". Both sides changed the same lines; the files now hold both versions "
                "between <<<<<<< and >>>>>>> markers.",
                kind="conflict",
            ) from exc
        raise
    return before != _head(top)


def _push(top, token, username):
    st = gitcmd.status(top)
    host = _remote_host(top)
    if st.upstream:
        _transport(lambda env: gitcmd.push(top, env=env), token, username, host)
        return True
    if not st.branch:
        raise ServiceError("HEAD is detached, so there is no branch to push.")
    if "origin" not in gitcmd.remotes(top):
        raise ServiceError(f"“{st.branch}” has nowhere to go: this repository has no origin.")
    _transport(lambda env: gitcmd.push(top, "origin", st.branch, set_upstream=True, env=env),
               token, username, host)
    return True


def op_pull(path, token="", username=""):
    top = _toplevel(path)
    with _lock_for(top):
        st = gitcmd.status(top)
        if not st.upstream:
            raise ServiceError(
                f"“{st.branch or 'HEAD'}” does not track a remote branch, so there is "
                "nothing to pull from.")
        changed = _pull(top, token, username)
        return {"repo": top, "changed": changed}


def op_push(path, token="", username=""):
    top = _toplevel(path)
    with _lock_for(top):
        if not gitcmd.has_commits(top):
            raise ServiceError("There are no commits to push yet.")
        _push(top, token, username)
        return {"repo": top}


def op_sync(path, message="", token="", username=""):
    """Commit everything, bring in the remote's changes, then send ours.

    The order matters. Pulling first would refuse over the uncommitted edits
    (or, worse, merge into them); pushing first would be rejected whenever
    the remote had moved. Commit, merge, push is the one order that always
    has a next step.
    """
    top = _toplevel(path)
    with _lock_for(top):
        committed = False
        gitcmd.stage_all(top)
        if gitcmd.status(top).staged_files:
            gitcmd.commit(top, (message or "").strip() or DEFAULT_SYNC_MESSAGE)
            committed = True
        pulled = _pull(top, token, username) if gitcmd.status(top).upstream else False
        # Pushing whenever we are ahead, not only when this call committed,
        # is what clears commits left behind by an earlier sync that failed.
        st = gitcmd.status(top)
        pushed = False
        nowhere = not st.upstream and "origin" not in gitcmd.remotes(top)
        if gitcmd.has_commits(top) and not nowhere and (st.ahead or not st.upstream):
            pushed = _push(top, token, username)
        return {"repo": top, "committed": committed, "pulled": pulled, "pushed": pushed,
                "no_remote": nowhere}


def op_clone(url, dest, token="", username=""):
    if not url:
        raise ServiceError("There is no address to clone from.")
    dest = os.path.abspath(os.path.expanduser(dest))
    if os.path.exists(dest) and (not os.path.isdir(dest) or os.listdir(dest)):
        raise ServiceError(f"{dest} already exists and is not empty.")
    host = url.split("://", 1)[1].split("/", 1)[0] if "://" in url else ""
    existed = os.path.isdir(dest)
    with _lock_for(dest):
        try:
            _transport(lambda env: gitcmd.clone(url, dest, env=env), token, username, host)
        except BaseException:
            # A clone that failed half-way leaves a directory behind, and the
            # next attempt then fails with "already exists". Take back only
            # what this call made: the folder if it made it, else its .git.
            import shutil

            if not existed:
                shutil.rmtree(dest, ignore_errors=True)
            else:
                shutil.rmtree(os.path.join(dest, ".git"), ignore_errors=True)
            raise
    return {"repo": dest}


def op_init(path):
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if gitcmd.is_repo(folder):
        raise ServiceError(f"{folder} is already inside a git repository.")
    if gitcmd.in_git_dir(folder):
        raise ServiceError("That is inside a .git directory.")
    gitcmd.init(folder)
    return {"repo": gitcmd.toplevel(folder)}


OPERATIONS = {
    "version": op_version,
    "status": op_status,
    "commit": op_commit,
    "pull": op_pull,
    "push": op_push,
    "sync": op_sync,
    "clone": op_clone,
    "init": op_init,
}

# Operations that need the window rather than git. The host answers these on
# the main loop; a caller running in-process starts a Git Manager instead.
UI_OPERATIONS = ("show",)


def run(operation, arguments=None):
    """Run one operation. Returns a JSON-able dict or raises ServiceError."""
    fn = OPERATIONS.get(operation)
    if fn is None:
        raise ServiceError(f"Git Manager has no operation called {operation!r}.", kind="unknown")
    arguments = dict(arguments or {})
    try:
        inspect.signature(fn).bind(**arguments)
    except TypeError as exc:
        raise ServiceError(f"{operation}: {exc}") from exc
    try:
        return fn(**arguments)
    except gitcmd.GitError as exc:
        raise ServiceError(_first_lines(str(exc))) from exc


def _first_lines(text, keep=4):
    """git's own words, minus the hint: paragraphs nobody asked for."""
    lines = [line for line in text.splitlines()
             if line.strip() and not line.startswith("hint:")]
    return "\n".join(lines[:keep]) or text


# -------------------------------------------------------------- the bus ----


class Host:
    """Serves `run()` on the session bus for the application that owns it.

    `on_idle` is called when the last caller that attached goes away -- the
    app uses it to let a background instance exit once LaRenderer has.
    `on_changed(repo)` runs after an operation that may have moved a
    repository, so an open window can refresh it. `on_show(path)` answers
    the one operation that needs a window.
    """

    def __init__(self, connection, on_show=None, on_changed=None, on_idle=None):
        from gi.repository import Gio

        self.connection = connection
        self.on_show = on_show
        self.on_changed = on_changed
        self.on_idle = on_idle
        self._clients: dict[str, int] = {}
        # Operations still running. The last caller leaving does not end a
        # background instance while one is: a push cut off half-way is worse
        # than a process that lives a few seconds longer.
        self._running = 0

        info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION).interfaces[0]
        register = getattr(connection, "register_object_with_closures2", None)
        if register is None:          # GLib before 2.84
            register = connection.register_object
        self._registration = register(OBJECT_PATH, info, self._on_call, None, None)

    @property
    def attached(self) -> int:
        return len(self._clients)

    def close(self):
        from gi.repository import Gio

        for watch in self._clients.values():
            Gio.bus_unwatch_name(watch)
        self._clients.clear()
        if self._registration:
            self.connection.unregister_object(self._registration)
            self._registration = 0

    # -- callers ------------------------------------------------------------

    def _attach(self, sender):
        from gi.repository import Gio

        if not sender or sender in self._clients:
            return
        self._clients[sender] = Gio.bus_watch_name_on_connection(
            self.connection, sender, Gio.BusNameWatcherFlags.NONE, None, self._detached)

    def _detached(self, _connection, name):
        from gi.repository import Gio

        watch = self._clients.pop(name, 0)
        if watch:
            Gio.bus_unwatch_name(watch)
        self._maybe_idle()

    def _maybe_idle(self):
        if not self._clients and not self._running and self.on_idle:
            self.on_idle()

    # -- calls --------------------------------------------------------------

    def _on_call(self, _connection, sender, _path, _interface, method, parameters, invocation):
        from gi.repository import GLib

        from . import jobs

        if method == "Attach":
            self._attach(sender)
            invocation.return_value(None)
            return
        if method != "Call":
            invocation.return_dbus_error(ERROR_PREFIX + "Unknown", f"No method {method}")
            return

        operation, raw = parameters.unpack()
        try:
            arguments = json.loads(raw or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as exc:
            invocation.return_dbus_error(ERROR_PREFIX + "Failed", f"Bad arguments: {exc}")
            return

        def reply(result):
            invocation.return_value(GLib.Variant("(s)", (json.dumps(result),)))

        def fail(exc):
            kind = getattr(exc, "kind", "failed")
            invocation.return_dbus_error(ERROR_PREFIX + kind.capitalize(), str(exc) or kind)

        if operation in UI_OPERATIONS:
            try:
                if self.on_show is None:
                    raise ServiceError("This Git Manager cannot show a window.")
                self.on_show(arguments.get("path") or "")
                reply({})
            except Exception as exc:  # noqa: BLE001 - goes back to the caller
                fail(exc)
            return

        def done(result):
            self._running -= 1
            reply(result)
            if self.on_changed and operation != "status" and isinstance(result, dict):
                repo = result.get("repo")
                if repo:
                    self.on_changed(repo)
            self._maybe_idle()

        def failed(exc):
            self._running -= 1
            fail(exc)
            # A failed pull is rarely a no-op -- it can leave a merge half done.
            repo = arguments.get("path")
            if self.on_changed and repo and operation in ("pull", "sync"):
                self.on_changed(repo)
            self._maybe_idle()

        self._running += 1
        jobs.run(lambda: run(operation, arguments), done, failed)
