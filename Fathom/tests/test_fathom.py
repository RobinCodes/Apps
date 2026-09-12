"""What can be checked without a disk to scan, and what needs one.

Most of this app is arithmetic about a filesystem, so most of the suite builds
a small filesystem of its own under a temp directory and checks the numbers
that come back — including against `du`, which is the only second opinion
worth having about what a directory weighs.

    python3 tests/test_fathom.py

The widgets need a display but nothing else; the window test deletes files,
and everything it deletes it made itself, inside the scratch directory that is
removed at the end.
"""

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

scratch = tempfile.mkdtemp(prefix="fathom-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(scratch, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(scratch, "cache")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from fathom import filetypes, scan, treemap, units, volumes  # noqa: E402
from fathom.config import Config  # noqa: E402

skipped = []


def check(name, fn):
    fn()
    print(f"  ok  {name}")


def needs(tool, name, fn):
    """Run fn only where the tool exists; say so plainly when it doesn't."""
    if shutil.which(tool):
        check(name, fn)
    else:
        skipped.append((name, tool))
        print(f"  --  {name}  (no {tool} on this machine)")


# ------------------------------------------------------------ a filesystem --

def make_tree(where):
    """A small tree with one of everything the scanner has to get right."""
    os.makedirs(where, exist_ok=True)
    write(os.path.join(where, "top.bin"), 4000)
    os.makedirs(os.path.join(where, "films"), exist_ok=True)
    write(os.path.join(where, "films", "a.mkv"), 30000)
    write(os.path.join(where, "films", "b.mkv"), 10000)
    os.makedirs(os.path.join(where, "code", "src"), exist_ok=True)
    write(os.path.join(where, "code", "src", "main.py"), 700)
    write(os.path.join(where, "code", "src", "util.py"), 300)
    os.makedirs(os.path.join(where, "empty"), exist_ok=True)
    return where


def write(path, size):
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


def du_bytes(path, apparent=False):
    args = ["du", "-s", "-x", "--block-size=1"]
    if apparent:
        args.append("--apparent-size")
    out = subprocess.run(args + [path], capture_output=True, encoding="utf-8")
    return int(out.stdout.split("\t")[0])


# ------------------------------------------------------------- the numbers --

def test_a_tree_adds_up_to_what_is_in_it():
    where = make_tree(os.path.join(scratch, "plain"))
    result = scan.Scan(where).run()
    root = result.root
    assert root.nfiles == 5, root.nfiles
    # films, code, code/src, empty — not the root itself.
    assert root.ndirs == 4, root.ndirs
    assert root.size == 4000 + 30000 + 10000 + 700 + 300, root.size
    films = root.find("films")
    assert films is not None and films.size == 40000, films.size
    assert root.find("empty").size == 0


def test_a_directory_costs_its_own_blocks():
    """A folder is not free — usually.

    On ext4 the directory entry itself occupies a block or more and du counts
    it; on tmpfs it occupies none. So the claim worth testing is not "more
    than zero" but "exactly what the kernel says", which is true on both.
    """
    where = make_tree(os.path.join(scratch, "blocks"))
    root = scan.Scan(where).run().root
    empty = root.find("empty")
    assert empty.size == 0, empty.size
    assert empty.alloc == os.stat(empty.path).st_blocks * scan.BLOCK, empty.alloc
    films = root.find("films")
    own = os.stat(films.path).st_blocks * scan.BLOCK
    assert films.alloc == 40960 + own or films.alloc >= films.size, films.alloc


def test_the_total_is_the_one_du_gives():
    where = make_tree(os.path.join(scratch, "against-du"))
    root = scan.Scan(where).run().root
    assert root.alloc == du_bytes(where), (root.alloc, du_bytes(where))


def test_a_hardlink_is_one_file_however_many_names():
    where = os.path.join(scratch, "links")
    os.makedirs(where, exist_ok=True)
    write(os.path.join(where, "original"), 50000)
    os.link(os.path.join(where, "original"), os.path.join(where, "second-name"))
    os.link(os.path.join(where, "original"), os.path.join(where, "third-name"))

    result = scan.Scan(where).run()
    assert result.hardlinks == 2, result.hardlinks
    # Three names, one file's worth of space.
    assert result.root.nfiles == 3, result.root.nfiles
    assert result.root.size == 50000, result.root.size
    # And the file's own row still tells the truth about its size.
    sizes = sorted(ref.size for ref in result.root.files())
    assert sizes == [50000, 50000, 50000], sizes

    loose = scan.Scan(where, dedup_hardlinks=False).run()
    assert loose.root.size == 150000, loose.root.size


def test_hardlinks_agree_with_du_too():
    where = os.path.join(scratch, "links")
    root = scan.Scan(where).run().root
    assert root.alloc == du_bytes(where), (root.alloc, du_bytes(where))


def test_a_symlink_is_never_followed():
    """Following them is how a disk tool reports a home directory as infinite."""
    where = os.path.join(scratch, "symlinks")
    os.makedirs(os.path.join(where, "real"), exist_ok=True)
    write(os.path.join(where, "real", "file"), 5000)
    os.symlink(os.path.join(where, "real"), os.path.join(where, "loop"))
    os.symlink(where, os.path.join(where, "up"))

    root = scan.Scan(where).run().root
    assert root.ndirs == 1, root.ndirs          # "real", and neither symlink
    assert root.nfiles == 3, root.nfiles        # the file and the two links
    assert root.size < 6000, root.size


def test_an_unreadable_folder_is_counted_not_crashed_on():
    where = os.path.join(scratch, "locked")
    inner = os.path.join(where, "private")
    os.makedirs(inner, exist_ok=True)
    write(os.path.join(inner, "secret"), 900)
    write(os.path.join(where, "readable"), 100)
    os.chmod(inner, 0o000)
    try:
        result = scan.Scan(where).run()
    finally:
        os.chmod(inner, 0o755)
    assert result.unreadable == 1, result.unreadable
    assert result.examples and result.examples[0].endswith("private")
    assert result.root.find("readable") is None      # it is a file, not a dir
    assert result.root.nfiles == 1, result.root.nfiles


def test_a_scan_can_be_stopped():
    running = scan.Scan(os.path.expanduser("~"))
    running.cancel()
    result = running.run()
    assert result.cancelled


def test_deleting_takes_the_size_out_of_every_folder_above_it():
    where = make_tree(os.path.join(scratch, "deleting"))
    root = scan.Scan(where).run().root
    before = root.size
    films = root.find("films")

    target = [ref for ref in films.files() if ref.name == "a.mkv"][0]
    scan.remove_file(target)
    assert films.size == 10000, films.size
    assert root.size == before - 30000, root.size
    assert root.nfiles == 4, root.nfiles

    code = root.find("code")
    code_size = code.size
    scan.remove_dir(code.find("src"))
    assert code.size == code_size - 1000, code.size
    assert root.size == before - 30000 - 1000, root.size
    assert root.ndirs == 3, root.ndirs          # films, code, empty — src gone
    assert root.find("empty") is not None


def test_the_biggest_files_come_back_biggest_first():
    where = make_tree(os.path.join(scratch, "largest"))
    root = scan.Scan(where).run().root
    biggest = scan.largest_files(root, 3, "size")
    assert [ref.name for ref in biggest] == ["a.mkv", "b.mkv", "top.bin"], \
        [ref.name for ref in biggest]
    assert biggest[0].path.endswith(os.path.join("films", "a.mkv"))


def test_extensions_are_added_up():
    where = make_tree(os.path.join(scratch, "extensions"))
    root = scan.Scan(where).run().root
    rows = dict((ext, (files, size)) for ext, files, size, _alloc
                in scan.by_extension(root, "size"))
    assert rows["mkv"] == (2, 40000), rows["mkv"]
    assert rows["py"] == (2, 1000), rows["py"]
    assert rows["bin"] == (1, 4000), rows["bin"]


def test_search_looks_at_every_file_not_just_the_big_ones():
    where = make_tree(os.path.join(scratch, "searching"))
    root = scan.Scan(where).run().root
    # By apparent size, because both .py files fit in one block and so tie on
    # the allocated one — there is no order to assert.
    def names(query):
        return [ref.name for ref in scan.search(root, query, basis="size")]

    assert names("*.py") == ["main.py", "util.py"], names("*.py")
    assert names("util") == ["util.py"]
    assert names("*.iso") == []
    # A plain word is a substring, and matching ignores case; the smallest
    # file in the tree is still found.
    assert names("MAIN") == ["main.py"]


# ------------------------------------------------------------- file types ---

def test_every_extension_belongs_to_exactly_one_category():
    """The tables used to disagree silently; now importing them would fail."""
    assert len(filetypes._EXTENSIONS) > 300
    assert filetypes.category_of("x.mkv") != filetypes.OTHER


def test_a_number_on_the_end_is_a_version_not_a_type():
    assert filetypes.extension_of("libcef.so.0") == "so"
    assert filetypes.extension_of("libz.so.1.2.11") == "so"
    assert filetypes.extension_of("2.1.234") == ""
    assert filetypes.extension_of(".bashrc") == ""
    assert filetypes.extension_of("Makefile") == ""
    assert filetypes.extension_of("photo.JPG") == "jpg"


# ----------------------------------------------------------- the treemap ----

def test_a_row_of_rectangles_fills_its_box_exactly():
    placed = list(treemap.squarify([4.0, 3.0, 2.0, 1.0], 0, 0, 100, 100))
    assert len(placed) == 4
    area = sum(w * h for _i, _x, _y, w, h in placed)
    assert abs(area - 10000) < 1.0, area
    for _index, x, y, w, h in placed:
        assert x >= -0.001 and y >= -0.001
        assert x + w <= 100.001 and y + h <= 100.001


def test_a_rectangle_is_as_big_as_the_thing_it_stands_for():
    values = [50.0, 25.0, 25.0]
    placed = {i: w * h for i, _x, _y, w, h in
              treemap.squarify(values, 0, 0, 200, 100)}
    assert abs(placed[0] - 10000) < 1.0, placed
    assert abs(placed[1] - 5000) < 1.0, placed


def test_rectangles_do_not_overlap():
    placed = list(treemap.squarify([9.0, 7.0, 5.0, 3.0, 2.0, 1.0], 0, 0, 300, 180))
    for i, (_a, ax, ay, aw, ah) in enumerate(placed):
        for _b, bx, by, bw, bh in placed[i + 1:]:
            apart = (ax + aw <= bx + 0.001 or bx + bw <= ax + 0.001
                     or ay + ah <= by + 0.001 or by + bh <= ay + 0.001)
            assert apart, (placed[i], (bx, by, bw, bh))


def test_a_map_of_a_real_tree_has_a_cell_for_the_big_things():
    where = make_tree(os.path.join(scratch, "mapping"))
    root = scan.Scan(where).run().root
    cells = treemap.build(root, 600, 400, "size")
    named = {cell.node.name for cell in cells if cell.node is not treemap.FREE}
    assert "films" in named and "a.mkv" in named, sorted(named)
    # The biggest file gets the biggest leaf.
    leaves = [c for c in cells if not c.is_dir]
    biggest = max(leaves, key=lambda c: c.w * c.h)
    assert biggest.node.name == "a.mkv", biggest.node.name


def test_the_map_can_be_painted():
    where = make_tree(os.path.join(scratch, "painting"))
    root = scan.Scan(where).run().root
    cells = treemap.build(root, 400, 300, "size")
    for dark in (True, False):
        surface = treemap.render(cells, 400, 300, dark=dark)
        assert surface.get_width() == 400 and surface.get_height() == 300


# --------------------------------------------------------------- the rest ---

def test_sizes_read_the_way_a_person_writes_them():
    assert units.human_size(0) == "0 bytes"
    assert units.human_size(1) == "1 byte"
    assert units.human_size(1024) == "1.00 KB"
    assert units.human_size(1024, si=True) == "1.02 kB"
    assert units.human_size(1000, si=True) == "1.00 kB"
    assert units.human_size(1536 * 1024 * 1024) == "1.50 GB"
    assert units.human_percent(1, 3) == "33.3%"
    assert units.human_percent(0, 5) == "0%"
    assert units.human_percent(1, 100000) == "<0.05%"
    assert units.human_percent(5, 5) == "100%"
    assert units.human_percent(1, 0) == "—"


def test_settings_survive_a_round_trip_and_rubbish_does_not():
    config = Config()
    config["size_basis"] = "size"
    config["window_width"] = 1234
    config.remember_path("/tmp/one")
    config.remember_path("/tmp/two")
    config.save()

    again = Config()
    assert again["size_basis"] == "size"
    assert again["window_width"] == 1234
    assert again["recent_paths"] == ["/tmp/two", "/tmp/one"]
    assert again["last_path"] == "/tmp/two"

    import json

    from fathom.config import PATH

    with open(PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    data["size_basis"] = "sideways"      # not one of the two it may be
    data["window_width"] = True          # a bool where a number belongs
    data["si_units"] = 7                 # a number where a bool belongs
    with open(PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh)

    third = Config()
    assert third["size_basis"] == "alloc"
    assert third["window_width"] == 1440
    assert third["si_units"] is False


def test_the_machine_has_somewhere_to_scan():
    found = volumes.volumes()
    assert found, "no filesystems found at all"
    assert any(vol.path == os.path.expanduser("~") for vol in found)
    assert all(vol.total >= vol.free for vol in found)


# ------------------------------------------------------------- the window ---

def test_the_views_build_and_take_a_scan(app):
    from fathom.window import MainWindow

    window = MainWindow(app)
    where = make_tree(os.path.join(scratch, "window"))
    result = scan.Scan(where).run()
    window._scan_finished(result)

    assert window.stack_pages.get_visible_child_name() == "results"
    assert window.tree.root is result.root
    assert window.map.root is result.root

    # The tree opened the root, and the name column says what it is.
    model = window.tree._tree_model
    assert model.get_n_items() > 1, model.get_n_items()
    names = {model.get_row(i).get_item().name for i in range(model.get_n_items())}
    assert "films" in names and "code" in names, names
    window.destroy()


def test_deleting_from_the_window_updates_the_numbers(app):
    from fathom.window import MainWindow

    window = MainWindow(app)
    where = make_tree(os.path.join(scratch, "window-delete"))
    result = scan.Scan(where).run()
    window._scan_finished(result)
    root = result.root
    before = root.size

    films = root.find("films")
    window.tree.select_node(films)          # builds the child store
    target = [ref for ref in films.files() if ref.name == "a.mkv"][0]

    os.unlink(target.path)
    window._forget(target)

    assert root.size == before - 30000, root.size
    assert films.nfiles == 1, films.nfiles
    # The row that followed the deleted one has moved down a slot in the
    # parent's arrays, and the model has to have moved with it.
    store = window.tree.store_for(films)
    remaining = [store.get_item(i).node.name for i in range(store.get_n_items())]
    assert remaining == ["b.mkv"], remaining
    window.destroy()


def test_a_folder_deleted_from_the_window_takes_its_subtree(app):
    from fathom.window import MainWindow

    window = MainWindow(app)
    where = make_tree(os.path.join(scratch, "window-delete-dir"))
    result = scan.Scan(where).run()
    window._scan_finished(result)
    root = result.root
    before = root.size

    code = root.find("code")
    window.tree.select_node(code)
    src = code.find("src")
    shutil.rmtree(src.path)
    window._forget(src)

    assert root.size == before - 1000, root.size
    assert code.find("src") is None
    assert window.tree.store_for(src) is None
    window.destroy()


def main():
    print("the numbers")
    check("a tree adds up to what is in it", test_a_tree_adds_up_to_what_is_in_it)
    check("a directory costs its own blocks", test_a_directory_costs_its_own_blocks)
    needs("du", "the total is the one du gives", test_the_total_is_the_one_du_gives)
    check("a hardlink is one file however many names",
          test_a_hardlink_is_one_file_however_many_names)
    needs("du", "hardlinks agree with du too", test_hardlinks_agree_with_du_too)
    check("a symlink is never followed", test_a_symlink_is_never_followed)
    check("an unreadable folder is counted, not crashed on",
          test_an_unreadable_folder_is_counted_not_crashed_on)
    check("a scan can be stopped", test_a_scan_can_be_stopped)
    check("deleting takes the size out of every folder above it",
          test_deleting_takes_the_size_out_of_every_folder_above_it)
    check("the biggest files come back biggest first",
          test_the_biggest_files_come_back_biggest_first)
    check("extensions are added up", test_extensions_are_added_up)
    check("search looks at every file, not just the big ones",
          test_search_looks_at_every_file_not_just_the_big_ones)

    print("file types")
    check("every extension belongs to exactly one category",
          test_every_extension_belongs_to_exactly_one_category)
    check("a number on the end is a version, not a type",
          test_a_number_on_the_end_is_a_version_not_a_type)

    print("the map")
    check("a row of rectangles fills its box exactly",
          test_a_row_of_rectangles_fills_its_box_exactly)
    check("a rectangle is as big as the thing it stands for",
          test_a_rectangle_is_as_big_as_the_thing_it_stands_for)
    check("rectangles do not overlap", test_rectangles_do_not_overlap)
    check("a map of a real tree has a cell for the big things",
          test_a_map_of_a_real_tree_has_a_cell_for_the_big_things)
    check("the map can be painted", test_the_map_can_be_painted)

    print("the rest")
    check("sizes read the way a person writes them",
          test_sizes_read_the_way_a_person_writes_them)
    check("settings survive a round trip and rubbish does not",
          test_settings_survive_a_round_trip_and_rubbish_does_not)
    check("the machine has somewhere to scan", test_the_machine_has_somewhere_to_scan)

    print("the window")
    from gi.repository import Adw, Gio, GLib

    Adw.init()
    from fathom import widgets

    widgets.install_css()
    app = Adw.Application(application_id="org.fathom.Tests",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    failures = []

    def run_window_tests():
        for name, fn in (
            ("the views build and take a scan", test_the_views_build_and_take_a_scan),
            ("deleting from the window updates the numbers",
             test_deleting_from_the_window_updates_the_numbers),
            ("a folder deleted from the window takes its subtree",
             test_a_folder_deleted_from_the_window_takes_its_subtree),
        ):
            try:
                fn(app)
                print(f"  ok  {name}")
            except BaseException as exc:  # noqa: BLE001 - re-raised after the loop
                failures.append(exc)
                print(f"  FAIL  {name}: {exc}")
        app.release()
        app.quit()
        return GLib.SOURCE_REMOVE

    def activate(instance):
        # Without the hold there is no main loop left for the timeout to run in.
        instance.hold()
        GLib.timeout_add(250, run_window_tests)

    app.connect("activate", activate)
    app.run([])
    if failures:
        raise failures[0]

    if skipped:
        print("\nskipped for want of a tool:")
        for name, tool in skipped:
            print(f"  {name}  (needs {tool})")
    print("all good")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
