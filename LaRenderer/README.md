# LaRenderer

A LaTeX editor with the pages next to the text. You type on the left, and
about six tenths of a second after you stop, the compiled document appears on
the right — with the preview scrolled to the line the caret is on.

```bash
larenderer                # reopen whatever you had last
larenderer paper.tex      # open a document
```

Real LaTeX does the typesetting: the buffer goes to `pdflatex` (or XeLaTeX, or
LuaLaTeX), poppler rasterises the pages, and SyncTeX maps the caret's line to a
spot on a page. Nothing about the output is approximated.

## Where it's installed

Entry points go through a symlink, the same way Git Manager reaches its
launcher, so moving this folder means re-pointing one file:

```bash
ln -sfn "$PWD/larenderer.py" ~/.local/bin/larenderer
```

| Location | What it is |
|---|---|
| `~/.local/bin/larenderer` | The symlink. Everything else depends on it. |
| `~/.local/share/applications/larenderer.desktop` | Applications menu, under **Office**. Registered for `text/x-tex`, so a `.tex` file can open with it. |
| `~/Desktop/larenderer.desktop` | Desktop icon. Executable, and marked trusted with `gio set … metadata::xfce-exe-checksum` so xfdesktop launches it without the "untrusted" prompt. |
| `~/.config/xfce4/panel/launcher-25/larenderer.desktop` | The bottom dock's copy, registered as panel plugin 25. |
| `~/.local/share/icons/hicolor/*/apps/larenderer.*` | The icon, scalable plus rasters at 24–256 px. |

The launcher resolves its own symlink with `realpath` before putting its
directory on `sys.path`, so it finds the `larenderer/` package whether it is
run through `~/.local/bin` or directly. There is no install step and nothing is
byte-compiled anywhere but next to the source.

The dock entry needs two xfconf keys as well, since the panel keeps its layout
in `xfconfd`'s memory and rewrites the XML behind your back:

```bash
xfconf-query -c xfce4-panel -p /plugins/plugin-25 --create -t string -s launcher
xfconf-query -c xfce4-panel -p /plugins/plugin-25/items --create --force-array \
    -t string -s larenderer.desktop
# then rewrite /panels/panel-2/plugin-ids whole, and: xfce4-panel -r
```

`panel-2` is the bottom dock. `plugin-ids` is a single array-valued property,
so the only way to add one entry is to write the entire array back —
`11 12 20 13 14 19 16 21 22 23 24 25 17 18`, which puts LaRenderer after Claude
Desk and before the trailing separator. A backup of the pre-change panel
configuration sits in `~/.config/xfce4/panel-backup-*/`.

**Changing the `.desktop` file breaks the desktop icon's trust.** The checksum
in `metadata::xfce-exe-checksum` is a plain sha256 of the file's contents, so
editing it — even just the `Icon=` line — means running `gio set` again or
xfdesktop starts asking whether you trust it.

To remove everything: delete the files above, drop `/plugins/plugin-25` and
`/plugins/plugin-25/items` with `xfconf-query -r`, remove `25` from
`plugin-ids`, and restart the panel.

Requires `python-gobject`, `gtk4`, `libadwaita`, `poppler` and a TeX
distribution, all already present.

---

## Layout

| File | Role |
|---|---|
| `larenderer.py` | The launcher. |
| `larenderer.svg` | The icon — a sheet with an integral on it, drawn as paths so it needs no font. |
| `larenderer.desktop` | The entry copied to the menu, the desktop and the dock. |
| `larenderer/app.py` | `Adw.Application` subclass, `--help`/`--version`, entry point. |
| `larenderer/window.py` | The window, and the compile cycle that drives everything. |
| `larenderer/editor.py` | The source editor: highlighting, gutter, editing courtesies. |
| `larenderer/preview.py` | The page column: lazy rasterisation, zoom, SyncTeX flash. |
| `larenderer/compiler.py` | Every TeX invocation, and the log parser. Knows no GTK. |
| `larenderer/packages.py` | The package catalogue, and what to install for a missing one. |
| `larenderer/templates.py` | Starting documents and the Insert menu's snippets. |
| `larenderer/widgets.py` | Problems list, dialogs, the stylesheet. |
| `larenderer/jobs.py` | Thread helpers — the only route from a worker back to the main loop. |
| `larenderer/config.py` | Settings, in `~/.config/larenderer/config.json`. |

## Your .tex folder stays clean

**Nothing is written next to your document.** No `.aux`, no `.log`, no `.pdf`
appearing beside `paper.tex` because you happened to look at it.

The buffer is written to a private build directory under
`~/.cache/larenderer/build/`, keyed by a hash of the document's path, and the
engine runs *there* — with `TEXINPUTS`, `BIBINPUTS` and `BSTINPUTS` pointing
recursively back at the real folder so `\input`, `\includegraphics{figures/…}`
and `\bibliography` all still resolve. The directory persists between sessions,
so reopening a document has its `.aux` ready and cross-references are right on
the first compile rather than the second. **Delete build files** in the menu
throws it away.

Because the buffer is written verbatim as the `.tex` the engine reads, the line
numbers in the log *are* the editor's line numbers. Nothing has to be mapped
back, which is why clicking an error always lands on the right line.

You get a PDF out of it with **Export PDF…** (Ctrl+E), which is the only thing
that writes where you point it.

## Compiling

Every keystroke restarts a 400 ms timer; when it expires the document compiles
on a worker thread. A compile still running when the next one is due is
**killed**, not waited for — so holding a key down never builds a queue. An
article with maths and a table takes 0.13–0.20 s to compile here, and the
measured gap between the last keystroke and the compile starting is 407 ms, so
the page lands roughly six tenths of a second after you stop typing.

LaTeX is run up to three times, because one pass is not enough to resolve
cross-references: if the log says `Rerun to get…` or `Label(s) may have
changed`, it goes again. BibTeX runs between the first and second pass when the
document cites anything and the `.aux` proves it. Passes beyond the first show
up in the status strip, so you can see when a document is costing you three.

A runaway macro is stopped after 40 seconds rather than hanging the window.
Shell escape is **off** — a `.tex` file can run arbitrary commands with it on —
and the toggle says so when you turn it on.

## Problems, not a log dump

The log is parsed into errors, warnings and bad boxes. Clicking one jumps the
editor to its line; lines with errors get a red dot in the gutter and a tinted
background. `max_print_line` is raised to 8192 before the engine runs, because
TeX otherwise wraps its log at 79 columns and splits messages mid-word, which
is what makes log parsing famously unreliable.

A few warnings are filtered as pure noise — the `Font shape` substitutions, and
`epstopdf`'s complaint that shell escape is off, which every document loading
`graphicx` emits by default. The raw log is one menu item away when you want
all of it.

## Packages

The **Packages** menu lists 46 packages across nine groups — the ones that
account for nearly every real document, weighted towards STEM: **Maths**
(`amsmath`, `mathtools`, `bm`, `braket`, `nicematrix`, `thmtools`, `cancel`),
**Science & engineering** (`siunitx`, `physics`, `mhchem`, `chemfig`,
`circuitikz`), **Graphics** (`tikz`, `pgfplots`, `tikz-cd`, `wrapfig`) and
**Code** (`listings`, `algorithm2e`, `algpseudocode`, `minted`), alongside the
ordinary layout, table, reference and language packages. Clicking one
inserts its `\usepackage` line after the last one in the preamble — with
sensible options already filled in, so `hyperref` arrives with `colorlinks` set
and `tikz` brings a `\usetikzlibrary` line with it. Anything the document
already loads is marked *loaded*.

**Packages that aren't installed are marked, and the menu tells you the exact
command to install them.** That answer is not guessed. TeX Live ships its own
database at `/usr/share/tlpkg/texlive.tlpdb`, which maps every file to a TeX
Live package and every package to a collection; Arch's `texlive-*` split
follows those collections exactly. So a missing `siunitx.sty` resolves through
`collection-mathscience` to `texlive-mathscience` — which is not the package
you would have guessed, and is the right one.

The stock Arch install here is `texlive-basic`, `-latex`, `-latexrecommended`
and `-fontsrecommended`, which covers 24 of the 46. The other 22 — essentially
all of the STEM half — come from four more collections:

| Collection | Brings | Download |
|---|---|---|
| `texlive-pictures` | `tikz`, `pgfplots`, `tikz-cd`, `circuitikz`, `chemfig` | 23 MiB |
| `texlive-mathscience` | `siunitx`, `physics`, `mhchem`, `algorithm2e`, `algpseudocode`, `nicematrix`, `thmtools` | 4 MiB |
| `texlive-latexextra` | `enumitem`, `cleveref`, `csquotes`, `titlesec`, `tcolorbox`, `braket`, `wrapfig`, `cancel`, `minted` | 37 MiB |
| `texlive-bibtexextra` | `biblatex` | 7 MiB |

```bash
sudo pacman -S texlive-pictures texlive-mathscience texlive-latexextra texlive-bibtexextra
```

About 71 MiB down, 275 MiB installed. `texlive-fontsextra` is deliberately not
in that list: it is 678 MiB for fonts you are unlikely to name.

The same resolution runs on the compile log, so a document that loads something
you don't have gets the install command at the top of the problems list, with a
**Copy** button, rather than `LaTeX Error: File 'tikz.sty' not found.`

Nothing is blocked. Load whatever you like; the app only tells you what it
would cost.

## The editor

GtkSourceView 5 is not installed on this machine — only the GTK3-era 3 and 4,
which a GTK4 app cannot use — so rather than add a dependency that isn't here,
the three things it would have provided are done directly:

- **Highlighting** is one regex pass over the buffer, debounced 90 ms.
  Commands, environment names, package names, comments, maths and `& # ~ ^ _`
  each get their own colour, in a light and a dark palette that follow the
  system theme. Commands stay highlighted *inside* maths; nothing is
  highlighted inside `verbatim` or `lstlisting`. Past 200 KB it scans only
  what's on screen.
- **The gutter** is a DrawingArea beside the text, painted from the scroll
  offset. It carries the line numbers, brightens the current one, and shows the
  error and warning dots.
- **Courtesies**: Enter after `\begin{env}` writes the `\end{env}` and puts you
  between them — but only if that environment is actually unclosed. Enter after
  an `\item` starts the next one. Tab and Shift+Tab indent a selection, Ctrl+/
  comments it. Selecting a word and inserting **Bold** wraps it.

## Keyboard

| Keys | Does |
|---|---|
| `Ctrl+R`, `F5` | Compile now |
| `Ctrl+S`, `Ctrl+Shift+S` | Save, save as |
| `Ctrl+E` | Export the PDF |
| `F7` | Jump the preview to the cursor |
| `F8` | Show or hide the problems list |
| `Ctrl+ +` / `Ctrl+ −` | Zoom the preview |
| `Ctrl+Page Up/Down` | Previous, next page |
| `Ctrl+/` | Comment or uncomment |
| `Ctrl+scroll` | Over the preview zooms it; over the text resizes the text |

## Notes

**The preview never blinks.** On recompile the existing pages keep their old
textures until the new ones arrive, and the scroll position is left alone
unless the page count or paper size actually changed. Watching a document
recompile as you type should not feel like watching it reload.

**A broken document keeps its last good page.** When a compile fails outright
and produces no PDF, the pane goes on showing the previous render rather than
going blank, because a blank pane while you fix a typo tells you nothing. That
is deliberate, not an accident of a leftover file: the engine's output is
compared against its timestamp from before the run, and when the page on screen
is the older one the status strip says *preview is the last good render* and
exporting warns you as well.

**The pages have a dark mode of their own.** Not just the window: the rendered
PDF. A white sheet is the brightest thing on a dark screen, so *Preview → Page
colours* — also in Preferences — will follow the app's theme, or pin the pages
light or dark. Dark pages are inverted by the renderer as the texture is drawn,
through one GSK colour matrix, so switching costs nothing: no second
rasterisation, no second cache, no wait. White paper lands on `#171717` and
black text a shade under white, rather than at pure black and white, which is
easier on the eyes than maximum contrast — and it is the colour the blank sheet
is painted before its texture arrives, so a page never flashes white on its way
in. **Hues survive.** A plain inversion would turn a blue hyperlink yellow;
inverting *and* rotating the hue a half turn — the pair CSS dark-mode filters
use, and linear, so the two collapse into that single matrix — flips lightness
and leaves colour alone. Blue links stay blue, red stays red, and a yellow
highlight comes out a dark yellow rather than blue.

**Pages render only when they are about to be seen**, one `pdftoppm` per page
at roughly 80 ms, cached by page and DPI, queued one at a time so twelve
visible pages don't open twelve processes. A hundred-page document opens as
fast as a two-page one.

**Following the cursor is off on open.** SyncTeX runs after each compile you
caused by typing, so the preview tracks where you're working — but not when a
document is first loaded, because nobody wants a file they just opened scrolled
to its last line.

**Text labels, not icons**, for the same reason Git Manager uses them: the
active `elementary` icon theme resolves several symbolic names successfully and
then renders them blank.

---

## Windows

The app runs on Windows unchanged in everything but three places, which are
answered once in `winenv.py` so no other module has to know which operating
system it is on:

* **Where files go.** XDG has one root with subdirectories; Windows has two
  roots. Settings go to `%APPDATA%`, regenerable state to `%LOCALAPPDATA%`.
  An explicitly set `XDG_CONFIG_HOME` still wins on both — the test suite
  points it at a scratch directory, and ignoring that would make a test run
  write to real settings.
* **Where the tools are.** A Windows installer puts its program in
  `Program Files` and edits the PATH of interactive shells, which a process
  started from a shortcut does not inherit. `winenv.which()` looks where the
  installers actually put things and puts what it finds on PATH, so child
  processes see it too.
* **Console windows.** Every child process of a windowed program opens a
  console unless told not to, so each one is started with `CREATE_NO_WINDOW`.

### Installing

GTK4 does exist for Windows, but only from MSYS2 — PyGObject publishes no
Windows wheels, so `pip install pygobject` cannot work. Install
[MSYS2](https://www.msys2.org/), then in an **MSYS2 MINGW64** shell:

```bash
pacman -S mingw-w64-x86_64-gtk4 mingw-w64-x86_64-libadwaita mingw-w64-x86_64-python-gobject
```

Then, from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File windows\install.ps1 -Desktop
```

That checks the runtime is really there before it makes anything, generates
the icons from the `.svg`, and puts a shortcut in the Start Menu (and on the
Desktop with `-Desktop`). `-Uninstall` removes them again.

The shortcuts run MSYS2's `pythonw.exe` directly rather than going through the
`.bat`. Windows looks for a program's DLLs beside the `.exe` first, so GTK is
found without anything being added to PATH — and going direct means no console
window blinks up before the app appears. The `.bat` in this folder stays for
running it from a command line, where being handed a file to open is the point.

### What it needs

A TeX distribution — MiKTeX or TeX Live — and poppler, which arrives with the
GTK stack above. Both are found in their installed locations without being on
PATH.

Two things genuinely differ from Linux rather than merely moving:

* **`TEXINPUTS` is separated by `;` on Windows, not `:`.** Hardcoding a colon
  turns the whole variable into one directory that does not exist, and every
  `\input` and `\includegraphics` silently stops resolving. It uses
  `os.pathsep` now.
* **SyncTeX records absolute paths under MiKTeX**, where TeX Live records the
  relative name it was given. Forward search asked for the relative name and
  got `No tag for paper.tex` every time, so the preview never followed the
  caret. It now tries both forms, likelier one first.

Missing packages are reported in the package manager this machine actually
has: `miktex packages install siunitx` under MiKTeX, `tlmgr install` under TeX
Live, and the existing `pacman` advice on Arch.
