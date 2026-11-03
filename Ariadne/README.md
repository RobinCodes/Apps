# Ariadne

The questions worth keeping, and the logical threads that answer them.

Some questions are not for this week. They are asked at a moment — on a bus,
at two in the morning, in the middle of something else — and they are worth
keeping exactly as they came, then asking again later, and answering slowly
or never. Ariadne is where they are kept, and where they are thought through.

```bash
python3 ariadne.py                         # the window
python3 ariadne.py --ask                   # the window, straight on a new question
python3 ariadne.py --ask "Is time real?"   # keep it now, no window at all
```

## Keeping a question

**Ctrl+N**, type, **Enter**. That is the whole of it, on purpose: the moment
a question strikes is the moment it is worth catching, and anything between
the thought and the file is a chance to lose it. The time it was asked is
the time the box opened, not the time Enter was pressed — a question takes a
minute to word, and the minute that matters is the first.

Outside the window, `ariadne --ask "…"` keeps one without opening anything,
which is what a key of the desktop's own is for. On niri:

```kdl
binds { Mod+Q { spawn "ariadne" "--ask"; } }
```

A question is a file in `SecondBrain/questions/`, `Is time real.md`, written
as SYSTEM.md §10 says, so Anamnesis and a text editor read it too.

## Asking again

A question has a status — **open**, **living** (asked for good, never to be
closed), **answered**, **let go** — and, if you want one, a date to ask it
again. When that date comes it moves to the top of the list with a small
badge, which is a nudge and not an alarm: a question kept for years owes
nobody an answer this week.

Each time it is asked again, what you think *then* goes in its **Revisits**,
one dated line each (Ctrl+R). Read down them and you have how the answer
moved, which is often worth more than the answer.

## Threads

A **logical thread** is how a question gets answered: thoughts, each a title
and as much text as it needs, and arrows between them, each optionally with
a word on it — *therefore*, *because*, *but*, *unless*. A thread may answer
one question, several, or none; some threads are just thinking.

On the canvas:

| | |
|---|---|
| Double-click empty canvas | a thought there |
| Double-click a thought | edit it — Markdown emphasis and `[[links]]` work |
| Drag a thought | move it |
| Drag from its handle to another | an arrow |
| Click an arrow | its word, or remove it |
| Delete | take away what is selected — with an Undo |
| Scroll, Ctrl+scroll, pinch | pan and zoom |

A `[[link]]` in a thought opens what it names: a question or a thread here,
any other note in whatever opens notes. An arrow to a thought that no longer
exists is kept, dashed, with its id — never dropped, because it may be on its
way back.

A thread written by hand, without positions, is arranged left to right as a
flow: every thought one column right of the furthest thought that leads to
it, and the order within a column chosen to cross as few arrows as a few
sweeps can manage. Nothing is written until you move something.

## Thinking with Claude

With Claude Code installed, **Ctrl+Shift+A** asks Claude about what is in
front of you: the selected thought, or the question the thread answers, or
the thread itself. Claude answers in the thread's own syntax, so what comes
back is read by the same parser as the file — and shown as *ghosts*: dashed
thoughts and arrows laid over the canvas, which you can drag about. Accept
makes them real; Discard leaves the file exactly as it was. Claude only
reads; it changes no file.

## Keyboard

| | | | |
|---|---|---|---|
| Ctrl+N | Keep a question | Ctrl+Enter | Add a thought |
| Ctrl+Shift+N | New thread | Ctrl+L | Arrange |
| Ctrl+R | Add a revisit | Ctrl++ / Ctrl+− | Zoom |
| Ctrl+F | Find | Ctrl+0 | Everything in view |
| F9 | The list | Ctrl+Shift+A | Think with Claude |
| Ctrl+? | These keys | Delete | Take away, with Undo |

## The files are the truth

Ariadne holds what it last read and what it last wrote, and nothing else.
Every edit is on disk a moment after you stop typing, written beside the file
and moved into place so no reader ever sees half of one. A file changed by
something else — the Anamnesis viewer, a text editor, `order sync` bringing
another machine's edit — is noticed and read again.

What it writes it writes the way `house/threads.py` does for every program:
a file nobody changed is written back byte for byte, a thought nobody changed
keeps its own lines, and anything in the file that is not a thought is kept
untouched.

## In Mind Palace

`ariadne/room.py` makes Ariadne a room of Mind Palace: the same view, without
the window's header bar, its actions under `room`, its own rules alone added
to the stylesheet, and `[[links]]` to notes handed to whichever room opens
them. The palace keeps Ctrl+Shift+A, F9 and Ctrl+? for itself, so there Think
with Claude and the list are the buttons on the page. Run on its own, it is
exactly as above.

## Running it

Needs GTK4, libadwaita and PyGObject — on Arch, `sudo pacman -S gtk4
libadwaita python-gobject`. On Windows, see [the shared README](../README.md#on-windows);
`ariadne.bat` starts it on MSYS2's Python.

```bash
ln -sf "$PWD/ariadne.py" ~/.local/bin/ariadne
cp ariadne.desktop ~/.local/share/applications/
cp ariadne.svg ~/.local/share/icons/hicolor/scalable/apps/
```

## Tests

```bash
python3 tests/test_ariadne.py
```

The first half needs nothing but Python. The second builds the real view, in
its own window and in a stand-in for Mind Palace, against a scratch home
(`ORDER_HOME`), with Claude Code played by a script — nothing reaches the
real brain or a model. It also checks the content fits a 455px column.

## How it is put together

| | |
|---|---|
| `view.py` | the whole app as one widget: the list, the pages, the actions |
| `sidebar.py` | the questions and threads, due ones first |
| `questionpage.py` | a question: its words, status, revisits, threads |
| `threadpage.py` | a thread: its title, its questions, the canvas, Claude's ghosts |
| `canvas.py` | thoughts and arrows, drawn with cairo, and every gesture on them |
| `layout.py` | the arrangement of a thread nobody has arranged |
| `inline.py` | a thought's emphasis and links, for Pango |
| `thinking.py` | what Claude is asked, and how its answer becomes ghosts |
| `library.py` | every question and thread, and which file changed under us |
| `window.py`, `app.py`, `cli.py` | the window, the application, the command line |
| `room.py` | Ariadne as a room of Mind Palace |
| `threads.py` `frontmatter.py` `brain.py` `claude.py` … | the house's, copied in by `house/sync.py` |

## Licence

MIT. See [LICENSE](../LICENSE).
