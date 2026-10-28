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
# The Order, in miniature: a home of its own with a brain in it, and the
# shared note index in the scratch folder too — on Windows that index lives
# under %LOCALAPPDATA%, where every real app keeps the real one.
HOME = os.path.join(scratch, "home")
os.environ["ORDER_HOME"] = HOME
os.environ.pop("ANAM_HOME", None)
os.environ["LOCALAPPDATA"] = os.path.join(scratch, "local")
os.environ["ORDER_MACHINE"] = "test-machine"
# "Claude Code is installed" — as this interpreter, which no test ever asks
# anything: every request the suite makes goes to a stand-in script instead.
os.environ["ORDER_CLAUDE"] = sys.executable
for folder in ("SecondBrain/vault/nodes/idea", "SecondBrain/inbox", "SecondBrain/journal",
               "SecondBrain/learning", "Projects/Studies/Biology"):
    os.makedirs(os.path.join(HOME, *folder.split("/")), exist_ok=True)

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


# ---------------------------------------------------------- the Order -------
#
# Links and cards are not this parser's to define: a link is what brain.py
# indexes and a card is what cards.py schedules. These hold the page to them.

LINKED = ("See [[Library of Wisdom]], [[Fémek#Áram|the current]], [[Plain#Head]] "
          "and ![[photo.png]].\n\n`[[in code]]` and\n\n```\n[[in a fence]]\n```\n")


def test_a_wiki_link_is_a_link_to_its_target_heading_and_label():
    links = list(markdown.wiki_links(markdown.parse(LINKED)))
    assert [(n.text, n.title) for n in links] == [
        ("Library of Wisdom", ""), ("Fémek", "Áram"), ("Plain", "Head")], links
    assert [markdown.node_text(n) for n in links] == [
        "Library of Wisdom", "the current", "Plain › Head"]
    # Unresolved until the document that owns the tree says otherwise.
    assert all(n.url == "" for n in links)


def test_the_page_underlines_what_the_index_counts_as_a_link():
    from notedown import brain

    page = {(n.text, n.title) for n in markdown.wiki_links(markdown.parse(LINKED))}
    index = {(link.target, link.heading or "") for link in brain.wikilinks(LINKED)
             if not link.target.endswith(".png")}
    assert page == index, (page, index)


def test_the_editor_highlights_what_the_index_counts_as_a_link():
    from notedown import brain
    from notedown.editor import _INLINE

    for sample in ("[[A]]", "[[A#B]]", "[[A|b]]", "[[A#B|c d]]", "![[A]]", "[[a]b]]",
                   "[[]]", "[[x|y|z]]", "[[A^b]]", "[[ #x]]"):
        editor = [m.group(0) for m in _INLINE.finditer(sample) if m.lastgroup == "wikilink"]
        index = [sample[link.start:link.end] for link in brain.wikilinks(sample)]
        assert editor == index, (sample, editor, index)


def test_an_embedded_picture_is_a_picture_and_code_is_code():
    doc = markdown.parse(LINKED)
    images = [n for n in doc.blocks[0].spans if n.kind == "image"]
    assert images and images[0].url == "photo.png"
    assert [n.kind for n in doc.blocks[1].spans][0] == "code"
    assert doc.blocks[2].kind == "code" and "[[in a fence]]" in doc.blocks[2].text


CARDED = """---
title: A gyökér
---

# Biology

- Wilson-tétel :: (p−1)! ≡ −1 (mod p)
- il gesso ::: kréta
Code `std::vector :: nope` stays prose.

Q: Miért kevés a gyökérnyomás
önmagában?
A: Mert csak néhány méterre
emeli a vizet.

A {{mitokondrium}} termeli az {{ATP|energia}}-t.

| a | b |
|---|---|
| The {{xylem}} | carries water |

```
not :: a card
```
"""


def test_a_card_is_a_block_of_its_own_in_each_of_its_forms():
    from notedown import cards

    doc = markdown.parse(CARDED)
    found = []

    def walk(blocks):
        for block in blocks:
            if block.kind == "card":
                found.append(block)
            if block.kind == "list":
                for item in block.items:
                    walk(item.children)
            if block.kind == "quote":
                walk(block.children)

    walk(doc.blocks)
    assert [b.form for b in found] == ["line", "both", "qa", "cloze"], [b.form for b in found]
    line, both, qa, cloze = found
    assert text_of(line) == "Wilson-tétel" and markdown.spans_text(line.back).startswith("(p")
    assert text_of(both) == "il gesso" and markdown.spans_text(both.back) == "kréta"
    assert markdown.spans_text(qa.spans, "\n") == "Miért kevés a gyökérnyomás\nönmagában?"
    assert markdown.spans_text(qa.back, "\n") == "Mert csak néhány méterre\nemeli a vizet."
    gaps = [n for n in cloze.spans if n.kind == "cloze"]
    assert [(markdown.node_text(n), n.title) for n in gaps] == [
        ("mitokondrium", ""), ("ATP", "energia")]
    table = next(b for b in doc.blocks if b.kind == "table")
    assert [n.kind for n in table.rows[0][0]] == ["text", "cloze"]
    # The count is the scheduler's own, both directions and every gap.
    assert len(doc.cards) == len(cards.parse_text(CARDED, "x.md")) == 7


def test_a_note_with_cards_off_has_no_cards_and_its_braces_are_text():
    doc = markdown.parse("---\ncards: false\n---\nA :: B\n\nThe {{x}} is here.\n")
    assert doc.cards == []
    assert [b.kind for b in doc.blocks] == ["frontmatter", "para", "para"]
    assert text_of(doc.blocks[2]) == "The {{x}} is here."


def test_cards_are_counted_as_words_and_exported_as_cards():
    doc = markdown.parse("A :: B\n\nC ::: D\n\nThe {{gap|hint}} here.\n")
    assert markdown.statistics_of(doc)["words"] == 7
    out = export.to_html(doc, standalone=False)
    assert '<div class="card line"><p class="front">A</p><p class="back">B</p></div>' in out
    assert '<span class="cloze" title="hint">gap</span>' in out
    text = export.to_text(doc)
    assert "A → B" in text and "C ↔ D" in text and "The gap here." in text


def test_an_exported_link_goes_to_its_note_and_an_unwritten_one_is_words():
    doc = markdown.parse("[[Here#A Head|here]] and [[Nowhere]]\n")
    here, nowhere = markdown.wiki_links(doc)
    here.url = os.path.join(scratch, "notes", "Here.md")
    out = export.to_html(doc, standalone=False, base_dir=scratch)
    assert '<a href="notes/Here.md#a-head">here</a>' in out, out
    assert "Nowhere" in out and 'href="wiki' not in out
    assert "here and Nowhere" in export.to_text(doc)


def test_a_wiki_href_carries_its_target_and_heading_through_the_page():
    from notedown import render

    href = render.wiki_href("Fémek & co/Áram", "A # B")
    assert render.parse_wiki_href(href) == ("Fémek & co/Áram", "A # B")
    assert render.parse_wiki_href(render.wiki_href("X")) == ("X", "")


def test_a_note_made_from_a_link_is_made_beside_the_one_that_links():
    from notedown import order

    folder = os.path.join(scratch, "made")
    assert order.new_note_path(folder, "Ide: kettő?") == os.path.join(folder, "Ide kettő.md")
    assert order.new_note_path(folder, "Sub/Deeper") == os.path.join(folder, "Sub", "Deeper.md")
    # A way out of the folder is not followed.
    assert order.new_note_path(folder, "../../x") == os.path.join(folder, "x.md")


def test_a_completed_link_is_the_title_unless_another_note_answers_to_it():
    from notedown import order

    here = os.path.join(scratch, "a", "Here.md")
    mine = os.path.join(scratch, "b", "Day.md")
    other = os.path.join(scratch, "a", "Day.md")
    assert order.link_text("Day", mine, here, lambda _n, _near: mine) == "Day"
    assert order.link_text("Day", mine, here, lambda _n, _near: other) == "../b/Day|Day"
    # A title that cannot be a target is not typed as one.
    assert order.link_text("A|B", mine, None, lambda *_: "") == "Day"


def test_only_the_cards_in_an_answer_are_kept_and_never_twice():
    from notedown import order

    reply = ("Here are some cards for you:\n\n"
             "- Wilson-tétel :: (p−1)! ≡ −1 (mod p)\n"
             "- Fermat :: a^p ≡ a\n"
             "The {{xylem}} carries water.\n"
             "Q: Why?\nA: Because.\n\n"
             "Hope these help!\n")
    text, count = order.proposed_cards(reply, existing="- Wilson-tétel :: whatever\n")
    assert "Wilson" not in text and "Here are" not in text and "Hope" not in text, text
    assert "- Fermat :: a^p ≡ a" in text and "{{xylem}}" in text and "Q: Why?\nA: Because." in text
    assert count == 3, (count, text)
    # Fenced replies are read inside the fence.
    fenced, n = order.proposed_cards("Sure:\n```markdown\nA :: B\n```\n")
    assert (fenced, n) == ("A :: B", 1)
    assert order.proposed_cards("No cards here, sorry.") == ("", 0)


def test_the_index_is_not_read_until_it_is_built_and_a_sibling_needs_none():
    from pathlib import Path

    from notedown import brain, order

    notes = brain.Notes(db_path=Path(scratch) / "fresh-index.db", roots=[])
    assert not order.index_ready(notes)
    notes.refresh()
    assert order.index_ready(notes)

    folder = os.path.join(scratch, "unindexed")
    os.makedirs(folder, exist_ok=True)
    near, sibling = os.path.join(folder, "here.md"), os.path.join(folder, "Sibling.md")
    open(sibling, "w").close()
    assert same(order.resolve(None, "Sibling", near), sibling)
    assert order.resolve(None, "Nobody", near) == ""


def test_mnemosyne_is_found_only_beside_notedown():
    from notedown import order

    beside = os.path.join(scratch, "checkout")
    assert order.mnemosyne(beside) is None
    os.makedirs(os.path.join(beside, "Mnemosyne"), exist_ok=True)
    script = os.path.join(beside, "Mnemosyne", "mnemosyne.py")
    open(script, "w").close()
    assert order.mnemosyne(beside) == script
    argv = order.study_command("note.md", script, "python")
    assert argv == ["python", script, "--study", os.path.abspath("note.md")]


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


def tagged(editor, name):
    """The stretches of the buffer that carry tag `name`, as text."""
    buf = editor.buffer
    tag = buf.get_tag_table().lookup(name)
    found, it = [], buf.get_start_iter()
    while True:
        if it.starts_tag(tag):
            end = it.copy()
            end.forward_to_tag_toggle(tag)
            found.append(buf.get_text(it, end, True))
        if not it.forward_to_tag_toggle(tag):
            return found


def test_a_wiki_link_is_highlighted_like_a_link(editor):
    editor.set_text("See [[Fémek#Áram|the current]] and `[[code]]`.")
    editor._highlight_now()
    assert tagged(editor, "link") == ["[[Fémek#Áram|the current]]"], tagged(editor, "link")
    # What is shown stands out; the target behind the label steps back.
    assert "[[Fémek#Áram|" in tagged(editor, "marker")


def test_card_syntax_is_coloured_where_cards_py_finds_cards(editor):
    editor.set_text("- A :: B\nC ::: D\n`x :: y` is code\n\nQ: Why?\nA: So.\n\n"
                    "The {{gap|hint}} here.\n\n```\nE :: F\n```\n")
    editor._highlight_now()
    assert tagged(editor, "cardmark") == ["::", ":::", "Q:", "A:"], tagged(editor, "cardmark")
    assert tagged(editor, "cloze") == ["gap"], tagged(editor, "cloze")
    editor.set_text("---\ncards: false\n---\nA :: B\n")
    editor._highlight_now()
    assert tagged(editor, "cardmark") == []


def test_two_brackets_offer_notes_and_enter_closes_the_link(editor):
    from gi.repository import Gdk

    from notedown.editor import Editor

    asked = []

    def source(query):
        asked.append(query)
        return [(title, "~/notes", title) for title in ("Fémek", "Fermat")]

    linking = Editor(complete=source)
    linking.set_text("Read [[fe")
    place(linking, 1, 9)
    linking.completion.update()
    assert linking.completion.active and asked[-1] == "fe"
    assert [item[0] for item in linking.completion.items] == ["Fémek", "Fermat"]
    linking.completion.key(Gdk.KEY_Down, 0)
    assert linking.completion.key(Gdk.KEY_Return, 0) is True
    assert linking.get_text() == "Read [[Fermat]]", repr(linking.get_text())
    assert not linking.completion.active

    # Already closed: the name goes in, and the cursor goes past the ]].
    linking.set_text("Read [[fe]] on")
    place(linking, 1, 9)
    linking.completion.update()
    linking.completion.accept()
    assert linking.get_text() == "Read [[Fémek]] on", repr(linking.get_text())
    assert linking.cursor_position() == (1, 15)

    # Escape closes it for this link, and in code there is none to close.
    linking.set_text("[[fe")
    place(linking, 1, 4)
    linking.completion.update()
    linking.completion.key(Gdk.KEY_Escape, 0)
    linking.completion.update()
    assert not linking.completion.active
    linking.set_text("`[[fe")
    place(linking, 1, 5)
    linking.completion.update()
    assert not linking.completion.active
    linking.close()


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


def test_a_link_that_leads_somewhere_is_a_link_and_one_that_does_not_is_quieter(preview):
    doc = markdown.parse("[[Here]] and [[Nowhere]]\n")
    here, _nowhere = markdown.wiki_links(doc)
    here.url = os.path.join(scratch, "Here.md")
    preview.render(doc, scratch)
    label = next(w for w in descendants(preview.column) if isinstance(w, Gtk.Label))
    markup = label.get_label()
    assert 'href="wiki:Here"' in markup and 'href="wiki:Nowhere"' in markup, markup
    assert markup.count("alpha=") == 1 and '<span alpha="55%">Nowhere</span>' in markup, markup
    # Once the index knows the second one, the widget is rebuilt for it.
    doc = markdown.parse("[[Here]] and [[Nowhere]]\n")
    for node in markdown.wiki_links(doc):
        node.url = os.path.join(scratch, node.text + ".md")
    preview.render(doc, scratch)
    label = next(w for w in descendants(preview.column) if isinstance(w, Gtk.Label))
    assert "alpha=" not in label.get_label()


def test_a_card_is_drawn_as_a_card(preview):
    preview.render(markdown.parse("Front :: Back\n\n- In a list ::: both ways\n"), "")
    boxes = [w for w in descendants(preview.column) if w.has_css_class("md-card")]
    assert len(boxes) == 2, boxes
    texts = [w.get_text() for w in descendants(boxes[0]) if isinstance(w, Gtk.Label)]
    assert texts == ["Front", "Back"], texts
    assert boxes[1].has_css_class("md-card-both")
    assert any(w.has_css_class("md-card-ways") for w in descendants(boxes[1]))
    assert boxes[0]._line == 0


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


def test_the_status_bar_says_saved_once_it_is(window):
    path = os.path.join(scratch, "status.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("text\n")
    document = window.open_path(path)
    document.editor.buffer.insert_at_cursor("more ")
    pump(400)                       # past the render tick, as typing would be
    assert window.status_state.get_text() == "Unsaved"
    window.save_document(document, silent=True)
    pump(400)
    assert window.status_state.get_text() == "Saved", window.status_state.get_text()


def test_text_typed_with_the_preview_hidden_shows_when_it_comes_back(window):
    window.new_document()
    document = window.active
    document.set_panes(True, False)
    document.editor.buffer.insert_at_cursor("# Pasted while hidden\n")
    pump(400)
    document.set_panes(True, True)
    document.ensure_rendered()
    shown = [w.get_text() for w in _descendants_of(document.preview.column)
             if isinstance(w, Gtk.Label)]
    assert "Pasted while hidden" in shown, shown


def _descendants_of(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants_of(child)
        child = child.get_next_sibling()


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


# ------------------------------------------------------ the Order, windowed --

def brain_note(rel, text):
    """A note in the scratch home, at its home-relative path."""
    path = os.path.join(HOME, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def reindex(window):
    window.notes.refresh()
    window.notes_changed()


def same(a, b):
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def test_a_link_leads_to_its_note_and_to_the_heading_it_names(window):
    from notedown import render

    target = brain_note("SecondBrain/vault/nodes/idea/femek--1a2b3c4d.md",
                        "---\ntitle: Fémek\n---\n# Fémek\n\nintro\n\n## Áram\n\nflows\n")
    source = brain_note("Projects/Studies/Biology/2026-10-27.md",
                        "# Óra\n\nSee [[femek#aram|the current]].\n")
    reindex(window)
    document = window.open_path(source)
    link = next(markdown.wiki_links(document.parsed))
    assert same(link.url, target), link.url

    # The [[ list finds it by its title, accents aside, and types the title.
    suggestions = window.link_suggestions("fem", source)
    assert ("Fémek", "SecondBrain/vault/nodes/idea", "Fémek") in suggestions, suggestions

    window.open_url(document, render.wiki_href(link.text, link.title))
    pump(200)
    assert window.active.is_at(target)
    assert window.active.editor.cursor_position()[0] == 8, window.active.editor.cursor_position()


def test_a_link_to_a_note_not_written_offers_it_and_makes_it_beside_this_one(window):
    from notedown import order, render

    source = brain_note("Projects/Studies/Biology/Növények.md", "# Növények\n\n[[Gyökér]] next.\n")
    reindex(window)
    document = window.open_path(source)
    assert next(markdown.wiki_links(document.parsed)).url == ""

    offered = []
    window.offer_new_note = lambda doc, target: offered.append((doc, target))
    try:
        window.open_url(document, render.wiki_href("Gyökér"))
    finally:
        del window.offer_new_note
    assert offered == [(document, "Gyökér")], offered

    where = order.new_note_path(document.folder, "Gyökér")
    window.make_note("Gyökér", where)
    settle(lambda: window.active is not None and window.active.is_at(where))
    with open(where, encoding="utf-8") as fh:
        assert fh.read() == "# Gyökér\n\n"
    settle(lambda: next(markdown.wiki_links(document.parsed)).url != "")
    assert same(next(markdown.wiki_links(document.parsed)).url, where)


def test_the_backlinks_drawer_lists_the_notes_that_link_here(window):
    target = brain_note("SecondBrain/vault/nodes/idea/Library of Wisdom.md",
                        "# Library of Wisdom\n\n[[Not written]]\n")
    brain_note("SecondBrain/journal/2026/2026-10-26.md",
               "# 26\n\nRead in the [[Library of Wisdom]] today.\n")
    reindex(window)
    window.config["backlinks_visible"] = False
    window.lookup_action("toggle-backlinks").activate(None)
    try:
        assert window.backlinks.get_reveal_child()
        window.open_path(target)
        pump(200)
        links = window.backlinks.links
        assert [(os.path.basename(str(link.path)), link.line) for link in links] == \
            [("2026-10-26.md", 3)], links
        assert window.backlinks.heading.get_text() == "Linked from 1 note"
        assert window.backlinks.unwritten == ["Not written"]
        assert window.backlinks.count.get_text() == "1 link leads nowhere yet"

        window.backlinks.list.emit("row-activated", window.backlinks.list.get_row_at_index(0))
        pump(200)
        assert window.active.is_at(str(links[0].path))
        assert window.active.editor.cursor_position()[0] == 3
    finally:
        window.lookup_action("toggle-backlinks").activate(None)


def test_the_switcher_finds_notes_by_name_and_starts_from_the_recent_ones(window):
    brain_note("SecondBrain/vault/nodes/idea/kreta--0badf00d.md",
               "---\ntitle: Kréta\naliases: [chalk]\n---\nbody\n")
    reindex(window)
    recent = brain_note("Projects/Studies/Biology/recent.md", "# Most recent\n")
    window.open_path(recent)
    window.new_document()
    dialog = window.show_switcher()
    try:
        assert dialog.caption.get_visible()
        assert dialog.rows[0][0] == "Most recent" and same(dialog.rows[0][2], recent)
        dialog.entry.set_text("CHALK")
        dialog.fill()
        assert [row[0] for row in dialog.rows] == ["Kréta"], dialog.rows
        opened = []
        dialog.on_open = opened.append
        dialog.open_selected()
        assert opened and opened[0].endswith("kreta--0badf00d.md"), opened
        # Opening a note is also closing the switcher.
        assert dialog.get_root() is None
    finally:
        if dialog.get_root() is not None:
            dialog.close()


def test_the_selection_goes_to_the_inbox_and_undo_takes_it_back(window):
    inbox = os.path.join(HOME, "SecondBrain", "inbox")
    path = brain_note("Projects/Studies/Biology/capture.md",
                      "first line\na thought worth keeping\n")
    document = window.open_path(path)
    buf = document.editor.buffer
    start = buf.get_iter_at_line(1)[1]
    end = start.copy()
    end.forward_to_line_end()
    buf.select_range(start, end)

    toasts = []
    window._toast_with_undo = lambda title, undo: toasts.append((title, undo))
    before = set(os.listdir(inbox))
    try:
        window.send_to_inbox()
        settle(lambda: bool(toasts))
    finally:
        del window._toast_with_undo
    made = (set(os.listdir(inbox)) - before).pop()
    assert made.endswith(" a thought worth keeping.md"), made
    with open(os.path.join(inbox, made), encoding="utf-8") as fh:
        body = fh.read()
    assert "origin: notedown" in body and body.rstrip().endswith("a thought worth keeping")
    assert made in toasts[0][0]
    # Sent, not moved: the note it came from is as it was.
    assert document.text() == "first line\na thought worth keeping\n"

    toasts[0][1]()
    assert made not in os.listdir(inbox)

    # A saved note with nothing selected already has a place, and is not sent.
    buf.place_cursor(buf.get_start_iter())
    window.send_to_inbox()
    pump(200)
    assert set(os.listdir(inbox)) == before


def test_a_new_note_sent_whole_leaves_its_tab_empty_until_undone(window):
    document = window.new_document()
    document.editor.buffer.insert_at_cursor("# An idea\n\nto file later\n")
    toasts = []
    window._toast_with_undo = lambda title, undo: toasts.append((title, undo))
    try:
        window.send_to_inbox()
        settle(lambda: bool(toasts))
    finally:
        del window._toast_with_undo
    assert document.text() == "" and not document.modified
    assert "An idea.md" in toasts[0][0], toasts
    toasts[0][1]()
    assert document.text() == "# An idea\n\nto file later\n" and document.modified
    document.set_text("")


def test_saving_puts_the_note_in_the_index_at_once(window):
    path = brain_note("SecondBrain/vault/nodes/idea/renamed.md", "# Old name\n")
    reindex(window)
    document = window.open_path(path)
    document.editor.buffer.set_text("# Brand new name\n")
    window.save_document(document, silent=True)
    settle(lambda: window.resolve_link("Brand new name") != "")
    assert same(window.resolve_link("Brand new name"), path)


def test_todays_note_is_opened_from_the_journal(window):
    from datetime import date

    today = date.today()
    window.open_daily_note()
    expected = os.path.join(HOME, "SecondBrain", "journal", f"{today:%Y}", f"{today:%Y-%m-%d}.md")
    assert window.active.is_at(expected), window.active.path
    assert window.active.text().startswith(f"# {today:%Y-%m-%d}")


def test_the_status_bar_counts_the_cards_the_scheduler_will_find(window):
    path = brain_note("Projects/Studies/Biology/counted.md", "A :: B\n\nC ::: D\n")
    window.open_path(path)
    pump(300)
    assert window.status_cards.get_visible()
    assert window.status_cards.get_text() == "3 cards", window.status_cards.get_text()
    window.new_document()
    pump(300)
    assert not window.status_cards.get_visible()


def test_study_is_offered_only_beside_mnemosyne(window):
    from notedown import order

    shipped = order.CHECKOUT
    beside = os.path.join(scratch, "apps")
    script = os.path.join(beside, "Mnemosyne", "mnemosyne.py")
    os.makedirs(os.path.dirname(script), exist_ok=True)
    open(script, "w").close()
    spawned = []
    try:
        order.CHECKOUT = os.path.join(scratch, "no-apps-here")
        window._offer_study()
        assert window.lookup_action("study") is None
        order.CHECKOUT = beside
        window._offer_study()
        action = window.lookup_action("study")
        assert action is not None

        path = brain_note("Projects/Studies/Biology/to-study.md", "A :: B\n")
        window.open_path(path)
        pump(300)
        assert action.get_enabled()
        window._spawn = lambda argv, cwd: spawned.append(argv)
        window.study_note()
        assert spawned and spawned[0][1:] == [script, "--study", os.path.abspath(path)], spawned
    finally:
        order.CHECKOUT = shipped
        window.__dict__.pop("_spawn", None)
        window._offer_study()


def test_no_key_does_two_things_and_every_new_one_is_written_down(window):
    from notedown.window import ACCELERATORS, SHORTCUTS

    app = window.get_application()
    actions = {action for action, _keys in ACCELERATORS}
    actions |= {f"win.heading{level}" for level in range(7)}
    actions |= {f"win.view-{mode}" for mode in ("editor", "split", "preview")}
    seen = {}
    bound = [(action, accel) for action in sorted(actions)
             for accel in app.get_accels_for_action(action)]
    # The app's own two, which NotedownApp installs and this test app does not.
    bound += [("app.quit", "<Control>q"), ("app.new-window", "<Control><Shift>n")]
    for action, accel in bound:
        _ok, key, mods = Gtk.accelerator_parse(accel)
        pressed = (Gdk.keyval_to_lower(key), int(mods))
        assert pressed not in seen, f"{accel} is both {seen[pressed]} and {action}"
        seen[pressed] = action

    for action, _keys in ACCELERATORS:
        name = action.split(".", 1)[1]
        assert window.lookup_action(name) is not None, f"{action} is bound but does not exist"

    sheet = " ".join(keys for _group, rows in SHORTCUTS for keys, _what in rows)
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
        readme = fh.read()
    for keys in ("Ctrl+P", "Ctrl+Shift+B", "Ctrl+Shift+D", "Ctrl+Shift+I", "Ctrl+Shift+A"):
        assert keys in sheet, f"{keys} is not in the shortcuts sheet"
        assert f"| {keys} |" in readme, f"{keys} is not in the README's table"


STANDIN = """
import json, os, sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "reply.txt"), encoding="utf-8") as fh:
    reply = fh.read()
with open(os.path.join(here, "prompt.txt"), "w", encoding="utf-8") as fh:
    fh.write(sys.argv[sys.argv.index("-p") + 1])
for event in ({"type": "system", "session_id": "s-1"},
              {"type": "assistant", "session_id": "s-1",
               "message": {"content": [{"type": "text", "text": reply}]}},
              {"type": "result", "result": reply, "is_error": False, "session_id": "s-1"}):
    print(json.dumps(event), flush=True)
"""


def standin(reply):
    """Claude Code, played by a script: it answers `reply`, and keeps the prompt."""
    folder = os.path.join(scratch, "claude")
    os.makedirs(folder, exist_ok=True)
    script = os.path.join(folder, "claude_standin.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(STANDIN)
    with open(os.path.join(folder, "reply.txt"), "w", encoding="utf-8") as fh:
        fh.write(reply)
    return [sys.executable, script]


def test_claude_answers_beside_the_note_on_the_page(window):
    from pathlib import Path

    assert window.assistant is not None, "ORDER_CLAUDE should make Claude Code available"
    window.assistant.command = standin("It is about **roots**. See [[Fémek]].")
    path = brain_note("Projects/Studies/Biology/roots.md", "# Roots\n\ntext\n")
    window.open_path(path)
    window.show_claude(True)
    try:
        assert window.claude_split.get_show_sidebar()
        window.assistant.ask("What is this about?")
        settle(lambda: not window.assistant.running, 15000)
        assert window.assistant.text == "It is about **roots**. See [[Fémek]].", \
            window.assistant.status.get_text()
        with open(os.path.join(scratch, "claude", "prompt.txt"), encoding="utf-8") as fh:
            prompt = fh.read()
        assert prompt.startswith("What is this about?") and Path(path).as_posix() in prompt
        shown = " ".join(w.get_text() for w in descendants(window.assistant.page.column)
                         if isinstance(w, Gtk.Label))
        assert "It is about roots. See Fémek." in shown, shown
        assert window.assistant.session == "s-1" and window.assistant.desk_button.get_visible()
    finally:
        window.show_claude(False)


def test_cards_from_claude_are_shown_first_and_added_only_when_asked(window):
    window.assistant.command = standin(
        "Some cards:\n\n- Xylem :: carries water\n- Phloem :: carries sugar\n")
    original = "# Vessels\n\n- Xylem :: carries water\n"
    path = brain_note("Projects/Studies/Biology/vessels.md", original)
    document = window.open_path(path)
    window.assistant.make_cards()
    settle(lambda: not window.assistant.running, 15000)
    assert window.assistant.proposal == "- Phloem :: carries sugar", window.assistant.proposal
    assert window.assistant.add_button.get_visible()
    assert window.assistant.add_button.get_label() == "Add 1 card"
    assert any(w.has_css_class("md-card") for w in descendants(window.assistant.page.column))
    assert document.text() == original, "cards were written before they were approved"

    window.assistant.add_cards()
    assert document.text() == original + "\n- Phloem :: carries sugar\n", repr(document.text())
    document.editor.buffer.undo()
    assert document.text() == original


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
    from gi.repository import Adw, Gdk as _Gdk, Gio, GLib as _GLib, Gtk as _Gtk  # noqa: F811

    globals()["Gdk"] = _Gdk
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
