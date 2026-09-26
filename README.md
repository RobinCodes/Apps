# Apps

Six desktop applications, written for Linux and now also running on Windows.

| | What it is | Windows |
|---|---|---|
| [Claude Desk](Claude%20Program/) | A native front end for the Claude Code CLI | GTK4 via MSYS2 |
| [Fathom](Fathom/) | A disk scanner: what is taking up the space, as a tree and as a map | GTK4 via MSYS2 |
| [Git Manager](Git%20Manager/) | Every git repository on the machine, and the GitHub account behind them | GTK4 via MSYS2 |
| [LaRenderer](LaRenderer/) | A LaTeX editor with the compiled pages beside the text | GTK4 via MSYS2 |
| [Lyndon Browser](Lyndon%20Browser/) | A small, fast, private browser | Win32 + WebView2 |
| [Notedown](Notedown/) | A Markdown editor with the rendered page beside the text | GTK4 via MSYS2 |

Each folder has its own README; this one only covers what is common.

## On Windows

The five Python apps are GTK4 and libadwaita, and the only GTK4 that exists
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
startup. To Windows all five are `pythonw.exe`, and without it their windows
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

The apps run straight out of this checkout, so the newest version of each is
whatever `origin` has. A few seconds after an app opens it fetches in the
background, and if there is something new every window offers a *Restart*;
the launcher fast-forwards to it before the app loads. Starting never waits
on the network. Only a fast-forward is ever made — a checkout with local
commits, or a local edit the update would overwrite, is left alone — and
`APPS_NO_UPDATE=1` turns it off.

Lyndon is compiled, so its shortcut starts `Lyndon Browser\win32\launch.py`
rather than the `.exe`. That starts Lyndon at once, then fetches, and if the
source has moved on since `dist\` was built it builds the new version into
`dist-next\` with MSYS2's `make`, in the background. Windows will not replace
a running program, so the new build is swapped in the next time Lyndon starts.
What each build was made from is recorded in its `.source`, and anything that
goes wrong is written to `Lyndon Browser\update.log`.

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

Unchanged. Each README describes its own `~/.local/bin` symlink, `.desktop`
files and XFCE panel entries, and `Lyndon Browser/Makefile` still builds
against GTK4 and WebKitGTK. The one shared file that both platforms compile,
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
| Claude Desk | The protocol — slash commands, question parsing, the answer frame — the session state machine, and the widgets in a real window. `CLAUDE_DESK_LIVE=1` also drives a real child, which spends tokens. |
| Git Manager | The porcelain parsers against throwaway repositories built for the purpose: status, renames, conflicts, log, branches, stashes, diffs. Then paths, settings and the ignore rules. No network. |
| LaRenderer | The engine-log parser against captured text, which needs nothing installed, and a real compile where there is an engine to do it. |
| Fathom | A small filesystem built for the purpose — hardlink, symlink loop, unreadable folder, empty folder — and the totals that come back off it, checked against `du` where there is a `du`. Then the treemap as geometry, and a real window that deletes real files in its own scratch directory. |
| Notedown | The Markdown parser against documents built to break it — positions, tight and loose lists, tables, footnotes, front matter — then the editing courtesies against a real text buffer, the preview's widget reuse, and a window that opens and saves real files. Also that the window still fits a 455px tiling column. |
| Lyndon Browser | The filter-list compiler on Linux, the request matcher on Windows, and on both the shared URL handling — address-or-search, first-party grouping, a file named on the command line, and the JavaScript escaping the password manager fills through. |
| Windows installer | What `windows\install.ps1` writes to the registry, written to a sandbox of it: another program's claim on a file type and its entry in Settings survive an install, a second run changes nothing, and an uninstall takes back only what was installed. Windows only. |

## Licence

MIT. See [LICENSE](LICENSE).
