# Apps

Nine desktop applications, written for Linux; seven of them also run on
Windows.

| | What it is | Windows |
|---|---|---|
| [Claude Desk](Claude%20Program/) | A native front end for the Claude Code CLI | GTK4 via MSYS2 |
| [Fathom](Fathom/) | A disk scanner: what is taking up the space, as a tree and as a map | GTK4 via MSYS2 |
| [Git Manager](Git%20Manager/) | Every git repository on the machine, and the GitHub account behind them | GTK4 via MSYS2 |
| [Hermione](Hermione/) | A file manager: two panes, tabs, a command palette, and a window made of glass | GTK4 via MSYS2 |
| [LaRenderer](LaRenderer/) | A LaTeX editor with the compiled pages beside the text | GTK4 via MSYS2 |
| [Lyndon Browser](Lyndon%20Browser/) | A small, fast, private browser | Win32 + WebView2 |
| [Notedown](Notedown/) | A Markdown editor with the rendered page beside the text | GTK4 via MSYS2 |
| [Prism](Terminal/) | A terminal made of glass: tabs, splits, the house themes | Linux only — VTE has no Windows build |
| [The Eye](The%20Eye/) | A viewer for photographs and video, with a colour grading editor built in | Untested — needs GStreamer under MSYS2 |

Each folder has its own README; this one only covers what is common.

## The house style

Hermione, The Eye and Prism are the three the look was worked out in, and
everything else here is drawn to match them. The style is a real thing on
disk rather than a description: [`house/`](house/) holds `theme.py` — the
ten palettes, and the glass each one is made of — and `housestyle.py` —
libadwaita's whole named palette restated in a theme's colours, the shared
idiom on top of it, and the two widgets CSS cannot draw.

Both are **vendored**, byte for byte, into every app's own package, the way
`updater.py` has been since the beginning: each app folder is a thing you can
copy to another machine and run on its own, and an app that imported its
colours from a sibling directory would not be. `house/sync.py` copies them and
`house/sync.py --check` says whether any copy has drifted — which the test
suite runs, so a drift fails the build rather than being noticed a month later
when one app's Porcelain has stopped being everyone else's Porcelain.

Lyndon Browser is C and has no Python package to vendor into, so `sync.py`
generates `Lyndon Browser/src/house-themes.h` from the same `theme.py`
instead. The numbers are the same numbers.

### The themes

Ten, the same ten everywhere, and every app's preferences list them in the
same order with the same words. Two of them are what the rest is tuned
against:

| | |
|---|---|
| **Porcelain** | Warm paper under glass, the colour of gallery walls. The light default. |
| **Moss** | A desaturated forest black under sage. The dark default. Green is the hue the eye resolves most sharply, which is exactly why a dull one makes such a restful ground: all of the acuity and none of the pull. The accent is sage rather than a leaf green — bright enough to find, dull enough to live with for a working day. |

Two more dark themes were drawn alongside it and kept, because the dark half
is no longer the one with no choice in it: **Lacquer**, black lacquer and
brass — Porcelain with the light turned down — and **Petrol**, a deep
blue-green under a copper accent, a cold room and a warm lamp. Then Aurora,
Obsidian, Ember, Studio and Monochrome, and Daylight in the light half: ten in
all.

Every one of them carries more than the colour of text: the tint of its glass,
how much light comes through it, how bright its edges catch that light, and
the four hues of the slow-moving light behind it. Both halves are designed
together, because a rose-tinted glass over a cold palette looks like two
themes fighting.

Every app follows the desktop between light and dark by default — the pair it
switches between is yours to choose — and the question is put to the XDG
settings portal rather than to libadwaita, whose answer stops being the
desktop's as soon as anything forces a colour scheme.

None of this is a matter of taste alone. `house/test_house.py` measures every
theme's text, every colour in it that carries meaning, every label that lands
on an accent and every matte against its own background, and fails a palette
that would not meet WCAG's readable threshold. A palette here is arithmetic
that happens to look nice, not the other way round.

### Windows

Every window in every app remembers the size it was last closed at and comes
back at it, through one shared `housestyle.Geometry`.

The size and the maximise are treated differently under a tiling compositor,
and the asymmetry is the whole content of that class. The **size** is always
remembered: the size niri gave a window is still the size that window was,
saving it costs nothing while tiled — a tiled window gets its column's width
whatever it asks for — and it is exactly right the moment the window is
floated, or the same checkout is run on a stacking desktop or on Windows. The
**maximise** is not: niri honours `set_maximized` by handing the window the
whole output, which overrides the column widths you chose, and `is_maximized`
then reports true so the flag re-saves itself for ever. So a remembered
maximise is applied before the map and handed back once if the compositor
turns out to be a tiling one. Tiling is detected from the toplevel's
capabilities — no minimise is the tell.

One thing no app can do from the inside: niri's own `default-column-width`
wins over the size a window asks for. With `proportion 0.5` set globally every
window opens at half the screen however well it remembers. The matching
`window-rule` with an empty `default-column-width` is what lets the app's own
answer through, and it lives in the compositor's config —
`~/.config/niri/apps-window-size.kdl` on this machine.

## On Windows

The six Python apps are GTK4 and libadwaita, and the only GTK4 that exists
for Windows comes from MSYS2 — PyGObject publishes no Windows wheels, so
`pip install pygobject` cannot work. Install
[MSYS2](https://www.msys2.org/), then in an **MSYS2 MINGW64** shell:

```bash
pacman -S mingw-w64-x86_64-gtk4 mingw-w64-x86_64-libadwaita mingw-w64-x86_64-python-gobject mingw-w64-x86_64-poppler
```

Then, from this directory:

```powershell
powershell -ExecutionPolicy Bypass -File windows\install.ps1 -Desktop
```

It checks the runtime is really there before making anything, generates `.ico`
files from each app's `.svg`, and puts shortcuts in the Start Menu — and on
the Desktop with `-Desktop`. `-Uninstall` removes them again. Nothing is
copied; the shortcuts point at the files where they sit, which is why
**moving this folder means running it again**. It rewrites every shortcut it
made, the ones pinned to the taskbar included, and every registration below.

Each shortcut carries its app's ID, which the app also claims for itself at
startup. To Windows all six are `pythonw.exe`, and without it their windows
group together on the taskbar under whichever icon it met first. For the same
reason each app puts its own icon on its windows: GTK sets none on Windows,
and they would otherwise show the Python logo in Alt+Tab.

### Opening files

Three of the apps are registered, under the current user only, for what they
open:

| | Opens |
|---|---|
| Notedown | Markdown — `.md`, `.markdown`, `.mdown`, `.mkd`, `.mkdn`, `.mdwn`, `.mdtxt`, `.mdtext` |
| LaRenderer | LaTeX — `.tex`, `.ltx`, `.latex`, `.sty`, `.cls`, `.dtx` |
| Lyndon | Web pages — `.htm`, `.html`, `.shtml`, `.xht`, `.xhtml` — and `http` and `https` links |

Each is offered under *Open with* by its own name and icon, and a file it
opens gets a document icon of its own. Which program opens a type is the
user's to choose — Windows lets no installer make that choice — so make it
once, in Settings → Apps → Default apps → the app → **Set default**. The
installer opens that page when asked, and ends by listing what opens each
type now:

```powershell
powershell -ExecutionPolicy Bypass -File windows\install.ps1 -Default Notedown
```

Choose the app itself rather than browsing to a file under *Open with*.
Browsing to `pythonw.exe` gets the document run as a Python script, and to a
`.bat` gets every file of the type drawn blank, since a batch file has no icon
to give them; the installer removes a `.bat` chosen that way. A choice made
in Settings survives moving the folder and running the installer again.

A page or link opened while Lyndon is running becomes a tab in the window
already open, as it does on Linux, rather than a second browser.

## Updates

This section is about both platforms, and it is about **every** app here —
there is no app in this repository that does not keep itself current. They run
straight out of this checkout, so the newest version of each is whatever
`origin` has. A few seconds after an app opens it fetches in the background,
and if there is something new every window offers a *Restart*; the launcher
fast-forwards to it before the app loads. Starting never waits on the network.
Only a fast-forward is ever made — a checkout with local commits, or a local
edit the update would overwrite, is left alone — and `APPS_NO_UPDATE=1` turns
it off.

`updater.py` is the same file in every app, vendored beside its package and
kept in step by `house/sync.py` along with the house style, so "every app
updates itself" is a thing the test suite checks rather than a thing somebody
has to remember.

Lyndon is compiled, so a new version of it is not the source but a build of
one, and its launcher is `Lyndon Browser/lyndon.py` rather than the binary.
That starts Lyndon at once, then fetches, and if the source has moved on since
`dist/` was built it builds the new version into `dist-next/`, in the
background — with MSYS2's `make` on Windows. When that build lands the
launcher writes `.update-ready`, the running Lyndon notices it and offers a
*Restart*, and the restart comes back through the launcher, which swaps the
new build in before starting it. A build nobody restarted for is swapped in
at the next start anyway. What each build was made from is recorded in its
`.source`, so a commit touching only the other apps is not a reason to rebuild
Lyndon, and anything that goes wrong is written to
`Lyndon Browser/update.log`.

Lyndon is also the default browser, so its launcher runs every time anything
else opens a link, not only when somebody starts it. The fetch is throttled to
once every half hour for that reason; the swap and the fast-forward, which are
local and instant, happen on every start.

The apps run on MSYS2's Python rather than a python.org one, because that is
where the GTK bindings are. The shortcuts start `pythonw.exe` directly rather
than going through the `.bat` launchers: Windows looks for a program's DLLs
beside the `.exe` first, so GTK is found without touching PATH, and going
direct means no console window blinks up before the app appears.

Each app also has a `.bat` next to it for running from a command line, where
being handed a file to open is the point.

### Lyndon is different

It is C, not Python, and it is built on WebKitGTK, which has no Windows port
at all. Its Windows build replaces the toolkit and the engine — Win32 for the
window, WebView2 for the pages — while compiling the filter translator, the
config file, the history store, the importer and the URL helpers from `src/`
unchanged. Settings, bookmarks, history, downloads, saved logins and session
restore are all there; see [its README](Lyndon%20Browser/#windows) for how,
and for the short list of what this engine cannot do.

```bash
cd "Lyndon Browser"
make -f win32/Makefile dist     # a folder that runs without MSYS2
```

### What the port needed

Very little of it was the toolkit. The GTK and libadwaita code moved without
edits; what did not move was everything around it:

* **Paths.** XDG has one root with subdirectories, Windows has `%APPDATA%` for
  what should follow you between machines and `%LOCALAPPDATA%` for what should
  not.
* **Finding tools.** A Windows installer puts its program in `Program Files`
  and edits the PATH of interactive shells only, so a process started from a
  shortcut has to be told where to look.
* **Console windows.** Every child process of a windowed program opens one
  unless told not to — `git status` every fifteen seconds is a black rectangle
  blinking over the window forever.

The Python apps answer all of that in one `winenv.py` each, so no other module
knows which operating system it is on. Fathom shells out to nothing, so its
copy is the short one: where files go, and what counts as a drive. Notedown's
is nearly as short — it renders everything itself, and the only tool it ever
looks for is pandoc.

## On Linux

Each README describes its own `~/.local/bin` symlink, `.desktop` files and
XFCE panel entries, and `Lyndon Browser/Makefile` still builds against GTK4
and WebKitGTK. Lyndon is set up the same way as the rest —
`make -C "Lyndon Browser" user-install` links `~/.local/bin/lyndon` to
`lyndon.py` — which is what lets it update itself: a copy installed into
`/usr/local` with `sudo make install` has no checkout under it and no write
permission over itself, so it stays at whatever version it was installed at.

The one shared file that both platforms compile,
`Lyndon Browser/src/lyndon.h`, selects its toolkit on `_WIN32` and is
otherwise the same header it was.

## Tests

Each app has its own suite and each can be run on its own; `run-tests.py`
runs them all and is the quickest way to know the repository is sound.

```bash
python3 run-tests.py            # everything this machine can run
python3 run-tests.py --list     # what it would run, and what it would skip
```

A suite that needs something the machine hasn't got — a TeX distribution for
LaRenderer's compile half, a C compiler for Lyndon — is reported as skipped
rather than failed, because "not installed here" and "broken" are different
answers and only one of them is worth acting on.

On Windows, run it with MSYS2's Python, which is the one that has the GTK
bindings:

```powershell
C:\msys64\mingw64\bin\python.exe run-tests.py
```

What each suite covers:

| | Checks |
|---|---|
| House style | The palettes, measured: every theme's text, every colour in it that carries meaning, every label on an accent and every matte, against that theme's own background. Then the generated stylesheet, loaded into a real `GtkCssProvider` for every theme at three settings so a parse error cannot pass as a rule that silently does nothing. Then `sync.py --check`, so the nine vendored copies cannot drift. |
| Claude Desk | The protocol — slash commands, question parsing, the answer frame — the session state machine, and the widgets in a real window. `CLAUDE_DESK_LIVE=1` also drives a real child, which spends tokens. |
| Git Manager | The porcelain parsers against throwaway repositories built for the purpose: status, renames, conflicts, log, branches, stashes, diffs. Then paths, settings and the ignore rules. No network. |
| LaRenderer | The engine-log parser against captured text, which needs nothing installed, and a real compile where there is an engine to do it. |
| Fathom | A small filesystem built for the purpose — hardlink, symlink loop, unreadable folder, empty folder — and the totals that come back off it, checked against `du` where there is a `du`. Then the treemap as geometry, and a real window that deletes real files in its own scratch directory. |
| Notedown | The Markdown parser against documents built to break it — positions, tight and loose lists, tables, footnotes, front matter — then the editing courtesies against a real text buffer, the preview's widget reuse, and a window that opens and saves real files. Also that the window still fits a 455px tiling column. |
| Lyndon Browser | The filter-list compiler on Linux, the request matcher on Windows, and on both the shared URL handling — address-or-search, first-party grouping, a file named on the command line, and the JavaScript escaping the password manager fills through. |
| Windows installer | What `windows\install.ps1` writes to the registry, written to a sandbox of it: another program's claim on a file type and its entry in Settings survive an install, a second run changes nothing, and an uninstall takes back only what was installed. Windows only. |

## Licence

MIT. See [LICENSE](LICENSE).
