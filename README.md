# Apps

Four desktop applications, written for Linux and now also running on Windows.

| | What it is | Windows |
|---|---|---|
| [Claude Desk](Claude%20Program/) | A native front end for the Claude Code CLI | GTK4 via MSYS2 |
| [Git Manager](Git%20Manager/) | Every git repository on the machine, and the GitHub account behind them | GTK4 via MSYS2 |
| [LaRenderer](LaRenderer/) | A LaTeX editor with the compiled pages beside the text | GTK4 via MSYS2 |
| [Lyndon Browser](Lyndon%20Browser/) | A small, fast, private browser | Win32 + WebView2 |

Each folder has its own README; this one only covers what is common.

## On Windows

The three Python apps are GTK4 and libadwaita, and the only GTK4 that exists
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
copied and nothing is registered; the shortcuts point at the files where they
sit.

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

The first three apps answer all of that in one `winenv.py` each, so no other
module knows which operating system it is on.

## On Linux

Unchanged. Each README describes its own `~/.local/bin` symlink, `.desktop`
files and XFCE panel entries, and `Lyndon Browser/Makefile` still builds
against GTK4 and WebKitGTK. The one shared file that both platforms compile,
`Lyndon Browser/src/lyndon.h`, selects its toolkit on `_WIN32` and is
otherwise the same header it was.

## Tests

Each app has its own suite and each can be run on its own; `run-tests.py`
runs all four and is the quickest way to know the repository is sound.

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
| Lyndon Browser | The filter-list compiler on Linux, the request matcher on Windows, and on both the shared URL handling — address-or-search, first-party grouping, and the JavaScript escaping the password manager fills through. |

## Licence

MIT. See [LICENSE](LICENSE).
