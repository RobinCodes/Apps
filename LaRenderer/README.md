# LaRenderer

A LaTeX editor with the pages next to the text. You type on the left, and
about six tenths of a second after you stop, the compiled document appears on
the right — with the preview scrolled to the line the caret is on.

```bash
larenderer                # reopen whatever you had last
larenderer paper.tex      # open a document
larenderer ~/thesis       # open a folder, the way an IDE opens a project
```

Real LaTeX does the typesetting: the buffer goes to `pdflatex` (or XeLaTeX, or
LuaLaTeX), poppler rasterises the pages, and SyncTeX maps the caret's line to a
spot on a page. Nothing about the output is approximated.

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
| `larenderer/window.py` | The window, the tabs, and the compile cycle that drives everything. Also where the folder, git and Overleaf meet the editor. |
| `larenderer/document.py` | One open document: its editor, its preview, its build. One per tab. |
| `larenderer/filetree.py` | The folder side bar: every file, live, with git's letters beside the changed ones. |
| `larenderer/project.py` | Which file in a folder is the document, and which one a part belongs to. Knows no GTK. |
| `larenderer/gitlink.py` | The line to Git Manager, started in the background on first use. No git runs in this process. |
| `larenderer/overleafweb.py` | Overleaf's own editor in a tab, through WebKit — loaded only when one is opened. |
| `larenderer/editor.py` | The source editor: highlighting, gutter, editing courtesies. |
| `larenderer/preview.py` | The page column: lazy rasterisation, zoom, SyncTeX flash. |
| `larenderer/compiler.py` | Every TeX invocation, and the log parser. Knows no GTK. |
| `larenderer/packages.py` | The package catalogue, and what to install for a missing one. |
| `larenderer/templates.py` | Starting documents and the Insert menu's snippets. |
| `larenderer/widgets.py` | Problems list, dialogs, the stylesheet. |
| `larenderer/jobs.py` | Thread helpers — the only route from a worker back to the main loop. |
| `larenderer/config.py` | Settings, in `~/.config/larenderer/config.json`. |
| `tests/test_larenderer.py` | `python3 tests/test_larenderer.py` — the log parser against captured text, which needs nothing installed, and a real compile where there is an engine to do it. |

## Tabs

Several documents at once, the way a text editor does it. Open a second file
and it arrives in a tab beside the first rather than replacing it.

| | |
|---|---|
| `Ctrl+T` / `Ctrl+N` | A new empty document |
| `Ctrl+W` | Close this one |
| `Ctrl+Tab` / `Ctrl+Shift+Tab` | The next tab, or the previous one |

`Ctrl+Page Up` and `Ctrl+Page Down` stay what they were — the previous and
next *page* of the preview — so the tabs take `Ctrl+Tab` alone rather than
fighting them for a key that would then mean two things.

Each tab is a whole document, not just a buffer: its own text, its own
rendered pages at their own scroll position, its own problems list, and its
own compile. A document being rebuilt in a background tab writes its result
into that tab, so switching away mid-compile does not land someone else's
errors on the document in front of you.

Opening a file that is already open selects the tab it is in rather than
making a second one — two tabs over one path would be two buffers racing each
other to save. The tab bar hides itself when only one document is open, so a
single document looks exactly as it did before.

The tabs that were open when the window closed come back when it opens, in
the same order; a file deleted in between is skipped rather than opening a tab
named after something that isn't there. Closing a window with unsaved work in
any tab — not only the one in front — asks first.

## Folders

**Open folder…** (Ctrl+Shift+O, or the arrow beside **Open**) puts a folder in
a side bar and treats it as one project. Every file in it is there —
dotfiles, `.git`, figures, the `.latexmkrc` — because the point is to see what
the folder actually holds; *Show hidden files* in the side bar's `⋯` menu
hides the dotfiles for anyone who would rather not. **Files** at the left of
the status strip, or F9, shows and hides it.

The tree is GTK's own directory listing, which reads each folder off the main
loop and keeps watching it: a file made in a terminal, by a `git pull` or in
another editor appears by itself. A folder is only read when it is first
expanded, so opening one with a hundred thousand files somewhere below it
costs what its top level costs.

Clicking a file opens it. Anything that reads as text — `.tex`, `.bib`,
`.cls`, a Makefile, whatever the extension — opens in a tab; a PDF, an image or
anything else binary goes to the app the desktop opens it with. Right-click
for the rest: *Use as the main document*, *New file…*, *New folder…*,
*Rename…* (F2), *Move to trash* (Delete), *Copy path*, *Show in file manager*.
A renamed file's tab follows it; a trashed one's tab stays open, marked
unsaved, since its text is now the only copy outside the trash.

The folder comes back on the next start, along with the tabs. The **Open**
arrow keeps the last eight folders.

### One document, many files

A folder is usually not one document but one document and its parts —
`main.tex` with `\input{chapters/intro}`, a `refs.bib` — and editing a part has
to rebuild the whole, the way Overleaf does it. So the first question about
any file is which document it belongs to, answered in this order:

1. A `% !TEX root = ../main.tex` line at the top says so outright — the
   convention TeXShop, TeXstudio and VS Code's LaTeX Workshop all read.
2. A file with its own `\documentclass` is its own document.
3. Inside the open folder, anything else belongs to the folder's **main
   document**: the one marked *main* in the tree. It is `main.tex` if there is
   one (Overleaf's name for it), else the file named after the folder, else
   the whole document that pulls in the most parts. *Use as the main
   document* overrides that, per folder, for good.
4. A part opened without its folder looks one or two folders up for a
   document that `\input`s it.

A chapter's tab then shows the main document's pages, and the status strip
says so — `main.tex: Compiled 12 pages` — so the page count is not read as the
chapter's.

**Unsaved edits in a part reach the preview.** The main file is compiled as
always, and every modified part is written into the build directory beside it
at the same relative path. The build directory comes first on `TEXINPUTS`, so
`\input{chapters/intro}` finds the copy with your typing in it before the one
on disk. A part whose tab is closed or saved has its copy removed, so nothing
stale can shadow the real file. `\include{chapters/two}` works too: TeX writes
`chapters/two.aux` beside the main `.aux`, so that directory is made in the
build directory first.

**Problems in other files point at them.** The log names each file the way the
engine opened it — the build copy, a part copied beside it, or a part found
through `TEXINPUTS` — and all three are mapped back to the file in your
folder. An error in `chapters/intro.tex` puts its red dot in that file's
gutter, not in `main.tex`'s, and clicking it in the problems list opens the
chapter at the line. SyncTeX follows the caret inside a part as well.

Every open part of one document shows the same pages: the compile one tab
asks for updates the others too, rather than each compiling the same thing.
Two tabs asking at once take turns — two engines writing one `.aux` would
corrupt it.

## More than one window

`Ctrl+Shift+N` opens another window — shifted, because `Ctrl+N` is a new tab
and that is the far commoner thing to want. **New Window** on the launcher
icon's right-click menu does the same. Each window has its own tabs, its own
compiler and its own preview, so two documents can be side by side rather than
two tabs deep in one window.

From outside, one instance serves the lot:

```bash
larenderer               # show it, or reopen what you had
larenderer FILE.tex      # open it in the window already open
larenderer -n FILE.tex   # open it in a window of its own
```

Remembering which documents to reopen had to move up to the application for
this. A window that wrote down only its own tabs would be fine on its own and
wrong with two: windows close one at a time even when the whole app is
quitting, so the last one out — always the smallest — would overwrite
everything the others had open. What gets written now is the union across
every window, and nothing is lost. The one wrinkle is that a window you close
on purpose keeps counting until you open another one; that errs towards
remembering a document you had rather than losing one.

## Git, through Git Manager

No git runs inside LaRenderer. The **Git** menu in the status strip, and the
shortcuts below, ask [Git Manager](../Git%20Manager) to do it, over the
session bus. That keeps one app responsible for git — its error messages, its
locking, its rules about credentials — and a Git Manager window that happens
to be open shows every commit made from here straight away.

| Keys | Does |
|---|---|
| `Ctrl+Alt+C` | Commit all changes… (saves the tabs in the repository first) |
| `Ctrl+Alt+U` | Pull, and bring every unmodified tab in line with the disk |
| `Ctrl+Alt+P` | Push; a branch with no upstream is published to `origin` |
| `Ctrl+Alt+S` | Sync: commit everything, pull, push |
| `Ctrl+Alt+G` | Open the repository in Git Manager's window |

They act on the open folder's repository, or the current document's when no
folder is open. `Ctrl+Alt` because every `Ctrl+letter` an editor might want is
taken, and niri keeps its own bindings to Super.

**Nothing is started until it is needed.** The first git action of a session
— opening the Git menu counts — looks for a Git Manager on the bus. If one is
open, that is the one used. If not, `git-manager --background` starts one with
no window, which exits by itself once LaRenderer has: it watches LaRenderer's
connection to the bus and leaves with it, after finishing anything still
running. Until then the window shows no git state, because it has not asked.
Once it has, the status strip shows the branch and what changed —
`main ↑1 · 3 changed` — and the tree puts git's letters beside changed files
and a dot on folders with a change somewhere below.

A pull that stops on a conflict says what that means, reloads the conflicted
files so the markers are there to fix, and offers Git Manager. A tab with
unsaved edits is never overwritten by a pull; it is named, and left as it is.

## Overleaf

There are two ways to work on an Overleaf project here, and they answer
different wants.

### Live, in a tab

**Overleaf's editor, live, in a tab** (`Ctrl+Alt+O`, in the **Open** arrow's
menu, or *Edit live instead* in the project dialog) shows Overleaf itself, in
WebKit, as a tab beside your documents. It is exactly Overleaf: real-time with
collaborators, their cursors, the chat, Overleaf's own PDF — and it works on
any plan, free included. With a folder cloned from Overleaf open, it opens
that project; otherwise your project list. Sign in once; LaRenderer keeps its
own session (not Lyndon's — two programs sharing one WebKit profile corrupt
it).

While an Overleaf tab is in front, Save, Compile, zoom and the rest step
aside, and their keys are switched off rather than just hidden, so `Ctrl+S`,
`Ctrl+Enter` and the like reach Overleaf's editor. Links to other sites open in
your browser; Overleaf's downloads (PDF, source zip) go to Downloads.

LaRenderer does not do real-time sync in its *own* editor. That would mean
reimplementing Overleaf's private collaboration protocol — unofficial,
signed in with a browser cookie, and liable to break whenever Overleaf
changes it — and a mistake there would not fail quietly: it would corrupt a
document other people are writing in. Showing Overleaf's editor gets the real
thing with none of that risk.

**Ctrl+A works.** WebKitGTK 2.52 kills the page when a selection runs past the
last line of an editor that recycles its lines as you scroll — in Overleaf,
Ctrl+A in the LaTeX pane, every time. The fix Lyndon Browser found applies
here too: the page's process is pointed at an accessibility bus that isn't
there, so it never builds the tree the crash walks. The cost is that a screen
reader sees nothing of the page; `LARENDERER_ACCESSIBILITY=1` keeps the tree
and takes the crash.

**It costs memory only while it is open.** WebKit is not loaded until the
first Overleaf tab, and the page's process takes 170–270 MB. Closing the last
Overleaf tab ends every process it started within a second: the tabs use a
WebKit context of their own with WebKit's page caches off, since otherwise
WebKit keeps the process of a page you left — the project list, left behind
by the sign-in redirect — resident for a Back that never comes.

Closing a live tab whose project is also the open folder's git clone pulls
what was done in it into that copy, so the two ways meet.

### A copy here, through git

**Open an Overleaf project…** (in the **Open** arrow's menu) clones a project
through Overleaf's git bridge and opens it as a folder. Paste the project's
address — `overleaf.com/project/64b1…`, its git address, or the bare id — and
pick a name for the folder, under `~/Projects/Overleaf` by default.

From then on it is an ordinary folder here, and **Sync** (`Ctrl+Alt+S`) is how
edits cross: it saves the project's tabs, commits everything, merges what your
collaborators did, and pushes. An Overleaf project commits without asking for
a message, since nobody reads git messages in Overleaf's history. When the
project opens it pulls first, so you start from their latest version; *Sync
after every save* in the main menu makes Ctrl+S send your edits as well, a
moment later, so three tabs saved together are one sync.

The git bridge wants a **Git authentication token** instead of your password —
Overleaf → Account settings → Git integration → *Generate token*. It is asked
for once. It reaches git through the environment of that one command, never
its command line or a file of LaRenderer's; when it works, git hands it to the
credential helper already configured (`credential.helper store` on this
machine), which is what keeps it from then on. A token Overleaf refuses is
asked for again, and nothing is stored.

The git bridge is part of Overleaf's paid plans. A self-hosted Overleaf's git
address (`…/git/<id>`) is used as it is.

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
| `Ctrl+O`, `Ctrl+Shift+O` | Open a document, open a folder |
| `F9` | Show or hide the folder |
| `F2`, `Delete` | In the folder: rename, move to the trash |
| `Ctrl+Alt+C` `U` `P` `S` `G` | Git: commit, pull, push, sync, open in Git Manager |
| `Ctrl+Alt+O` | Overleaf's own editor, live, in a tab |
| `Ctrl+R`, `F5` | Compile now |
| `Ctrl+S`, `Ctrl+Shift+S` | Save, save as |
| `Ctrl+E` | Export the PDF |
| `F7` | Jump the preview to the cursor |
| `F8` | Show or hide the problems list |
| `Ctrl+ +` / `Ctrl+ −` | Zoom the preview |
| `Ctrl+0` | The preview at 100% |
| `Ctrl+Page Up/Down` | Previous, next page |
| `Ctrl+/` | Comment or uncomment |
| Pinch, or `Ctrl+scroll` | Over the preview zooms it; over the text resizes the text |

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

**Zoom goes anywhere from 25% to 400%.** Click the size in the status strip
and type one — `137`, Enter — or pick a fit or a preset below the box.
Pinching on the touchpad zooms around the point between your fingers, and so
does Ctrl with two fingers: the pages on screen are stretched as the fingers
move, and poppler is asked for sharp ones once they stop, rather than
rasterising every size the gesture passes through. A touchpad reports
scrolling in pixels, dozens of events a swipe, so it is read as a continuous
amount; a mouse wheel still steps through the presets a notch at a time.

The rendered pages are cached up to 192 MB rather than up to a number of
pages. Sixty pages was harmless at 100%; at 400% one A4 page is about 50 MB,
and sixty of those would be most of this machine's memory.

**Pages render only when they are about to be seen**, one `pdftoppm` per page
at roughly 80 ms, cached by page and DPI, queued one at a time so twelve
visible pages don't open twelve processes. A hundred-page document opens as
fast as a two-page one.

**The editor and the preview keep their shares.** The split between them is
remembered as a fraction of the width, not in pixels, so showing the folder
takes its 250 pixels from both rather than leaving the pages a sliver.

**The window still fits two thirds of a small screen.** Its narrowest width is
set by the header, so the folder and git went into the status strip rather
than the header: on this 1366-pixel screen the header alone decides whether
the window fits niri's two-thirds column, and it does, at 767 pixels.

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
[MSYS2](https://www.msys2.org/), then in an **MSYS2 UCRT64** shell:

```bash
pacman -S mingw-w64-ucrt-x86_64-gtk4 mingw-w64-ucrt-x86_64-libadwaita mingw-w64-ucrt-x86_64-python-gobject
```

Then, from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File windows\install.ps1 -Desktop
```

That checks the runtime is really there before it makes anything, generates
the icons from the `.svg`, and puts a shortcut in the Start Menu (and on the
Desktop with `-Desktop`). `-Uninstall` removes them again. It also registers
LaRenderer for `.tex` and LaTeX's other source types, the way the `.desktop`
file does for `text/x-tex`: it is offered under *Open with*, and becomes the
default with `-Default LaRenderer`, which opens its page in Settings.

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
