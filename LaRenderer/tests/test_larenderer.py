"""What can be checked without a TeX distribution, and what needs one.

Most of this app's difficulty is in reading an engine log, and a log is a
string — so the parsing half runs anywhere, against captured text, and is the
part worth having most. The compiling half needs a real engine and is skipped
with a note when there isn't one, rather than failing on a machine that never
claimed to have TeX. The widgets need a display but no engine.

    python3 tests/test_larenderer.py

Everything it makes lives under one temp directory and is removed at the end.
"""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

scratch = tempfile.mkdtemp(prefix="larenderer-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(scratch, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(scratch, "cache")
# An app built by a test must not go to the network for an update.
os.environ["APPS_NO_UPDATE"] = "1"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from larenderer import compiler, packages, project, templates, widgets, winenv  # noqa: E402

skipped = []


def check(name, fn):
    fn()
    print(f"  ok  {name}")


def needs(tool, name, fn):
    """Run fn only where the tool exists; say so plainly when it doesn't."""
    if winenv.which(tool):
        check(name, fn)
    else:
        skipped.append((name, tool))
        print(f"  --  {name}  (no {tool} on this machine)")


# ----------------------------------------------------------- reading a log --

def test_an_error_is_found_with_its_line():
    msgs = compiler.parse_log("doc.tex:4: Undefined control sequence.", "doc")
    assert len(msgs) == 1, msgs
    assert msgs[0].severity == "error"
    assert msgs[0].line == 4
    assert msgs[0].text == "Undefined control sequence."
    # Its own file is blank, which is what tells the editor the line is a line
    # in the buffer the user is looking at.
    assert msgs[0].file == ""


def test_the_job_is_recognised_however_the_engine_spells_it():
    """TeX Live echoes the relative path it was handed, and the separator is
    the platform's. All three of these are the document itself."""
    for spelling in ("doc.tex:4: Undefined control sequence.",
                     "./doc.tex:4: Undefined control sequence.",
                     ".\\doc.tex:4: Undefined control sequence."):
        msgs = compiler.parse_log(spelling, "doc")
        assert msgs and msgs[0].file == "", (spelling, msgs)
    # A genuinely different file keeps its name, so the jump goes elsewhere.
    other = compiler.parse_log("chapters/intro.tex:9: Runaway argument.", "doc")
    assert other[0].file == "chapters/intro.tex", other


def test_a_bang_error_looks_ahead_for_its_line():
    """"! LaTeX Error" carries no file:line; the number arrives several lines
    later as "l.42", and without the look-ahead the error has no line at all."""
    log = "\n".join([
        "! LaTeX Error: \\begin{itemize} on input line 7 ended by \\end{document}.",
        "",
        "See the LaTeX manual or LaTeX Companion for explanation.",
        "Type  H <return>  for immediate help.",
        " ...",
        "l.12 \\end{document}",
    ])
    msgs = compiler.parse_log(log, "doc")
    assert msgs[0].severity == "error", msgs
    assert msgs[0].line == 12, msgs[0]


def test_a_badbox_carries_its_first_line():
    log = "Overfull \\hbox (12.34pt too wide) in paragraph at lines 30--32"
    msgs = compiler.parse_log(log, "doc")
    assert msgs[0].severity == "badbox", msgs
    assert msgs[0].line == 30
    assert "Overfull" in msgs[0].text and "hbox" in msgs[0].text


def test_a_package_warning_keeps_its_package_and_line():
    log = "Package hyperref Warning: Difference detected on input line 88."
    msgs = compiler.parse_log(log, "doc")
    assert msgs[0].severity == "warning", msgs
    assert msgs[0].package == "hyperref", msgs[0]
    assert msgs[0].line == 88


def test_the_noise_of_a_healthy_document_is_dropped():
    """A warning that is true of every working document is not a complaint."""
    log = "\n".join([
        "LaTeX Font Warning: Font shape `OT1/cmr/bx/sc' undefined",
        "LaTeX Warning: There were undefined references.",
        "Package epstopdf Warning: Shell escape feature is not enabled.",
    ])
    assert compiler.parse_log(log, "doc") == []


def test_the_same_complaint_is_only_made_once():
    """Three passes over the document means three copies of every message."""
    once = "doc.tex:4: Undefined control sequence."
    assert len(compiler.parse_log("\n".join([once] * 3), "doc")) == 1


def test_errors_sort_before_warnings_before_badboxes():
    log = "\n".join([
        "Overfull \\hbox (1.0pt too wide) in paragraph at lines 5--6",
        "Package hyperref Warning: Something on input line 4.",
        "doc.tex:99: Undefined control sequence.",
    ])
    assert [m.severity for m in compiler.parse_log(log, "doc")] == \
        ["error", "warning", "badbox"]


def test_a_missing_package_is_named():
    # A package is named the way \usepackage names it, without the extension,
    # because that is what the install hint has to say back to the user.
    log = "! LaTeX Error: File `siunitx.sty' not found."
    assert compiler._missing_packages(log) == ["siunitx"]
    # Anything that is not a package is a missing input the user has to supply.
    graphic = "! LaTeX Error: File `diagram.png' not found."
    assert compiler._missing_files(graphic) == ["diagram.png"]
    assert compiler._missing_files(log) == [], "a .sty is not a missing input"


# ------------------------------------------------------- build bookkeeping --

def test_a_document_keeps_its_own_build_directory():
    """Keyed by the absolute source path, so reopening a file reuses its .aux
    and cross-references resolve on the first compile rather than the second."""
    one = compiler.build_dir(os.path.join(scratch, "one.tex"))
    two = compiler.build_dir(os.path.join(scratch, "two.tex"))
    assert one != two
    assert one == compiler.build_dir(os.path.join(scratch, "one.tex")), "stable"
    assert os.path.isdir(one)
    # An unsaved buffer still gets somewhere to build.
    assert os.path.isdir(compiler.build_dir(None))


def test_a_job_name_is_safe_for_tex():
    assert compiler.job_name("/tmp/paper.tex") == "paper"
    assert compiler.job_name(None) == "untitled"
    # TeX chokes on spaces and shell-active characters in a jobname.
    assert compiler.job_name("/tmp/my paper (final).tex") == "my_paper__final"
    assert " " not in compiler.job_name("/tmp/a b c.tex")
    # A name with nothing usable left in it still has to produce something the
    # engine will accept as a jobname.
    assert compiler.job_name("/tmp/___.tex") == "untitled"
    assert compiler.job_name("/tmp/你好.tex") == "untitled", "non-ASCII is not a jobname"


def test_clear_build_removes_the_directory():
    path = compiler.build_dir(os.path.join(scratch, "throwaway.tex"))
    with open(os.path.join(path, "junk.aux"), "w", encoding="utf-8") as fh:
        fh.write("x")
    compiler.clear_build(os.path.join(scratch, "throwaway.tex"))
    assert not os.path.exists(os.path.join(path, "junk.aux"))


# -------------------------------------------------------------- the source --

def test_packages_are_read_out_of_the_preamble():
    text = ("\\documentclass[11pt]{article}\n"
            "\\usepackage{amsmath, amssymb}\n"
            "\\usepackage[utf8]{inputenc}\n")
    assert packages.document_class(text) == "article"
    assert packages.loaded_by(text) == {"amsmath", "amssymb", "inputenc"}
    assert packages.document_class("no class here") == ""
    assert packages.loaded_by("") == set()


def test_every_template_is_a_whole_document():
    assert templates.TEMPLATES, "there should be templates to offer"
    for name, description, body in templates.TEMPLATES:
        assert name and description, name
        assert "\\documentclass" in body, name
        assert "\\begin{document}" in body and "\\end{document}" in body, name
        # The cursor marker is removed on insertion; it must not reach a file.
        assert body.count(templates.CURSOR) <= 1, name


def test_every_snippet_is_balanced():
    for group, items in templates.SNIPPETS:
        assert group and items, group
        for label, body in items:
            assert label, group
            assert body.count("{") == body.count("}"), (group, label)


# ---------------------------------------------------------------- folders --
#
# A folder is one document and its parts. Which file is "the document" is the
# question everything else rests on, and it is answered from text alone.

def make_project(name="thesis"):
    """main.tex with a chapter it \\inputs, one it \\includes, and a .bib."""
    root = os.path.join(scratch, name)
    os.makedirs(os.path.join(root, "chapters"), exist_ok=True)
    files = {
        "main.tex": ("\\documentclass{article}\n\\begin{document}\n"
                     "\\input{chapters/intro}\n\\include{chapters/two}\n"
                     "\\bibliographystyle{plain}\n\\bibliography{refs}\n\\end{document}\n"),
        "chapters/intro.tex": "Intro on disk.\n",
        "chapters/two.tex": "% !TEX root = ../main.tex\nChapter two.\n",
        "refs.bib": "@misc{x, title={X}}\n",
        # A whole document of its own that happens to live in the folder.
        "figure.tex": "\\documentclass{standalone}\n\\begin{document}x\\end{document}\n",
    }
    for rel, text in files.items():
        with open(os.path.join(root, rel), "w", encoding="utf-8") as fh:
            fh.write(text)
    return root


def test_the_main_document_is_found():
    root = make_project("found")
    assert project.find_main(root) == os.path.join(root, "main.tex")
    # main.tex wins over a file named after the folder, the way Overleaf
    # names things; and with no main.tex, the folder's namesake does.
    os.rename(os.path.join(root, "main.tex"), os.path.join(root, "found.tex"))
    assert project.find_main(root) == os.path.join(root, "found.tex")
    empty = os.path.join(scratch, "no-documents")
    os.makedirs(empty, exist_ok=True)
    assert project.find_main(empty) is None


def test_a_part_knows_what_it_is_part_of():
    root = make_project("parts")
    intro = os.path.join(root, "chapters", "intro.tex")
    main = os.path.join(root, "main.tex")
    assert not project.is_standalone("Intro on disk.\n")
    assert project.is_standalone("% preamble\n\\documentclass{article}\n")
    assert not project.is_standalone("% \\documentclass{article}\n"), "a comment is not a class"
    assert project.root_directive("% !TEX root = ../main.tex\nx") == "../main.tex"
    assert project.root_directive("%!TeX root=main.tex\n") == "main.tex"
    assert project.find_including_root(intro) == main
    assert project.find_including_root(os.path.join(root, "refs.bib")) == main
    assert project.included_dirs("\\include{chapters/two}\n\\include{end}") == ["chapters"]


def test_overleaf_links_are_read():
    project_id = "64b1c2d3e4f5a6b7c8d9e0f1"
    expected = (f"https://git.overleaf.com/{project_id}", project_id)
    for link in (f"https://www.overleaf.com/project/{project_id}",
                 f"https://git.overleaf.com/{project_id}", project_id.upper()):
        assert project.overleaf_git_url(link) == expected, link
    own = f"https://latex.example.edu/git/{project_id}"
    assert project.overleaf_git_url(own) == (own, project_id), "a self-hosted address is kept"
    assert project.overleaf_git_url("https://www.overleaf.com/read/abc") is None
    assert project.overleaf_git_url("") is None


def test_overleaf_addresses_are_understood():
    from larenderer import overleafweb

    project_id = "64b1c2d3e4f5a6b7c8d9e0f1"
    editor = f"https://www.overleaf.com/project/{project_id}"
    assert overleafweb.web_url_for_remote(f"https://git.overleaf.com/{project_id}") == editor
    assert overleafweb.web_url_for_remote(f"https://tex.example.edu/git/{project_id}") == \
        f"https://tex.example.edu/project/{project_id}"
    assert overleafweb.web_url_for_remote("https://github.com/someone/thesis.git") is None
    assert overleafweb.project_id_of(editor + "/detached") == project_id
    assert overleafweb.project_id_of("https://www.overleaf.com/project") is None
    assert overleafweb.is_overleaf("https://www.overleaf.com/learn")
    assert not overleafweb.is_overleaf("https://accounts.google.com/o/oauth2")
    assert not overleafweb.is_overleaf("file:///tmp/x.html", "file:///tmp/y.html")
    # The Ctrl+A workaround has to be in the environment before any web
    # process starts, which is why it is set when the module is imported.
    assert os.environ.get("WEBKIT_A11Y_BUS_ADDRESS") or os.environ.get("LARENDERER_ACCESSIBILITY")


def test_text_is_told_from_binary():
    text = os.path.join(scratch, ".latexmkrc")
    binary = os.path.join(scratch, "figure.png")
    with open(text, "w", encoding="utf-8") as fh:
        fh.write("$pdf_mode = 1; # caf\u00e9\n")
    with open(binary, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + bytes(range(256)))
    assert project.looks_like_text(text)
    assert not project.looks_like_text(binary)


# ---------------------------------------------------------- with an engine --

def test_a_real_document_compiles_to_a_pdf():
    source = os.path.join(scratch, "good.tex")
    text = ("\\documentclass{article}\n\\begin{document}\n"
            "Hello, world.\n\\end{document}\n")
    with open(source, "w", encoding="utf-8") as fh:
        fh.write(text)
    result = compiler.compile_document(text, source, timeout=120)
    assert not result.failure, result.failure
    assert result.pdf and os.path.getsize(result.pdf) > 0, result.log[-800:]
    assert result.ok, [m.text for m in result.messages]
    assert result.pages >= 1, result.pages


def test_a_broken_document_reports_the_line_it_broke_on():
    source = os.path.join(scratch, "broken.tex")
    text = ("\\documentclass{article}\n\\begin{document}\n"
            "Fine.\n\\notacommand\nAlso fine.\n\\end{document}\n")
    with open(source, "w", encoding="utf-8") as fh:
        fh.write(text)
    result = compiler.compile_document(text, source, timeout=120)
    errors = [m for m in result.messages if m.severity == "error"]
    assert errors, result.log[-800:]
    assert errors[0].line == 4, errors[0]
    assert errors[0].file == "", "the error is in the buffer being edited"
    assert not result.ok, "a document with an error is not ok"


def test_unicode_in_the_source_survives_the_round_trip():
    """The buffer is written out as UTF-8 and the log is read back as UTF-8;
    neither step may go through the locale encoding."""
    source = os.path.join(scratch, "unicode.tex")
    text = ("\\documentclass{article}\n\\begin{document}\n"
            "Caf\u00e9 na\u00efve \u2014 stra\u00dfe.\n\\end{document}\n")
    with open(source, "w", encoding="utf-8") as fh:
        fh.write(text)
    result = compiler.compile_document(text, source, timeout=120)
    assert not result.failure, result.failure
    with open(os.path.join(compiler.build_dir(source),
                           compiler.job_name(source) + ".tex"),
              encoding="utf-8") as fh:
        assert "Caf\u00e9 na\u00efve \u2014 stra\u00dfe." in fh.read()


def test_a_part_compiles_through_its_main_with_its_unsaved_text():
    """The part's buffer goes into the build directory at its relative path,
    ahead of the file on disk — so an error typed into it, unsaved, is found
    by the engine and filed against the part, at the part's own line."""
    root = make_project("overlay")
    main = os.path.join(root, "main.tex")
    with open(main, encoding="utf-8") as fh:
        text = fh.read()
    result = compiler.compile_document(
        text, main, timeout=120,
        overlays={os.path.join("chapters", "intro.tex"): "Fine.\n\\notacommand\n"})
    errors = result.errors
    assert errors, result.log[-800:]
    assert compiler.message_source(result, errors[0]) == \
        os.path.join(root, "chapters", "intro.tex"), errors[0]
    assert errors[0].line == 2, errors[0]
    # \include wrote chapters/two.aux, which needs its directory made first.
    assert os.path.exists(os.path.join(result.build, "chapters", "two.aux"))

    # The tab closes: its copy must stop shadowing the file on disk.
    result = compiler.compile_document(text, main, timeout=120, overlays={})
    assert not os.path.exists(os.path.join(result.build, "chapters", "intro.tex"))
    assert not result.errors, [m.text for m in result.errors]


def test_pdf_info_reads_a_real_pdf():
    source = os.path.join(scratch, "good.tex")
    pdf = os.path.join(compiler.build_dir(source), compiler.job_name(source) + ".pdf")
    if not os.path.exists(pdf):
        raise AssertionError("the compile test should have left a pdf behind")
    pages, (width, height) = compiler.pdf_info(pdf)
    assert pages >= 1
    assert width > 100 and height > 100, (width, height)


# ------------------------------------------------------------------ widgets --

def test_widgets_build_without_a_document():
    from gi.repository import Gtk
    assert isinstance(widgets.pill("pdflatex"), Gtk.Widget)
    assert isinstance(widgets.small_button("Compile"), Gtk.Widget)
    assert isinstance(widgets.ProblemsList(lambda _m: None), Gtk.Widget)


def test_the_problems_list_takes_what_the_log_gave_it():
    """The list is fed straight from parse_log, so every shape that parses has
    to be one it can show — a bang error with no file among them."""
    messages = compiler.parse_log("\n".join([
        "doc.tex:4: Undefined control sequence.",
        "Package hyperref Warning: Something on input line 8.",
        "Overfull \\hbox (1.0pt too wide) in paragraph at lines 5--6",
        "! Emergency stop.",
    ]), "doc")
    assert len(messages) == 4, messages
    problems = widgets.ProblemsList(lambda _m: None)
    problems.set_messages(messages)
    problems.set_messages(messages, missing_hint="siunitx is not installed")
    problems.set_messages([])  # and it empties again without complaint


def test_zoom_stays_in_bounds_and_says_what_it_is():
    from larenderer.preview import Preview, ZOOM_MAX, ZOOM_MIN

    preview = Preview()
    preview.zoom_to(1.37)
    assert preview.zoom == 1.37 and preview.zoom_label() == "137%", preview.zoom
    preview.zoom_to(9)
    assert preview.zoom == ZOOM_MAX == 4.0
    preview.zoom_to(0.01)
    assert preview.zoom == ZOOM_MIN
    preview.set_zoom("fit-width")
    assert preview.zoom_label() == "Fit width"

    # A gesture only reports its result once, when it ends — not on each of
    # the dozens of steps a pinch is made of.
    told = []
    preview.on_zoom_changed = lambda: told.append(preview.zoom)
    preview.zoom_to(1.0)
    preview.begin_live_zoom()
    for step in (1.1, 1.3, 1.6):
        preview.live_zoom(step)
    assert told == [] and preview.zoom == 1.6
    preview.end_live_zoom()
    assert told == [1.6], told


def test_the_page_cache_is_bounded_by_size():
    """Sixty pages was a fine limit at 100%; at 400% it is gigabytes."""
    from gi.repository import Gdk, GLib

    from larenderer import preview as module

    budget = module.CACHE_BYTES
    module.CACHE_BYTES = 3 * 400 * 400 * 4
    try:
        pane = module.Preview()
        for n in range(1, 8):
            texture = Gdk.MemoryTexture.new(400, 400, Gdk.MemoryFormat.R8G8B8A8,
                                            GLib.Bytes.new(bytes(400 * 400 * 4)), 400 * 4)
            pane._remember((n, 96), texture)
        assert pane._cache_bytes <= module.CACHE_BYTES, pane._cache_bytes
        assert sorted(pane._cache) == [(5, 96), (6, 96), (7, 96)], sorted(pane._cache)
        pane.clear()
        assert pane._cache_bytes == 0 and not pane._cache
    finally:
        module.CACHE_BYTES = budget


# -------------------------------------------------------------------- tabs --

def test_tabs_in_a_real_window(app):
    """Several documents at once, in the window itself.

    Each tab is a whole document — its own buffer, its own rendered pages, its
    own build — so the things worth checking are that they stay separate and
    that the window's idea of "the" document follows whichever is in front.
    """
    from larenderer.window import MainWindow

    files = []
    for name, body in (("alpha", "First."), ("beta", "Second.")):
        path = os.path.join(scratch, name + ".tex")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\\documentclass{article}\n\\begin{document}\n"
                     f"{body}\n\\end{{document}}\n")
        files.append(path)

    window = MainWindow(app)
    names = lambda: [d.name for d in window.documents]

    # A window with nothing open still has somewhere to type.
    assert len(window.documents) == 1 and window.active is not None
    assert window.path is None

    # Opening the first file takes over that untouched tab rather than
    # leaving an empty one behind it.
    window.load_file(files[0])
    assert names() == ["alpha.tex"], names()

    window.load_file(files[1])
    assert names() == ["alpha.tex", "beta.tex"], names()
    assert window.active.name == "beta.tex"

    # The same file twice is one tab: two buffers over one path would race
    # each other to save.
    window.load_file(files[0])
    assert names() == ["alpha.tex", "beta.tex"], names()
    assert window.active.name == "alpha.tex", "and it selects the one already open"

    # The window's "current document" is whichever tab is in front.
    assert os.path.basename(window.path) == "alpha.tex"
    assert window.editor is window.active.editor
    assert window.preview is window.active.preview

    # Separate buffers, not one shared between tabs.
    first, second = window.documents[0], window.documents[1]
    assert "First." in first.editor.get_text()
    assert "Second." in second.editor.get_text()
    first.editor.set_text("changed\n")
    assert "Second." in second.editor.get_text(), "editing one must not touch the other"

    window.next_tab()
    assert window.active is second, window.active.name
    window.previous_tab()
    assert window.active is first
    # And it wraps, rather than stopping at the end.
    window.previous_tab()
    assert window.active is second

    window.new_document()
    assert len(window.documents) == 3 and window.active.path is None

    # Closing an unmodified tab needs no dialog and moves off it.
    window.active.editor.mark_saved()
    window.close_current()
    assert len(window.documents) == 2, names()
    assert window.active is not None and window.active.name in names()

    # Every document is remembered for next time, in tab order.
    window._remember_open_files()
    assert window.config["open_files"] == [d.path for d in window.documents]

    for document in window.documents:
        document.editor.mark_saved()   # so closing asks nothing
    window.close()


def test_a_folder_in_a_real_window(app):
    """Opening a folder opens its main document; a part compiles through it."""
    from gi.repository import GLib

    from larenderer.window import MainWindow

    root = make_project("window")
    window = MainWindow(app)

    def settle(until, seconds=30):
        import time
        deadline = time.monotonic() + seconds
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if until():
                return True
            time.sleep(0.02)
        return False

    window.open_folder(root)
    assert settle(lambda: window.active.path == os.path.join(root, "main.tex")), \
        window.active.path
    assert window.project_main() == os.path.join(root, "main.tex")
    assert window.tree.folder == root and window.config["open_folder"] == root

    intro = window.load_file(os.path.join(root, "chapters", "intro.tex"))
    assert window._root_for(intro) == os.path.join(root, "main.tex")
    figure = window.load_file(os.path.join(root, "figure.tex"))
    assert window._root_for(figure) == figure.path, "a whole document compiles as itself"
    rc = window.load_file(os.path.join(root, "refs.bib"))
    assert window._root_for(rc) == os.path.join(root, "main.tex")

    # A tab restored at start-up has compiled nothing yet. A compile landing
    # for another tab of the same document must pass it by, not trip on it —
    # and the tab widget, Document.root, must still be the widget.
    restored = window.open_document(path=os.path.join(root, "chapters", "two.tex"),
                                    text="Chapter two.\n", focus=False)
    assert restored.compiled_root is None and not isinstance(restored.root, str)

    if winenv.which("pdflatex"):
        assert settle(lambda: not any(d.compiling for d in window.documents), 60)
        intro.editor.set_text("Fine.\n\\notacommand\n")
        intro.editor.buffer.set_modified(True)
        window.compile_now(intro)
        assert settle(lambda: not intro.compiling and intro.result is not None
                      and intro.result.errors, 60), "the part's error came back"
        assert intro.editor._error_lines == {1}, intro.editor._error_lines
        # The other open part of the same document shows the same compile,
        # with the error in intro.tex's gutter and not in the .bib's.
        assert rc.result is intro.result, "refs.bib's tab was brought up to date"
        assert rc.editor._error_lines == set(), rc.editor._error_lines
        assert restored.result is None

    window.close_folder()
    assert window.tree.folder is None and window.config["open_folder"] == ""
    for document in window.documents:
        document.editor.mark_saved()
    window.close()


def test_zoom_in_a_real_window(app):
    """An exact size, Ctrl+0, and a pinch that keeps its spot under the fingers."""
    import time

    from gi.repository import GLib

    from larenderer.window import MainWindow

    window = MainWindow(app)
    window.present()

    def settle(until, seconds=20):
        deadline = time.monotonic() + seconds
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if until():
                return True
            time.sleep(0.02)
        return False

    # Let start-up restore whatever the tests before this left open, so the
    # tab in front stays the tab in front from here on.
    settle(lambda: False, 1.0)

    window._set_zoom_mode("1.37")
    assert window.preview.zoom == 1.37 and window.zoom_button.get_label() == "137%"
    assert window.config["zoom"] == "1.37"
    window.activate_action("win.zoom-reset", None)
    assert window.preview.zoom == 1.0 and window.config["zoom"] == "1.0"

    if winenv.which("pdflatex"):
        source = os.path.join(scratch, "long.tex")
        text = ("\\documentclass{article}\n\\begin{document}\n"
                + "\n\n".join(f"Paragraph {i}. " * 60 for i in range(60))
                + "\n\\end{document}\n")
        with open(source, "w", encoding="utf-8") as fh:
            fh.write(text)
        result = compiler.compile_document(text, source, timeout=120)
        preview = window.preview
        preview.show_result(result)
        vadj = preview.scroller.get_vadjustment()
        # Keeping the spot needs layout, and some displays — Broadway with no
        # browser attached — never lay anything out. Say so rather than fail.
        if settle(lambda: vadj.get_upper() > 2 * vadj.get_page_size() > 0, 5):
            preview.scroll_to_page(2)
            settle(lambda: False, 0.3)
            anchor = (120.0, 200.0)
            before = preview._document_point(anchor)
            preview.begin_live_zoom()
            for step in (1.1, 1.25, 1.4, 1.6, 1.8):
                preview.live_zoom(step, anchor)
                settle(lambda: preview._pending_scroll is None, 2)
            preview.end_live_zoom()
            after = preview._document_point(anchor)
            assert before[0] == after[0], (before, after)
            assert abs(before[1] - after[1]) < 3 and abs(before[2] - after[2]) < 3, \
                (before, after)
            assert window.zoom_button.get_label() == "180%", window.zoom_button.get_label()
        else:
            print("  --  a pinch keeps its spot  (this display draws no frames)")

    window.config["zoom"] = "9"
    assert window.zoom_from_config() == 4.0, "an out-of-range setting is brought back in"
    window.config["zoom"] = "1.0"

    for document in window.documents:
        document.editor.mark_saved()
    window.close()


def test_an_overleaf_tab_in_a_real_window(app):
    """Overleaf's editor in a tab: the document chrome steps aside, then returns.

    A local page stands in for Overleaf, so the test needs no network and
    signs in to nothing; links that would leave for the browser are caught.
    """
    import time

    from gi.repository import GLib

    from larenderer import overleafweb
    from larenderer.window import MainWindow

    if not overleafweb.available() or os.environ.get("GDK_BACKEND") == "broadway":
        print("  --  an Overleaf tab  (needs WebKitGTK 6.0 and a display it can draw on)")
        return False

    def settle(until, seconds=30):
        deadline = time.monotonic() + seconds
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if until():
                return True
            time.sleep(0.02)
        return False

    page_file = os.path.join(scratch, "standin.html")
    with open(page_file, "w", encoding="utf-8") as fh:
        fh.write("<title>Thesis - Overleaf, Online LaTeX Editor</title><p>stand-in</p>")

    window = MainWindow(app)
    window.present()
    settle(lambda: False, 1.0)
    window._open_external_uri = lambda uri: None
    document = window.active
    save = window.lookup_action("save")

    view = window.open_overleaf_web("file://" + page_file)
    assert view is not None
    assert settle(lambda: view.title() == "Thesis"), view.title()
    assert not save.get_enabled() and not window.compile_button.get_visible()
    assert window.lookup_action("close-tab").get_enabled()
    assert window.open_overleaf_web("file://" + page_file) is view, "one tab per page"

    window.tabs.set_selected_page(window._page_for(document))
    assert save.get_enabled() and window.compile_button.get_visible()

    page = next(p for p, v in window._web_tabs.items() if v is view)
    window.tabs.close_page(page)
    assert not window._web_tabs

    document.editor.mark_saved()
    window.close()
    return True


# ----------------------------------------------------------------- windows --

_TEST_APP = None


def _test_app():
    """A LaRendererApp of its own, registered but never run.

    Registering emits ::startup, which installs the actions and lets the
    application hold windows. The id and flags are changed first so this never
    reaches the real LaRenderer that may be running on this desktop, and it is
    built once, because a second application exporting the same object path is
    an error.
    """
    global _TEST_APP
    if _TEST_APP is None:
        from gi.repository import Gio

        from larenderer.app import LaRendererApp

        app = LaRendererApp()
        app.set_application_id("com.robin.LaRendererTests")
        app.set_flags(Gio.ApplicationFlags.NON_UNIQUE
                      | Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        app.register(None)
        _TEST_APP = app
    return _TEST_APP


def test_the_new_window_flag_never_reaches_the_paths():
    from larenderer.app import split_new_window_flag

    for argv, expected in (
        (["-n", "a.tex"], (True, ["a.tex"])),
        (["--new-window", "a.tex"], (True, ["a.tex"])),
        (["--new-window"], (True, [])),
        (["a.tex"], (False, ["a.tex"])),
        ([], (False, [])),
    ):
        got = split_new_window_flag(list(argv))
        assert got == expected, f"{argv} gave {got}, wanted {expected}"


def test_two_windows_open_at_once_and_share_no_document():
    from larenderer.window import MainWindow

    app = _test_app()
    before = len([w for w in app.get_windows() if isinstance(w, MainWindow)])
    first = app.new_window()
    second = app.new_window()

    mains = [w for w in app.get_windows() if isinstance(w, MainWindow)]
    assert len(mains) == before + 2, f"expected two more windows, got {len(mains)}"
    assert first is not second
    assert first.config is not second.config
    # A document belongs to the window showing it, or the second window would
    # compile and repaint over the first one's pages.
    assert first.documents is not second.documents
    assert first.active is not second.active
    assert app.front_window() in mains

    for window in (first, second):
        window.destroy()


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


def test_closing_one_window_does_not_forget_the_others_documents():
    """The reason remembering belongs to the application and not the window.

    Windows close one at a time even when the whole app is quitting, so a
    window that wrote down only its own tabs would leave the last one out --
    always the smallest -- overwriting everything the others had open.
    """
    app = _test_app()
    first = app.new_window()
    second = app.new_window()

    alpha = os.path.join(scratch, "alpha.tex")
    beta = os.path.join(scratch, "beta.tex")
    for path in (alpha, beta):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\\documentclass{article}\\begin{document}x\\end{document}\n")

    first.open_document(path=alpha, text="x", focus=False)
    second.open_document(path=beta, text="x", focus=False)

    both = app.remember_session()
    assert alpha in both and beta in both, f"one window's work is missing: {both}"

    # Now close them in turn, the way quitting does it. The last window out
    # must still write down what the first one had.
    after_first = app.remember_session(closing=first)
    first.destroy()
    assert alpha in after_first and beta in after_first, after_first

    after_second = app.remember_session(closing=second)
    second.destroy()
    assert alpha in after_second, f"the first window's document was forgotten: {after_second}"
    assert beta in after_second, after_second
    assert len(after_second) == len(set(after_second)), "a document was listed twice"


def test_a_new_window_stops_earlier_closed_ones_counting():
    """Otherwise a window closed on purpose would keep coming back all run."""
    app = _test_app()
    first = app.new_window()
    gamma = os.path.join(scratch, "gamma.tex")
    with open(gamma, "w", encoding="utf-8") as fh:
        fh.write("x\n")
    first.open_document(path=gamma, text="x", focus=False)
    app.remember_session(closing=first)
    first.destroy()

    fresh = app.new_window()
    assert gamma not in app.remember_session(), "a deliberately closed window came back"
    fresh.destroy()


def test_the_new_window_action_is_there_with_its_accelerator():
    from gi.repository import Gtk

    app = _test_app()
    assert app.lookup_action("new-window") is not None, "no app.new-window action"

    # Compared as GTK parses them, not as they are spelled: GTK reorders the
    # modifiers, so "<Control><Shift>n" comes back as "<Shift><Control>n".
    def parsed(accels):
        return {Gtk.accelerator_parse(a)[1:] for a in accels}

    # Ctrl+N is a new tab here; a whole window is the shifted version.
    assert parsed(app.get_accels_for_action("app.new-window")) == \
        parsed(["<Control><Shift>n"]), app.get_accels_for_action("app.new-window")
    assert parsed(app.get_accels_for_action("app.new-window")).isdisjoint(
        parsed(["<Control>n"])), "a new window would steal the new-tab key"


def main():
    print("reading a log")
    check("an error is found with its line", test_an_error_is_found_with_its_line)
    check("the job is recognised however the engine spells it",
          test_the_job_is_recognised_however_the_engine_spells_it)
    check("a bang error looks ahead for its line", test_a_bang_error_looks_ahead_for_its_line)
    check("a bad box carries its first line", test_a_badbox_carries_its_first_line)
    check("a package warning keeps its package and line",
          test_a_package_warning_keeps_its_package_and_line)
    check("the noise of a healthy document is dropped",
          test_the_noise_of_a_healthy_document_is_dropped)
    check("the same complaint is only made once", test_the_same_complaint_is_only_made_once)
    check("errors sort before warnings before bad boxes",
          test_errors_sort_before_warnings_before_badboxes)
    check("a missing package or file is named", test_a_missing_package_is_named)

    print("build bookkeeping")
    check("a document keeps its own build directory", test_a_document_keeps_its_own_build_directory)
    check("a job name is safe for TeX", test_a_job_name_is_safe_for_tex)
    check("clearing a build removes its directory", test_clear_build_removes_the_directory)

    print("folders")
    check("the main document is found", test_the_main_document_is_found)
    check("a part knows what it is part of", test_a_part_knows_what_it_is_part_of)
    check("Overleaf links are read however they are pasted", test_overleaf_links_are_read)
    check("text is told from binary", test_text_is_told_from_binary)
    check("Overleaf addresses are understood", test_overleaf_addresses_are_understood)

    print("the source")
    check("packages are read out of the preamble", test_packages_are_read_out_of_the_preamble)
    check("every template is a whole document", test_every_template_is_a_whole_document)
    check("every snippet is balanced", test_every_snippet_is_balanced)

    print("with an engine")
    needs("pdflatex", "a real document compiles to a pdf", test_a_real_document_compiles_to_a_pdf)
    needs("pdflatex", "a broken document reports the line it broke on",
          test_a_broken_document_reports_the_line_it_broke_on)
    needs("pdflatex", "unicode in the source survives the round trip",
          test_unicode_in_the_source_survives_the_round_trip)
    needs("pdflatex", "a part compiles through its main, with its unsaved text",
          test_a_part_compiles_through_its_main_with_its_unsaved_text)
    # The pdf it reads is the one the compile tests leave behind, so it needs
    # pdflatex as much as pdfinfo — and a machine can have either alone.
    if winenv.which("pdflatex"):
        needs("pdfinfo", "pdf_info reads a real pdf", test_pdf_info_reads_a_real_pdf)
    else:
        skipped.append(("pdf_info reads a real pdf", "pdflatex"))
        print("  --  pdf_info reads a real pdf  (no pdflatex on this machine)")

    print("widgets")
    from gi.repository import Adw
    Adw.init()
    widgets.install_css()
    check("widgets build without a document", test_widgets_build_without_a_document)
    check("the problems list takes what the log gave it",
          test_the_problems_list_takes_what_the_log_gave_it)
    check("zoom stays in bounds and says what it is",
          test_zoom_stays_in_bounds_and_says_what_it_is)
    check("the page cache is bounded by size", test_the_page_cache_is_bounded_by_size)

    print("tabs")
    from gi.repository import Gio, GLib
    app = Adw.Application(application_id="org.larenderer.Tests",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    failure = []

    def run_window():
        try:
            test_tabs_in_a_real_window(app)
            print("  ok  several documents, each in a tab of its own")
            test_a_folder_in_a_real_window(app)
            print("  ok  a folder opens at its main document, and a part compiles through it")
            test_zoom_in_a_real_window(app)
            print("  ok  an exact zoom, Ctrl+0, and a pinch that keeps its spot")
            test_the_new_window_flag_never_reaches_the_paths()
            print("  ok  the new-window flag never reaches the paths")
            test_two_windows_open_at_once_and_share_no_document()
            print("  ok  two windows open at once and share no document")
            test_on_windows_a_window_wears_the_apps_icon_not_pythons()
            print("  ok  on Windows, a window wears the app's icon, not Python's")
            test_closing_one_window_does_not_forget_the_others_documents()
            print("  ok  closing one window does not forget the other's documents")
            test_a_new_window_stops_earlier_closed_ones_counting()
            print("  ok  a new window stops earlier closed ones counting")
            test_the_new_window_action_is_there_with_its_accelerator()
            print("  ok  the new-window action is there with its accelerator")
            if test_an_overleaf_tab_in_a_real_window(app):
                print("  ok  an Overleaf tab, and the document chrome stepping aside for it")
        except BaseException as exc:  # noqa: BLE001 - re-raised after the loop
            failure.append(exc)
        finally:
            app.release()
            app.quit()
        return GLib.SOURCE_REMOVE

    def activate(a):
        # Without the hold there is no window yet and run() returns before the
        # timeout below has had a chance to make one.
        a.hold()
        GLib.timeout_add(400, run_window)

    app.connect("activate", activate)
    app.run([])
    if failure:
        raise failure[0]

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
