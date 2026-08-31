# Git Manager

A desktop front end for every git repository on this machine, and for the
GitHub account behind them. It scans the filesystem for repositories, shows
what changed in each, and drives the operations you'd otherwise type — stage,
commit, push, branch, merge, stash — from one window.

```bash
git-manager               # open the window (on PATH via ~/.local/bin)
git-manager .             # open it with the repository containing . selected
```

## Where it's installed

**This folder's name contains a space, and `Exec=` in a `.desktop` file splits
on whitespace.** A launcher pointing straight here silently tries to run
`…/Useful/Git` and does nothing. So the entry points go through a symlink
instead, the same way `launcher-21` reaches `markpad`:

```bash
ln -sfn "$PWD/git-manager" ~/.local/bin/git-manager
```

Every shortcut below uses `Exec=/home/robin/.local/bin/git-manager %f`, so
moving this folder means re-pointing only that one symlink.

| Location | What it is |
|---|---|
| `~/.local/bin/git-manager` | The symlink. Everything else depends on it. |
| `~/.local/share/applications/git-manager.desktop` | Applications menu, under **Development**. |
| `~/Desktop/git-manager.desktop` | Desktop icon. Executable, and marked trusted with `gio set … metadata::xfce-exe-checksum` so xfdesktop launches it without the "untrusted" prompt. |
| `~/.config/xfce4/panel/launcher-23/git-manager.desktop` | The bottom dock's copy, registered as panel plugin 23. |

The dock entry also needs two xfconf keys, since the panel keeps its layout in
`xfconfd`'s memory and rewrites the XML behind your back:

```bash
xfconf-query -c xfce4-panel -p /plugins/plugin-23 --create -t string -s launcher
xfconf-query -c xfce4-panel -p /plugins/plugin-23/items --create --force-array \
    -t string -s git-manager.desktop
# then append 23 to /panels/panel-2/plugin-ids and: xfce4-panel -r
```

`panel-2` is the bottom dock — the autohiding one with Firefox and Geany on it;
`panel-1` is the bar across the top. Rebuilding the whole array is the only way
to change it, because `plugin-ids` is a single array-valued property. A backup
of the pre-change panel XML sits in `~/.config/xfce4/panel-backup-*/`.

To remove everything: delete the four files above, drop `/plugins/plugin-23`
and `/plugins/plugin-23/items` with `xfconf-query -r`, remove `23` from
`plugin-ids`, and restart the panel.

Requires `python-gobject`, `gtk4`, `libadwaita` and `git`, all already present.
The GitHub half additionally needs `github-cli`; without it the app still works
and the GitHub tab explains what's missing.

---

## Layout

| File | Role |
|---|---|
| `git-manager` | The launcher. Puts its own directory on `sys.path`, so the app runs from wherever this folder sits — there is no install step. |
| `gitmanager/app.py` | `Adw.Application` subclass and entry point. |
| `gitmanager/window.py` | The window: repository sidebar, header, and the tab host. Owns `RepoState`, the per-repo cache of status and GitHub name. |
| `gitmanager/changes.py` | Changes tab — staged/unstaged file lists, the diff, hunk staging, the commit box. |
| `gitmanager/history.py` | History tab — commit log, per-commit detail and diff, revert and reset. |
| `gitmanager/branches.py` | Branches tab — local and remote branches, and the stash. |
| `gitmanager/ghtab.py` | GitHub tab — pull requests, issues, CI runs, cloning, publishing. |
| `gitmanager/link.py` | Connect helper — joining a folder that already exists to a repository that already exists. |
| `gitmanager/gitcmd.py` | Every git invocation, returning dataclasses. Knows no GTK. |
| `gitmanager/github.py` | Every `gh` invocation, likewise. |
| `gitmanager/ignore.py` | Gitignore helper — the file picker, the pattern writer, the tracked-file check. |
| `gitmanager/readme.py` | README helper — repository introspection and the markdown generator. |
| `gitmanager/scanner.py` | The filesystem walk that finds repositories. |
| `gitmanager/widgets.py` | Diff rendering, confirmation dialogs, the stylesheet. |
| `gitmanager/jobs.py` | Thread helpers — the only route from a worker back to the main loop. |
| `gitmanager/config.py` | Settings, in `~/.config/git-manager/config.json`. |

State lives in two places: settings in `~/.config/git-manager/config.json`, and
the last scan's repository list in `~/.cache/git-manager/repos.json`. Deleting
either is safe; the cache only exists so the window has content before the walk
finishes.

## Scanning

The walk starts from the roots in settings — `~` by default — and is
breadth-first, so shallow repositories appear first. It stops descending as
soon as it finds a `.git`, and prunes about thirty directory names that are
pure cost (`node_modules`, `__pycache__`, `.cache`, `.venv`, package caches,
`Trash`). Over this home directory that's 19 repositories in half a second.

A `.git` *file* rather than a directory counts too, so worktrees and submodules
are found. Bare repositories are recognised by having `HEAD`, `objects` and
`refs` with no worktree.

Add more roots — `/mnt`, an external drive — under **Scan settings** in the
sidebar menu. Adding `/` works and is prevented from wandering into `/proc`,
`/sys`, `/dev` and `/run`, but it is much slower and mostly finds other
people's vendored dependencies.

*Forget this folder*, in the Actions menu, takes one repository out of the
list without touching anything on disk. It works on any row, not only the
ones added by hand: a repository found by the scan would be found again by
the next one, so forgetting it is written down and the merge that builds the
list applies it every time. Nothing is deleted, and there are two ways back —
**Undo** on the toast, and the **Forgotten folders** group that appears in
Scan settings for as long as anything is in it. Adding the folder again with
*Add existing folder…* also brings it back.

## What each tab does

**Changes** lists staged and unstaged files separately. Selecting one shows its
diff split into hunks, each with its own *Stage hunk* / *Discard* button —
the same granularity as `git add -p`, done by rebuilding a patch from the
selected hunk and feeding it to `git apply --cached`. Untracked files are
whole-file only: there is no index entry to apply a partial patch against, so
the diff is shown for reading and the row's `+` stages the file.

**History** pages through the log 150 commits at a time and can search messages.
The dropdown above the diff narrows it to a single file in that commit.

**Branches** covers local and remote branches — check out, merge, delete,
create — and the stash, which is where *Stash changes…* in the Actions menu
puts things.

**GitHub** shows open pull requests, open issues and recent workflow runs for
whichever repository is selected, matched by parsing its remote URLs — `origin`
first, then any other remote pointing at github.com, so a fork wired up as
`upstream` still counts. A repository with no GitHub remote gets two buttons
instead: *Publish as a new repository…*, which creates a private repository,
wires up `origin` and pushes, and *Connect to an existing repository…*, which
is the next section. *Clone from GitHub…* lists the whole account and marks
which repositories are already on disk — currently everything except
`kocsisagnes.hu`.

## Connecting a folder to a repository that already exists

Publishing covers the case where only the folder exists; cloning covers the
case where only the repository does. **Connect to GitHub…** — in the sidebar
menu, in **Actions**, and on the GitHub tab of any repository with no remote —
is the fourth quadrant: both already there, made separately, never introduced.

Pick the folder (it needn't be a repository yet, or be in the list at all),
then the repository, either from your account or by pasting a URL, an
`owner/name`, or a bare name. Nothing runs until the plan below it reads right,
and the plan is written out in full first:

```
→ Create a repository in ~/Projects/example
→ Add origin → https://github.com/RobinCodes/example.git
→ Fetch origin
→ Point main at origin/main and track it
· The difference then shows up in the Changes tab
```

What that plan says depends on what the two sides hold:

| The folder | GitHub | What happens |
|---|---|---|
| No commits, no files | Has commits | Checks the default branch out into it. |
| No commits, has files | Has commits | Moves HEAD onto the remote tip and **leaves every file exactly as it is**. |
| Has commits | Empty | Offers to push the branch and track it. |
| Has commits | Has that branch | Sets the upstream, then reports how the two histories relate. |
| Has commits | Hasn't that branch | Says so; Push creates it. |

The second row is the one that needs care, and it is the common one — a folder
of work, and a repository made in the browser to hold it. `git checkout` and
`git pull` both reach that state by overwriting files. `git reset --mixed`
doesn't read or write the working tree at all: it moves HEAD and the index to
the remote's tip and leaves the folder alone, so every difference between your
copy and GitHub's turns into an ordinary row in the Changes tab — edits to
read, and deletions for the files GitHub has that you don't, which *Discard*
brings back.

Two histories with no commit in common are the other trap: GitHub's *add a
README* tick-box starts one, after which git refuses to pull ("unrelated
histories") and rejects every push, and neither message says what to do. That
case is detected once connected and offered as a merge, which is the only thing
that joins them without throwing one side away.

The remote is checked with `git ls-remote` before a single thing is written —
with the credentials the later fetch and push will use, rather than `gh`'s word
for it — so a repository that doesn't exist, can't be reached, or can't be
written to is caught while the plan is still just text on the screen.

**A folder inside another repository** can still become one in its own right —
one project in a folder of projects, all under a single `.git`, growing up into
a repository of its own. Git nests working trees without complaint, so this is
allowed, and it is not blocked.

What git does not do is let go. Everything the surrounding repository already
tracks inside that folder it goes on tracking afterwards, and the same files
then sit in two repositories, each blind to what the other commits. The plan
counts them and says so:

```
! ~/Projects/Apps tracks 37 files inside this folder
→ Create a repository in ~/Projects/Apps/Git Manager
→ Add origin → https://github.com/RobinCodes/Git-Manager.git
→ Fetch origin
```

*Take it out of Apps* — the switch under **How**, off until it is turned on —
is the way out of that. It drops the folder from the outer repository's index
(`git rm -r --cached`, so every file stays exactly where it is on disk) and
adds an ignore rule for it there. Neither half is committed: the staged removal
and the edited `.gitignore` are left in that repository's own Changes tab,
because whether its history loses those files is a commit for its owner to
make, not something to slip into a connect.

Where the outer repository tracks nothing in the folder, the switch offers the
ignore rule alone — still worth having, since `git add -A` there would
otherwise record the folder as a bare pointer to a commit, a submodule with
none of the wiring. Where it already ignores the folder there is nothing to do,
and the plan says that instead.

*Add existing folder…* pointed at a folder with no `.git` in it asks which of
the three things was meant — connect it to GitHub, start a repository in it, or
scan it for repositories — rather than silently turning it into a scan root.
Pointed at a folder that is *part* of a repository, it asks the matching
question — add the repository around it, or connect this folder to GitHub as
one of its own — rather than adding a repository nobody picked.

## Project files

Two generators under **Actions → Project files**, both showing the file they
are about to write in an editable pane beside the controls. Nothing is written
until you press the button in the header.

**Gitignore helper** lists the repository's files and folders with a checkbox
each, above a set of common patterns filtered to what the project shows
evidence of — `__pycache__/` only where there is Python, `node_modules/` only
where there is a `package.json`. Ticking a box inserts one line; hand-edits to
the pane are never overwritten by the picker.

It exists because .gitignore has two silent failure modes, and both produce the
same symptom — the file still shows up — with no error anywhere:

- *The pattern doesn't match what you think.* .gitignore is not a shell, so
  `piac/"Options Trading"` matches a directory whose name literally contains
  quote marks. Spaces need no quoting; `*`, `?`, `[`, `]` and a trailing space
  are what need escaping. Generated patterns are anchored and escaped, and the
  pane flags existing lines with this mistake in them.
- *The path is already tracked.* Ignore rules are consulted only for untracked
  files, so a pattern added after the first `git add` changes nothing. After
  writing, the helper re-checks the index with `git check-ignore --no-index`
  and offers *Stop tracking* for anything that matches a rule but is still
  tracked. That runs `git rm --cached`, which leaves every file on disk and
  stages the removal for your next commit.

**README helper** reads the repository — manifests for the name, blurb and
commands, `git ls-files` for the structure tree, `origin` for the clone line,
the LICENSE file for the licence — and assembles a README from whichever
sections have something behind them. Manifests one directory down are found
too, for repos that keep their code in `app/` or `src/`. Sections with no
evidence are switched off rather than filled with confident prose, and the
gaps it can't infer are left as HTML comments to replace. It warns before
replacing an existing README, whatever its capitalisation.

## Destructive operations

They are all present, and all behind an `Adw.AlertDialog` that names what is
about to be lost — the count of unpushed commits, the count of uncommitted
files — rather than asking a generic "are you sure".

| Operation | Where | Guard |
|---|---|---|
| Discard hunk | Changes, per hunk | Confirm |
| Discard all changes | Actions menu | Confirm, counts tracked and untracked separately |
| Amend last commit | Changes, checkbox | Confirm, mentions the force push it implies |
| Force push | Actions menu | Confirm; always `--force-with-lease` |
| Hard reset to upstream | Actions menu | Confirm, names what is lost |
| Reset branch to a commit | History | Confirm; `--mixed`, so the working tree survives |
| Delete branch | Branches | Confirm; unmerged branches prompt a second time |
| Delete remote branch | Branches | Confirm, says it affects everyone |
| Drop stash | Branches | Confirm |
| Stop tracking ignored files | Gitignore helper | Confirm, lists the paths; files stay on disk, the removal is staged |
| Repoint an existing remote | Connect to GitHub | Flagged in the plan, naming the URL it forgets; nothing on either server changes |
| Take a folder out of the repository around it | Connect to GitHub | Off unless switched on; counted in the plan first; files stay on disk, the removal is staged there, nothing is committed |
| Forget this folder | Actions menu | No confirmation: nothing is deleted, and Undo on the toast or Scan settings puts it back |

Force push is deliberately `--force-with-lease` rather than `--force`: it
refuses if the remote moved since your last fetch, which is the difference
between rewriting your own branch and clobbering someone else's work.

## Notes

**Authentication is borrowed, not stored.** Every GitHub call shells out to
`gh`, so the app inherits `~/.config/gh` and has no token of its own to prompt
for, store, or leak. `gh auth login` in a terminal is the only way to change
accounts.

**Nothing blocks the main loop.** A `git status` over a repository with 800
untracked files, or any network call, would freeze the window if run inline, so
everything goes through `jobs.run` — work on a thread, result delivered back
via `GLib.idle_add`. Sidebar statuses load through `jobs.Serial`, a
one-at-a-time queue, so nineteen repositories don't open nineteen threads.

**Text labels, not icons.** The active icon theme is `elementary`, and several
of its symbolic icons — `folder-download-symbolic`, `document-edit-symbolic`,
`document-open-recent-symbolic` — resolve successfully but render blank. Rather
than depend on which names happen to work, the toolbar, tabs and row actions
use words. The tab bar is a `Gtk.StackSwitcher` rather than `Adw.ViewSwitcher`
for the same reason: the Adw one always reserves an icon slot, so every tab
carried a missing-image glyph.

**The selected repository is polled** every 15 seconds while the window is
focused, and refreshed whenever the window regains focus, since files change in
an editor while this sits in the background. The interval is in settings. A
poll that finds a new HEAD invalidates the History tab; one that doesn't leaves
the open diff exactly where it was, so a refresh never yanks the file you're
reading out from under you.

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

`git`, and `gh` for the GitHub tab. Both are found in `Program Files` whether
or not they are on PATH. The filesystem scan skips the Windows directories
that make scanning a whole drive slow — `Windows`, `Program Files`,
`ProgramData`, the package caches under `AppData`, `$Recycle.Bin` — and
compares paths case-insensitively, which on this platform is what "the same
directory" means.

"Open in terminal" uses Windows Terminal if it is installed, then Git's bash,
then `cmd`, which is always there.
