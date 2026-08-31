# Claude Desk

A native desktop front end for Claude Code. It runs the real `claude` CLI in
the background — same engine, same tools, same session files, same
permissions — and replaces only the part you interact with: a window with a
list of conversations on the left and one chat on the right.

```bash
claude-desk                  # open the window (on PATH via ~/.local/bin)
claude-desk ~/Projects/foo   # open it with a new session in that folder
```

Nothing is reimplemented. Anything the terminal can do — Bash, Edit, Task
subagents, MCP servers, skills, hooks, your `CLAUDE.md` — works here, because
it is the same program underneath.

## The idea

Claude Code has a machine-facing mode the terminal never shows you:

```bash
claude -p --input-format stream-json --output-format stream-json --verbose
```

In that mode the process stays alive across turns and speaks JSON in both
directions. That is the whole integration. Two details make it usable as an
interactive app rather than a batch pipe:

* **`--permission-prompt-tool stdio`** — undocumented in `--help`, and the
  difference between an app and a toy. Without it a session in `manual` mode
  silently *denies* every write instead of asking. With it, permission
  questions arrive as `can_use_tool` control requests, which become the inline
  Allow / Deny card.
* **The control channel** also carries `interrupt` and `set_permission_mode`,
  so Escape stops a run and the header dropdown changes permissions mid-session
  exactly as they do in the terminal.
* **It is also how Claude asks *you* something.** `AskUserQuestion` arrives as
  a `can_use_tool` request like any other — but allowing it is not the answer,
  and a card with an Allow button on it is a conversation where every question
  goes unanswered. See below.

## Several sessions on a small machine

A live `claude` child process holds **about 450 MB resident** — measured, not
estimated. On a machine with 4 GB that is two of them, not ten, and yet running
several at once is the entire point of a desktop front end.

What makes it work is that Claude Code sessions are resumable. So a session
here is a *conversation*, not a process:

* Each has its own folder, model, permission mode and transcript.
* A child process is a resource lent to a conversation while it is being used.
* When the budget is full, the least recently used idle session is put to
  sleep and its memory handed back.
* Speaking to a sleeping session relaunches it with `--resume <session-id>`,
  which restores the full context. You notice only that the first reply takes
  a moment longer.
* If every slot is busy, your turn is **queued** rather than exceeding the
  budget — the session shows "Waiting for a free slot" and starts on its own.

The sidebar footer shows the current cost (`1 of 2 live · ~450 MB`). The limit
and the idle-sleep timeout are in **Preferences**; raise them if you move this
to a bigger machine.

Moving a session to a different folder always starts a fresh Claude Code
session there rather than resuming the old id, because a session is tied to
the directory it started in. If the conversation had history, a line in the
transcript says so — the text stays readable, but the model is no longer
carrying it.

Transcripts live in `~/.local/share/claude-desk/transcripts/` so history is
readable without waking anything. The real conversation state stays where
Claude Code puts it, under `~/.claude` — this app never duplicates it, which
is why `--resume` works and why `/resume` in a terminal can pick up a
conversation you started here.

## The window

| | |
|---|---|
| **Sidebar** | One row per conversation. The dot is its state: green live and idle, blue working, amber waiting on you, grey asleep. |
| **Header** | Session name and what it is doing, plus model and permission-mode dropdowns. Changing the model re-launches on the next message, since it is fixed at spawn. |
| **Folder chip** | The working folder, as a button next to the sidebar toggle — click it to move the session somewhere else. Also under ⋮ → **Change folder…**. |
| **Chat** | Your turns in a tinted card, replies as plain prose. Fenced code becomes a card with a copy button; reasoning folds away behind "Thought for a moment"; each tool call collapses to its name and the one argument that matters, and opens on click. |
| **Markdown** | Tables become real grids, task lists get checkboxes, and a bullet the model wrapped across three source lines stays one bullet. |
| **Maths** | `$inline$` renders in the sentence; `$$displayed$$` gets stacked fractions, roots under a radical, limits above and below their operator, and drawn brackets around matrices. Click a formula to copy its LaTeX. |
| **Scrolling** | The view follows the conversation while you are at the bottom, holds its place the moment you scroll up, and follows again when you come back. A button appears in the corner to jump to the latest. |
| **Permissions** | Inline at the bottom of the thread, never a modal — other sessions may want attention at the same time, and a dialog would block the window. |
| **Commands** | A leading slash opens the command menu above the composer — every command the child knows, each with its arguments and a one-line description. ↑↓ chooses, Tab completes, Esc dismisses. `/usage`, `/context` and `/mcp` answer onto a card of their own instead of becoming messages, and `/usage` answers **while a turn is running**, the way it does in the terminal. `/clear`, `/rename` and `/model` do what the menu and the header do. |
| **Questions** | When Claude asks something rather than asks to do something, the same place fills with the questions themselves: options to choose, previews where the model attached one, **Something else…** to write your own, and Skip to carry on unanswered. Typing in the composer answers too. |
| **Queue** | Messages typed while Claude is working, listed above the composer until their turn comes. |

| Shortcut | |
|---|---|
| `Ctrl+N` | New session |
| `Ctrl+1`…`9` | Jump to the *n*th session |
| `Enter` / `Shift+Enter` | Send — or queue if a turn is running, or answer a question on screen / newline |
| `Tab` | Complete the highlighted command, when the menu is open |
| `Esc` | Close the command menu — or, with none open, interrupt the turn and take the queue back |
| `Ctrl+Q` | Quit |

## Markdown, and maths without a browser

Claude replies in Markdown and writes maths in LaTeX, so both are rendered
rather than shown as source — and neither is worth 300 MB of web view.

Markdown is parsed into blocks in `markdown.py` and each becomes a native
widget: paragraphs and headings are Pango markup on a label, fenced code is a
card with a copy button, a pipe table is a `GtkGrid` that scrolls sideways
when it is wider than the column. Soft line breaks are treated as Markdown
defines them — one newline is a space — because the model wraps at eighty
columns and the window is not eighty columns wide.

`latex.py` parses the maths into a small tree, and rendering it splits in two:

* **Most of a formula is a line.** Symbols, italic variables, superscripts and
  subscripts go through Pango markup and Unicode, which is fast, selectable,
  and wraps with the text around it. All inline maths is drawn this way.
* **The parts that are genuinely two-dimensional get widgets.** Fractions
  stack over a drawn rule, roots sit under an overline, `\sum` wears its
  limits above and below, and matrices are a grid between delimiters drawn
  with CSS borders — so a bracket grows with what it encloses instead of being
  a glyph at a fixed size.

Around 250 commands are understood: Greek, the usual relations and operators,
`\frac`, `\sqrt`, scripts, accents, `\text`, `\mathbb`, `\mathbf`,
`\left`/`\right`, and the `matrix`, `cases` and `align` environments.
Anything unknown renders as its own name rather than vanishing, and nothing in
the module raises — a formula that fails to parse must not take the window
with it. **Noto Sans Math** is named first in the maths font stack; Pango
falls back per glyph, so a machine without it still draws everything it can.

## Typing ahead

A reply you are already reading is not a reason to forget the next thing you
meant to say. Enter always takes the message: if a turn is running it joins a
queue under the composer, and one message goes out each time Claude finishes,
so a session works through your follow-ups while you do something else.

* Queued messages are **not** in the transcript. Nothing has been said yet, and
  they are still yours to change — click one to pull it back into the composer,
  or × to drop it.
* **Escape gives them back rather than sending them.** Stopping a turn usually
  means the run went somewhere you did not want, and the follow-ups were
  written for the version of events you just cancelled; flushing them would
  restart what you stopped. They land in the composer instead. **Sleep now**
  does the same, since queued work would otherwise wake the session straight
  back up.
* A queue survives the process it was typed at. Eviction, a crash, or a model
  change relaunches the session and the next message goes out on its own — the
  only thing that discards a queue is quitting, because it lives in memory
  rather than on disk.

The sidebar footer counts turns waiting for a *slot*, which is the other queue:
that one is about memory, this one is about you.

## Where it's installed

**This folder's name contains a space, and `Exec=` in a `.desktop` file splits
on whitespace.** A launcher pointing straight here would try to run
`…/Useful/Claude` and do nothing, so the entry points go through a symlink,
the same way Git Manager does:

```bash
ln -sfn "$PWD/claude-desk" ~/.local/bin/claude-desk
```

| Location | What it is |
|---|---|
| `~/.local/bin/claude-desk` | The symlink. Everything else depends on it. |
| `~/.local/share/applications/claude-desk.desktop` | Applications menu, under **Development**. |
| `~/Desktop/claude-desk.desktop` | Desktop icon. Executable, and marked trusted with `gio set … metadata::xfce-exe-checksum` so xfdesktop launches it without the "untrusted" prompt. |
| `~/.config/xfce4/panel/launcher-24/claude-desk.desktop` | The bottom dock's copy, registered as panel plugin 24. |
| `~/.local/share/icons/hicolor/scalable/apps/claude-desk.svg` | The app icon, referenced as `Icon=claude-desk`. |
| `~/.config/claude-desk/config.json` | Sessions, models, folders, preferences, and the last command list the child sent. |
| `~/.local/share/claude-desk/transcripts/` | One JSON file per conversation. |

Moving this folder means re-pointing only the symlink.

**The trust checksum is of the file's contents**, so editing
`~/Desktop/claude-desk.desktop` — even just its `Icon=` line — invalidates it
and xfdesktop starts asking again. Re-stamp it after any edit:

```bash
gio set ~/Desktop/claude-desk.desktop metadata::xfce-exe-checksum \
    "$(sha256sum ~/Desktop/claude-desk.desktop | cut -d' ' -f1)"
```

The dock entry also needs two xfconf keys, since the panel keeps its layout in
`xfconfd`'s memory and rewrites the XML behind your back:

```bash
xfconf-query -c xfce4-panel -p /plugins/plugin-24 --create -t string -s launcher
xfconf-query -c xfce4-panel -p /plugins/plugin-24/items --create --force-array \
    -t string -s claude-desk.desktop
# then rewrite /panels/panel-2/plugin-ids with 24 inserted, and: xfce4-panel -r
```

`panel-2` is the bottom dock; `plugin-ids` is a single array-valued property,
so the whole array has to be written at once. Claude Desk sits at position 24,
directly after Git Manager's 23. A backup of the pre-change panel XML is in
`~/.config/xfce4/panel-backup-20260814-174855/`.

Create the launcher directory and the two xfconf keys **before** adding the id
to `plugin-ids`, or the panel briefly sees an id with nothing behind it. Note
that `xfce4-panel` rewrites its own copy of the `.desktop` file on restart —
that is normal, and it preserves `Exec` and `Icon`.

To remove everything: delete the files above, drop `/plugins/plugin-24` and
`/plugins/plugin-24/items` with `xfconf-query -r`, remove `24` from
`plugin-ids`, and restart the panel.

**A menu launcher does not inherit your shell's PATH.** XFCE hands its
children `/usr/local/bin:/usr/bin:/bin:…` and nothing more — no
`~/.local/bin`, which is where `claude` actually lives. From a terminal the
app started; from the dock it exited before drawing a window, with its one
line of explanation going to `~/.xsession-errors` where nobody reads it. So
`backend.resolve_bin()` searches the usual install directories itself and
prepends the winning one to PATH, which the child inherits too. If the binary
is genuinely missing and there is no terminal attached, the complaint now
comes as a dialog.

Requires `python-gobject`, `gtk4`, `libadwaita` and `claude` — on PATH or in
one of the directories in `backend.BIN_DIRS` — all already present.
`CLAUDE_DESK_BIN` overrides which binary is driven, and takes an absolute
path.

---

## Layout

```
claude-desk            launcher; puts its own directory on sys.path
tests/                 python3 tests/test_claudedesk.py — no display needed for
                       the protocol half, no tokens for any of it; add
                       CLAUDE_DESK_LIVE=1 to drive a real child as well
claudedesk/
  app.py               Adw.Application, command line, missing-CLI check
  window.py            main window, sidebar, header, preferences
  chat.py              transcript, tool cards, permissions, questions, queue, composer
  markdown.py          Markdown to blocks, inline spans to Pango markup
  latex.py             LaTeX to a small tree, and that tree to markup
  widgets.py           stylesheet, tool icons and summaries, dialogs
  manager.py           sessions, the live-process budget, eviction and queueing
  backend.py           one `claude` child and the protocol we speak to it
  config.py            settings, session registry, transcript files
```

The dependency direction is strict and worth keeping:

* `backend.py` imports no GTK at all. It is a plain subprocess wrapper, and its
  reader runs on a worker thread.
* `manager.py` is the only glue: it marshals those worker-thread events onto
  the GTK main loop with `GLib.idle_add`, and it is where the memory budget
  lives.
* Everything in `chat.py` and `window.py` is pure UI reading off a `Session`.

So the protocol can be tested without a display, and the widgets can be tested
without spending a token — which is how both are tested.

## Notes for later

* **`/usage` cannot wait for the turn in front of it.** The child reads stdin
  only between turns: a command written to it mid-run is answered *after* the
  reply you were trying to look past — three seconds of question, twenty of
  waiting, measured. But `/usage` is about the account and `/cost` about the
  plan, and any child can answer those, so they get one of their own: `claude
  -p /usage`, about three seconds, gone again. `/context` and `/mcp` stay with
  this conversation's own child, because a fresh one would report on itself —
  an empty context window and MCP servers still connecting. Those two wait,
  and say so. Closing stdin on the throwaway child matters, incidentally: left
  open, the CLI spends three seconds waiting for input that is never coming,
  which was most of the wait.
* **A report is not a message.** `/usage`, `/context` and `/mcp` are answered
  by the CLI itself — no model, no tokens — and what they say is about the
  tool rather than to it. So they are sent to the child but kept out of the
  conversation entirely: no user turn, no reply in the transcript, no title
  taken from them, just a card you close when you have read it. The guess is
  made from the command's name, and a project is free to define a command of
  its own by the same name; the moment one of them thinks or picks up a tool,
  what was held back goes into the conversation as the turn it evidently is.
* **`/clear`, `/rename` and `/model` never reach the child.** They would work
  if they did, and leave the window wrong — the name in the sidebar, the
  transcript on disk and the model the next child is launched with are the
  app's, and it would never hear that they had changed. They run the same
  code the menu and the header dropdowns do.
* **Two frames carry the command list and only one is worth keeping.** The
  handshake reply has a description and an argument hint per command; the
  init frame that follows has only names. Taking whichever arrived last threw
  the descriptions away, which is why `remember_commands` prefers the
  described list. It is also cached in `config.json`, so the menu works before
  anything has been launched.
* **An answer travels in `updatedInput`.** `AskUserQuestion` is allowed like a
  permission, but the permission is only consent for it to run: the tool then
  reads what you chose out of the input it is run with. So the card sends
  `answers` back with the allow, keyed by the text of each question, and the
  questions themselves go back untouched — `updatedInput` replaces the input
  rather than adding to it. Allowing and nothing more is what the terminal
  sends when the dialog is dismissed, and the model is told in as many words
  that nobody answered.
* **A question suspends the composer's queue rule.** Enter queues while a turn
  is running, but a turn stopped on a question cannot end, so a queued answer
  would never go out and the two of you would wait on each other. With a
  question on screen Enter sends what you typed as the answer instead — the
  tool has a field for exactly that — and it joins the transcript as the turn
  of yours that it is.
* **Nothing in that card is selectable.** A selectable label selects all of its
  text the moment focus lands on it, and the card is a row of controls to move
  through rather than prose to copy out of — the question is in the tool card
  above it in any case.
* **Streaming** uses `--include-partial-messages`, coalesced to ~12 frames a
  second. Redrawing per token pegs a low-end CPU for no visible gain.
* **Deltas are transient.** They paint a live preview which is cleared when the
  authoritative block arrives, because Claude Code emits one `assistant` frame
  per content block and appending both would double every reply.
* **Code blocks are labels, not text views.** `GtkTextView` works out its
  height during layout validation, so a `ScrolledWindow` asking for its natural
  height up front gets a value that is too small and clips the block.
* **Freeing a slot re-enters the queue pump** — evicting sleeps a session,
  which releases its slot, which pumps again. `_pumping` keeps one frame in
  charge so two cannot claim the same waiting turn.
* **A session holding queued messages is never evicted.** It looks idle
  between turns, and sleeping it would only make it claim a slot straight back
  — two sessions can trade the same slot back and forth that way.
* **The composer button is Stop only when the box is empty.** Enter and the
  button then always agree, so neither can stop a run you meant to type at.
* **`$` is guessed at, deliberately conservatively.** `$x$` is maths and
  `$5 and $6` is money, and only the shape between them tells you which: no
  space against the delimiters, something operator-like inside, no digit
  straight after the close. Italicising prices is a worse failure than missing
  a formula, so anything that fails those tests stays text. `\(…\)` and
  `$$…$$` are unambiguous and always render.
* **`GLib.idle_add(widget.grab_focus)` never stops.** `grab_focus()` returns
  True to report that it worked, and an idle source reads True as "call me
  again" — so the focus grab repeats for as long as the widget lives, tens of
  thousands of times a second. In the rename dialog that re-selected the text
  under each keystroke, so a rename could never be longer than one character;
  on the permission card it held focus away from everything else. `widgets.focus_soon()` wraps the grab in a callback that returns `SOURCE_REMOVE`.
* **Two adjustment signals tell you who moved the view.** `value-changed` is
  the view moving — you, whether by wheel, scrollbar or keyboard — and decides
  whether it keeps following. `changed` is the content growing underneath it,
  and follows only if it was already following. Nothing has to know *how* you
  scrolled, which is why every input method works without a special case.
* **Destroying a focused widget leaves the scrolled window checking the
  ancestry of something that no longer exists** — every message label is
  selectable, so one of them may hold the focus when the transcript is
  rebuilt. `reload()` hands focus back to the composer first. Without that,
  GTK logs `gtk_widget_is_ancestor: assertion 'GTK_IS_WIDGET (widget)' failed`
  on the next scroll.
* **A wrapping table cell asks for the height of its narrowest wrap.** Left
  alone, a one-line cell that *could* wrap to eight reserves eight lines and
  the table trails a gap. Pinning each cell's minimum width to the width its
  text actually needs makes the minimum and natural heights agree.
* **Operators space themselves by their neighbours**, so a leading minus is a
  sign and not a subtraction. Where a run is split around a fraction, an empty
  node stands in for what is on the other side — otherwise `dx =` against a
  fraction renders as `dx=`.
* Only the last 300 transcript entries are built as widgets; the rest stay on
  disk. Tool results are stored truncated at 20 000 characters.

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

The `claude` CLI itself, which is not part of this app:

```powershell
npm install -g @anthropic-ai/claude-code
```

npm's global directory is under `%APPDATA%`, which a shortcut-started process
does not have on PATH — `winenv.which()` knows to look there. Run `claude`
once in a terminal and log in before using the app; without that every turn
comes back `Not logged in`, because the credentials are the CLI's, not this
app's. Point `CLAUDE_DESK_BIN` at the binary if it lives somewhere unusual.

The protocol is identical on Windows: the same `stream-json` on stdin and
stdout, the same control channel, the same `--permission-prompt-tool stdio`.
