# Notedown

A Markdown editor with the rendered page beside the text.

Type on the left, read on the right. Both halves scroll together, a click in
the page puts the cursor in the line that made it, and ticking a checkbox in
the page ticks it in the file.

```bash
python3 notedown.py            # reopens whatever you had open last
python3 notedown.py notes.md
python3 notedown.py -n notes.md   # in a window of its own
```

## A new line is a new line

Markdown says consecutive lines are one paragraph, and that a line break has
to be asked for with two trailing spaces or a backslash. That rule comes from
a world of 80-column terminals where a paragraph was wrapped by hand. In a
window that soft-wraps for you it is an invisible difference between what you
typed and what you see: you press Return and nothing happens.

So here a newline is a line break — in the preview, in the HTML export and in
the plain-text export, which all agree. A **blank** line still starts a new
paragraph, and paragraphs are still set further apart than the lines inside
one, so the structure of a hand-wrapped note is visible rather than a uniform
column.

A code span is unaffected: a newline inside backticks never reaches the rule,
so a span reads the same whether or not it was written across two lines.

*Preferences → Writing → A new line is a new line* turns it off, which gets
Markdown's own rule back exactly — worth it if a document is going somewhere
that follows it.

## The preview is not a browser

Almost every Markdown editor renders its preview in a web view, which means
shipping a browser engine to draw a page of text. On this machine that is
three hundred megabytes of resident memory to look at a shopping list.

So the preview here is built out of the widgets GTK already has. A heading is
a label, a code block is a monospace label on a tinted ground, a table is a
grid, a task is a real `GtkCheckButton`. The whole app is a few megabytes on
top of the toolkit, it starts instantly, and it runs comfortably on a machine
with three gigabytes of memory.

What that costs you is honest to state:

* **Raw HTML in a document is shown as written**, in a box that says so. This
  is not a browser and will not pretend that it is. Inline `<b>`, `<i>`,
  `<code>`, `<kbd>`, `<mark>`, `<sub>`, `<sup>` and `<br>` do work, because
  Pango has all of them.
* **Maths is laid out on one line.** `latex.py` parses the formula and draws
  it with Unicode and Pango — `\frac{a}{b}` becomes `a⁄b`, `\sum_{i=1}^n`
  keeps its limits as sub- and superscripts. It is readable, and it needs no
  TeX installed, but it is not KaTeX and a tall derivation will not stack.
* **Remote images are not fetched.** A note that quietly makes an HTTP request
  every time it is opened is a tracking pixel waiting to happen. Local images
  load; remote ones show their URL until you turn "Load Remote Images" on in
  the View menu.

Everything else — GitHub tables, task lists, footnotes, reference links,
strikethrough, `==highlights==`, YAML front matter, setext headings, nested
lists, lazy continuation — is there.

## The two halves stay together

Every block the parser produces remembers the line of the source it started
on. That one fact is what the rest of this is built from:

* **Scrolling** either pane moves the other to the matching place, including
  partway through a long code block.
* **Double-clicking** anything in the page puts the cursor on the line that
  produced it.
* **The outline** (F9) is the headings, live, with the one you are inside
  marked as you type.
* **A checkbox** in the page knows the exact line and column of its `[ ]` in
  the file, so clicking it edits two characters rather than rewriting the line.

## Writing in it

The editor is a `GtkTextView` — GtkSourceView 5 is not on this machine — with
the highlighting done on the buffer and the line numbers drawn beside it. The
highlighting is semantic rather than decorative: a heading is drawn at the
size it will be, bold source is bold, a link is underlined, a fence is
monospace on a tinted ground. The markers themselves are dimmed rather than
hidden, because hiding them is a trick that looks wonderful until you try to
put the cursor between two asterisks you can no longer see. Which monospace
font, and at what size, is yours to say — see [Settings](#settings).

The courtesies are the other half of it:

| | |
|---|---|
| Enter | continues the list, quote or table row you are in |
| Enter on an empty item | leaves the list |
| Enter in an ordered list | renumbers the rest of it |
| Tab | indents the item, or jumps to the next table cell |
| Backspace at the text | takes the list marker off |
| Ctrl+B on a word | wraps it — and unwraps it next time |
| Paste a URL over a selection | makes it a link |

### Keyboard

`Ctrl+?` shows all of this in the app.

| | | | |
|---|---|---|---|
| Ctrl+N | New | Ctrl+B | Bold |
| Ctrl+O | Open | Ctrl+I | Italic |
| Ctrl+S | Save | Ctrl+E | Inline code |
| Ctrl+Shift+S | Save as | Ctrl+K | Link |
| Ctrl+W | Close tab | Ctrl+Shift+X | Strikethrough |
| Ctrl+F | Find | Ctrl+Shift+H | Highlight |
| Ctrl+H | Replace | Ctrl+1…6 | Heading level |
| Ctrl+G | Next match | Ctrl+7 | No heading |
| F9 | Outline | Ctrl+Shift+L | Bulleted list |
| Alt+1/2/3 | Text, split, page | Ctrl+Shift+O | Numbered list |
| Ctrl++ / Ctrl+− | Text size | Ctrl+Shift+T | Task list |
| Ctrl+0 | Reset text size | Ctrl+D | Tick or untick |
| Ctrl+Shift+F | Focus mode | Ctrl+Shift+Q | Block quote |
| Ctrl+Shift+C | Statistics | Ctrl+/ | Comment out |
| Ctrl+Shift+E | Export as HTML | Ctrl+Tab | Next tab |

Find and replace does whole-word, match-case and regular expressions, and
counts the matches as you type.

## Getting it out again

**HTML** is written from the same tree the preview is built from, so the file
you send someone is the document you were looking at. One page, the stylesheet
inside it, images optionally inlined as data URIs — it opens in any browser
with no network and no server, and it has a print stylesheet, which is the
shortest route to a decent PDF.

**Plain text** drops the syntax and keeps the shape: headings stay on their
own lines, lists keep their markers, tables are aligned with spaces.

**Anything else** — PDF, Word, ODT, EPUB, LaTeX — goes through pandoc if you
have it. If you do not, the menu says so rather than offering an export that
fails.

`Copy as HTML` puts a fragment on the clipboard, for pasting into an email or
a CMS.

## The house style

The look is the house's, from [`house/`](../house/README.md): the same eight
themes as the rest of the suite, the same preference rows in the same order,
and the same material. The window paints nothing itself — everything sits on
one rounded, translucent slab of glass inside it, with a highlight that
follows the pointer and the theme's own slow light drifting behind.

**Porcelain** is the light default — warm paper under glass — and **Hearth**
the dark one, a lamplit room in winter. With **Follow the desktop** on, which it
is by default, the app uses one in a light session and the other in a dark
one and switches when the desktop does. The rest of the eight are in
Preferences → Look, along with how translucent the glass is, how far a
floating panel blurs what is behind it, and how round the corners are.

The window also remembers the size it was last closed at, and whether it was
maximised, and comes back that way. Under a tiling compositor it does
neither — see below.

## Settings

In `~/.config/notedown/config.json`, and all of it is reachable from the
menus. The ones worth knowing about:

| | |
|---|---|
| `editor_font_family` `editor_font_size` | the font the text half is written in |
| `preview_font_family` `preview_font_size` | the page's, in points — every size on the page is a ratio of it |
| `auto_save` | save a named file a second and a half after you stop typing |
| `focus_mode` | dim everything but the paragraph you are in |
| `typewriter` | keep the line you are typing in the middle of the view |
| `reading_width` | the measure in pixels, or 0 for the whole pane |
| `sync_scroll` | whether the two halves follow each other |
| `trim_trailing_space` | off by default — two trailing spaces are a line break |
| `backup_on_save` | keep the previous version as `file.md~` |
| `load_remote_images` | off by default, see above |

Both fonts are in the menu as well, under **Text → Fonts…**, where they are
picked and applied as you pick them; a family with nothing chosen is the one
the desktop would have used anyway. `Ctrl+±` moves the two sizes together and
`Ctrl+0` puts them back to the ones this app ships with, which are larger than
the toolkit's own — a page of prose is not a row of buttons and there is no
reason it should be set at the same size as one.

The documents you had open are reopened next time, and a file changed by
something else while you were away is reloaded when the window comes back —
silently if you had not touched it, and with a question if you had.

## On a tiling desktop

Written on niri, and it behaves there. It does not restore a maximised state
under a compositor that lays windows out itself, because "maximised" there
means taking the whole output away from the arrangement you made. Below 700px
the outline folds away, the header drops the buttons that are also in the
menu, and the split turns vertical — so the whole window fits in a 455px
column, which the test suite checks on every run.

## Running it

Needs GTK4, libadwaita and PyGObject. On Arch:

```bash
sudo pacman -S gtk4 libadwaita python-gobject
```

Nothing else is required. pandoc is optional and only for the exports GTK
cannot write itself.

To put it on the menu and on `$PATH`:

```bash
ln -sf "$PWD/notedown.py" ~/.local/bin/notedown
cp notedown.desktop ~/.local/share/applications/
cp notedown.svg ~/.local/share/icons/hicolor/scalable/apps/
update-desktop-database ~/.local/share/applications
```

To make it the default for `.md` files, and to give those files Notedown's
own page icon rather than the generic document one:

```bash
xdg-mime default notedown.desktop text/markdown text/x-markdown
cp notedown-document.svg \
   ~/.local/share/icons/hicolor/scalable/mimetypes/text-markdown.svg
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor
```

`.md`, `.markdown` and `.mkd` are `text/markdown` to
[shared-mime-info](https://gitlab.freedesktop.org/xdg/shared-mime-info), which
is what a file manager asks; the longer spellings Notedown opens are plain
text to it, and open in Notedown by being given to it rather than by being its
type.

The *Create Document* menu in Thunar, Nautilus and the rest is whatever is in
`~/Templates`, so a Markdown entry in it is a file:

```bash
printf '# Title\n\n' > ~/Templates/"Markdown File.md"
```

On Windows, see [the shared README](../README.md#on-windows); `notedown.bat`
starts it on MSYS2's Python. There, the default is chosen in Settings, which
the installer opens at Notedown's page:

```powershell
powershell -ExecutionPolicy Bypass -File windows\install.ps1 -Default Notedown
```

## Tests

```bash
python3 tests/test_notedown.py
```

The parser half needs nothing but Python and runs in under a second. The rest
needs a display: the editing courtesies against a real text buffer, the
preview against real widgets, and the window opening and saving real files in
a scratch directory it removes afterwards. Nothing in it follows a link —
a test that activates a URL hands it to the desktop portal, which opens a
browser on whatever session the suite is running in.

## How it is put together

| | |
|---|---|
| `markdown.py` | the parser — one tree, read by the preview, the exporter and the outline |
| `render.py` | that tree as GTK widgets, reusing the ones that have not changed |
| `editor.py` | the text view: highlighting, the gutter, the editing courtesies |
| `highlight.py` | colour for fenced code, without a syntax engine behind it |
| `latex.py` | `$maths$` drawn with Unicode and Pango, no TeX needed |
| `export.py` | HTML and plain text from the same tree; pandoc for the rest |
| `search.py` | find and replace, matched over the text rather than the buffer |
| `outline.py` | the headings sidebar |
| `document.py` | one open document: editor, preview, and the scroll sync |
| `window.py` | tabs, files, menus, keyboard, geometry |
| `config.py` | settings, in JSON, with the defaults as the schema |
| `winenv.py` | the Windows differences, so nothing else has to know |

## Licence

MIT. See [LICENSE](../LICENSE).
