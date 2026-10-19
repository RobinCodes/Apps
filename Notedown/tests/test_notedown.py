"""What can be checked without a screen, and what needs one.

Most of this app is a parser, so most of the suite is the parser: text in,
tree out, checked against what the tree ought to say. That half needs nothing
installed and runs in well under a second.

    python3 tests/test_notedown.py

The rest needs a display but nothing else. The editor tests drive a real
Gtk.TextBuffer, because the editing courtesies are the part most likely to
break quietly, and the window tests open and save real files — all of them
inside the scratch directory that is removed at the end.

Nothing here follows a link or launches anything. A test that activates a URL
hands it to the desktop portal, which opens a browser on whatever session the
suite happens to be running in.
"""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

scratch = tempfile.mkdtemp(prefix="notedown-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(scratch, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(scratch, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(scratch, "cache")
# An app built by a test must not go to the network for an update.
os.environ["APPS_NO_UPDATE"] = "1"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from notedown import app as app_module  # noqa: E402
from notedown import export, highlight, markdown, search  # noqa: E402
from notedown.config import Config, DEFAULTS  # noqa: E402

failures = []


def check(name, fn):
    try:
        fn()
    except BaseException as exc:  # noqa: BLE001 - re-raised after the run
        failures.append((name, exc))
        print(f"  FAIL  {name}: {exc}")
        return
    print(f"  ok  {name}")


def kinds(blocks):
    return [block.kind for block in blocks]


def text_of(block):
    return markdown.spans_text(block.spans)


# ----------------------------------------------------------- the parser -----

def test_the_blocks_of_a_document_come_out_in_order():
    doc = markdown.parse(
        "# Head\n\ntext\n\n- a\n- b\n\n> quote\n\n```py\nx\n```\n\n---\n")
    assert kinds(doc.blocks) == ["heading", "para", "list", "quote", "code", "rule"], \
        kinds(doc.blocks)
    assert doc.blocks[0].level == 1
    assert doc.blocks[4].language == "py"
    assert doc.blocks[4].text == "x"


def test_every_block_knows_the_line_it_started_on():
    source = "intro\n\n# Head\n\nbody\n\n- item\n"
    doc = markdown.parse(source)
    lines = source.split("\n")
    for block in doc.blocks:
        assert 0 <= block.line < len(lines), (block.kind, block.line)
    heading = next(b for b in doc.blocks if b.kind == "heading")
    assert lines[heading.line] == "# Head", lines[heading.line]
    listing = next(b for b in doc.blocks if b.kind == "list")
    assert lines[listing.line] == "- item", lines[listing.line]


def test_a_line_inside_a_quote_reports_its_own_line_not_the_quotes():
    source = "before\n\n> quoted\n>\n> ## Inside\n"
    doc = markdown.parse(source)
    quote = next(b for b in doc.blocks if b.kind == "quote")
    heading = next(b for b in quote.children if b.kind == "heading")
    assert source.split("\n")[heading.line] == "> ## Inside", heading.line


def test_a_task_records_where_its_checkbox_is():
    source = "- [ ] undone\n- [x] done\n"
    doc = markdown.parse(source)
    items = doc.blocks[0].items
    assert [item.task for item in items] == [False, True]
    for item in items:
        line = source.split("\n")[item.task_line]
        assert line[item.task_column:item.task_column + 3] in ("[ ]", "[x]"), line


def test_an_indented_task_records_the_right_column():
    source = "- one\n  - [x] nested\n"
    doc = markdown.parse(source)
    nested = doc.blocks[0].items[0].children[1]
    item = nested.items[0]
    line = source.split("\n")[item.task_line]
    assert line[item.task_column:item.task_column + 3] == "[x]", repr(line)


def test_a_list_is_tight_without_blank_lines_and_loose_with_them():
    assert markdown.parse("- a\n- b\n").blocks[0].tight is True
    assert markdown.parse("- a\n\n- b\n").blocks[0].tight is False
    # A sub-list does not by itself make the parent loose.
    assert markdown.parse("- a\n  - b\n- c\n").blocks[0].tight is True


def test_a_numbered_list_keeps_the_number_it_starts_at():
    block = markdown.parse("5. five\n6. six\n").blocks[0]
    assert block.ordered and block.start == 5 and len(block.items) == 2


def test_three_dashes_are_a_rule_after_a_blank_and_a_heading_after_text():
    assert kinds(markdown.parse("para\n\n---\n\nafter\n").blocks) == \
        ["para", "rule", "para"]
    blocks = markdown.parse("Title\n---\n").blocks
    assert kinds(blocks) == ["heading"] and blocks[0].level == 2


def test_front_matter_is_taken_off_the_top_only():
    doc = markdown.parse("---\ntitle: T\n---\n\n# H\n")
    assert doc.frontmatter == "title: T"
    assert markdown.title_of(doc) == "T"
    # The same three dashes further down are a rule.
    doc = markdown.parse("# H\n\n---\n")
    assert doc.frontmatter == "" and "rule" in kinds(doc.blocks)


def test_a_table_needs_a_delimiter_row_that_matches_its_header():
    doc = markdown.parse("| a | b |\n|---|--:|\n| 1 | 2 |\n")
    table = doc.blocks[0]
    assert table.kind == "table"
    assert table.aligns == ["left", "right"]
    assert [markdown.spans_text(c) for c in table.rows[0]] == ["1", "2"]
    # A pipe with no delimiter row underneath is just a paragraph.
    assert kinds(markdown.parse("| a | b |\n| 1 | 2 |\n").blocks) == ["para"]


def test_a_short_table_row_is_padded_and_a_long_one_is_cut():
    table = markdown.parse("| a | b |\n|---|---|\n| 1 |\n| 1 | 2 | 3 |\n").blocks[0]
    assert [[markdown.spans_text(c) for c in row] for row in table.rows] == \
        [["1", ""], ["1", "2"]]


def test_emphasis_nests_and_intraword_underscores_do_not_emphasise():
    spans = markdown.inline("**bold with *italic* inside**")
    assert spans[0].kind == "strong"
    assert any(child.kind == "emph" for child in spans[0].children)
    assert markdown.spans_text(markdown.inline("snake_case_name")) == "snake_case_name"
    assert [n.kind for n in markdown.inline("snake_case_name")] == ["text"]


def test_a_code_span_wins_over_everything_inside_it():
    spans = markdown.inline("`**not bold**`")
    assert len(spans) == 1 and spans[0].kind == "code"
    assert spans[0].text == "**not bold**"


def test_backtick_runs_match_by_length():
    spans = markdown.inline("``a ` b``")
    assert spans[0].kind == "code" and spans[0].text == "a ` b"


def test_links_images_and_reference_links():
    doc = markdown.parse("[a](http://x 'T') ![i](p.png)\n\n[r]: http://y\n\nsee [r]\n")
    first = doc.blocks[0].spans
    link = next(n for n in first if n.kind == "link")
    assert link.url == "http://x" and link.title == "T"
    image = next(n for n in first if n.kind == "image")
    assert image.url == "p.png" and image.text == "i"
    referenced = next(n for n in doc.blocks[-1].spans if n.kind == "link")
    assert referenced.url == "http://y"


def test_a_bare_url_becomes_a_link_and_keeps_its_text():
    spans = markdown.inline("see https://example.com/a?b=1 now")
    link = next(n for n in spans if n.kind == "link")
    assert link.url == "https://example.com/a?b=1"
    assert markdown.spans_text(spans) == "see https://example.com/a?b=1 now"


def test_dollars_are_maths_or_money_by_what_is_between_them():
    assert markdown.is_math("E = mc^2")
    assert markdown.is_math("x")
    assert not markdown.is_math("5 and $6 today")
    spans = markdown.inline("It costs $5 and $6")
    assert not any(n.kind == "math" for n in spans)
    spans = markdown.inline("where $a^2 + b^2 = c^2$ holds")
    assert any(n.kind == "math" for n in spans)


def test_hard_breaks_are_two_spaces_or_a_backslash():
    """Markdown's own two ways of asking for a break, which still work.

    Asserted with `breaks=False`, because that is the only mode in which the
    question means anything: with the house default on, *every* newline is a
    break and the two syntaxes are indistinguishable from a plain one. What
    this pins is that turning the house rule off gets Markdown back exactly,
    which is what the setting is for.
    """
    spans = markdown.inline("one  \ntwo\\\nthree\nfour", breaks=False)
    assert [n.kind for n in spans].count("break") == 2
    assert markdown.spans_text(spans).replace(" ", "") == "onetwothreefour"

    # And with the default on, the third newline counts too.
    spans = markdown.inline("one  \ntwo\\\nthree\nfour")
    assert [n.kind for n in spans].count("break") == 3


def test_escapes_stop_syntax_from_meaning_anything():
    assert markdown.spans_text(markdown.inline(r"\*not em\*")) == "*not em*"
    assert kinds(markdown.parse(r"\# not a heading" + "\n").blocks) == ["para"]


def test_footnotes_are_collected_and_referenced():
    doc = markdown.parse("text[^a]\n\n[^a]: the note\n")
    assert "a" in doc.footnotes
    assert markdown.spans_text(doc.footnotes["a"].children[0].spans) == "the note"
    assert any(n.kind == "footref" for n in doc.blocks[0].spans)


def test_an_unclosed_fence_still_makes_a_code_block():
    doc = markdown.parse("```py\nx = 1\n")
    assert kinds(doc.blocks) == ["code"] and doc.blocks[0].text == "x = 1"


def test_nothing_in_the_parser_raises_on_rubbish():
    for rubbish in ("`" * 400, "[" * 300, "*" * 200, "|" * 100 + "\n" + "-|" * 60,
                    "#" * 80, "> " * 200, "$" * 90, "\\", "![](", "[a](b"):
        markdown.parse(rubbish)
    # Deeply nested emphasis must terminate rather than recurse for ever.
    markdown.parse("*" * 40 + "x" + "*" * 40)


def test_the_outline_reads_headings_wherever_they_are():
    doc = markdown.parse("# One\n\n## Two\n\n> ### Three\n")
    found = [(level, title) for level, title, _line, _anchor in markdown.outline(doc)]
    assert found == [(1, "One"), (2, "Two"), (3, "Three")], found


def test_anchors_look_like_the_ones_github_makes():
    assert markdown.slug("A Table!") == "a-table"
    assert markdown.slug("Quotes and maths") == "quotes-and-maths"
    assert markdown.slug("**bold** head") == "bold-head"
    assert markdown.slug("###") == "section"


def test_the_word_count_ignores_the_syntax():
    stats = markdown.statistics("# Head\n\n**two** words, `code` here.\n")
    assert stats["words"] == 5, stats
    # A fenced block is not prose and is not counted.
    assert markdown.statistics("a\n\n```\nb c d e f\n```\n")["words"] == 1


# -------------------------------------------------------- the highlighter ---

def test_the_highlighter_names_a_language_from_its_aliases():
    assert highlight.language_of("py") == "python"
    assert highlight.language_of("Bash") == "shell"
    assert highlight.language_of("") == ""
    assert highlight.known("python") and not highlight.known("klingon")


def test_the_highlighter_marks_keywords_strings_and_comments():
    out = highlight.markup('def f():\n    # c\n    return "s"', "python", False)
    for colour in (highlight.LIGHT["keyword"], highlight.LIGHT["string"],
                   highlight.LIGHT["comment"]):
        assert colour in out, colour


def test_the_highlighter_escapes_what_it_does_not_colour():
    out = highlight.markup("a < b && c > d", "text", False)
    assert "&lt;" in out and "&amp;" in out and "&gt;" in out


def test_an_unknown_language_still_gets_strings_and_numbers():
    out = highlight.markup('x = "a" + 12', "klingon", True)
    assert highlight.DARK["string"] in out and highlight.DARK["number"] in out


# -------------------------------------------------------------- exporting ---

def test_html_carries_the_document_the_preview_showed():
    doc = markdown.parse(
        "# H\n\ntext **b**\n\n- [x] done\n\n| a |\n|---|\n| 1 |\n\n```py\nx\n```\n")
    out = export.to_html(doc, "T")
    for fragment in ('<h1 id="h">H</h1>', "<strong>b</strong>", "checked",
                     "<table>", 'class="language-python"', "<title>T</title>"):
        assert fragment in out, fragment


def test_html_escapes_what_came_from_the_document():
    out = export.to_html(markdown.parse("a <script>alert(1)</script> b\n"))
    assert "&lt;script&gt;" in out


def test_a_fragment_export_has_no_page_around_it():
    out = export.to_html(markdown.parse("# H\n"), standalone=False)
    assert out.strip().startswith("<h1") and "<html" not in out


def test_plain_text_export_keeps_the_shape_and_drops_the_syntax():
    out = export.to_text(markdown.parse("# H\n\n**b** text\n\n- one\n- two\n"))
    assert "H" in out and "**" not in out
    assert "- one" in out and "- two" in out


def test_pandoc_is_optional_and_says_so():
    if export.pandoc_path() is None:
        try:
            export.pandoc_convert("x", os.path.join(scratch, "x.pdf"))
        except RuntimeError as exc:
            assert "pandoc" in str(exc)
        else:
            raise AssertionError("no pandoc, but the convert did not complain")


# ---------------------------------------------------------------- finding ---

def test_find_all_counts_matches_the_way_the_options_say():
    text = "Cat cat catalogue"
    assert len(search.find_all(text, "cat")) == 3
    assert len(search.find_all(text, "cat", case=True)) == 2
    assert len(search.find_all(text, "cat", word=True)) == 2
    assert len(search.find_all(text, "c.t", regex=True)) == 3
    assert search.find_all(text, "") == []


def test_a_broken_regular_expression_finds_nothing_rather_than_raising():
    assert search.find_all("abc", "(", regex=True) == []
    assert search.substitute("abc", "(", "x", regex=True) == ("abc", 0)


def test_an_empty_match_cannot_loop_for_ever():
    assert search.find_all("abc", "x*", regex=True) == []


def test_substitute_replaces_every_match_and_counts_them():
    assert search.substitute("a a a", "a", "b") == ("b b b", 3)
    assert search.substitute("a1 a2", r"a(\d)", r"[\1]", regex=True) == ("[1] [2]", 2)
    # A plain replacement is literal: backslashes in it are not group refs.
    assert search.substitute("a", "a", r"\1") == (r"\1", 1)


# ----------------------------------------------------------------- config ---

def test_the_config_keeps_defaults_for_anything_it_does_not_recognise():
    config = Config()
    config["window_width"] = 999
    config["nonsense"] = True
    config.save()
    again = Config()
    assert again["window_width"] == 999
    assert "nonsense" not in again


def test_the_recent_list_has_no_duplicates_and_a_limit():
    config = Config()
    config["recent_files"] = []
    config["recent_limit"] = 3
    for path in ("/a", "/b", "/a", "/c", "/d"):
        config.remember_file(path)
    assert config["recent_files"] == ["/d", "/c", "/a"], config["recent_files"]
    config.forget_file("/c")
    assert "/c" not in config["recent_files"]


def test_a_new_line_is_a_new_line():
    """The one place this parser leaves Markdown on purpose.

    Markdown joins consecutive lines into one paragraph and wants two
    trailing spaces for a break, which in a window that soft-wraps for you is
    an invisible difference between what you typed and what you see. So a
    newline is a break here — and `breaks=False` still gets the original
    rule, because an exported document may be read by something that follows
    it.
    """
    spans = markdown.parse("one\ntwo\n").blocks[0].spans
    assert [n.kind for n in spans] == ["text", "break", "text"]
    assert [n.text for n in spans if n.kind == "text"] == ["one", "two"]

    classic = markdown.parse("one\ntwo\n", breaks=False).blocks[0].spans
    assert [n.kind for n in classic] == ["text"]
    assert classic[0].text == "one two"


def test_a_new_line_does_not_make_a_new_paragraph():
    """A break is not a paragraph, and a blank line still is one.

    The distinction matters for spacing: two paragraphs are set apart, two
    lines of one paragraph are not, and conflating them would turn every
    hand-wrapped note into a column of gaps.
    """
    doc = markdown.parse("one\ntwo\n\nthree\n")
    assert [b.kind for b in doc.blocks] == ["para", "para"]
    assert sum(1 for n in doc.blocks[0].spans if n.kind == "break") == 1
    assert not [n for n in doc.blocks[1].spans if n.kind == "break"]


def test_the_new_line_rule_reaches_inside_the_other_blocks():
    """Lists, quotes and emphasis hold paragraphs too, as far as this goes."""
    item = markdown.parse("- one\n  two\n").blocks[0].items[0]
    assert [n.kind for n in item.children[0].spans] == ["text", "break", "text"]

    quote = markdown.parse("> one\n> two\n").blocks[0]
    assert [n.kind for n in quote.children[0].spans] == ["text", "break", "text"]

    emph = markdown.parse("*one\ntwo*\n").blocks[0].spans[0]
    assert emph.kind == "emph"
    assert [n.kind for n in emph.children] == ["text", "break", "text"]


def test_a_new_line_inside_a_code_span_is_not_a_break():
    """The newline rule only ever sees ordinary text.

    A code span is consumed whole by the branch that opens it, so a newline
    inside one never reaches the rule above — which is what keeps a span
    spelled the same whether or not it was written across two lines.
    """
    spans = markdown.parse("`one\ntwo`\n").blocks[0].spans
    assert [n.kind for n in spans] == ["code"]
    assert "break" not in [n.kind for n in spans]


def test_a_font_chosen_is_a_font_remembered():
    config = Config()
    config["editor_font_family"] = "IBM Plex Mono"
    config["editor_font_size"] = 17
    config["preview_font_family"] = "Georgia"
    config["preview_font_size"] = 15
    config.save()

    again = Config()
    assert again["editor_font_family"] == "IBM Plex Mono"
    assert again["editor_font_size"] == 17
    assert again["preview_font_family"] == "Georgia"
    assert again["preview_font_size"] == 15

    for key in ("editor_font_family", "editor_font_size",
                "preview_font_family", "preview_font_size"):
        again[key] = DEFAULTS[key]
    again.save()


def test_a_font_family_is_quoted_unless_the_word_is_the_choice():
    from notedown.widgets import css_font_family

    assert css_font_family("JetBrains Mono") == '"JetBrains Mono"'
    # Not the name of a font: the word asks the desktop to pick one.
    assert css_font_family("monospace") == "monospace"
    assert css_font_family("  Serif ") == "serif"
    assert css_font_family("") == ""
    assert css_font_family('Ev"il') == '"Evil"'


def test_the_new_window_flag_never_reaches_the_paths():
    wants, rest = app_module.split_new_window_flag(["-n", "a.md", "b.md"])
    assert wants and rest == ["a.md", "b.md"]
    wants, rest = app_module.split_new_window_flag(["a.md"])
    assert not wants and rest == ["a.md"]


# ------------------------------------------------------- the editing tests --
#
# These need a display. Everything below runs inside a real application.

def test_enter_continues_a_bullet_list(editor):
    editor.set_text("- one")
    place(editor, 1, 5)
    editor.view.emit("move-cursor", 0, 0, False)   # settle the cursor mark
    press_enter(editor)
    assert editor.get_text() == "- one\n- ", repr(editor.get_text())


def test_enter_on_an_empty_item_leaves_the_list(editor):
    editor.set_text("- one\n- ")
    place(editor, 2, 2)
    press_enter(editor)
    assert editor.get_text() == "- one\n\n", repr(editor.get_text())


def test_enter_continues_a_task_and_leaves_it_unticked(editor):
    editor.set_text("- [x] done")
    place(editor, 1, 10)
    press_enter(editor)
    assert editor.get_text() == "- [x] done\n- [ ] ", repr(editor.get_text())


def test_enter_continues_a_numbered_list_and_renumbers_the_rest(editor):
    editor.set_text("1. one\n2. two")
    place(editor, 1, 6)
    press_enter(editor)
    assert editor.get_text().startswith("1. one\n2. \n"), repr(editor.get_text())
    editor._renumber_from(1)
    assert editor.get_text() == "1. one\n2. \n3. two", repr(editor.get_text())


def test_enter_continues_a_quote(editor):
    editor.set_text("> quoted")
    place(editor, 1, 8)
    press_enter(editor)
    assert editor.get_text() == "> quoted\n> ", repr(editor.get_text())


def test_backspace_at_the_text_takes_the_marker_off(editor):
    editor.set_text("- one")
    place(editor, 1, 2)
    assert editor._on_backspace() is True
    assert editor.get_text() == "one", repr(editor.get_text())


def test_bold_wraps_the_selection_and_unwraps_it_again(editor):
    editor.set_text("hello world")
    select(editor, 0, 5)
    editor.wrap_selection("**")
    assert editor.get_text() == "**hello** world", repr(editor.get_text())
    editor.wrap_selection("**")
    assert editor.get_text() == "hello world", repr(editor.get_text())


def test_bold_with_no_selection_takes_the_word_under_the_cursor(editor):
    editor.set_text("hello world")
    place(editor, 1, 3)
    editor.wrap_selection("**")
    assert editor.get_text() == "**hello** world", repr(editor.get_text())


def test_bold_outside_a_word_opens_an_empty_pair(editor):
    editor.set_text("")
    editor.wrap_selection("**")
    assert editor.get_text() == "****"
    line, column = editor.cursor_position()
    assert (line, column) == (1, 3), (line, column)


def test_setting_a_heading_replaces_whatever_was_there(editor):
    editor.set_text("### old")
    place(editor, 1, 1)
    editor.set_heading(1)
    assert editor.get_text() == "# old"
    editor.set_heading(0)
    assert editor.get_text() == "old"


def test_toggling_a_list_goes_both_ways(editor):
    editor.set_text("one\ntwo")
    select_lines(editor, 0, 1)
    editor.toggle_list()
    assert editor.get_text() == "- one\n- two", repr(editor.get_text())
    select_lines(editor, 0, 1)
    editor.toggle_list()
    assert editor.get_text() == "one\ntwo", repr(editor.get_text())


def test_toggling_a_numbered_list_numbers_from_one(editor):
    editor.set_text("a\nb\nc")
    select_lines(editor, 0, 2)
    editor.toggle_list(ordered=True)
    assert editor.get_text() == "1. a\n2. b\n3. c", repr(editor.get_text())


def test_ticking_a_task_from_the_preview_edits_two_characters(editor):
    editor.set_text("- [ ] one\n- [x] two")
    editor.set_task_at(0, 2, True)
    assert editor.get_text() == "- [x] one\n- [x] two", repr(editor.get_text())
    editor.set_task_at(1, 2, False)
    assert editor.get_text() == "- [x] one\n- [ ] two", repr(editor.get_text())


def test_toggling_a_task_adds_the_box_when_there_is_none(editor):
    editor.set_text("- plain")
    place(editor, 1, 3)
    editor.toggle_task()
    assert editor.get_text() == "- [ ] plain", repr(editor.get_text())
    editor.toggle_task()
    assert editor.get_text() == "- [x] plain", repr(editor.get_text())


def test_a_quote_toggles_on_and_off(editor):
    editor.set_text("one\ntwo")
    select_lines(editor, 0, 1)
    editor.toggle_quote()
    assert editor.get_text() == "> one\n> two"
    select_lines(editor, 0, 1)
    editor.toggle_quote()
    assert editor.get_text() == "one\ntwo"


def test_the_comment_toggle_uses_html_because_markdown_has_none(editor):
    editor.set_text("secret")
    place(editor, 1, 1)
    editor.toggle_comment()
    assert editor.get_text() == "<!--\nsecret\n-->", repr(editor.get_text())


def test_the_editor_draws_in_the_font_it_is_given(editor):
    from gi.repository import Pango

    from notedown.editor import MAX_FONT_SIZE

    editor.set_font("IBM Plex Mono", 15)
    assert editor.get_font_family() == "IBM Plex Mono"
    assert editor.get_font_size() == 15

    # The gutter draws its numbers itself rather than letting CSS do it, so
    # the font it uses has to be the same one or the numbers stop lining up
    # with the lines they count.
    description = editor._font_description()
    assert description.get_family() == "IBM Plex Mono"
    assert description.get_size() / Pango.SCALE == 15

    # A size out of reach is clamped, and the family it was chosen with stays.
    editor.set_font_size(999)
    assert editor.get_font_size() == MAX_FONT_SIZE
    assert editor.get_font_family() == "IBM Plex Mono"
    editor.set_font(DEFAULTS["editor_font_family"], DEFAULTS["editor_font_size"])


def test_highlighting_survives_every_shape_of_document(editor):
    editor.set_text(open(os.path.join(ROOT, "notedown", "markdown.py"),
                         encoding="utf-8").read()[:6000])
    editor._highlight_now()
    editor.set_text("#" * 9 + "\n" + "`" * 20 + "\n| |\n|-|\n" + "*" * 30)
    editor._highlight_now()
    editor.set_text("")
    editor._highlight_now()


# ------------------------------------------------------ the rendering tests --

def test_the_preview_builds_one_widget_per_block(preview):
    doc = markdown.parse("# H\n\ntext\n\n- a\n\n```\nx\n```\n\n---\n")
    preview.render(doc, "")
    built = list(children(preview.column))
    assert len(built) == len(doc.blocks), (len(built), len(doc.blocks))
    assert [w._line for w in built] == [b.line for b in doc.blocks]


def test_editing_one_paragraph_keeps_the_other_widgets(preview):
    first = markdown.parse("# H\n\nalpha\n\nbeta\n")
    preview.render(first, "")
    before = list(children(preview.column))

    second = markdown.parse("# H\n\nalpha CHANGED\n\nbeta\n")
    preview.render(second, "")
    after = list(children(preview.column))

    assert after[0] is before[0], "the heading was rebuilt for nothing"
    assert after[2] is before[2], "the untouched paragraph was rebuilt"
    assert after[1] is not before[1], "the edited paragraph was not rebuilt"


def test_a_task_in_the_preview_is_a_real_check_button(preview):
    preview.render(markdown.parse("- [ ] one\n- [x] two\n"), "")
    boxes = [w for w in descendants(preview.column) if isinstance(w, Gtk.CheckButton)]
    assert len(boxes) == 2
    assert [b.get_active() for b in boxes] == [False, True]


def test_clicking_a_checkbox_reports_where_to_edit(preview):
    seen = []
    preview.on_task = lambda line, column, ticked: seen.append((line, column, ticked))
    preview.render(markdown.parse("- [ ] one\n"), "")
    box = next(w for w in descendants(preview.column) if isinstance(w, Gtk.CheckButton))
    box.set_active(True)
    assert seen == [(0, 2, True)], seen


def test_a_missing_image_says_so_instead_of_disappearing(preview):
    preview.render(markdown.parse("![alt](nope.png)\n"), scratch)
    text = " ".join(w.get_text() for w in descendants(preview.column)
                    if isinstance(w, Gtk.Label))
    assert "nope.png" in text


def test_a_remote_image_is_not_fetched_unless_it_is_allowed(preview):
    preview.load_remote_images = False
    preview.render(markdown.parse("![a](https://example.com/x.png)\n"), scratch)
    text = " ".join(w.get_text() for w in descendants(preview.column)
                    if isinstance(w, Gtk.Label))
    assert "not fetched" in text, text


def test_the_preview_never_raises_on_a_document_it_cannot_make_sense_of(preview):
    for rubbish in ("`" * 200, "|" * 40 + "\n" + "-|" * 20, "$" * 60,
                    "![](" * 30, "#" * 40, "\x00 null"):
        preview.render(markdown.parse(rubbish), scratch)


# ---------------------------------------------------------- the window tests --

def test_a_window_opens_a_file_and_saves_it_back(window):
    path = os.path.join(scratch, "note.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Hello\n\nbody\n")

    document = window.open_path(path)
    assert document is not None and document.is_at(path)
    assert document.text() == "# Hello\n\nbody\n"
    assert not document.modified

    document.editor.buffer.insert_at_cursor("more ")
    assert document.modified
    window.save_document(document, silent=True)
    pump(400)
    with open(path, encoding="utf-8") as fh:
        assert fh.read().startswith("more # Hello")
    assert not document.modified


def test_opening_the_same_file_twice_finds_the_tab_it_is_in(window):
    path = os.path.join(scratch, "twice.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("x\n")
    first = window.open_path(path)
    before = window.tabs.get_n_pages()
    second = window.open_path(path)
    assert second is first
    assert window.tabs.get_n_pages() == before


def test_a_file_that_is_not_markdown_opens_as_plain_text(window):
    path = os.path.join(scratch, "script.sh")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("echo *not emphasis*\n")
    document = window.open_path(path)
    assert not document.is_markdown
    assert document.parsed.blocks[0].kind == "code"


def test_a_windows_file_is_read_and_written_back_with_its_line_endings(window):
    path = os.path.join(scratch, "crlf.md")
    with open(path, "wb") as fh:
        fh.write(b"one\r\ntwo\r\n")
    document = window.open_path(path)
    assert document.newline == "\r\n"
    assert document.text() == "one\ntwo\n"
    window.save_document(document, silent=True)
    pump(400)
    with open(path, "rb") as fh:
        assert fh.read() == b"one\r\ntwo\r\n"


def test_the_outline_follows_the_document(window):
    path = os.path.join(scratch, "outline.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# One\n\ntext\n\n## Two\n")
    window.open_path(path)
    pump(300)
    assert len(window.outline._entries) == 2, window.outline._entries
    assert window.outline._entries[1][1] == "Two"


def test_the_view_modes_show_what_they_say(window):
    window.new_document()
    document = window.active
    window.set_view_mode("editor")
    assert document.editor.get_visible() and not document.preview.get_visible()
    window.set_view_mode("preview")
    assert document.preview.get_visible() and not document.editor.get_visible()
    window.set_view_mode("split")
    assert document.editor.get_visible() and document.preview.get_visible()


def test_the_window_fits_in_a_tiling_column(window):
    """455px is the narrowest preset column on this machine's screen.

    The check is the whole point of the breakpoint: a header button added
    without thinking pushes the minimum past a column, and the window stops
    fitting where it is actually used.
    """
    window.set_default_size(455, 700)
    pump(500)
    minimum = window.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    assert minimum <= 455, f"the window cannot be made narrower than {minimum}px"


def test_export_writes_a_file_that_holds_the_document(window):
    path = os.path.join(scratch, "export-me.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Title\n\n- [x] done\n")
    window.open_path(path)
    out = os.path.join(scratch, "out.html")
    window._write_html(out)
    pump(400)
    with open(out, encoding="utf-8") as fh:
        body = fh.read()
    assert "<h1" in body and "Title" in body and "checked" in body


def test_a_link_to_a_local_file_opens_it_rather_than_a_browser(window):
    target = os.path.join(scratch, "linked.md")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("# Linked\n")
    source = os.path.join(scratch, "linker.md")
    with open(source, "w", encoding="utf-8") as fh:
        fh.write("[go](linked.md)\n")
    document = window.open_path(source)
    assert window.open_url(document, "linked.md") is True
    assert any(d.is_at(target) for d in window.documents)


def test_a_name_typed_bare_is_saved_as_markdown():
    # The Windows save dialog hands back exactly what was typed, and "notes"
    # with nothing after it is a file Explorer calls "File" and opens with
    # nothing.
    from notedown.window import with_extension

    assert with_extension(os.path.join("d", "notes"), ".md") == os.path.join("d", "notes.md")
    assert with_extension(os.path.join("d", "notes.md"), ".md") == os.path.join("d", "notes.md")
    # An extension the user chose is theirs, even one this app would not pick.
    assert with_extension(os.path.join("d", "notes.txt"), ".md") == os.path.join("d", "notes.txt")
    # A dot in the folder is not an extension on the file.
    assert with_extension(os.path.join("v1.2", "notes"), ".md") == os.path.join("v1.2", "notes.md")


def native_icons(window):
    """The big and the small icon Windows holds for `window`; 0 for none."""
    import ctypes

    from notedown import winenv

    user32 = ctypes.WinDLL("user32")
    user32.SendMessageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                    ctypes.c_void_p)
    hwnd = winenv.window_handle(window)
    return [user32.SendMessageW(hwnd, 0x007F, which, None) or 0   # WM_GETICON
            for which in (1, 0)]                                  # ICON_BIG, ICON_SMALL


def test_on_windows_the_window_wears_notedowns_icon_not_pythons(window):
    # GTK 4 sets no icon on a window there, so it wore pythonw.exe's: the
    # Python logo, in Alt+Tab and on the taskbar. NotedownApp does this at
    # startup; the window here belongs to a plain test application.
    from notedown import winenv

    if not winenv.WINDOWS:
        return
    winenv.use_icon(window.get_application(), "notedown.ico")
    big, small = native_icons(window)
    assert big and small, "the window has no icon of its own"


def test_ctrl_t_is_a_new_document_like_ctrl_n(window):
    accels = window.get_application().get_accels_for_action("win.new")
    assert "<Control>t" in accels and "<Control>n" in accels, accels


def test_replacing_one_regex_match_keeps_its_groups(window):
    document = window.new_document()
    document.set_text("a1 a2 a3\n")
    window.find.open(replace=True, seed=r"a(\d)")
    window.find.regex.set_active(True)
    window.find.replace_entry.set_text(r"[\1]")
    pump(300)
    window.find.replace_one()
    pump(200)
    assert document.text().startswith("[1] a2 a3"), repr(document.text())
    window.find.close()


def test_find_counts_what_it_highlights(window):
    document = window.new_document()
    document.set_text("cat Cat catalogue\n")
    window.find.open(seed="cat")
    pump(300)
    assert len(window.find._matches) == 3, window.find._matches
    window.find.word.set_active(True)
    pump(300)
    assert len(window.find._matches) == 2, window.find._matches
    window.find.word.set_active(False)
    window.find.close()


def test_the_page_is_drawn_in_the_font_the_settings_say(window):
    from notedown import render

    document = window.new_document(text="# Heading\n\nA paragraph.\n")
    document.render_now()
    pump(300)

    def paragraph_font():
        label = list(children(document.preview.column))[-1]
        return label.get_pango_context().get_font_description()

    window.config["preview_font_family"] = "Serif"
    window.config["preview_font_size"] = 12
    window._apply_fonts()
    settle(lambda: "serif" in paragraph_font().get_family().lower())
    small = paragraph_font()

    window.config["preview_font_size"] = 24
    window._apply_fonts()
    settle(lambda: paragraph_font().get_size() > small.get_size())
    large = paragraph_font()

    # The page is a tree of widgets and the font is one rule on the display;
    # this is the check that the rule reaches the text rather than the box.
    assert "serif" in small.get_family().lower(), small.to_string()
    assert large.get_size() > small.get_size(), (small.to_string(), large.to_string())
    assert render.page_font() == ("Serif", 24)

    for key in ("preview_font_family", "preview_font_size"):
        window.config[key] = DEFAULTS[key]
    window._apply_fonts()


def test_zooming_moves_both_halves_and_reset_puts_them_back(window):
    from notedown import render

    before = (window.config["editor_font_size"], window.config["preview_font_size"])
    window.zoom(1)
    assert window.config["editor_font_size"] == before[0] + 1
    assert window.config["preview_font_size"] == before[1] + 1
    assert window.active.editor.get_font_size() == before[0] + 1
    assert render.page_font()[1] == before[1] + 1

    window.zoom(0)
    assert window.config["editor_font_size"] == DEFAULTS["editor_font_size"]
    assert window.config["preview_font_size"] == DEFAULTS["preview_font_size"]


def test_the_session_is_what_was_open_when_the_window_closed(window):
    for name in ("s1.md", "s2.md"):
        path = os.path.join(scratch, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        window.open_path(path)
    window._shut_down()
    remembered = window.config["open_files"]
    assert any(p.endswith("s1.md") for p in remembered), remembered
    assert any(p.endswith("s2.md") for p in remembered), remembered


# ------------------------------------------------------------------ helpers --

def children(box):
    child = box.get_first_child()
    while child is not None:
        yield child
        child = child.get_next_sibling()


def descendants(widget):
    for child in children(widget):
        yield child
        yield from descendants(child)


def place(editor, line, column):
    buf = editor.buffer
    buf.place_cursor(buf.get_iter_at_line_offset(line - 1, column)[1])


def select(editor, start, end):
    buf = editor.buffer
    buf.select_range(buf.get_iter_at_offset(start), buf.get_iter_at_offset(end))


def select_lines(editor, first, last):
    buf = editor.buffer
    start = buf.get_iter_at_line(first)[1]
    end = buf.get_iter_at_line(last)[1]
    if not end.ends_line():
        end.forward_to_line_end()
    buf.select_range(start, end)


def press_enter(editor):
    """What the key handler does for Return, without a key press to deliver."""
    if not editor._on_enter():
        editor.buffer.insert_at_cursor("\n")


def pump(milliseconds):
    """Let the main loop run — for the idle callbacks the app leans on."""
    deadline = GLib.get_monotonic_time() + milliseconds * 1000
    context = GLib.MainContext.default()
    while GLib.get_monotonic_time() < deadline:
        while context.pending():
            context.iteration(False)
        GLib.usleep(2000)


def settle(predicate, milliseconds=3000):
    """Pump until `predicate()` is true, or give up and let the caller fail.

    For anything that waits on a *style* change rather than on an idle
    callback. Loading a provider does not restyle a widget there and then;
    GTK does it on the next style pass, which is driven by the frame clock,
    and a window that is not drawing may not get one for a while. A fixed
    `pump(300)` is therefore a coin toss — which is exactly how the preview
    font check behaved, passing or failing with the same code depending on
    how fast the compositor was feeling.
    """
    deadline = GLib.get_monotonic_time() + milliseconds * 1000
    context = GLib.MainContext.default()
    while GLib.get_monotonic_time() < deadline:
        if predicate():
            return True
        while context.pending():
            context.iteration(False)
        GLib.usleep(2000)
    return predicate()


def main():
    print("Notedown tests")
    print("\nthe parser")
    for name, fn in sorted(
            (n[5:].replace("_", " "), f) for n, f in globals().items()
            if n.startswith("test_") and f.__code__.co_argcount == 0):
        check(name, fn)

    print("\nwith a display")
    from gi.repository import Adw, Gio, GLib as _GLib, Gtk as _Gtk  # noqa: F811

    globals()["GLib"] = _GLib
    globals()["Gtk"] = _Gtk

    from notedown import widgets
    from notedown.editor import Editor
    from notedown.render import Preview
    from notedown.window import MainWindow

    app = Adw.Application(application_id="com.robin.NotedownTests",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

    def run_gtk_tests():
        widgets.install_css()
        window = MainWindow(app)
        window.present()
        pump(500)

        suites = [
            ("editor", Editor(), [f for n, f in sorted(globals().items())
                                  if n.startswith("test_") and _wants(f, "editor")]),
            ("preview", Preview(), [f for n, f in sorted(globals().items())
                                    if n.startswith("test_") and _wants(f, "preview")]),
            ("window", window, [f for n, f in sorted(globals().items())
                                if n.startswith("test_") and _wants(f, "window")]),
        ]
        for label, subject, tests in suites:
            for fn in tests:
                name = fn.__name__[5:].replace("_", " ")
                check(name, lambda fn=fn, subject=subject: fn(subject))

        app.release()
        app.quit()
        return GLib.SOURCE_REMOVE

    def activate(instance):
        instance.hold()
        GLib.timeout_add(250, run_gtk_tests)

    app.connect("activate", activate)
    app.run([])

    if failures:
        print(f"\n{len(failures)} failed:")
        for name, exc in failures:
            print(f"  {name}: {exc}")
        raise SystemExit(1)
    print("\nall good")


def _wants(fn, argument):
    code = getattr(fn, "__code__", None)
    return bool(code and code.co_argcount == 1 and code.co_varnames[0] == argument)


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
