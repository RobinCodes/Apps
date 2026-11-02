# The room contract

An app joins Mind Palace with one file: `<package>/room.py`. The standalone app
stays exactly as it is. Without a `room.py` it is still in the sidebar, as a
**door**: a page that says what the app is and opens it in its own window.

The palace gives a room everything a window would otherwise have to provide: the
header bar, the glass, the theme, the toasts, the window itself. So a room is what
is left of an app once those are taken away: its content, its actions and its keys.

## `room.py`

```python
TITLE = "Fathom"            # in the sidebar and the header bar, and how a room is addressed
ICON = "fathom"             # the app's icon, in the app's folder; ".svg" optional
ORDER = 70                  # place in the sidebar, smallest first

# Optional, both for routing (see "Where a file goes"):
OPENS = (".csv", "scan:")   # extensions, "scheme:" prefixes, "/" for folders, "http:" for links
KINDS = ("timeline",)       # the front-matter `kind:` of the notes this room keeps


def build(host):
    """Called once, the first time the room is wanted. Returns the room."""
    return Room(host)
```

**These names must be plain literals.** The palace reads them with `ast`, without
importing the file, so a sidebar of twelve apps does not mean a process holding
twelve apps. Nothing in `room.py` runs until `build` is wanted; it may import what
it likes at module level, but `build` is the first time that costs anything.

## The room

`build(host)` returns any object with these attributes. Only `widget` is required.

| | |
|---|---|
| `widget` | The whole of the room's interface, as one `Gtk.Widget`. **No header bar and no slab of its own.** |
| `actions` | A `Gio.ActionGroup`. The palace inserts it as `"room"`, on the widget for as long as the room exists, and on its own window while the room is showing. |
| `accels` | A list of `(action, [accelerators])`, usually `("room.x", ["<Control>x"])`. Set on the application while the room is showing, and cleared when it is not, so Ctrl+O can mean one thing in one room and another in the next. An accelerator GTK cannot parse is dropped, and the action's others kept. |
| `open(target) -> bool` | Open a file, a folder or a `scheme:` target, and say whether it did. `False` is a real answer: the palace offers it to the next. |
| `save_state()` | Write down whatever should survive a restart. Called when the palace closes. |
| `can_close() -> bool` | `False` while closing would lose work. The palace stays open and brings this room forward. |

These are optional too, and came out of the rooms built so far:

| | |
|---|---|
| `shutdown()` | Put away what has to be put away (a player, say). Called after `save_state` as the palace closes. |
| `shown()`, `hidden()` | The room has come into view, or left it. A good moment to read things again. |
| `context() -> [path]` | The files the room is showing. Ask Claude is about these unless told otherwise. |

## The host

What `build` is given, and what a room can ask of the palace. Keep it: it is good for
the life of the room.

| | |
|---|---|
| `host.app` | The running `Gtk.Application`. |
| `host.parent` | A widget to present dialogs from: the palace's window. |
| `host.toast(text, undo=None)` | A toast. `undo` is a callable, and adds an Undo button. |
| `host.open(target) -> bool` | Open a file, a folder, a link or a `scheme:` target wherever it belongs (below). The room asking is not offered it back, so a room can pass on what it declined. |
| `host.show(title) -> bool` | Go to the room called `title`, ignoring case. `False` if there is none. |
| `host.ask_claude(prompt="", files=())` | Open the palace's Claude panel. With a prompt it asks at once, and the answer streams into the panel. Without `files` it uses the room's `context()`. |
| `host.set_subtitle(text="")` | What the room is showing, under its title in the header bar. An addition to the first design: a document's name has to go somewhere. |

## Where a file goes

`host.open`, Ctrl+K and `mind-palace FILE` all go the same way. Everyone who might
take the target is offered it in this order, until one says yes:

1. **`scheme:` targets** go to whoever declared the scheme. A `scheme:` is a request
   between rooms that is not a file: `study:<note path>` is the palace's form of
   `mnemosyne --study <note>`. Mnemosyne's room takes it and declines a plain `.md`,
   so a note opens in the room that writes notes and is *studied* in the room that
   schedules cards. Declare a new scheme in `OPENS`.
2. **Links** (`http://`, `https://`) go to whoever has `"http:"` in `OPENS`.
3. **Folders** go to whoever has `"/"` in `OPENS`.
4. **A Markdown note** whose front matter says `kind: <k>` goes first to a room with
   `<k>` in `KINDS`: a timeline to Clio, a question to Ariadne. Every kind is in
   SYSTEM.md.
5. **Anything else** goes by extension to whoever has it in `OPENS`, in sidebar order.
6. **The desktop**, if nobody here took it.

A door takes part too: it is handed the target on its app's command line, in a window
of its own. `KNOWN` in `mindpalace/rooms.py` holds what the palace already knows about
this repository's apps (Notedown opens Markdown, The Eye opens pictures), so a room
need only declare what is not there. What a room declares comes first.

## Building a room out of a window

Every room so far is the app's ordinary `MainWindow`, built and never presented. The
recipe, as Mnemosyne, The Eye and Fathom all do it:

1. **Never give that window the application.** An application keeps running while it
   has a window, hidden or not, and an unpresented window in the list is one somebody
   might present.
2. **Take its content, not its slab.** No `housestyle.Glazing` for a hosted window: the
   palace has the glass and the house sheet, and `Glazing` would replace them.
3. **The app's own CSS goes on a provider of its own**, or is scoped to the room's
   content. Never install anything that restyles the whole display, and never force
   light or dark: the palace's theme is in force, for every room.
4. **Swap the `Adw.HeaderBar` for a plain bar of the room's own buttons.**
5. **Put the breakpoints on an `Adw.BreakpointBin`** as the room's `widget`, with a size
   request. A window that is never on screen never reaches its breakpoints.
6. **Insert the window as the content's `"win"` group**, so that every `win.*` the code
   already says still resolves. Give the same window to the palace as `actions`, and
   offer its keys as `room.*`.
7. **Present dialogs on a widget inside the content**, and parent file choosers on that
   widget's root. A dialog presented on a hidden window is a dialog nobody sees.
   Toasts must start from inside the content for the same reason.
8. **Do not save the window's size.** It was never on screen, and the standalone app's
   remembered size is not this one's to overwrite.

The window's actions stay `win.*` in menus, and `room.*` in `accels`: GTK looks up an
application's shortcuts from the window, where only `room` is. Name the palace's own
keys nowhere: **Ctrl+K, Ctrl+Shift+A, Ctrl+1…9, Ctrl+`,`, Ctrl+? and F9** are caught
before a room can see them, whatever has the focus.

Fathom's `room.py` is the reference: it is short, and `tests/test_fathom.py` shows the
fake host a room's own suite can test against, without opening the palace.

## What the palace promises

* A room is built at most once, and kept. Going back to it is instant, and it is as
  you left it.
* A room whose `build` raises costs that room. Its page says what went wrong and offers
  the app in its own window; the palace and the other rooms carry on.
* One room's trouble in `save_state`, `can_close` or `shutdown` is printed and stays
  that room's.
* Rooms share a process, and one Python: a package imports only from itself, which
  every app here does already, since each is a folder you can copy elsewhere and run.
