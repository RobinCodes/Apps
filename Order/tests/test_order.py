#!/usr/bin/env python3
"""The Order's suite: the manifest, the three things done to a repository, and the drive.

Every test builds its own world in a scratch folder — a home, a brain with a
manifest, bare repositories standing in for GitHub — and points the Order at it
with ORDER_HOME, so nothing on the machine running the suite is read, cloned
or moved, and no network is used.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from order import cli, drive, gitrepo, manifest  # noqa: E402

SCRATCH = Path(tempfile.mkdtemp(prefix="order-test-"))


def rmtree(path) -> None:
    """shutil.rmtree that also removes git's read-only object files on Windows."""
    def writable(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onexc=writable)


def git(*args, cwd=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def bare_with(name: str, files: dict[str, str]) -> str:
    """A bare repository with one commit of `files`; returns its URL."""
    bare = SCRATCH / "remotes" / f"{name}.git"
    work = SCRATCH / "seed" / name
    git("init", "-q", "--bare", "-b", "main", str(bare))
    git("init", "-q", "-b", "main", str(work))
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text, encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "first", cwd=work)
    git("remote", "add", "origin", bare.as_uri(), cwd=work)
    git("push", "-q", "origin", "main", cwd=work)
    return bare.as_uri()


def push_change(name: str, rel: str, text: str) -> None:
    work = SCRATCH / "seed" / name
    (work / rel).write_text(text, encoding="utf-8")
    git("commit", "-qam", "more", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)


def world(name: str, repos: list[dict], places=("SecondBrain/inbox",)) -> Path:
    home = SCRATCH / name
    os.environ["ORDER_HOME"] = str(home)
    os.environ.pop("ANAM_HOME", None)
    os.environ["ORDER_MACHINE"] = "test-box"
    (home / "SecondBrain" / "config").mkdir(parents=True, exist_ok=True)
    import yaml
    (home / "SecondBrain" / "config" / "order.yaml").write_text(
        yaml.safe_dump({"version": 1, "repositories": repos, "places": list(places),
                        "refresh": {"every_hours": 100000}}), encoding="utf-8")
    return home


def quiet(fn, *args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = fn(*args)
    return code, out.getvalue()


class Args:
    def __init__(self, **kw):
        self.__dict__.update({"quiet": False, "fetch": False, "verbose": True, "paths": []}, **kw)


# ---------------------------------------------------------------- manifest --

def test_the_manifest_is_read_strictly():
    m = manifest.parse({"repositories": [
        {"path": "Projects\\A", "url": "u1"},
        {"path": "B", "url": "u2", "sync": "push", "machines": "arch", "optional": True}],
        "places": ["SecondBrain/inbox"]})
    assert [r.path for r in m.repositories] == ["Projects/A", "B"]
    assert m.repositories[1].machines == ("arch",) and m.repositories[1].optional
    os.environ["ORDER_MACHINE"] = "laptop"
    assert [r.path for r in m.for_this_machine()] == ["Projects/A"]
    for bad in ({"repositories": [{"path": "x"}]},
                {"repositories": [{"path": "x", "url": "u"}, {"path": "x", "url": "v"}]},
                {"repositories": [{"path": "x", "url": "u", "sync": "sometimes"}]}):
        try:
            manifest.parse(bad)
        except manifest.ManifestError:
            continue
        raise AssertionError(f"accepted {bad}")


def test_the_real_manifest_parses():
    real = Path.home() / "SecondBrain" / "config" / "order.yaml"
    if not real.is_file():
        print("        (no real manifest on this machine; skipped)")
        return
    m = manifest.load(real)
    assert any(r.sync == "push" for r in m.repositories), "the brain should push"


def test_remotes_compare_by_meaning():
    assert gitrepo.same_remote("https://github.com/A/B.git", "https://github.com/a/b")
    assert gitrepo.same_remote("git@github.com:A/B.git", "https://github.com/A/B/")
    assert not gitrepo.same_remote("https://github.com/A/B", "https://github.com/A/C")


# ---------------------------------------------------------------- sync -----

def test_sync_clones_then_fast_forwards_and_never_touches_local_work():
    url = bare_with("alpha", {"README.md": "one\n"})
    gone = (SCRATCH / "remotes" / "missing.git").as_uri()
    home = world("sync", [{"path": "Projects/Alpha", "url": url},
                          {"path": "Projects/Optional", "url": gone, "optional": True}])
    code, out = quiet(cli.cmd_sync, Args())
    assert code == 0, out
    alpha = home / "Projects" / "Alpha"
    assert (alpha / "README.md").read_text() == "one\n"
    assert (home / "SecondBrain" / "inbox").is_dir()
    assert "Optional" not in out, "an optional repository fails quietly"

    push_change("alpha", "README.md", "two\n")
    code, out = quiet(cli.cmd_sync, Args())
    assert (alpha / "README.md").read_text() == "two\n", out

    push_change("alpha", "README.md", "three\n")
    (alpha / "notes.txt").write_text("mine", encoding="utf-8")
    code, out = quiet(cli.cmd_sync, Args())
    assert (alpha / "README.md").read_text() == "two\n", "local work must stop the update"
    assert (alpha / "notes.txt").read_text() == "mine"
    assert "local work" in out


def test_doctor_names_what_is_out_of_order():
    url = bare_with("beta", {"a.txt": "a"})
    home = world("doctor", [{"path": "Projects/Beta", "url": url},
                            {"path": "Projects/Gamma", "url": url}])
    quiet(cli.cmd_sync, Args())
    rmtree(home / "Projects" / "Gamma")
    (home / "Projects" / "Gamma").mkdir()
    (home / "Projects" / "Gamma" / "loose.md").write_text("x", encoding="utf-8")
    git("init", "-q", str(home / "Projects" / "Stray"))
    code, out = quiet(cli.cmd_doctor, Args())
    assert code == 1
    assert "not a repo" in out and "Gamma" in out
    assert "not in manifest" in out and "Stray" in out


def test_adopting_a_folder_keeps_every_local_file():
    url = bare_with("delta", {"keep.md": "remote\n", "only-remote.md": "r\n"})
    home = world("adopt", [{"path": "Projects/Delta", "url": url}])
    folder = home / "Projects" / "Delta"
    folder.mkdir(parents=True)
    (folder / "keep.md").write_text("local edit\n", encoding="utf-8")
    (folder / "new.md").write_text("new\n", encoding="utf-8")
    code, out = quiet(cli.cmd_adopt, Args())
    assert code == 0, out
    assert (folder / "keep.md").read_text() == "local edit\n"
    assert (folder / "new.md").read_text() == "new\n"
    s = gitrepo.state(folder)
    assert s.is_repo and s.upstream and s.dirty >= 2, s


# ---------------------------------------------------------------- drive ----

def test_a_drive_plan_is_checked_applied_and_undone():
    root = SCRATCH / "drive"
    (root / "Documents" / "Books").mkdir(parents=True)
    (root / "Documents" / "Books" / "a.txt").write_text("a", encoding="utf-8")
    (root / "Music").mkdir()
    (root / "Media").mkdir()
    (root / "Media" / "Music").mkdir()          # a destination already taken
    plan = root / ".order" / "plan.jsonl"
    moves = [drive.Move("Documents/Books", "Library/Books", "other people's works"),
             drive.Move("Music", "Media/Music", "media together"),
             drive.Move("Nowhere", "Elsewhere", "")]
    drive.write_plan(plan, moves, header="test plan")
    problems = {(m.source, why) for m, why in drive.check(root, drive.read_plan(plan))}
    assert ("Music", "destination already exists") in problems
    assert ("Nowhere", "source does not exist") in problems
    journal = drive.apply(root, drive.read_plan(plan), report=lambda _: None)
    assert (root / "Library" / "Books" / "a.txt").exists()
    assert (root / "Music").exists(), "a taken destination is skipped, not merged"
    events = [json.loads(line)["event"] for line in journal.read_text().splitlines()]
    assert events.count("done") == 1 and events.count("skip") == 2
    undo_ps1 = journal.parent / journal.name.replace("journal-", "undo-").replace(".jsonl", ".ps1")
    assert undo_ps1.exists() and "Library/Books" in undo_ps1.read_text(encoding="utf-8-sig")
    assert drive.undo(root, journal, report=lambda _: None) == 1
    assert (root / "Documents" / "Books" / "a.txt").exists()
    assert not (root / "Library" / "Books").exists()


def test_a_move_inside_itself_is_refused():
    root = SCRATCH / "inside"
    (root / "A").mkdir(parents=True)
    problems = drive.check(root, [drive.Move("A", "A/B")])
    assert any("inside" in why for _, why in problems)


TESTS = [(name[5:].replace("_", " "), fn) for name, fn in sorted(globals().items())
         if name.startswith("test_") and callable(fn)]


def main() -> int:
    if shutil.which("git") is None:
        print("no git on PATH — skipped")
        return 0
    failures = []
    try:
        for name, test in TESTS:
            try:
                test()
            except Exception as exc:                        # noqa: BLE001
                import traceback
                failures.append(name)
                print(f"  FAIL  {name}: {exc!r}")
                for line in traceback.format_exc().strip().splitlines()[-4:]:
                    print(f"        {line}")
            else:
                print(f"  ok  {name}")
    finally:
        for var in ("ORDER_HOME", "ORDER_MACHINE"):
            os.environ.pop(var, None)
        rmtree(SCRATCH) if SCRATCH.exists() else None
    if failures:
        print(f"\n{len(failures)} failed")
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
