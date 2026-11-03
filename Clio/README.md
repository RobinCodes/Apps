# Clio

Timelines to write against: what happened, when, and the periods it belonged to.

A board is one axis that runs from centuries down to days, with the same zoom all
the way. Things that happened hang above the line, coloured by what kind of thing
they were. Below it, `}` braces group them into the periods they belonged to, and
a brace inside a brace nests outwards. The first board is a life; the same form
serves a history course or a project.

```bash
python3 clio.py                # the window
python3 clio.py BOARD.md       # open a board
```

## A board is a file

One Markdown file per board in `SecondBrain/timelines/`, in the format of
`SYSTEM.md` §11, and that is all the state there is. Clio holds nothing the file
doesn't:

```markdown
---
kind: timeline
title: My life
born: 2011-10-10        # optional: the axis also shows age
colours:
  family: "#c0623b"
  school: "#4f7cac"
---

## Events
- 2011-10-10 · Born {family} !
- ~2017-09 · First day at school {school}
  Indented lines under an event are its notes; [[links]] work in them.

## Spans
- 2017-09 .. 2025-06 · Primary school {school}
- 2025-09 .. · Szent István Gimnázium {school}
```

Other programs edit the same file — the Anamnesis viewer, a text editor, a sync
bringing another machine's edit — so Clio follows the rules that make that safe.
A save replaces the file atomically. A line nobody changed is written back exactly
as it was read, comments and unknown sections included. A file that changes on disk
is read again, and what is on screen follows, staying where it was on the axis. An
edit begun before such a change is applied to the new version rather than lost.

## Drawing what the date says

An event is drawn as precisely as its date is written. `2023` is a mark across the
whole of that year, not a point on 1 January; `2023-05` is a mark across the month;
a day is a dot until you zoom in far enough for it to have a width. `~` makes a date
soft: paler, with a broken edge. `!` makes a milestone larger with its name in bold,
and milestones choose their place first, so they are the last to lose their names
when it gets crowded. A name that doesn't fit is left off rather than drawn across
another; hovering over the mark still says it.

Years before the common era are on the same line as the rest (`-0044-03-15` is the
Ides of March, 44 BC), with no year nought between 1 BC and AD 1. With `born:` set,
a row under the axis gives the age. Zoomed in between two birthdays, it says at the
edge how old you were.

## The hands

| | |
|---|---|
| Drag, or scroll | Along the axis |
| Ctrl+scroll, or pinch | Zoom, at the pointer, smoothly |
| Ctrl++ / Ctrl+− / Ctrl+0 | Zoom in, out, everything |
| Ctrl+J | Jump to a date (`1241`, `2017-09`, `-0044-03-15`, `today`) |
| Double-click | Add an event there, or edit what is there |
| Ctrl+N / Ctrl+B | New event / new span, the `}` brace |
| Click, Shift+click | Select, add to the selection |
| Shift+drag | Select everything across a stretch |
| Ctrl+G | Group what is selected into a span |
| Delete | Take away what is selected, with Undo |
| Ctrl+E | This board: its name, `born:`, its categories |
| Ctrl+L | The axis or the list |
| Ctrl+Shift+N | New board |
| Ctrl+Shift+A | Ask Claude |
| F9 | The list of boards |
| Ctrl+? | These keys |

When there are more braces than fit below the axis, Shift+scroll or a drag moves up
and down as well.

A date field takes §11's syntax and forgives a year not padded to four digits, or
`44 BC`; the file always gets the strict form. **Group into a span** fills in the
range from the first thing's start to the last thing's end, at the precision those
dates were written, with the categories all of them share. It leaves the name to you.

The **Filter** button takes categories off the axis and out of the list. An item
goes only when all of its categories are off, and an item with none always stays.
The filter is for this sitting and is not written anywhere. In **This Board**
(Ctrl+E) a category has a name and a colour; renaming one follows it onto every item
that wears it. A category with no line in `colours:` gets a colour from the house
palette, stable by name, and Clio won't add a line for it unless you change that
colour, so a board doesn't grow a `colours:` just from being looked at.

## Asking Claude

**Ask Claude…** takes a request such as "add the Árpád kings". The answer comes back
in the board's own format and appears on the axis dashed, as ghosts, with nothing
written yet. Anything the board already has is left out. Select a ghost and press
Delete to leave it out, double-click to correct it, and **Keep Them** adds the rest
to the file (with Undo). **Discard** forgets them. The button isn't there on a machine
without `claude`.

## In Mind Palace

Clio is the palace's room for timelines (`clio/room.py`): `ClioView` is the same
widget its own window puts a header bar over, with its own stylesheet rules and its
keys as `room.*`, set only while the room is on show. A board's file opens there, and
a `[[link]]` to a note in an event's notes goes on to the palace's room for notes.
Ctrl+Shift+A, F9 and Ctrl+? are the palace's there; Ask Claude is on the board's own
bar and the list of boards has its own button.

## On a small machine

A board is drawn in one cairo pass, not built from widgets, and only what is on
screen is laid out, so a board of hundreds of events is cheap on the 3 GB laptop. The
content goes down to 360 pixels wide, so it fits a 455-pixel tiling column. Below
820 the list of boards folds away, and the bar gives back what is also in its menu.

## Tests

```bash
python3 tests/test_clio.py
```

The first half needs no display: the axis from centuries to days, where marks and
braces and names go, the date fields, what is read out of Claude's answer. The second
half drives the real view against a scratch home, in its own window and in a stand-in
for the palace. Nothing touches the real brain or reaches a model.
