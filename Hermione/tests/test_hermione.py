"""What can be checked without a disk to move things around on, and what needs one.

Most of this app is "take this path and do something irreversible to it", so
most of the suite builds a small filesystem of its own under a temp directory
and checks what came of it. Everything it writes, it wrote; everything it
deletes, it made.

    python3 tests/test_hermione.py

The widgets need a display and nothing else. **No window is ever presented.**
A suite that maps a window steals the focus of whoever is running it — and
under a tiling compositor it also takes a column — so the window tests build
the real window, measure it, drive its actions and throw it away, all
unmapped. `measure()` on an `AdwWindow` accounts for its breakpoints, which
is what makes the "fits in a tiling column" test possible without showing
anything.

Two things are stubbed rather than exercised, on purpose:

*Opening a file.* `Gtk.FileLauncher` goes to the desktop portal, and a test
that activates a .png opens the user's image viewer. `HERMIONE_NO_LAUNCH`
turns it into a signal instead, and the suite checks the signal.

*The trash.* GIO refuses to trash anything on a tmpfs, which is what /tmp
usually is, so the trash tests run in a scratch directory under $HOME and
report themselves skipped where even that will not do.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

def _scratch_root() -> tuple[str, bool]:
    """Somewhere to work, under $HOME if that is possible.

    Under $HOME on purpose, in a hidden directory removed at the end. The
    trash is the reason: GIO will not trash a file on a tmpfs, and it will
    not trash one across a filesystem boundary either — so a trash test needs
    its file and `$XDG_DATA_HOME/Trash` on the same real filesystem, and the
    only one this suite can count on is the user's own. Everything else is
    happy anywhere, and /tmp is the fallback when $HOME will not have us.
    """
    home = os.path.expanduser("~")
    if os.path.isdir(home) and os.access(home, os.W_OK):
        try:
            return tempfile.mkdtemp(prefix=".hermione-tests-", dir=home), True
        except OSError:
            pass
    return tempfile.mkdtemp(prefix="hermione-tests-"), False


scratch, in_home = _scratch_root()
# Set before anything imports GLib: it caches these directories on first use,
# and a later change to the environment does not reach it.
os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(scratch, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(scratch, "cache")
# Nothing in this suite may reach the desktop.
os.environ["HERMIONE_NO_LAUNCH"] = "1"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from hermione import (archive, config as configmod, entries, filetypes,  # noqa: E402
                      ops, palette, search, style, thumbs, trash, units,
                      vcs, winenv)

skipped = []
failures = []


def check(name, fn):
    fn()
    print(f"  ok  {name}")


def needs(tool, name, fn):
    """Run fn only where the tool exists; say so plainly when it doesn't."""
    if winenv.tool(tool):
        check(name, fn)
    else:
        skipped.append((name, f"no {tool} on this machine"))
        print(f"  --  {name}  (no {tool} on this machine)")


def skip(name, why):
    skipped.append((name, why))
    print(f"  --  {name}  ({why})")


def tree(*names, root=None):
    """A scratch directory with these files in it. Returns its path."""
    where = tempfile.mkdtemp(dir=root or scratch)
    for name in names:
        full = os.path.join(where, name)
        if name.endswith("/"):
            os.makedirs(full, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(name)
    return where


# ------------------------------------------------------------- the units ---

def test_sizes_read_the_way_a_person_writes_them():
    assert units.human_size(0) == "0 bytes"
    assert units.human_size(1) == "1 byte"
    assert units.human_size(1536) == "1.50 KB"
    assert units.human_size(1024 ** 3) == "1.00 GB"
    assert units.human_size(1000, si=True) == "1.00 kB"
    # Three significant figures, so a column lines up.
    for value in (1234, 12345, 123456, 1234567):
        text = units.human_size(value)
        assert len(text.split()[0]) <= 4, text


def test_permissions_are_spelled_the_way_ls_spells_them():
    assert units.human_mode(0o755) == "rwxr-xr-x"
    assert units.human_mode(0o640) == "rw-r-----"
    assert units.human_mode(0o4755) == "rwsr-xr-x"
    assert units.human_mode(0o4644) == "rwSr--r--"
    assert units.human_mode(0o1777) == "rwxrwxrwt"


def test_counts_of_things_are_said_in_words():
    assert units.human_items(0, 0) == "empty"
    assert units.human_items(1, 0) == "1 file"
    assert units.human_items(3, 1) == "1 folder, 3 files"


# --------------------------------------------------------- the file types ---

def test_every_extension_belongs_to_exactly_one_category():
    seen = {}
    for key, words in filetypes._TABLE.items():
        for word in words.split():
            assert word not in seen, (
                f"{word!r} is claimed by both {seen.get(word)} and {key}")
            seen[word] = key


def test_a_compressed_tar_is_an_archive_under_its_whole_suffix():
    assert filetypes.extension("linux-6.1.tar.xz") == "tar.xz"
    assert filetypes.category("linux-6.1.tar.xz") == "archive"
    assert filetypes.category("pkg-1.0-x86_64.pkg.tar.zst") == "archive"


def test_a_number_on_the_end_is_a_version_not_a_type():
    assert filetypes.extension("libfoo.so.6") == "so"
    assert filetypes.category("libfoo.so.6") == "binary"
    assert filetypes.extension("backup.2024") == ""


def test_the_names_we_know_by_heart_beat_the_mime_database():
    # GIO reads go.mod as an Amiga sound module, which is true of the
    # extension and wrong about the file.
    assert filetypes.category("go.mod") == "code"
    assert "audio" not in filetypes.icon_name_for("go.mod")
    assert filetypes.category("Makefile") == "code"
    assert filetypes.category(".bashrc") == "text"


def test_each_category_has_a_colour_for_both_themes():
    for key in filetypes.CATEGORIES:
        if key == "other":
            continue
        assert filetypes.accent(key, True).startswith("#")
        assert filetypes.accent(key, False).startswith("#")
        assert filetypes.accent(key, True) != filetypes.accent(key, False)


# ----------------------------------------------------------- the listing ---

def test_a_folder_is_read_with_its_links_and_its_broken_links():
    where = tree("file.txt", "sub/")
    os.symlink("file.txt", os.path.join(where, "link"))
    os.symlink("nowhere-at-all", os.path.join(where, "broken"))
    found, error = entries.read_dir(where)
    assert error == ""
    by_name = {entry.name: entry for entry in found}
    assert by_name["sub"].is_dir
    assert by_name["link"].is_link and not by_name["link"].broken
    assert by_name["broken"].is_link and by_name["broken"].broken
    assert by_name["broken"].link_target == "nowhere-at-all"


def test_hidden_files_are_hidden_including_the_ones_a_folder_asks_to_hide():
    where = tree("shown.txt", ".dotfile", "secret.txt")
    with open(os.path.join(where, ".hidden"), "w", encoding="utf-8") as handle:
        handle.write("secret.txt\n")
    names = {entry.name for entry in entries.read_dir(where)[0]}
    assert names == {"shown.txt"}
    names = {entry.name for entry in entries.read_dir(where, True)[0]}
    assert {"shown.txt", ".dotfile", "secret.txt", ".hidden"} <= names


def test_file2_sorts_before_file10():
    where = tree("file10.txt", "file2.txt", "File1.txt")
    found = entries.sort_entries(entries.read_dir(where)[0])
    assert [e.name for e in found] == ["File1.txt", "file2.txt", "file10.txt"]


def test_folders_stay_first_whichever_way_the_sort_goes():
    where = tree("a.txt", "z.txt", "mid/")
    found = entries.read_dir(where)[0]
    for reverse in (False, True):
        ordered = entries.sort_entries(found, "name", reverse)
        assert ordered[0].is_dir, "a reversed sort put the folder last"


def test_an_unreadable_folder_is_a_message_not_a_crash():
    found, error = entries.read_dir(os.path.join(scratch, "no-such-folder"))
    assert found == []
    assert "no longer exists" in error
    # A folder the user may not read: /root, where there is one. Windows has
    # no uid and no such folder, so there the missing-folder half is the test.
    if hasattr(os, "getuid") and os.getuid() != 0 and os.path.isdir("/root"):
        _found, error = entries.read_dir("/root")
        assert "permission" in error.lower(), error


def test_the_filter_takes_a_glob_when_it_looks_like_one():
    where = tree("a.png", "b.txt", "c.PNG")
    found = entries.read_dir(where)[0]
    assert {e.name for e in entries.filter_entries(found, "*.png")} == {"a.png",
                                                                        "c.PNG"}
    assert {e.name for e in entries.filter_entries(found, "b")} == {"b.txt"}


# ------------------------------------------------------------- the config ---

def test_settings_survive_a_round_trip_and_rubbish_does_not():
    path = os.path.join(scratch, "round-trip.json")
    first = configmod.Config(path)
    first["view_mode"] = "grid"
    first["translucency"] = 3.0          # out of range
    first["glass_blur"] = -5             # out of range
    first.add_bookmark("/somewhere")
    first.save()

    second = configmod.Config(path)
    assert second["view_mode"] == "grid"
    assert second["translucency"] == 1.0, "a value out of range was not clamped"
    assert second["glass_blur"] == 0
    assert second["bookmarks"] == [{"name": "somewhere", "path": "/somewhere"}]

    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"view_mode": 7, "show_hidden": "yes", "rounding": 999}')
    third = configmod.Config(path)
    assert third["view_mode"] == "list"
    assert third["show_hidden"] is False
    assert third["rounding"] == 28

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("not json at all")
    assert configmod.Config(path)["view_mode"] == "list"


def test_a_folder_remembers_how_it_was_looked_at():
    path = os.path.join(scratch, "views.json")
    settings = configmod.Config(path)
    settings.set_folder_view("/pictures", view_mode="grid")
    settings.save()
    assert configmod.Config(path).folder_view("/pictures")["view_mode"] == "grid"
    assert configmod.Config(path).folder_view("/elsewhere") == {}


# --------------------------------------------------------- the operations ---

def test_a_copy_brings_the_whole_tree_with_its_permissions():
    source = tree("a.txt", "deep/b.txt", "deep/deeper/c.txt")
    os.chmod(os.path.join(source, "a.txt"), 0o640)
    os.symlink("a.txt", os.path.join(source, "link"))
    destination = tree()

    result = ops.Job(ops.COPY, [source], destination).run()
    assert result.ok, result.errors
    landed = os.path.join(destination, os.path.basename(source))
    assert os.path.exists(os.path.join(landed, "deep", "deeper", "c.txt"))
    assert os.stat(os.path.join(landed, "a.txt")).st_mode & 0o777 == 0o640
    assert os.path.islink(os.path.join(landed, "link")), "a link was followed"


def test_a_folder_cannot_be_copied_into_itself():
    source = tree("sub/x.txt")
    result = ops.Job(ops.COPY, [source], os.path.join(source, "sub")).run()
    assert not result.ok
    assert "itself" in result.errors[0]


def test_a_taken_name_asks_and_the_answer_can_cover_the_rest():
    source = tree("one.txt", "two.txt")
    destination = tree()
    ops.Job(ops.COPY, [source], destination).run()

    asked = []

    def resolver(_source, target, _is_dir):
        asked.append(os.path.basename(target))
        return (ops.SKIP, True)          # and all the rest like it

    result = ops.Job(ops.COPY, [source], destination, resolver=resolver).run()
    assert len(asked) == 1, f"asked {len(asked)} times for one answer"
    assert result.skipped


def test_keeping_both_names_the_copy_rather_than_the_original():
    source = tree("notes.txt")
    destination = tree()
    ops.Job(ops.COPY, [source], destination).run()
    ops.Job(ops.COPY, [source], destination,
            resolver=lambda *_a: ops.RENAME).run()
    names = sorted(os.listdir(destination))
    assert len(names) == 2 and any("(copy)" in name for name in names), names


def test_a_duplicate_is_named_copy_then_copy_2():
    where = tree("archive.tar.gz")
    assert ops.unique_name(where, "archive.tar.gz") == "archive (copy).tar.gz"
    with open(os.path.join(where, "archive (copy).tar.gz"), "w") as handle:
        handle.write("x")
    assert ops.unique_name(where, "archive.tar.gz") == "archive (copy 2).tar.gz"


def test_a_move_puts_it_there_and_takes_it_from_here():
    source = tree("thing.txt")
    destination = tree()
    result = ops.Job(ops.MOVE, [os.path.join(source, "thing.txt")],
                     destination).run()
    assert result.ok and result.moved
    assert not os.path.exists(os.path.join(source, "thing.txt"))
    assert os.path.exists(os.path.join(destination, "thing.txt"))


def test_a_cancelled_copy_stops_and_leaves_no_half_file():
    source = tree()
    big = os.path.join(source, "big.bin")
    with open(big, "wb") as handle:
        handle.write(os.urandom(48 * 1024 * 1024))
    destination = tree()
    job = ops.Job(ops.COPY, [big], destination)
    job.start()
    while job.progress.bytes_done < 1 << 20 and job.progress.phase != "done":
        time.sleep(0.005)
    job.cancel()
    job.join(20)
    assert job.result.cancelled
    landed = os.path.join(destination, "big.bin")
    assert not os.path.exists(landed), "a half-written file was left behind"


def test_deleting_a_tree_removes_all_of_it():
    where = tree("a.txt", "deep/b.txt", "deep/deeper/c.txt")
    result = ops.Job(ops.DELETE, [where]).run()
    assert result.ok, result.errors
    assert not os.path.exists(where)


def test_a_recursive_chmod_leaves_the_folders_walkable():
    where = tree("file.txt", "sub/inner.txt")
    ok, why = ops.set_mode(where, 0o644, recursive=True)
    assert ok, why
    assert os.stat(where).st_mode & 0o111, "the top folder lost its x bit"
    assert os.stat(os.path.join(where, "sub")).st_mode & 0o111
    assert os.stat(os.path.join(where, "file.txt")).st_mode & 0o777 == 0o644


def test_renaming_refuses_a_name_that_is_not_one():
    where = tree("a.txt")
    path = os.path.join(where, "a.txt")
    assert ops.rename(path, "b.txt") == (True, "")
    ok, why = ops.rename(os.path.join(where, "b.txt"), "x/y")
    assert not ok and "slash" in why


# ---------------------------------------------------------------- undoing ---

def test_undoing_a_move_puts_each_thing_back_where_it_was():
    first = tree("one.txt")
    second = tree("two.txt")
    destination = tree()
    sources = [os.path.join(first, "one.txt"), os.path.join(second, "two.txt")]
    result = ops.Job(ops.MOVE, sources, destination).run()
    change = ops.Change(ops.MOVE, sources, destination, result)
    assert change.undoable
    change.undo().run()
    assert os.path.exists(sources[0]) and os.path.exists(sources[1])


def test_an_undo_never_destroys_what_took_the_old_name():
    where = tree("thing.txt")
    destination = tree()
    source = os.path.join(where, "thing.txt")
    result = ops.Job(ops.MOVE, [source], destination).run()
    with open(source, "w", encoding="utf-8") as handle:
        handle.write("something else entirely")
    ops.Change(ops.MOVE, [source], destination, result).undo().run()
    with open(source, encoding="utf-8") as handle:
        assert handle.read() == "something else entirely"
    assert any("restored" in name for name in os.listdir(where))


def test_undoing_a_copy_deletes_the_copy_and_only_the_copy():
    source = tree("a.txt", "deep/b.txt")
    destination = tree()
    result = ops.Job(ops.COPY, [source], destination).run()
    change = ops.Change(ops.COPY, [source], destination, result)
    assert len(change._top_level_created()) == 1, "nested paths were not folded"
    change.undo().run()
    assert os.listdir(destination) == []
    assert os.path.exists(os.path.join(source, "deep", "b.txt"))


def test_the_undo_stack_is_bounded_and_redoable():
    stack = ops.UndoStack(limit=3)
    for index in range(5):
        stack.record("create", [f"/tmp/thing-{index}"])
    assert len(stack.done) == 3
    assert stack.can_undo and not stack.can_redo
    change = stack.pop_undo()
    assert change.sources == ["/tmp/thing-4"]
    assert stack.can_redo and stack.pop_redo() is change
    assert stack.record(ops.DELETE, ["/x"], result=ops.Result(ops.DELETE)) is None


# ----------------------------------------------------------- the searching ---

def test_a_search_finds_names_globs_and_contents():
    where = tree("one/hello.py", "one/two/notes.txt", "plain.txt")
    with open(os.path.join(where, "one", "hello.py"), "w") as handle:
        handle.write("import os\nprint('needle here')\n")
    with open(os.path.join(where, "one", "two", "notes.txt"), "w") as handle:
        handle.write("nothing\nNEEDLE in caps\n")

    hits = []
    search.Search(where, search.Query("*.py"), on_hit=hits.append).run()
    assert [hit.entry.name for hit in hits] == ["hello.py"]

    hits = []
    search.Search(where, search.Query("needle", in_contents=True),
                  on_hit=hits.append).run()
    assert {hit.entry.name for hit in hits} == {"hello.py", "notes.txt"}
    assert all(hit.line_no > 0 for hit in hits)

    hits = []
    search.Search(where, search.Query("needle", in_contents=True,
                                      case_sensitive=True),
                  on_hit=hits.append).run()
    assert {hit.entry.name for hit in hits} == {"hello.py"}


def test_an_unfinished_regular_expression_matches_nothing_rather_than_raising():
    assert search.Query("(unclosed", regex=True).name_matches("anything") is False


def test_duplicates_are_found_by_contents_not_by_name():
    where = tree("one/same.txt", "two/also-same.txt", "three/different.txt")
    for name in ("one/same.txt", "two/also-same.txt"):
        with open(os.path.join(where, name), "w", encoding="utf-8") as handle:
            handle.write("identical contents" * 100)
    groups = search.duplicates(where)
    assert len(groups) == 1 and len(groups[0]) == 2
    assert {os.path.basename(p) for p in groups[0]} == {"same.txt",
                                                        "also-same.txt"}


def test_the_biggest_files_come_back_biggest_first():
    where = tree()
    for index, size in enumerate((500, 50_000, 5_000)):
        with open(os.path.join(where, f"f{index}"), "wb") as handle:
            handle.write(b"x" * size)
    found = search.largest_files(where, 2)
    assert [size for _path, size in found] == [50_000, 5_000]


def test_empty_folders_include_the_ones_holding_only_empty_folders():
    where = tree("chain/a/b/c/", "kept/file.txt")
    found = search.empty_folders(where)
    names = {os.path.relpath(path, where) for path in found}
    assert names == {"chain", "chain/a", "chain/a/b", "chain/a/b/c"}
    # Deepest first, so deleting them in order works.
    for path in found:
        os.rmdir(path)
    assert sorted(os.listdir(where)) == ["kept"]


# ------------------------------------------------------------- the archives ---

def test_an_archive_round_trips_through_every_format_this_machine_can_write():
    source = tree("a.txt", "deep/b.txt")
    os.chmod(os.path.join(source, "a.txt"), 0o755)
    for key, _label, _suffix in archive.available_formats():
        if key == "7z":
            continue                     # the tool's own business, tested below
        out = tree()
        made = archive.compress([source], out, key).run()
        assert made.ok, (key, made.errors)
        assert made.created, key
        back = tree()
        pulled = archive.extract(made.created[0], back).run()
        assert pulled.ok, (key, pulled.errors)
        landed = os.path.join(back, os.path.basename(source), "deep", "b.txt")
        assert os.path.exists(landed), f"{key} lost a file"


def test_an_archive_member_cannot_escape_the_folder_it_is_extracted_into():
    import io
    import tarfile
    import zipfile

    where = tree()
    evil_zip = os.path.join(where, "evil.zip")
    with zipfile.ZipFile(evil_zip, "w") as zipped:
        zipped.writestr("../../escaped.txt", "pwned")
        zipped.writestr("/absolute.txt", "pwned")
        zipped.writestr("fine.txt", "fine")

    out = tree()
    result = archive.extract(evil_zip, out, into_subfolder=False).run()
    assert len(result.errors) == 2, result.errors
    assert all("outside" in message for message in result.errors)
    assert os.listdir(out) == ["fine.txt"]
    assert not os.path.exists(os.path.join(where, "escaped.txt"))

    evil_tar = os.path.join(where, "evil.tar")
    with tarfile.open(evil_tar, "w") as tarred:
        info = tarfile.TarInfo("../../escaped.txt")
        info.size = 3
        tarred.addfile(info, io.BytesIO(b"abc"))
    out = tree()
    result = archive.extract(evil_tar, out).run()
    assert result.errors and "outside" in result.errors[0]
    assert not os.path.exists(os.path.join(where, "escaped.txt"))


def test_an_archive_with_one_folder_in_it_does_not_get_another():
    source = tree("inner/a.txt")
    out = tree()
    made = archive.compress([os.path.join(source, "inner")], out, "zip").run()
    back = tree()
    archive.extract(made.created[0], back).run()
    assert os.listdir(back) == ["inner"], os.listdir(back)

    loose = tree()
    import zipfile

    bare = os.path.join(loose, "bare.zip")
    with zipfile.ZipFile(bare, "w") as zipped:
        zipped.writestr("one.txt", "1")
        zipped.writestr("two.txt", "2")
    back = tree()
    archive.extract(bare, back).run()
    assert os.listdir(back) == ["bare"], "loose files were not given a folder"


# ----------------------------------------------------------- the thumbnails ---

def test_a_thumbnail_is_cached_where_the_rest_of_the_desktop_can_find_it():
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    where = tree()
    image = os.path.join(where, "picture.png")
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 400, 300)
    pixbuf.fill(0x3366FFFF)
    pixbuf.savev(image, "png", [], [])

    assert thumbs.valid_cached(image, 128) == ""
    made = thumbs.generate(image, 128)
    assert made and os.path.exists(made)
    assert os.path.join("thumbnails", "normal") in made
    assert thumbs.valid_cached(image, 128) == made

    # A file rewritten is a thumbnail that no longer describes it.
    os.utime(image, (time.time() + 500, time.time() + 500))
    assert thumbs.valid_cached(image, 128) == "", "a stale thumbnail was served"


def test_a_file_that_cannot_be_thumbnailed_is_only_tried_once():
    where = tree()
    broken = os.path.join(where, "broken.png")
    with open(broken, "wb") as handle:
        handle.write(b"this is not a png")
    assert thumbs.generate(broken, 128) == ""
    assert thumbs.known_bad(broken)


def test_nothing_enormous_is_decoded_for_a_tile():
    entry = entries.Entry("/x/film.mkv")
    entry.category = "video"
    entry.size = 4 * 1024 ** 3
    assert not thumbs.can_thumbnail(entry, 80)
    entry.size = 1024
    # Only where a thumbnailer exists at all.
    if winenv.tool("ffmpegthumbnailer") or winenv.tool("ffmpeg"):
        assert thumbs.can_thumbnail(entry, 80)


# ------------------------------------------------------------------ git ---

def test_git_status_marks_a_folder_with_what_is_under_it():
    where = tempfile.mkdtemp(dir=scratch)
    quiet = {"cwd": where, "capture_output": True}
    subprocess.run(["git", "init", "-q"], **quiet)
    subprocess.run(["git", "config", "user.email", "t@example.com"], **quiet)
    subprocess.run(["git", "config", "user.name", "Test"], **quiet)
    os.makedirs(os.path.join(where, "src"))
    for name in ("src/kept.txt", "tracked.txt"):
        with open(os.path.join(where, name), "w", encoding="utf-8") as handle:
            handle.write("first")
    subprocess.run(["git", "add", "-A"], **quiet)
    subprocess.run(["git", "commit", "-qm", "first"], **quiet)

    with open(os.path.join(where, "src", "kept.txt"), "a", encoding="utf-8") as handle:
        handle.write("changed")
    with open(os.path.join(where, "fresh.txt"), "w", encoding="utf-8") as handle:
        handle.write("new")

    vcs.forget()
    marks = vcs.status(where)
    assert marks.get(os.path.join(where, "src")) == vcs.MODIFIED, marks
    assert marks.get(os.path.join(where, "fresh.txt")) == vcs.UNTRACKED, marks
    assert os.path.join(where, "tracked.txt") not in marks
    assert vcs.branch(where)


def test_git_codes_reduce_to_one_letter_each():
    assert vcs._reduce("??") == vcs.UNTRACKED
    assert vcs._reduce(" M") == vcs.MODIFIED
    assert vcs._reduce("A ") == vcs.ADDED
    assert vcs._reduce("UU") == vcs.CONFLICT
    assert vcs._reduce("  ") == vcs.CLEAN
    # A conflict outranks everything; an ignored file loses to everything.
    assert vcs._stronger(vcs.UNTRACKED, vcs.CONFLICT) == vcs.CONFLICT
    assert vcs._stronger(vcs.IGNORED, vcs.MODIFIED) == vcs.MODIFIED


# --------------------------------------------------------------- the trash ---

def test_the_trash_takes_a_file_and_gives_it_back(where):
    victim = os.path.join(where, "deleted-by-the-test.txt")
    with open(victim, "w", encoding="utf-8") as handle:
        handle.write("contents")

    result = ops.Job(ops.TRASH, [victim]).run()
    if result.errors and "not supported" in result.errors[0]:
        raise RuntimeError("no trash on this filesystem")
    assert result.trashed == [victim], result.errors
    assert not os.path.exists(victim)

    items = [item for item in trash.list_items() if item.original == victim]
    assert len(items) == 1, "the deleted file is not in the trash"
    assert items[0].display_name == "deleted-by-the-test.txt"

    ok, why = trash.restore(items[0])
    assert ok, why
    assert os.path.exists(victim)
    assert not [i for i in trash.list_items() if i.original == victim]


def test_restoring_over_something_newer_refuses(where):
    victim = os.path.join(where, "collides.txt")
    with open(victim, "w", encoding="utf-8") as handle:
        handle.write("old")
    result = ops.Job(ops.TRASH, [victim]).run()
    if not result.trashed:
        raise RuntimeError("no trash on this filesystem")
    with open(victim, "w", encoding="utf-8") as handle:
        handle.write("new")
    item = [i for i in trash.list_items() if i.original == victim][0]
    ok, why = trash.restore(item)
    assert not ok and "already exists" in why
    with open(victim, encoding="utf-8") as handle:
        assert handle.read() == "new"
    trash.delete_item(item)


# -------------------------------------------------------------- the palette ---

def test_the_palette_finds_things_by_their_initials():
    items = [palette.Item("New tab", sort_hint=0),
             palette.Item("Downloads", "~", sort_hint=1),
             palette.Item("Documents", "~", sort_hint=1),
             palette.Item("notes.txt", "in this folder", sort_hint=2)]
    assert [i.label for i in palette.rank(items, "dnl")] == ["Downloads"]
    assert palette.rank(items, "doc")[0].label == "Documents"
    assert palette.rank(items, "new")[0].label == "New tab"
    assert palette.rank(items, "zzz") == []
    # An outright substring beats a scattered match.
    assert palette.score("down", "Downloads") > palette.score("dnl", "Downloads")


# -------------------------------------------------------------- the stylesheet ---

def test_the_stylesheet_parses_in_both_themes():
    from gi.repository import Gtk

    settings = configmod.Config(os.path.join(scratch, "style.json"))
    for dark in (True, False):
        for glass in (True, False):
            settings["glass"] = glass
            problems = []
            provider = Gtk.CssProvider()
            provider.connect("parsing-error",
                             lambda _p, _s, error, found=problems:
                             found.append(error.message))
            provider.load_from_string(style.css_text(settings, dark))
            assert not problems, (dark, glass, problems)


def test_the_window_is_layered_from_solid_to_sheer():
    """The depth order, which is the whole of the look.

    Two rungs, not four. The three modules — the toolbar, the places and the
    listing — are one material cut into three pieces, so they share an
    alpha; the slab behind them is the sheer thing, and the gaps between the
    cards are where it shows. Giving each card a strength of its own is what
    this used to do, and three tints of the same glass meeting at a hard
    edge read as a mistake rather than as depth.
    """
    from hermione.style import _numbers

    settings = configmod.Config(os.path.join(scratch, "layers.json"))
    for dark in (True, False):
        n = _numbers(settings, dark)
        assert n["card_alpha"] > n["slab_alpha"], (dark, n)
        assert n["card_alpha"] < 1.0, "the cards stopped being glass"
        # A strip inside a card is lifted with light, not cut in with
        # shadow, so it stays well under the card it sits on.
        assert 0.0 < n["inner_tint"] < n["inner_tint_strong"] < 0.5, (dark, n)
        # And a card is rounder than nothing and no rounder than the slab.
        assert 0 < n["module_radius"] <= max(n["radius"], 10), (dark, n)

    # And a control sitting on a bar is raised out of it rather than cut
    # into it: tinted with light, never with shadow.
    assert _numbers(settings, True)["field"] == "#ffffff"

    # The backgrounds libadwaita would otherwise paint are cleared, and only
    # for the window's own bars — a dialog is built from the same widget and
    # still needs its own. Its hairline between the two halves of the split
    # view goes too: with the cards apart that line lands in the gap.
    sheet = style.css_text(settings, True)
    assert ".plain-bars > revealer.top-bar" in sheet
    assert "overlay-split-view > widget.sidebar-pane" in sheet
    assert "\ntoolbarview,\n" not in sheet, "the rule is not scoped"


def test_the_window_is_three_cards_on_a_slab(app):
    """The toolbar, the places and the listing are each a module of their own.

    The toolbar used to be a top bar of the content pane, which put it
    between the other two with an edge shared with each — so it could not
    read as a card whatever it was painted, and its corners could not be
    round. It spans the window now, and all three clip what they hold to
    their own radius.
    """
    from gi.repository import Adw, Gtk

    from hermione import style as stylemod
    from hermione.window import MainWindow

    window = MainWindow(app, scratch)
    cards = {"the toolbar": window.header,
             "the places": window.sidebar_box,
             "the listing": window.content_card}
    for name, card in cards.items():
        assert card.has_css_class("module"), f"{name} is not a module"
    for name in ("the places", "the listing"):
        assert cards[name].get_overflow() == Gtk.Overflow.HIDDEN, \
            f"{name} does not clip to its corners"

    # The toolbar is above the split view rather than inside it, which is
    # what "the width of the window" means in widget terms.
    assert window.header.get_ancestor(Adw.OverlaySplitView) is None, \
        "the toolbar is back inside the content pane"
    assert window.split.get_parent() is window.modules
    assert window.modules.get_spacing() == stylemod.GAP
    assert window.sidebar_box.get_margin_end() == stylemod.GAP, \
        "the places and the listing have no gap between them"
    window.destroy()


def test_the_glass_is_really_switched_off_when_it_is_switched_off():
    settings = configmod.Config(os.path.join(scratch, "style2.json"))
    settings["glass"] = False
    assert "backdrop-filter" not in style.css_text(settings, True)
    settings["glass"] = True
    assert "backdrop-filter" in style.css_text(settings, True)


# ----------------------------------------------------------------- the window ---

def test_the_window_fits_in_a_tiling_column(app):
    """niri's narrowest preset column on this screen is about 455px.

    Measured rather than eyeballed, and measured on the window itself, which
    accounts for its breakpoints.
    """
    from gi.repository import Gtk

    from hermione.window import MainWindow

    window = MainWindow(app, scratch)
    minimum = window.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    window.destroy()
    assert minimum <= 455, f"the window cannot be narrower than {minimum}px"


def test_the_window_opens_a_folder_and_lists_it(app):
    from hermione.window import MainWindow

    where = tree("a.txt", "b.txt", "sub/")
    window = MainWindow(app, where)
    pane = window.current_pane()
    assert pane is not None
    _pump(0.6)
    names = {pane.model.selection.get_item(i).name
             for i in range(pane.model.selection.get_n_items())}
    assert names == {"a.txt", "b.txt", "sub"}, names
    window.destroy()


def test_tabs_and_two_panes(app):
    from hermione.window import MainWindow

    window = MainWindow(app, scratch)
    assert window.tabs.get_n_pages() == 1
    window.new_tab(scratch)
    assert window.tabs.get_n_pages() == 2
    assert not window.split_view_active()
    window.toggle_split()
    assert window.split_view_active()
    assert len(window.current_tab_holder()._panes) == 2
    window.toggle_split()
    assert not window.split_view_active(), "the second pane did not go away"
    window.destroy()


def test_every_shortcut_points_at_an_action_that_exists(app):
    from hermione.app import ACCELERATORS
    from hermione.window import MainWindow

    window = MainWindow(app, scratch)
    missing = []
    for name in ACCELERATORS:
        scope, _, action = name.partition(".")
        if scope == "app":
            if not app.lookup_action(action):
                missing.append(name)
        elif not window.lookup_action(action):
            missing.append(name)
    window.destroy()
    assert not missing, f"bound to nothing: {missing}"


def test_the_palette_offers_the_window_it_is_in(app):
    from hermione.window import MainWindow

    where = tree("findable.txt")
    window = MainWindow(app, where)
    _pump(0.6)
    labels = [item.label for item in window._palette_items()]
    assert "New tab" in labels
    assert "Find duplicates" in labels
    assert "findable.txt" in labels, "the folder's own files are not offered"
    window.destroy()


def test_activating_a_file_does_not_reach_the_desktop(app):
    """HERMIONE_NO_LAUNCH turns an open into a signal; nothing is launched."""
    from hermione.window import MainWindow

    where = tree("document.txt")
    window = MainWindow(app, where)
    pane = window.current_pane()
    _pump(0.6)
    opened = []
    pane.connect("activated", lambda _p, path: opened.append(path))
    entry = pane.model.selection.get_item(0)
    pane._on_activated(None, entry)
    assert opened == [os.path.join(where, "document.txt")]
    window.destroy()


def test_a_folder_opens_in_the_pane_rather_than_anywhere_else(app):
    from hermione.window import MainWindow

    where = tree("sub/inner.txt")
    window = MainWindow(app, where)
    pane = window.current_pane()
    _pump(0.6)
    pane._on_activated(None, pane.model.selection.get_item(0))
    assert pane.path == os.path.join(where, "sub")
    pane.go_up()
    assert pane.path == where
    window.destroy()


def test_a_clipboard_payload_is_read_the_way_every_other_file_manager_writes_it():
    """The format, and the reason the stream behind it is read asynchronously.

    Copying and pasting inside one app means reading a clipboard that same
    app owns. Reading it with a blocking call on the GTK thread deadlocks:
    the other end of the pipe is fed from the main loop, and the main loop is
    sitting in the read. `pane._paste_gnome` splices it asynchronously
    instead; this checks the parsing on the other side of that.
    """
    from hermione.pane import parse_copied_files

    payload = b"copy\nfile:///tmp/one%20two.txt\nfile:///tmp/three.txt\n"
    paths, move = parse_copied_files(payload)
    assert paths == ["/tmp/one two.txt", "/tmp/three.txt"]
    assert move is False
    paths, move = parse_copied_files(b"cut\nfile:///tmp/x.txt")
    assert paths == ["/tmp/x.txt"] and move is True
    # A remote URI is dropped rather than guessed at, and junk is survivable.
    paths, _move = parse_copied_files(b"copy\nsftp://host/x\n\nfile:///tmp/y")
    assert paths == ["/tmp/y"]
    assert parse_copied_files(b"") == ([], False)


def test_a_folder_opened_from_the_desktop_arrives_as_a_uri():
    """What a handler is actually handed.

    The desktop entry asks for `%U`, so whatever opens a folder passes a
    percent-encoded `file:` URI rather than a path. Reading that as a path
    produces a nonsense relative one, which is the first thing anybody who
    made this their default file manager would see.
    """
    from hermione.places import path_from_argument

    assert path_from_argument("file:///home/me/Downloads") == "/home/me/Downloads"
    assert path_from_argument("file:///home/me/My%20Folder") == "/home/me/My Folder"
    assert path_from_argument("FILE:///tmp") == "/tmp"
    assert path_from_argument("/plain/path") == "/plain/path"
    assert path_from_argument("~") == os.path.expanduser("~")
    # Real places, but not ones this app can list.
    assert path_from_argument("sftp://host/share") == ""
    assert path_from_argument("trash:///") == ""
    assert path_from_argument("") == ""


def test_a_uri_on_the_command_line_opens_the_right_folder(app):
    from hermione.window import MainWindow

    where = tree("inside.txt")
    window = MainWindow(app, os.path.expanduser("~"))
    _pump(0.4)
    window.select_path_arg(GLib_filename_to_uri(where))
    _pump(0.6)
    assert window.current_pane().path == where
    window.destroy()


def GLib_filename_to_uri(path: str) -> str:
    from gi.repository import GLib

    return GLib.filename_to_uri(path, None)


def test_there_is_one_toolbar_and_not_two(app):
    """The chrome above a listing is one row.

    It was two for a while — a nearly empty window header above a second
    bar inside the pane — which meant two search buttons, two menus and two
    rows of glass above every folder. Everything lives on the window's bar
    now and acts on whichever pane has the focus; a pane carries only a slim
    line of breadcrumbs, and only when the tab is split in two.
    """
    from hermione.window import MainWindow

    window = MainWindow(app, scratch)
    pane = window.current_pane()
    _pump(0.4)

    for gone in ("back_button", "forward_button", "up_button", "search_button",
                 "list_button", "grid_button", "menu_button", "path_entry",
                 "crumbs_bar"):
        assert not hasattr(pane, gone), f"the pane grew a {gone} back"
    for needed in ("back_button", "forward_button", "up_button", "find_button",
                   "list_button", "grid_button", "path_entry", "crumbs"):
        assert hasattr(window, needed), f"the toolbar has no {needed}"

    assert not pane.path_strip.get_visible(), \
        "a single pane is showing breadcrumbs of its own as well"
    window.toggle_split()
    panes = window.current_tab_holder()._panes
    assert len(panes) == 2
    assert all(p.path_strip.get_visible() for p in panes), \
        "a split pane does not say which folder it holds"

    # One bar's worth of chrome above the list, whichever it is.
    def count(widget, name, depth=0):
        found = 1 if widget.has_css_class(name) and widget.get_visible() else 0
        child = widget.get_first_child()
        while child is not None:
            found += count(child, name, depth + 1)
            child = child.get_next_sibling()
        return found

    window.toggle_split()
    assert count(window, "chrome") == 1, "more than one toolbar"
    window.destroy()


def test_a_crumb_survives_being_pressed(app):
    """Clicking a crumb above the folder you are in takes you there.

    Pressing a crumb focuses it, and the window resyncs its header on every
    focus change. When that rebuilt the trail, the pressed button was gone
    before the release and the click never landed.
    """
    from gi.repository import Gtk
    from hermione.window import MainWindow

    inner = os.path.join(scratch, "crumb-a", "crumb-b")
    os.makedirs(inner, exist_ok=True)
    window = MainWindow(app, inner)
    pane = window.current_pane()
    _pump(0.3)

    def crumbs():
        found, child = [], window.crumbs.box.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Button):
                found.append(child)
            child = child.get_next_sibling()
        return found

    before = crumbs()
    parent = before[-2]
    parent.grab_focus()
    _pump(0.1)
    assert crumbs()[-2] is parent, "focusing a crumb rebuilt the trail under it"
    parent.emit("clicked")
    _pump(0.2)
    assert pane.path == os.path.dirname(inner), "the crumb went nowhere"
    window.destroy()


# ------------------------------------------------------------- the portal ---

def test_the_portal_reads_what_it_is_handed():
    """One portal call, turned into the question the chooser asks.

    Every field here has bitten somebody: `current_folder` is a nul
    terminated byte array rather than a string, `current_file` carries both
    the folder and the name for a re-save, `filters` is a list of lists, and
    `current_filter` comes back as a whole filter rather than as an index
    into the list it is supposed to be in.
    """
    from hermione import portal

    ask = portal.ask_from_options("open", "Pick one", {
        "multiple": True,
        "accept_label": "_Upload",
        "current_folder": list(b"/usr/share") + [0],
        "filters": [("Images", [(0, "*.png"), (1, "image/jpeg")]),
                    ("Text", [(0, "*.txt")])],
        "current_filter": ("Text", [(0, "*.txt")]),
        "choices": [("mode", "Open as", [("ro", "Read only"),
                                         ("rw", "Editable")], "rw"),
                    ("follow", "Follow links", [], "true")],
    })
    assert ask.folder == "/usr/share", ask.folder
    assert ask.multiple is True
    # The mnemonic underscore is for a menu, not for a button face.
    assert ask.default_accept() == "Upload"
    assert [f.name for f in ask.filters] == ["Images", "Text"]
    assert ask.current_filter == 1, "the named filter is not the one chosen"
    assert ask.choices[0].initial == "rw"
    assert ask.choices[1].options == [], "a boolean choice grew options"

    # A save that names a file says both where to start and what to call it.
    ask = portal.ask_from_options("save", "", {
        "current_file": list(b"/home/someone/notes/thing.md") + [0]})
    assert ask.folder == "/home/someone/notes", ask.folder
    assert ask.name == "thing.md", ask.name

    # SaveFiles is handed the names it wants written.
    ask = portal.ask_from_options("save-many", "", {
        "files": [list(b"one.txt") + [0], list(b"two.txt") + [0]]})
    assert ask.names == ["one.txt", "two.txt"], ask.names

    # And nothing in a malformed call is allowed to be an exception: the
    # calling program is not ours to trust and a raise here is a dialog
    # that never opens.
    rubbish = portal.ask_from_options("open", "", {
        "filters": ["not a filter", ("No patterns",)],
        "choices": [("short",)], "current_folder": "not bytes"})
    assert rubbish.filters == [] and rubbish.choices == []


def test_the_portal_filter_matches_the_way_a_person_would_expect():
    from hermione import chooser

    images = chooser.Filter("Images", [(0, "*.png"), (1, "image/jpeg")])
    assert images.accepts("photo.png", "image/png")
    # A glob is matched without regard to case: `*.JPG` and `holiday.jpg`
    # are the same answer to anybody who is not a filesystem.
    assert images.accepts("HOLIDAY.PNG", "image/png")
    assert images.accepts("holiday.jpg", "image/jpeg"), "the MIME rule missed"
    assert not images.accepts("notes.txt", "text/plain")

    everything = chooser.Filter("Any", [(1, "image/*")])
    assert everything.accepts("x.gif", "image/gif")
    assert not everything.accepts("x.pdf", "application/pdf")

    # A filter with no patterns is not a filter that hides everything.
    assert chooser.Filter("All files", []).accepts("anything", "")


def test_the_portal_answers_in_the_types_the_bus_declared():
    """The reply is checked against the signature, not against hope.

    A results dictionary of the wrong shape is not a Python error — it is a
    reply D-Bus refuses to send, which the asking program sees as a dialog
    that never came back.
    """
    from gi.repository import GLib

    from hermione import chooser, portal

    answer = chooser.Answer(
        paths=["/tmp/a file.txt"],
        choices=[("mode", "ro")],
        chosen_filter=chooser.Filter("Text", [(0, "*.txt")]))
    results = portal.results_variant(answer)
    assert results.get_type_string() == "a{sv}", results.get_type_string()
    assert results.unpack()["uris"] == ["file:///tmp/a%20file.txt"]
    assert results.unpack()["current_filter"] == ("Text", [(0, "*.txt")])

    reply = GLib.Variant.new_tuple(GLib.Variant("u", portal.SUCCESS), results)
    assert reply.get_type_string() == "(ua{sv})", reply.get_type_string()

    # And the interface the backend claims really does declare the three
    # methods, with the signature the portal calls them by.
    from gi.repository import Gio

    node = Gio.DBusNodeInfo.new_for_xml(portal.FILE_CHOOSER_XML)
    methods = {m.name for m in node.interfaces[0].methods}
    assert methods == {"OpenFile", "SaveFile", "SaveFiles"}, methods


def test_the_portal_takes_its_bus_name_before_it_loads_the_toolkit():
    """The deadlock this module is shaped to avoid.

    xdg-desktop-portal blocks on `StartServiceByName` while it builds its
    backend proxies. Importing libadwaita reads the colour scheme from the
    settings portal — the same daemon — synchronously, so a backend that
    loads the toolkit before taking its name waits on a daemon that is
    waiting on it. D-Bus gives up after 25 seconds, and the portal then
    exports *no* FileChooser interface at all until it is restarted: every
    program on the desktop loses its file dialog, not just this one.

    So importing `hermione.portal` must not drag in Gtk or Adw. Checked in a
    subprocess, because this one has had them loaded since it started.
    """
    probe = (
        "import sys; sys.path.insert(0, %r);"
        "import hermione.portal;"
        "print(sorted(m for m in sys.modules"
        " if m in ('gi.repository.Gtk', 'gi.repository.Adw')))"
        % os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", (
        f"hermione.portal pulled in {out.stdout.strip()} — the backend will "
        "deadlock with xdg-desktop-portal on a cold start")


def test_the_portal_serves_before_the_toolkit_is_loaded():
    """The second half of the same deadlock, and the expensive half.

    Taking the name first stops the portal giving up on us, but it does not
    stop it *waiting*: if the toolkit is loaded before the main loop runs,
    the portal's first call to the backend goes unanswered, D-Bus times out
    after 25 seconds, and the portal starts anyway with nothing logged. The
    only symptom is that the first app launched after logging in sits with
    no window for half a minute, because GDK waits on that same portal —
    which is exactly what it did until 2026-10-19.

    So `main` must reach its main loop without building the application.
    Checked by reading the module rather than by running it: `main` is a
    D-Bus service that would take its real bus name.
    """
    import ast

    source = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "hermione", "portal.py")
    tree = ast.parse(open(source, encoding="utf-8").read())
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    called = {node.func.id for node in ast.walk(main)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "_build_app" not in called, (
        "main() builds the toolkit before its main loop runs — the portal "
        "will wait 25 seconds for an answer it cannot get")

    # And the thing that does build it is reachable from the one call that
    # needs a window, rather than having been dropped altogether.
    portal_src = open(source, encoding="utf-8").read()
    assert "def ensure_app" in portal_src
    assert "ensure_app()" in portal_src, (
        "nothing calls ensure_app, so no dialog would ever get a window")


def test_every_dialog_builds(app):
    """Build each one and look at it. None of them is presented.

    Worth having because a dialog is the one part of this app that is only
    reached by a person clicking something: a typo in the properties dialog
    would otherwise sit there until somebody opened it.
    """
    from hermione import dialogs as dialogmod
    from hermione import prefs as prefsmod
    from hermione import properties as propmod
    from hermione import tools as toolsmod
    from hermione.window import MainWindow

    where = tree("one.txt", "two.txt")
    one = os.path.join(where, "one.txt")
    window = MainWindow(app, where)
    pane = window.current_pane()
    _pump(0.4)

    built = [
        propmod.Properties(window, [one], app.config),
        propmod.Properties(window, [one, where], app.config),
        prefsmod.Preferences(window, app.config, lambda _key: None),
        dialogmod.BatchRename(window, [one, os.path.join(where, "two.txt")],
                              lambda _plan: None),
    ]
    for mode in (toolsmod.DUPLICATES, toolsmod.LARGEST, toolsmod.EMPTY):
        built.append(toolsmod.Tools(window, mode, where, app.config))
    for obj in built:
        assert obj.dialog.get_child() is not None, obj

    # The batch rename's preview is the part worth checking: it must plan
    # before it is shown, and refuse a plan whose names collide.
    rename = built[3]
    rename.find.set_text("one")
    rename.replace.set_text("two")
    plan = rename.plan()
    assert [new for _old, new in plan] == ["two.txt", "two.txt"]
    rename._refresh()
    assert not rename.apply.get_sensitive(), "a clashing rename was allowed"
    rename.replace.set_text("three")
    rename._refresh()
    assert rename.apply.get_sensitive()

    # Not closed: none of them was presented, and libadwaita complains at
    # being asked to close a dialog that was never shown.
    _pump(0.2)

    # And the ones that are plain functions rather than objects.
    asked = []
    dialogmod.ask_rename(pane, one, lambda name: asked.append(name))
    dialogmod.ask_create(pane, where, lambda kind, name: asked.append(kind))
    dialogmod.ask_archive(pane, [one], lambda name, fmt: asked.append(fmt))
    dialogmod.confirm(pane, "Title", "Body", "Go", lambda: asked.append("go"))
    _pump(0.2)
    window.destroy()


def test_the_sidebar_lists_somewhere_to_go(app):
    from hermione.window import MainWindow

    window = MainWindow(app, scratch)
    rows = []
    child = window.places_box.get_first_child()
    while child is not None:
        rows.append(child)
        child = child.get_next_sibling()
    assert len(rows) >= 3, "the sidebar is empty"
    window.destroy()


def _pump(seconds: float):
    """Let the main loop run — the listing arrives on an idle callback."""
    from gi.repository import GLib

    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.01)


# ------------------------------------------------------------------- main ---

def main():
    print("numbers and names")
    check("sizes read the way a person writes them",
          test_sizes_read_the_way_a_person_writes_them)
    check("permissions are spelled the way ls spells them",
          test_permissions_are_spelled_the_way_ls_spells_them)
    check("counts of things are said in words",
          test_counts_of_things_are_said_in_words)

    print("file types")
    check("every extension belongs to exactly one category",
          test_every_extension_belongs_to_exactly_one_category)
    check("a compressed tar is an archive under its whole suffix",
          test_a_compressed_tar_is_an_archive_under_its_whole_suffix)
    check("a number on the end is a version, not a type",
          test_a_number_on_the_end_is_a_version_not_a_type)
    check("the names we know by heart beat the mime database",
          test_the_names_we_know_by_heart_beat_the_mime_database)
    check("each category has a colour for both themes",
          test_each_category_has_a_colour_for_both_themes)

    print("reading a folder")
    check("a folder is read with its links and its broken links",
          test_a_folder_is_read_with_its_links_and_its_broken_links)
    check("hidden files are hidden, including the ones a folder asks to hide",
          test_hidden_files_are_hidden_including_the_ones_a_folder_asks_to_hide)
    check("file2 sorts before file10", test_file2_sorts_before_file10)
    check("folders stay first whichever way the sort goes",
          test_folders_stay_first_whichever_way_the_sort_goes)
    check("an unreadable folder is a message, not a crash",
          test_an_unreadable_folder_is_a_message_not_a_crash)
    check("the filter takes a glob when it looks like one",
          test_the_filter_takes_a_glob_when_it_looks_like_one)

    print("settings")
    check("settings survive a round trip and rubbish does not",
          test_settings_survive_a_round_trip_and_rubbish_does_not)
    check("a folder remembers how it was looked at",
          test_a_folder_remembers_how_it_was_looked_at)

    print("moving things about")
    check("a copy brings the whole tree with its permissions",
          test_a_copy_brings_the_whole_tree_with_its_permissions)
    check("a folder cannot be copied into itself",
          test_a_folder_cannot_be_copied_into_itself)
    check("a taken name asks, and the answer can cover the rest",
          test_a_taken_name_asks_and_the_answer_can_cover_the_rest)
    check("keeping both renames the copy, not the original",
          test_keeping_both_names_the_copy_rather_than_the_original)
    check("a duplicate is named (copy), then (copy 2)",
          test_a_duplicate_is_named_copy_then_copy_2)
    check("a move puts it there and takes it from here",
          test_a_move_puts_it_there_and_takes_it_from_here)
    check("a cancelled copy stops and leaves no half file",
          test_a_cancelled_copy_stops_and_leaves_no_half_file)
    check("deleting a tree removes all of it",
          test_deleting_a_tree_removes_all_of_it)
    check("a recursive chmod leaves the folders walkable",
          test_a_recursive_chmod_leaves_the_folders_walkable)
    check("renaming refuses a name that is not one",
          test_renaming_refuses_a_name_that_is_not_one)

    print("taking it back")
    check("undoing a move puts each thing back where it was",
          test_undoing_a_move_puts_each_thing_back_where_it_was)
    check("an undo never destroys what took the old name",
          test_an_undo_never_destroys_what_took_the_old_name)
    check("undoing a copy deletes the copy and only the copy",
          test_undoing_a_copy_deletes_the_copy_and_only_the_copy)
    check("the undo stack is bounded and redoable",
          test_the_undo_stack_is_bounded_and_redoable)

    print("finding things")
    check("a search finds names, globs and contents",
          test_a_search_finds_names_globs_and_contents)
    check("an unfinished regular expression matches nothing",
          test_an_unfinished_regular_expression_matches_nothing_rather_than_raising)
    check("duplicates are found by contents, not by name",
          test_duplicates_are_found_by_contents_not_by_name)
    check("the biggest files come back biggest first",
          test_the_biggest_files_come_back_biggest_first)
    check("empty folders include the ones holding only empty folders",
          test_empty_folders_include_the_ones_holding_only_empty_folders)

    print("archives")
    check("an archive round trips through every format this machine writes",
          test_an_archive_round_trips_through_every_format_this_machine_can_write)
    check("an archive member cannot escape the folder it goes into",
          test_an_archive_member_cannot_escape_the_folder_it_is_extracted_into)
    check("an archive with one folder in it does not get another",
          test_an_archive_with_one_folder_in_it_does_not_get_another)

    print("thumbnails")
    check("a thumbnail is cached where the desktop can find it",
          test_a_thumbnail_is_cached_where_the_rest_of_the_desktop_can_find_it)
    check("a file that cannot be thumbnailed is only tried once",
          test_a_file_that_cannot_be_thumbnailed_is_only_tried_once)
    check("nothing enormous is decoded for a tile",
          test_nothing_enormous_is_decoded_for_a_tile)

    print("git")
    needs("git", "git status marks a folder with what is under it",
          test_git_status_marks_a_folder_with_what_is_under_it)
    check("git codes reduce to one letter each",
          test_git_codes_reduce_to_one_letter_each)

    print("the trash")
    if not in_home:
        why = "the scratch directory is not on a real filesystem"
        skip("the trash takes a file and gives it back", why)
        skip("restoring over something newer refuses", why)
    else:
        where = tree()
        for name, fn in (
            ("the trash takes a file and gives it back",
             test_the_trash_takes_a_file_and_gives_it_back),
            ("restoring over something newer refuses",
             test_restoring_over_something_newer_refuses),
        ):
            try:
                fn(where)
                print(f"  ok  {name}")
            except RuntimeError as exc:
                skip(name, str(exc))

    print("the palette and the look")
    check("the palette finds things by their initials",
          test_the_palette_finds_things_by_their_initials)
    check("a clipboard payload is read the way everything else writes it",
          test_a_clipboard_payload_is_read_the_way_every_other_file_manager_writes_it)
    check("a folder opened from the desktop arrives as a URI",
          test_a_folder_opened_from_the_desktop_arrives_as_a_uri)
    check("the stylesheet parses in both themes",
          test_the_stylesheet_parses_in_both_themes)
    check("the window is layered from solid to sheer",
          test_the_window_is_layered_from_solid_to_sheer)
    check("the glass is really switched off when it is switched off",
          test_the_glass_is_really_switched_off_when_it_is_switched_off)

    print("the file chooser portal")
    check("the portal reads what it is handed",
          test_the_portal_reads_what_it_is_handed)
    check("the filter matches the way a person would expect",
          test_the_portal_filter_matches_the_way_a_person_would_expect)
    check("the portal answers in the types the bus declared",
          test_the_portal_answers_in_the_types_the_bus_declared)
    check("the portal takes its bus name before it loads the toolkit",
          test_the_portal_takes_its_bus_name_before_it_loads_the_toolkit)
    check("the portal serves before the toolkit is loaded",
          test_the_portal_serves_before_the_toolkit_is_loaded)

    print("the window")
    from gi.repository import Adw, Gio, GLib

    Adw.init()
    app = Adw.Application(application_id="com.robin.HermioneTests",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.config = configmod.Config(os.path.join(scratch, "window.json"))
    app.config["restore_session"] = False
    style.install(app.config, True)

    def window_tests():
        for name, fn in (
            ("the window fits in a tiling column",
             test_the_window_fits_in_a_tiling_column),
            ("the window opens a folder and lists it",
             test_the_window_opens_a_folder_and_lists_it),
            ("tabs, and two panes side by side", test_tabs_and_two_panes),
            ("every shortcut points at an action that exists",
             test_every_shortcut_points_at_an_action_that_exists),
            ("the palette offers the window it is in",
             test_the_palette_offers_the_window_it_is_in),
            ("activating a file does not reach the desktop",
             test_activating_a_file_does_not_reach_the_desktop),
            ("a folder opens in the pane rather than anywhere else",
             test_a_folder_opens_in_the_pane_rather_than_anywhere_else),
            ("the sidebar lists somewhere to go",
             test_the_sidebar_lists_somewhere_to_go),
            ("there is one toolbar and not two",
             test_there_is_one_toolbar_and_not_two),
            ("a crumb survives being pressed",
             test_a_crumb_survives_being_pressed),
            ("the window is three cards on a slab",
             test_the_window_is_three_cards_on_a_slab),
            ("every dialog builds", test_every_dialog_builds),
            ("a URI on the command line opens the right folder",
             test_a_uri_on_the_command_line_opens_the_right_folder),
        ):
            try:
                fn(app)
                print(f"  ok  {name}")
            except BaseException as exc:          # noqa: BLE001 - re-raised below
                failures.append(exc)
                print(f"  FAIL  {name}: {exc}")
        app.release()
        app.quit()
        return GLib.SOURCE_REMOVE

    def activate(instance):
        # Without the hold there is no main loop left for the timeout.
        instance.hold()
        # The window actions need the application's own ones to exist.
        for name in ("quit", "new-window"):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_a: None)
            instance.add_action(action)
        GLib.timeout_add(200, window_tests)

    app.connect("activate", activate)
    app.run([])
    if failures:
        raise failures[0]

    if skipped:
        print("\nskipped:")
        for name, why in skipped:
            print(f"  {name}  ({why})")
    print("all good")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
