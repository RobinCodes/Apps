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

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from larenderer import compiler, packages, templates, widgets, winenv  # noqa: E402

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
    needs("pdfinfo", "pdf_info reads a real pdf", test_pdf_info_reads_a_real_pdf)

    print("widgets")
    from gi.repository import Adw
    Adw.init()
    widgets.install_css()
    check("widgets build without a document", test_widgets_build_without_a_document)
    check("the problems list takes what the log gave it",
          test_the_problems_list_takes_what_the_log_gave_it)

    print("tabs")
    from gi.repository import Gio, GLib
    app = Adw.Application(application_id="org.larenderer.Tests",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    failure = []

    def run_window():
        try:
            test_tabs_in_a_real_window(app)
            print("  ok  several documents, each in a tab of its own")
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
