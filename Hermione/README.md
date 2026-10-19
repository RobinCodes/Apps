# Hermione

A file manager. Two panes if you want them, tabs, a command palette, and a
window made of glass.

```bash
hermione                  # reopen the tabs from last time
hermione ~/Projects       # go there
hermione ~/notes.md       # go to its folder and select it
hermione -n ~/Downloads   # in a window of its own
```

The short version of what makes it different: **nothing it does to your files
is a surprise, and nothing it does to your files is permanent.** A copy says
what it is about to do before it starts, asks once about a name that is
already taken, and can be stopped halfway without leaving a half-written file
pretending to be a whole one. Everything except a permanent delete can be
undone with `Ctrl+Z`, including putting things back out of the trash.

The long version is the rest of this file.

## What it does

| | |
|---|---|
| **Two views** | A detail list with sortable columns — size, type, date, permissions, owner — and an icon grid with thumbnails. They share one selection, so switching between them keeps your place. Each folder remembers which one you last used for it. |
| **Two panes** | `F3` splits the window. Drag between them, or `Open in the other pane` from the right-click menu. `F6` moves between them. |
| **Tabs** | `Ctrl+T`, middle-click a folder, or drag a tab out to make it a window. Reopened on the next launch. |
| **The palette** | `Ctrl+Shift+P` searches the commands, your places, your recent folders and the folder on screen, all at once, by initials: `dnl` finds Downloads, `nwt` finds New tab. Type a path instead and it completes folders. |
| **Search** | `Ctrl+F` filters the names in front of you. Turn on **Everywhere below** and it walks the whole tree; add **In contents** and it reads the files too, with the matching line shown. Results appear as they are found and replace the listing, so you can act on them like any other selection. |
| **Operations** | Copy, cut, paste, duplicate, rename, link, compress, extract, trash, delete — with a progress strip that gives files, bytes, rate and time left, and a stop button that works. |
| **Batch rename** | Find and replace (plain or regular expression), prefix, suffix, numbering, case — with every new name shown before anything is renamed, and clashes refused. |
| **Properties** | Everything about a file, a recursive size counted on a thread, and permissions as three dropdowns and a switch rather than nine checkboxes. Checksums on request. |
| **The three space tools** | Find duplicates (grouped, by contents, never by name), the biggest files, and empty folders. Each is a list you can act on from, and none of them pre-selects anything for deletion. |
| **The trash** | A real one, by the freedesktop spec: what is in it, where each thing came from, and putting it back. Restoring never overwrites whatever took the old name. |
| **Preview** | `F7` or `Space`. Images, the first page of a PDF, the head of a text file, the contents of an archive, and what `ffprobe` knows about a video or a sound file. |
| **Git** | Inside a repository, a coloured dot on anything git has an opinion about, and the branch in the status bar. One `git status` per folder, not one per file. |
| **Places** | Your XDG folders, your bookmarks — seeded once from the ones GTK's own file chooser uses — and every disc and stick, mounted or not, live. Drop files on any of them. |
| **The file chooser** | With `portal/install.sh` run, this *is* the desktop's "choose a file" dialog — same window, same places, same search, with the question on a bar along the bottom. |

## Three modules, one toolbar

The window is a slab of glass with three cards laid on it: the toolbar across
the top, the places and the listing side by side underneath, each rounded,
each with a gap around it so the slab shows between them. The gaps are the
only edges — no rules, no hairlines between panes, no corner anywhere in the
window that is not a round one.

The toolbar spans the whole width rather than beginning where the sidebar
ends. It used to be a top bar of the content pane, which put it between the
other two cards with an edge shared with each, and a bar wedged between two
surfaces cannot read as a surface of its own however it is painted — it reads
as a strip of a different material stuck to the top of the file list. Across
the window it is simply the third card.

There is still one row of chrome above a listing, and getting there took
removing one. The obvious structure — a window header for the app, a toolbar
inside each pane for the folder — gives you a nearly empty strip above a full
one, with a search button and a menu in both. So everything lives on the one
bar: the sidebar toggle, back/forward/up, the path, search, the view switch,
the split, a new tab, and the menu.

With two panes open that one bar drives whichever has the focus, and each
pane carries a slim line of breadcrumbs saying which folder it holds, with
the active one underlined in the accent colour. The menus name `pane.`
actions and the window points that action group at the active pane, which is
what lets one menu serve both halves without knowing anything about either.

It is a plain box rather than an `Adw.HeaderBar`, for one reason:
a header bar centres its title widget between the two groups of buttons, and
the title here is the path — which wants to start where the arrows end and
take whatever room is left, not sit squeezed in the middle being ellipsised
from both ends. `Gtk.WindowControls` still gives the close button the
desktop's own layout asks for, and the `Gtk.WindowHandle` around it still
drags the window.

## The glass

The colours are the house's — the same ten themes as the rest of the suite,
from [`house/theme.py`](../house/README.md), with **Porcelain** the light
default and **Moss** the dark one, and **Follow the desktop** on by
default so the window uses one in a light session and the other in a dark one.
What the sheet below is written against is libadwaita's named palette, and
what those names *mean* is restated from the chosen theme, so one list of
ten palettes drives every rule in it and every widget nobody wrote a rule
for.

The look itself is four separate effects, and it is worth saying which is
which, because only one of them is real blur.

**The window is transparent.** It paints no background of its own; a single
rounded slab inside it does, at whatever opacity the *Transparency* setting
says. The corners are rounded by clipping the children to the CSS radius —
GTK has no `overflow` property, but `Gtk.Overflow.HIDDEN` honours the corner
radius, which is what stops a selected row at the bottom of the list from
squaring off the window.

**The depth is in the gaps, not in the cards.** The three modules are one
material cut into three pieces, so they share one alpha; the slab behind them
is the sheer thing, and what you see between the cards is the desktop coming
through it. Everything moves together when you drag the one slider.

It was the other way round for a while — a ladder of four alphas, the sidebar
most solid, then the listing, then the bars — and it was wrong. Three tints of
the same glass meeting at a hard edge do not read as depth, they read as three
materials that do not match, and the toolbar read as the odd one out because
it was both the sheerest surface and the one tinted with the window colour
rather than the view colour. A strip *inside* a card — the column headers, the
status line, a split pane's breadcrumbs — is lifted with light instead, which
is the same rule the controls on the toolbar follow: on a translucent surface
a darker patch reads as a hole.

Getting it required undoing some work libadwaita does for you. Its split view
paints a solid background behind the sidebar pane, and its toolbar view does
the same behind the bars — so by default the sidebar comes out *completely*
opaque however translucent you make its own tint, and the pane's toolbar ends
up the least see-through thing in the window: a solid strip of chrome over a
file list you can see the desktop through, which reads as inside out. Those
backgrounds are cleared, scoped to the two toolbar views that belong to the
window, since dialogs are built from the same widget and still need theirs.

**A control on the glass is raised out of it, never cut into it.** Tinting a
text field or a button cluster darker than the bar it sits on is the obvious
thing to do and it is wrong here: on a translucent surface a darker patch
reads as a hole. They are tinted with light instead, with the same hairline
and top highlight as every other edge. The suite asserts the whole ladder,
because it is the kind of thing that inverts quietly.

**The blur is real where it is there at all.** GTK 4.22 implements CSS
`backdrop-filter`, and it genuinely blurs what a layer is drawn over. That is
only worth anything where something floats over something else, so it is spent
exactly there: the command palette, dialogs, the sliding sidebar, the progress
strips, toasts. A header bar with nothing behind it but a flat colour does not
get one — blurring a flat colour costs a full offscreen pass to produce the
same flat colour.

It is *not* spent on the file list scrolling under the toolbar, which was the
first thing tried. A `Gtk.GridView` is itself the scrollable: it is allocated
the viewport and scrolls its children inside it, so a margin added to clear
the bars cannot scroll away and the tiles never pass underneath. Getting them
to would mean giving up the view's widget recycling, which is the thing that
makes a folder of fifty thousand files possible at all. Not worth it.

**There is a highlight that follows the pointer.** One very faint radial
gradient under the content, redrawn only when the pointer moves more than a
few pixels, and switched off with the glass. It is meant to be noticed only
when it is taken away.

Everything is generated from the settings rather than written as a static
stylesheet, because three of the things that define the look — how
translucent, how blurred, how round — are numbers a person can change, and
GTK's CSS cannot be handed a number at runtime. Light and dark get different
glass: the rim that reads as light catching an edge on a dark theme is
invisible on a pale one, which leans on a soft shadow instead. File-type
colours get the same treatment — the tint that glows against a dark
background is a pale smudge against white, so there are two sets.

## It is also the file chooser

`portal/install.sh` makes Hermione the desktop's file selector. After that,
when something says *"choose a file"* — a Flatpak app, Firefox, an Electron
app, any GTK program with `GTK_USE_PORTAL=1` — the window that opens is this
one: the same places, the same tabs, the same search, the same thumbnails,
with a bar along the bottom asking the question and nothing else different.
A file chooser is a file manager that has to give an answer, and keeping two
of them means learning two sets of keys for the same job.

It is an `org.freedesktop.impl.portal.FileChooser` backend, run as a separate
process that D-Bus starts on demand and that exits a minute and a half after
the last dialog closes — so a chooser that falls over takes a chooser with
it, not the window you were working in. It serves all three of the calls
that are every file dialog there is: `OpenFile` (one file, several, or a
folder), `SaveFile` (a folder and a name) and `SaveFiles` (a folder for a
known list of names). Filters narrow the listing and folders always survive
them; the extra questions a program attaches — "open read-only", "follow
links" — become controls on the bar.

See [portal/README.md](portal/README.md) for what it covers and what it
cannot, and for the one ordering rule that file is written around: the
backend takes its bus name before it loads libadwaita, because libadwaita
reads the colour scheme from the settings portal and the settings portal is
the daemon waiting for the backend to answer.

## Not losing your files

Most of the work in a file manager is refusing to do damage.

**It knows what it is about to do.** A job counts its files and bytes as its
own phase before it starts, so the progress bar means something and "there is
not enough room" is said before the first byte rather than nine minutes in.

**A name that is taken asks once.** Skip, keep both, replace, or merge —
and "do this for everything else too", so a copy of nine hundred files asks
once rather than nine hundred times. The question blocks the copy, which is
the point.

**Stopping leaves nothing behind.** A cancelled copy deletes the file it was
halfway through writing. A truncated file that looks whole is worse than no
file.

**Undo is kept in the shape each operation needs.** A move is undone by
putting each thing back where it came from, one destination per item. A copy
is undone by deleting what appeared — the top-level paths only, so a folder is
removed once rather than child by child. Trashing is undone by restoring from
the trash. And an undo never destroys anything: if something has taken the old
name in the meantime, the restored file is put beside it, not over it.

**A folder cannot be copied into itself**, checked on real paths so a
symlinked route in does not get past it.

**An archive cannot write outside the folder you extracted it into.** A member
called `../../.bashrc` is refused, by name and by resolved path, for zip and
tar alike; the rest of the archive still extracts. An archive holding one
top-level folder extracts in place; one holding forty loose files gets a
folder made for it.

**A recursive permission change leaves the folders walkable.** Applying `644`
to a tree and locking yourself out of it is the classic way to ruin an
afternoon, so directories get the `x` they need wherever they have `r` —
including the one you started on, which has to stay walkable long enough to
finish the walk.

## Speed

Measured on the machine this was written on — an N100, NVMe SSD, Python 3.14.

| | |
|---|---|
| `/usr/bin`, 2,871 entries | **52 ms** to read, 13 ms to sort |
| A folder of 50,000 files | **864 ms** to read, 246 ms to sort |
| First rows on screen | ~400 entries, so about **7 ms** |
| Copying 200 MB | within **10%** of `shutil.copytree`, with progress and a working cancel |
| Searching 50,000 names | **0.9 s** |

Reading a folder happens on a worker thread and arrives in batches, so a
folder of fifty thousand files starts drawing immediately rather than after a
second of nothing. A `Gio.FileMonitor` then keeps it current: a file written
by something else appears and one deleted elsewhere goes, both without
rebuilding the list — a rebuild would lose the selection and jump the scroll
position, which is what makes other file managers unusable while something is
unpacking in the background.

Two things learned the hard way, both in the cells:

**Cell binding has to be idempotent.** Every cell is rebound as it scrolls,
and setting a style class invalidates the widget's style, which queues a
resize, which relays out the list, which binds the cells again. Removing and
re-adding the same class on every bind is an infinite loop that ends in a
`RecursionError` rather than on screen. Nothing is touched unless it is
actually different.

**Sizes belong in `setup`, not `bind`.** Same loop, same ending.

## Under a tiling compositor

This was written on a 1366×768 screen under niri, where the narrowest preset
column is about 455 pixels.

**The window's minimum width is 360.** An `Adw.Breakpoint` at 720 collapses
the sidebar into one that slides over the list, and folds away the header
buttons that are also in the menu. The test suite measures this on every run
and fails under 455, because a file manager that will not fit in a column is
not a file manager you can use here.

**A remembered maximise is handed back.** niri honours `set_maximized` by
giving the window the whole output, which overrides the column widths you
chose — and `is_maximized()` then saves `true` for ever. So a tiling
compositor is detected from the toplevel's capabilities (no MINIMIZE is the
tell), the remembered maximise is taken back once, and geometry is not saved
while tiled. Rounded corners are kept when tiled and dropped only when the
window really does cover the screen.

## The keyboard

`Ctrl+Shift+P` finds any command by name, and **Keyboard shortcuts** in the
menu lists them all. The ones worth knowing:

| | |
|---|---|
| `Alt+←` `Alt+→` `Alt+↑` | Back, forward, up — `Backspace` also goes up |
| `Ctrl+L` | Type a path |
| `Ctrl+F` | Search in this folder |
| `Ctrl+T` `Ctrl+W` `F3` `F6` | New tab, close tab, two panes, the other pane |
| `Ctrl+1` `Ctrl+2` | List or icons |
| `Ctrl+H` | Hidden files |
| `F2` | Rename — several at once if several are selected |
| `Delete` / `Shift+Delete` | Trash / delete for good |
| `Ctrl+Z` `Ctrl+Shift+Z` | Undo, redo |
| `Space` / `F7` | Preview pane |
| `Alt+Return` | Properties |
| `Ctrl+Alt+T` | A terminal here |
| Type a few letters | Jump to that name |

## Updating itself

The app runs out of this checkout, so the newest version of it is whatever
`origin` has. The launcher fast-forwards to anything fetched last time before
a single module is imported — local git, a few milliseconds — and a few
seconds after the window is up a worker thread fetches; if the upstream has
moved, every window offers a *Restart*. Only a fast-forward is ever made, so a
checkout with work of its own in it is left exactly as it is, and
`APPS_NO_UPDATE=1` turns the whole thing off. Same `updater.py` as every other
app here; see the [top-level README](../README.md#updates).

## Settings

In the menu, or `Ctrl+,`. Everything applies as you change it — a transparency
slider you have to confirm is a transparency slider you cannot judge.

Settings live in `~/.config/hermione/config.json`. Every value is validated
against its default on load: wrong type, out of range or not one of the
allowed answers all mean "use the default", never a crash on startup.
Thumbnails go in `~/.cache/thumbnails` under the freedesktop naming rules, so
a folder another file manager has already been through appears instantly here,
and the work this one does is not thrown away when you open a different one.

## External tools

Hermione works with none of these and is better with each. It looks once per
run and quietly leaves out what it cannot do.

| | |
|---|---|
| `ffmpegthumbnailer` or `ffmpeg` | Video thumbnails |
| `ffprobe` | Length, resolution and codec in the preview |
| `pdftoppm` or `pdftocairo` | PDF thumbnails and previews |
| `7z` or `bsdtar` | `.7z` and `.rar`; everything else is Python's own `zipfile` and `tarfile` |
| `ripgrep` | Content search, when it is there; otherwise the built-in one, which is slower per megabyte and behaves identically everywhere |
| `git` | The status dots and the branch name |
| a terminal | Whichever of ghostty, kitty, alacritty, foot, wezterm, gnome-terminal, konsole, xfce4-terminal, tilix or xterm is installed — or name your own in the settings |

## Tests

```bash
python3 tests/test_hermione.py
```

Sixty-seven of them, and they do real work: real copies with real conflicts, a
real cancelled copy checked for the half-file it must not leave, real
archives extracted both ways round, a real git repository, a real trip through
the trash and back.

Two things in the suite are worth knowing about. **No window is ever
presented** — a suite that maps a window steals the focus of whoever is
running it, and under a tiling compositor takes a column as well — so the
window tests build the real window, measure it, drive its actions and throw it
away, unmapped. And **nothing reaches the desktop**: `Gtk.FileLauncher` goes
through the portal, so a test that activated a `.png` would open your image
viewer; `HERMIONE_NO_LAUNCH` turns it into a signal the test checks instead.

The suite works in a hidden directory under `$HOME` rather than `/tmp`,
because GIO will not trash a file on a tmpfs or across a filesystem boundary,
and a trash test that cannot trash anything is not a test. It is removed at
the end.

## On Windows

It runs, with the rest of the suite's GTK4-from-MSYS2 arrangement — see the
[top-level README](../README.md). What changes there: the Recycle Bin can be
deleted into but not listed, so the trash view says so; permissions are not
shown, because Unix modes are not what NTFS has; and `.7z` and `.rar` want
7-Zip on the PATH.
