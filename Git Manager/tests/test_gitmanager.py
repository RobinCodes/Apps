"""What can be checked without a network and without touching a real repository.

Three parts, as the layout intends. The plumbing in gitcmd talks to a real git
— there is no value in asserting against a mock of the porcelain format when
the real thing is a subprocess away — so this builds throwaway repositories in
a temp directory and reads them back. The pure logic (paths, config, scanning,
ignore rules) needs neither git nor a display. The widgets need a display but
no repository.

    python3 tests/test_gitmanager.py

Everything it makes lives under one temp directory and is removed at the end.
Nothing here fetches, pushes, or asks GitHub anything, so it is safe to run
offline and costs nothing.
"""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

scratch = tempfile.mkdtemp(prefix="git-manager-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(scratch, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(scratch, "cache")
# An app built by a test must not go to the network for an update.
os.environ["APPS_NO_UPDATE"] = "1"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gitmanager import gitcmd, ignore, scanner, service, widgets, winenv  # noqa: E402

# A subject and a filename that a locale-encoded pipe would ruin: the em dash
# means something else in cp1252, and none of the rest exists there at all.
# See the encoding note in gitcmd.git for why this is the regression that
# matters most on Windows.
UNICODE_SUBJECT = "Fix the — dash, add 你好 and \U0001F600"
UNICODE_NAME = "café-привет.txt"

_made = 0


def check(name, fn):
    fn()
    print(f"  ok  {name}")


def write(repo, rel, text="hello\n"):
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def new_repo(committed=True):
    """A repository with an identity of its own, so a test run never depends
    on the user's name, email, or signing key — and never trips over them."""
    global _made
    _made += 1
    path = os.path.join(scratch, "repo%d" % _made)
    os.makedirs(path)
    gitcmd.init(path, initial_branch="main")
    for key, value in (
        ("user.name", "Test"),
        ("user.email", "test@example.com"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "false"),
    ):
        gitcmd.git(path, "config", key, value)
    if committed:
        write(path, "a.txt", "one\n")
        gitcmd.stage_all(path)
        gitcmd.commit(path, "first")
    return path


# -------------------------------------------------------------- plumbing ----

def test_status_clean():
    repo = new_repo()
    st = gitcmd.status(repo)
    assert st.branch == "main", st.branch
    assert not st.dirty and st.entries == []
    assert st.detached is False and st.state is None


def test_status_three_kinds():
    repo = new_repo()
    write(repo, "a.txt", "changed\n")     # tracked and modified
    write(repo, "staged.txt", "new\n")
    gitcmd.stage(repo, ["staged.txt"])    # added to the index
    write(repo, "loose.txt", "loose\n")   # never added
    st = gitcmd.status(repo)
    by_path = {e.path: e for e in st.entries}
    assert set(by_path) == {"a.txt", "staged.txt", "loose.txt"}, sorted(by_path)
    assert by_path["a.txt"].unstaged and not by_path["a.txt"].staged
    assert by_path["staged.txt"].staged
    assert by_path["loose.txt"].untracked and by_path["loose.txt"].label == "?"
    assert [e.path for e in st.staged_files] == ["staged.txt"]
    assert st.dirty


def test_rename_pairs_with_its_source():
    """A rename record carries its old path in the *next* NUL record, which is
    the one place the -z walk cannot be a plain for-loop."""
    repo = new_repo()
    os.replace(os.path.join(repo, "a.txt"), os.path.join(repo, "b.txt"))
    gitcmd.stage_all(repo)
    st = gitcmd.status(repo)
    assert len(st.entries) == 1, st.entries  # the source record was consumed
    entry = st.entries[0]
    assert entry.path == "b.txt" and entry.orig_path == "a.txt", entry
    assert entry.display == "a.txt → b.txt"


def test_conflict_is_unmerged_and_seen():
    repo = new_repo()
    gitcmd.create_branch(repo, "other", start="main", switch=True)
    write(repo, "a.txt", "theirs\n")
    gitcmd.stage_all(repo)
    gitcmd.commit(repo, "theirs")
    gitcmd.checkout(repo, "main")
    write(repo, "a.txt", "ours\n")
    gitcmd.stage_all(repo)
    gitcmd.commit(repo, "ours")
    try:
        gitcmd.merge(repo, "other")
    except gitcmd.GitError:
        pass  # the conflict is the point
    st = gitcmd.status(repo)
    conflicted = [e for e in st.entries if e.unmerged]
    assert conflicted and conflicted[0].path == "a.txt", st.entries
    assert conflicted[0].label == "U"
    # The in-progress state is what puts the banner on the window.
    assert st.state == "merge", st.state
    gitcmd.abort_state(repo, "merge")
    assert gitcmd.status(repo).state is None


def test_unicode_survives_the_pipe():
    """The regression the encoding fix exists for. Under the locale encoding
    this either mangles the subject or refuses to send it at all."""
    repo = new_repo()
    write(repo, UNICODE_NAME, "body\n")
    gitcmd.stage_all(repo)
    gitcmd.commit(repo, UNICODE_SUBJECT)

    head = gitcmd.log(repo, limit=1)[0]
    assert head.subject == UNICODE_SUBJECT, repr(head.subject)
    touched = [path for _code, path in gitcmd.commit_files(repo, head.sha)]
    assert UNICODE_NAME in touched, touched
    # And the same name comes back out of a fresh status.
    write(repo, UNICODE_NAME, "changed\n")
    assert gitcmd.status(repo).entries[0].path == UNICODE_NAME


def test_a_hunk_of_a_unicode_file_can_be_staged():
    """The other direction. Hunk staging hands git the patch text on stdin,
    and a patch of a file with an emoji in it has no cp1252 encoding at all —
    so this used to raise rather than stage anything."""
    repo = new_repo()
    write(repo, "a.txt", "one\nemoji \U0001F600 line\n")
    header, hunks = gitcmd.split_hunks(gitcmd.diff_file(repo, "a.txt"))
    assert hunks and "\U0001F600" in header + hunks[0]
    gitcmd.apply_patch(repo, header + hunks[0], cached=True)
    assert [e.path for e in gitcmd.status(repo).staged_files] == ["a.txt"]


def test_log_carries_parents_and_body():
    repo = new_repo()
    write(repo, "a.txt", "two\n")
    gitcmd.stage_all(repo)
    gitcmd.commit(repo, "second\n\nA body with\ntwo lines.")
    commits = gitcmd.log(repo, limit=10)
    assert [c.subject for c in commits] == ["second", "first"], commits
    assert "two lines." in commits[0].body
    assert commits[0].parents == [commits[1].sha]
    assert commits[1].parents == [] and not commits[1].is_merge
    assert commits[0].sha.startswith(commits[0].short)


def test_empty_repo_has_no_log():
    repo = new_repo(committed=False)
    assert gitcmd.log(repo) == []
    assert gitcmd.has_commits(repo) is False


def test_branches_know_which_is_current():
    repo = new_repo()
    gitcmd.create_branch(repo, "feature", switch=False)
    found = {b.name: b for b in gitcmd.branches(repo)}
    assert set(found) == {"main", "feature"}, sorted(found)
    assert found["main"].current and not found["feature"].current
    assert all(not b.remote for b in found.values())
    assert found["main"].subject == "first"


def test_diff_splits_into_hunks():
    repo = new_repo()
    write(repo, "a.txt", "one\ntwo\nthree\n")
    diff = gitcmd.diff_file(repo, "a.txt")
    assert "+two" in diff and "@@" in diff
    header, hunks = gitcmd.split_hunks(diff)
    assert len(hunks) == 1 and hunks[0].startswith("@@"), hunks
    assert "@@" not in header, header
    # A file with no index side is diffed against the null device instead.
    write(repo, "fresh.txt", "brand new\n")
    assert "brand new" in gitcmd.diff_file(repo, "fresh.txt", untracked=True)


def test_stage_unstage_discard_undo_each_other():
    repo = new_repo()
    write(repo, "a.txt", "dirty\n")
    gitcmd.stage(repo, ["a.txt"])
    assert gitcmd.status(repo).staged_files
    gitcmd.unstage(repo, ["a.txt"])
    assert not gitcmd.status(repo).staged_files
    gitcmd.discard(repo, gitcmd.status(repo).entries)
    assert not gitcmd.status(repo).dirty, "discard should restore the file"


def test_stash_goes_away_and_comes_back():
    repo = new_repo()
    write(repo, "a.txt", "work in progress\n")
    gitcmd.stash_push(repo, message="wip")
    assert not gitcmd.status(repo).dirty
    entries = gitcmd.stash_list(repo)
    assert entries and "wip" in entries[0].subject, entries
    gitcmd.stash_apply(repo, entries[0].ref, pop=True)
    assert gitcmd.status(repo).dirty
    assert gitcmd.stash_list(repo) == []


def test_repo_predicates():
    repo = new_repo()
    assert gitcmd.is_repo(repo)
    assert not gitcmd.is_repo(scratch)
    assert gitcmd.in_git_dir(os.path.join(repo, ".git"))
    assert not gitcmd.in_git_dir(repo)
    # toplevel comes back spelled the way the rest of the app spells paths.
    assert winenv.same_path(gitcmd.toplevel(repo), repo), gitcmd.toplevel(repo)


def test_a_failed_command_carries_a_message():
    """The failures that never reach an exit code used to surface as OS-speak,
    or not at all."""
    repo = new_repo()
    try:
        gitcmd.git(repo, "checkout", "no-such-branch")
    except gitcmd.GitError as exc:
        assert str(exc).strip(), "a GitError must carry something readable"
        assert exc.returncode != 0
    else:
        raise AssertionError("checking out a missing branch should raise")


# ----------------------------------------------------- paths and settings ----

def test_paths_compare_by_meaning_not_spelling():
    base = os.path.join(scratch, "Repo")
    assert winenv.same_path(base, base + os.sep)
    assert winenv.same_path(base, base.replace(os.sep, "/"))
    assert not winenv.same_path(base, os.path.join(scratch, "Other"))
    assert not winenv.same_path("", base) and not winenv.same_path(base, "")
    # Case folds only where the filesystem folds it.
    folds = os.path.normcase("A") == "a"
    assert winenv.same_path(base, base.lower()) == folds
    assert winenv.canonical("") == ""


def test_config_settles_spelling_and_dedupes():
    from gitmanager import config as cfgmod
    fresh = cfgmod.Config()
    fresh["roots"] = [scratch, scratch.replace(os.sep, "/"), scratch + os.sep]
    assert fresh.save(), fresh.last_error
    again = cfgmod.Config()
    assert len(again["roots"]) == 1, again["roots"]
    assert winenv.same_path(again["roots"][0], scratch)


def test_config_reports_a_bad_file():
    from gitmanager import config as cfgmod
    os.makedirs(cfgmod.DIR, exist_ok=True)
    with open(cfgmod.PATH, "w", encoding="utf-8") as fh:
        fh.write("{ not json at all")
    broken = cfgmod.Config()
    assert broken.last_error and "config.json" in broken.last_error
    assert broken["max_depth"] == cfgmod.DEFAULTS["max_depth"], "defaults still apply"
    os.remove(cfgmod.PATH)


def test_config_ignores_keys_and_types_it_does_not_know():
    import json
    from gitmanager import config as cfgmod
    os.makedirs(cfgmod.DIR, exist_ok=True)
    with open(cfgmod.PATH, "w", encoding="utf-8") as fh:
        json.dump({"max_depth": "not a number", "nonsense": 1, "diff_context": 7}, fh)
    loaded = cfgmod.Config()
    assert loaded["max_depth"] == cfgmod.DEFAULTS["max_depth"], "wrong type refused"
    assert "nonsense" not in loaded
    assert loaded["diff_context"] == 7, "a good value is still taken"
    os.remove(cfgmod.PATH)


def test_scan_finds_repos_and_stops_at_the_top():
    root = os.path.join(scratch, "scanroot")
    outer = os.path.join(root, "outer")
    os.makedirs(outer, exist_ok=True)
    gitcmd.init(outer, initial_branch="main")
    # A repo nested inside another is unreachable by scanning: the walk stops
    # at the first repository and never descends into a working tree.
    inner = os.path.join(outer, "inner")
    os.makedirs(inner, exist_ok=True)
    gitcmd.init(inner, initial_branch="main")
    paths = [r.path for r in scanner.scan([root], max_depth=6)]
    assert any(winenv.same_path(p, outer) for p in paths), paths
    assert not any(winenv.same_path(p, inner) for p in paths), "must not descend"


def test_scan_cache_round_trip():
    repos = scanner.scan([os.path.join(scratch, "scanroot")], max_depth=6)
    scanner.save_cache(repos)
    back = scanner.load_cache()
    assert [r.path for r in back] == [r.path for r in repos], back
    assert all(r.name for r in back)


# ------------------------------------------------------------ ignore rules ----

def test_pattern_for_anchors_and_escapes():
    assert ignore.pattern_for("build", True) == "/build/"
    assert ignore.pattern_for("src/main.py", False) == "/src/main.py"
    # A leading # or ! in a real filename must not read as a comment or a
    # negation. The anchoring slash is what prevents that -- the line no longer
    # begins with either character -- so neither of them needs escaping.
    assert ignore.pattern_for("#notes.txt", False) == "/#notes.txt"
    assert ignore.pattern_for("!bang.txt", False) == "/!bang.txt"
    # Glob metacharacters do have to be escaped, to match themselves.
    assert ignore.pattern_for("a[1].txt", False) == "/a\\[1\\].txt"
    # git strips a trailing space unless it is escaped.
    assert ignore.pattern_for("odd ", False).endswith("\\ ")


def test_suspicious_lines_only_flags_the_unambiguous():
    problems = ignore.suspicious_lines('"quoted.txt"\ntrailing \n#comment\nfine.txt\n')
    numbers = [n for n, _line, _why in problems]
    assert numbers == [1, 2], problems
    assert ignore.suspicious_lines("*.log\n!keep.log\nbuild/\n") == []


def test_gitignore_round_trip_keeps_unicode():
    repo = new_repo()
    ignore.write_gitignore(repo, "*.log\n" + UNICODE_NAME)
    back = ignore.read_gitignore(repo)
    assert UNICODE_NAME in back, repr(back)
    assert back.endswith("\n"), "a trailing newline is added"
    assert ignore.read_gitignore(scratch) == "", "a missing file reads as empty"


# ----------------------------------------------------------------- widgets ----

def test_describe_error_leads_with_the_first_line():
    head, detail = widgets.describe_error(
        gitcmd.GitError(["push"], 1, "fatal: no upstream\nhint: try --set-upstream"))
    assert head.startswith("no upstream"), head          # the noise word is dropped
    assert "+1 more" in head, head
    assert "$ git push" in detail and "exit 1" in detail
    plain, _ = widgets.describe_error(ValueError("bad value"), context="Saving")
    assert plain == "Saving: bad value", plain


def test_widgets_build_without_a_repository():
    from gi.repository import Gtk
    assert isinstance(widgets.pill("main"), Gtk.Widget)
    assert isinstance(widgets.status_page("folder", "None", "Nothing here"), Gtk.Widget)
    assert isinstance(widgets.diff_text_view("@@ -1 +1 @@\n-a\n+b\n"), Gtk.Widget)
    assert isinstance(widgets.DiffView(), Gtk.Widget)


# --------------------------------------------------------------- service ----
#
# What LaRenderer asks for over the bus. The bus itself is GLib's; what is
# worth testing is that the operations do what their callers are told they
# do, against a "remote" that is only a bare repository on disk.

def _identify(repo):
    for key, value in (("user.name", "Test"), ("user.email", "test@example.com"),
                       ("commit.gpgsign", "false")):
        gitcmd.git(repo, "config", key, value)


def _remote_and_two_clones():
    seed = new_repo()
    bare = os.path.join(scratch, "remote%d.git" % _made)
    gitcmd.git(scratch, "init", "--bare", "--initial-branch", "main", bare)
    gitcmd.add_remote(seed, "origin", bare)
    gitcmd.push(seed, "origin", "main", set_upstream=True)
    clones = []
    for name in ("mine", "theirs"):
        dest = os.path.join(scratch, "%s%d" % (name, _made))
        assert service.run("clone", {"url": bare, "dest": dest}) == {"repo": dest}
        _identify(dest)
        clones.append(dest)
    return clones


def test_service_status_reads_a_repository():
    repo = new_repo()
    write(repo, "a.txt", "changed\n")
    st = service.run("status", {"path": os.path.join(repo, "a.txt")})
    assert st["repo"] == gitcmd.toplevel(repo) and st["branch"] == "main"
    assert [(f["path"], f["label"]) for f in st["files"]] == [("a.txt", "M")], st["files"]
    # Outside any repository is an answer, not an error.
    outside = os.path.join(scratch, "not-a-repo")
    os.makedirs(outside, exist_ok=True)
    assert service.run("status", {"path": outside}) == {"repo": None}


def test_service_commit_takes_everything_or_says_why_not():
    repo = new_repo()
    write(repo, "b.txt", "new\n")
    write(repo, "a.txt", "changed\n")
    service.run("commit", {"path": repo, "message": "both"})
    assert not gitcmd.status(repo).dirty
    for arguments, kind in (({"path": repo, "message": "again"}, "nothing"),
                            ({"path": repo, "message": "  "}, "failed")):
        try:
            service.run("commit", arguments)
        except service.ServiceError as exc:
            assert exc.kind == kind, (exc.kind, str(exc))
        else:
            raise AssertionError(f"committed {arguments}")


def test_service_sync_commits_pulls_and_pushes():
    mine, theirs = _remote_and_two_clones()
    write(theirs, "theirs.txt", "from them\n")
    assert service.run("sync", {"path": theirs})["pushed"]
    write(mine, "mine.txt", "from me\n")
    result = service.run("sync", {"path": mine, "message": "mine"})
    assert result["committed"] and result["pulled"] and result["pushed"], result
    assert os.path.exists(os.path.join(mine, "theirs.txt"))
    assert service.run("pull", {"path": theirs})["changed"]
    assert os.path.exists(os.path.join(theirs, "mine.txt"))
    # In step already: nothing to do, and it says so rather than failing.
    again = service.run("sync", {"path": mine})
    assert not (again["committed"] or again["pushed"]), again


def test_service_reports_a_conflict_as_one():
    mine, theirs = _remote_and_two_clones()
    write(theirs, "a.txt", "their line\n")
    service.run("sync", {"path": theirs})
    write(mine, "a.txt", "my line\n")
    try:
        service.run("sync", {"path": mine})
    except service.ServiceError as exc:
        assert exc.kind == "conflict" and "a.txt" in str(exc), (exc.kind, str(exc))
    else:
        raise AssertionError("a conflicting sync went through")
    assert gitcmd.status(mine).state == "merge"


def test_service_refuses_what_it_does_not_know():
    for operation, arguments in (("nonsense", {}), ("status", {"bogus": 1})):
        try:
            service.run(operation, arguments)
        except service.ServiceError as exc:
            assert exc.kind in ("unknown", "failed"), exc.kind
        else:
            raise AssertionError(operation)
    # A clone that fails leaves nothing behind for the retry to trip over.
    dest = os.path.join(scratch, "failed-clone")
    try:
        service.run("clone", {"url": os.path.join(scratch, "no-such.git"), "dest": dest})
    except service.ServiceError:
        pass
    assert not os.path.exists(dest)


def test_a_credential_goes_through_the_environment():
    assert service.credential_env("") is None
    env = service.credential_env("secret-token")
    assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
    # The token itself is only ever in the environment, never in the helper
    # command line that `ps` can show.
    assert "secret-token" not in env["GIT_CONFIG_VALUE_0"]
    assert env["GITMANAGER_TOKEN"] == "secret-token" and env["GITMANAGER_USER"] == "git"
    if not winenv.WINDOWS:
        import subprocess
        out = subprocess.run(
            [gitcmd.GIT, "credential", "fill"], input="protocol=https\nhost=example.invalid\n\n",
            capture_output=True, text=True, env={**os.environ, **env,
                                                 "GIT_CONFIG_NOSYSTEM": "1",
                                                 "HOME": scratch, "XDG_CONFIG_HOME": scratch},
        ).stdout
        assert "username=git" in out and "password=secret-token" in out, out


# ----------------------------------------------------------------- windows --

_TEST_APP = None


def _test_app():
    """A GitManagerApp of its own, registered but never run.

    Registering emits ::startup, which is what installs the actions and lets
    the application hold windows. The id and flags are changed first so this
    never reaches the real Git Manager that may be running on this desktop,
    and it is built once, because a second application exporting the same
    object path is an error.
    """
    global _TEST_APP
    if _TEST_APP is None:
        from gi.repository import Gio

        from gitmanager.app import GitManagerApp

        app = GitManagerApp()
        app.set_application_id("com.robin.GitManagerTests")
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE
                      | Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        app.register(None)
        _TEST_APP = app
    return _TEST_APP


def test_the_new_window_flag_never_reaches_the_paths():
    from gitmanager.app import split_new_window_flag

    for argv, expected in (
        (["-n", "/tmp"], (True, ["/tmp"])),
        (["--new-window", "/tmp"], (True, ["/tmp"])),
        (["--new-window"], (True, [])),
        (["/tmp"], (False, ["/tmp"])),
        ([], (False, [])),
        # --background is not a window flag and must survive untouched, or the
        # service mode LaRenderer depends on would open a window instead.
        (["--background"], (False, ["--background"])),
    ):
        got = split_new_window_flag(list(argv))
        assert got == expected, f"{argv} gave {got}, wanted {expected}"


def test_two_windows_open_at_once_and_share_nothing():
    from gitmanager.window import MainWindow

    app = _test_app()
    before = len([w for w in app.get_windows() if isinstance(w, MainWindow)])
    first = app.new_window()
    second = app.new_window()

    mains = [w for w in app.get_windows() if isinstance(w, MainWindow)]
    assert len(mains) == before + 2, f"expected two more windows, got {len(mains)}"
    assert first is not second
    assert first.cfg is not second.cfg
    # Each window runs its own git commands in its own order.
    assert first.queue is not second.queue
    # Found by existence, not by focus: nothing here has ever been focused.
    assert app.front_window() in mains

    for window in (first, second):
        window.destroy()


def test_the_unhandled_error_handler_belongs_to_the_application():
    """It has one slot, so a window must not be allowed to own it.

    A window that claimed it would keep it after being closed, and every
    later background failure would be reported on a window that is gone.
    """
    from gitmanager import jobs
    from gitmanager.window import MainWindow

    app = _test_app()
    assert jobs.on_unhandled is not None, "nothing would report a worker failure"
    claimed = jobs.on_unhandled

    window = app.new_window()
    assert jobs.on_unhandled is claimed, "a window took the application's handler"
    window.destroy()
    assert jobs.on_unhandled is claimed, "a closed window left the handler dangling"
    assert isinstance(window, MainWindow)


def native_icons(window):
    """The big and the small icon Windows holds for `window`; 0 for none."""
    import ctypes

    user32 = ctypes.WinDLL("user32")
    user32.SendMessageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                    ctypes.c_void_p)
    hwnd = winenv.window_handle(window)
    return [user32.SendMessageW(hwnd, 0x007F, which, None) or 0   # WM_GETICON
            for which in (1, 0)]                                  # ICON_BIG, ICON_SMALL


def test_on_windows_a_window_wears_the_apps_icon_not_pythons():
    """GTK 4 sets no icon on a window there, so each one wore pythonw.exe's:
    the Python logo, in Alt+Tab and on the taskbar."""
    if not winenv.WINDOWS:
        return
    window = _test_app().new_window()
    window.realize()
    try:
        big, small = native_icons(window)
        assert big and small, "the window has no icon of its own"
    finally:
        window.destroy()


def main():
    print("plumbing")
    check("a fresh repository reads back clean", test_status_clean)
    check("staged, unstaged and untracked are told apart", test_status_three_kinds)
    check("a rename pairs with its source path", test_rename_pairs_with_its_source)
    check("a conflict is unmerged, and the merge state is seen", test_conflict_is_unmerged_and_seen)
    check("a unicode subject and filename survive the pipe", test_unicode_survives_the_pipe)
    check("a hunk of a unicode file can be staged", test_a_hunk_of_a_unicode_file_can_be_staged)
    check("the log carries parents and a multi-line body", test_log_carries_parents_and_body)
    check("an empty repository has no log", test_empty_repo_has_no_log)
    check("branches know which one is current", test_branches_know_which_is_current)
    check("a diff splits into hunks, tracked or not", test_diff_splits_into_hunks)
    check("stage, unstage and discard undo each other", test_stage_unstage_discard_undo_each_other)
    check("a stash goes away and comes back", test_stash_goes_away_and_comes_back)
    check("the repository predicates answer for real paths", test_repo_predicates)
    check("a failed command carries a readable message", test_a_failed_command_carries_a_message)

    print("the service LaRenderer uses")
    check("status reads a repository, or says there is none",
          test_service_status_reads_a_repository)
    check("commit takes everything, or says why not",
          test_service_commit_takes_everything_or_says_why_not)
    check("sync commits, pulls and pushes", test_service_sync_commits_pulls_and_pushes)
    check("a conflict is reported as a conflict", test_service_reports_a_conflict_as_one)
    check("unknown operations and failed clones are handled",
          test_service_refuses_what_it_does_not_know)
    check("a credential goes through the environment",
          test_a_credential_goes_through_the_environment)

    print("paths and settings")
    check("paths compare by meaning, not spelling", test_paths_compare_by_meaning_not_spelling)
    check("the config settles spelling and dedupes", test_config_settles_spelling_and_dedupes)
    check("a broken config is reported, not silently forgotten", test_config_reports_a_bad_file)
    check("unknown keys and wrong types are refused", test_config_ignores_keys_and_types_it_does_not_know)
    check("the scan finds repos and stops at the top of one", test_scan_finds_repos_and_stops_at_the_top)
    check("the scan cache round-trips", test_scan_cache_round_trip)

    print("ignore rules")
    check("a pattern is anchored and escaped", test_pattern_for_anchors_and_escapes)
    check("only unambiguous mistakes are flagged", test_suspicious_lines_only_flags_the_unambiguous)
    check("a .gitignore round-trips, unicode and all", test_gitignore_round_trip_keeps_unicode)

    print("widgets")
    from gi.repository import Adw
    Adw.init()
    widgets.install_css()
    check("an error leads with its first line", test_describe_error_leads_with_the_first_line)
    check("widgets build without a repository", test_widgets_build_without_a_repository)

    print("windows")
    check("the new-window flag never reaches the paths",
          test_the_new_window_flag_never_reaches_the_paths)
    check("two windows open at once and share nothing",
          test_two_windows_open_at_once_and_share_nothing)
    check("the unhandled-error handler belongs to the application",
          test_the_unhandled_error_handler_belongs_to_the_application)
    check("on Windows, a window wears the app's icon, not Python's",
          test_on_windows_a_window_wears_the_apps_icon_not_pythons)

    print("all good")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
