# Mind Palace

Every app here in one window, each a room. The standalone apps stay too: Notedown
is still Notedown, and it is also a room you can walk into without leaving the one
you are in.

One window, one header bar, one slab of glass. Down the side, the rooms; Ctrl+1…9
goes to them, and the palace comes back to the last one you were in.

## The rooms

An app becomes a room by adding one file, `room.py` ([the contract](ROOMS.md)). An
app that has not yet is a **door**: its page says what it is, and opens it in a
window of its own, on the same interpreter, through its own launcher. A door is never
less than the Start menu would have given you, and every door is a room waiting to
be written.

Nothing is loaded until it is wanted. The sidebar is read off the disk without
importing a single app, and a room is built the first time it is visited or a file
is routed to it. On a 3 GB machine that is the whole design: twelve apps in the
sidebar, and in memory only the ones you have actually been in. Once built a room
stays built, so going back to it is instant.

## Home

Today at a glance, all of it read off the brain and none of it stored by the palace:

* **Cards**: how many are due and how many are new today, counted as Mnemosyne counts
  them, with the decks switched off in `learning.yaml` left out.
* **The inbox**: how many, how long the oldest has waited, and the oldest few.
* **Today's drill**: one from The Mentalist's `drills.yaml`, the same one all day, a
  different one tomorrow.
* **Questions to ask again** and **on this day**, from `threads.py` and `timelines.py`,
  once those are in the house (below).
* **Recent notes**, from the index every app shares.
* **Sync**: what `order sync` last said, and how long ago.

A card with nothing to say is not drawn: an empty inbox is not news.

The note index is brought up to date once, in the background, after Home has drawn:
on a brain of tens of thousands of notes that can take minutes, and nobody should wait
for it to find out what is due.

## Everywhere

**Ctrl+K searches everything.** Rooms first, because going somewhere is the commonest
thing to want. Then the brain, through `brain.search`: the Anamnesis index where there
is one, note titles where not. Whatever you pick goes through the same routing as
everything else, so a hit opens in the room that opens that kind of thing.

**Ctrl+Shift+A asks Claude**, about what the room on show is showing. The panel floats
over the room, the answer streams into it, and it is read-only, as every app's Ask is.
An answer that turns into a piece of work carries on in Claude Desk.

**A file opens where it belongs**, from a search, from a room, or from the command
line. A note saying `kind: timeline` goes to whoever keeps timelines, a `.md` to the
room that writes, a picture to the room that looks, a folder to the room that files;
`study:<note>` asks Mnemosyne's room to study it. A room can decline, and the next is
asked; the desktop is the last resort, not the first.

## Keys

| | |
|---|---|
| Ctrl+K | Search the palace |
| Ctrl+Shift+A | Ask Claude |
| Ctrl+1 … 9 | Go to a room, in the sidebar's order |
| F9 | Show or hide the rooms |
| Ctrl+, | Preferences |
| Ctrl+? | Keyboard shortcuts |
| Ctrl+Q | Quit |

These are caught before any room can see them. A room's own keys work while it is
showing, and only then, so one room's Ctrl+O is nothing to do with the next's.

## On a tiling desktop

Written on niri, on a 1366-wide screen. Below 720px the sidebar folds over the rooms
instead of standing beside them, so the window works in a 455px column; the suite
checks it, in every kind of room. The size is remembered and the maximise is not,
under a tiling compositor. See `housestyle.Geometry`.

## The house style

The look is the house's, from [`house/`](../house/README.md): the same themes, the same
Look page in Preferences, the same glass. The palace's theme is the one in force, in
every room. Each room's settings stay in its own app's file, so a thing changed in the
palace is changed in the app too. The palace keeps only its own:
`~/.config/mind-palace/config.json` (`%APPDATA%\mind-palace` on Windows) is the look,
the window, and which room was open.

## Running it

Needs GTK4, libadwaita and PyGObject, as the rest of this repository does. PyYAML is
used for `drills.yaml` and `learning.yaml` when it is there, and a small reader of the
same subset when it is not.

```bash
ln -sf "$PWD/mind-palace.py" ~/.local/bin/mind-palace
cp mind-palace.desktop ~/.local/share/applications/
cp mind-palace.svg ~/.local/share/icons/hicolor/scalable/apps/
update-desktop-database ~/.local/share/applications
```

```bash
mind-palace                   # the last room you were in
mind-palace notes/today.md    # in whichever room opens it
mind-palace --room Fathom     # go to a room
```

A second `mind-palace` hands what it was given to the window already open. On Windows,
see [the shared README](../README.md#on-windows): `windows/install.ps1` makes the Start
menu shortcut, and `mind-palace.bat` starts it on MSYS2's Python.

## Tests

```bash
python3 tests/test_mindpalace.py
```

The first half needs no display: what it finds in a small checkout built for the
purpose and in the real one, where a file is routed, and Home's numbers against a small
home pointed at with `ORDER_HOME`, so nothing real is read or written. The second half
opens the real window and drives it. A fake room, written to disk the way an app would
write one, walks the whole of [the contract](ROOMS.md); then the real rooms on main are
built in it. Nothing is launched: starting a door only records, and Claude is a
stand-in script.

## How it is put together

| | |
|---|---|
| `rooms.py` | Which apps there are, read off the disk with no GTK; what a `room.py` declares, without running it; and where a target goes. |
| `window.py` | The window: the sidebar, the header bar, building and showing rooms, whose keys are in force, and the routing's last mile. |
| `host.py` | The palace as one room sees it. |
| `today.py` | What Home says, read off the brain, with no GTK. |
| `home.py` | Home: a native room, keeping the same contract as any other. |
| `door.py` | The apps that are not rooms yet, and the rooms that would not build. |
| `search.py` | Ctrl+K. |
| `ask.py` | The panel Claude answers in. |
| `widgets.py` | The palace's own CSS, and a few small pieces. |
| `config.py`, `jobs.py`, `winenv.py` | Settings, the worker thread, and what differs on Windows. |
| `theme.py`, `housestyle.py`, `updater.py`, `brain.py`, `cards.py`, `fsrs.py`, `claude.py` | Vendored from [`house/`](../house/README.md), byte for byte. |

`threads.py` and `timelines.py` are looked for beside these, and the two cards they
feed are drawn when they are there. Home asks `threads.questions_due(day, places)` and
`timelines.on_this_day(day, places)`: an iterable each, of strings, of objects with a
`title` and perhaps a `path`, or of `(title, path)` pairs.

## Licence

MIT. See [LICENSE](../LICENSE).
