# Fathom

A disk scanner. It walks a filesystem once and shows what is on it two ways at
the same time — a tree you can read, and a map you can see. Clicking either
half selects in the other.

```bash
fathom                    # open on the list of filesystems
fathom ~/Downloads        # scan a folder straight away
```

It opens on the disk list rather than reopening the last scan, which is the
one place it deliberately breaks the habit of the other apps here: reopening a
document is instant, and rescanning a filesystem is ten seconds of disk before
you have asked for anything. The last thing you scanned is the first entry
under **Recent**.

It is built to be an honest answer to WizTree rather than a flattering one, so
the numbers are checked against `du`: a scan of this machine's home directory
agrees with `du -sx --block-size=1` **byte for byte**, hardlinks and directory
blocks included.

## What it does

| | |
|---|---|
| **Tree** | Every folder, sorted biggest first, with its size, its share of its parent as a bar, how many items are in it and when it changed. |
| **Biggest files** | The largest files anywhere in the scan, flat, with what kind of thing each one is and which folder it came from. |
| **File types** | Every extension added up. Answers "what *sort* of thing is eating the disk", which is often the useful question. |
| **The map** | A squarified treemap, coloured by file type. Double click a folder to zoom in, Backspace to come back out. |
| **Acting on it** | Right click anything, in either half: open it, show it in the file manager, copy its path, move it to Trash, or delete it. The totals update without a rescan. |
| **Search** | `Ctrl+F`, then a word or a glob like `*.iso`. It searches every file in the scan, not just the ones already listed. |
| **Export** | `Ctrl+E` writes the current view to CSV. |

## How fast, and why

A home directory of 351,000 files in 61 GB takes **2.3 seconds** and about
300 MB of memory on an N100 with an NVMe SSD. `du` does the same walk in 0.7s,
so Fathom is within about 3× of C while drawing a picture at the end of it.

Three decisions got it there, and all three were measured rather than guessed:

**The walk is single threaded.** The obvious speed-up is to walk several
directories at once, and here it is *slower* — `scandir` and `stat` are
microseconds on a warm SSD, so threads queue for the GIL instead of waiting on
the disk. One thread 1.5s, four 3.2s, eight 4.2s. The walk still runs off the
main loop, so the window stays alive; it just doesn't fan out. A filesystem
where a stat really does have to wait — a spinning disk, NFS, a fuse mount —
would change that answer, and is the one case worth revisiting.

**Files are not objects.** Directories are, but files live in flat
`array.array`s on their parent — names in a list, sizes and times in typed
arrays. That is about 70 bytes a file instead of about 200, which on a 3.4 GB
machine is the difference between scanning a million files and swapping. A
`FileRef` is made only for the rows actually on screen.

**The map is laid out and painted on a thread, once.** Twenty thousand
rectangles is a fifth of a second of layout and about as much again of Cairo —
fine once, hopeless on every mouse move. So it goes onto an image surface that
is rebuilt only when the window resizes or the root changes; hovering blits
that and draws one outline on top. While a resize catches up, the previous
picture is stretched rather than blanked.

## Getting the numbers right

Most of the work in a disk tool is not the walking, it is refusing to lie.

**Two sizes, both kept.** `st_size` is how long a file is; `st_blocks * 512` is
what it costs. They differ for sparse files, compressed filesystems, and
anything smaller than a block — a thousand 10-byte files are 10 KB long and
4 MB on disk. Fathom shows the size on disk by default, because that is what
you get back by deleting it, and `du` and `df` agree with it. Apparent size is
a column you can turn on.

**A directory costs its own blocks.** An empty folder is not free, and `du`
counts it. On tmpfs it genuinely is free, and Fathom reports that too.

**One inode is one file.** Counting a hardlinked file once per name inflated
this machine's home directory by 9 GB — 72,000 links, mostly package caches.
Second and later sightings become a per-directory deduction, so a file's own
row still tells the truth about its size while the folder totals agree with
`du`. Turn it off in Preferences to see the raw sum.

**Symlinks are never followed.** Following them is how a disk tool reports a
home directory as infinite, or counts `/usr` twice.

**Unreadable folders are counted, not hidden.** Scanning `/` as yourself walks
into hundreds of them; the status bar says how many and lists the first few in
its tooltip, so a total that is short says so rather than quietly being wrong.

**A number on the end is a version, not a type.** Unix is full of
`libcef.so.0` and `libz.so.1.2.11`, which are shared objects, and files called
`2.1.234`, which are not `.234` files.

## Where it's installed

This folder's name has no space in it, but the entry points still go through a
symlink the way the other apps here do, so moving the folder means re-pointing
one file:

```bash
ln -sfn "$PWD/fathom.py" ~/.local/bin/fathom
```

| Location | What it is |
|---|---|
| `~/.local/bin/fathom` | The symlink. Everything else depends on it. |
| `~/.local/share/applications/fathom.desktop` | Applications menu, under **System**. |
| `~/.local/share/icons/hicolor/scalable/apps/fathom.svg` | The icon. |

The launcher resolves its own symlink with `realpath` before putting its
directory on `sys.path`, so it finds the `fathom/` package whether it is run
through `~/.local/bin` or directly. There is no install step.

Requires `python-gobject`, `gtk4` and `libadwaita`, all already present for the
other apps here. Nothing else — no external tools are shelled out to at all,
which is why this app has the shortest `winenv.py` of the four.

## Layout

| File | What's in it |
|---|---|
| `fathom/scan.py` | The walk, the `Dir` tree, the rollup, hardlink accounting, search, and the per-extension and biggest-file passes. |
| `fathom/treemap.py` | Squarified layout, the Cairo painting, and the widget that does the hit testing. |
| `fathom/listview.py` | The three `ColumnView`s, the lazy tree model, and the cells that redraw when a total changes under them. |
| `fathom/window.py` | The window, the selection sync between the two halves, deleting, preferences, export. |
| `fathom/filetypes.py` | Extension → category → colour, with the tables checked for collisions at import. |
| `fathom/volumes.py` | What there is to scan: `/proc/mounts` on Linux, drive letters on Windows. |
| `fathom/units.py` | Sizes, percentages and dates as a person writes them. |
| `fathom/config.py` | Settings, in `~/.config/fathom/config.json`. |
| `fathom/winenv.py` | Where files go on each platform, and nothing else. |

## Keyboard

| | |
|---|---|
| `Ctrl+O` | Scan a folder |
| `Ctrl+R`, `F5` | Scan again |
| `Ctrl+F` | Search |
| `Backspace`, `Alt+↑` | Zoom the map out one level |
| Double click | Zoom into a folder, or open a file |
| `Ctrl+Return` | Open the selection |
| `Ctrl+C` | Copy its path |
| `Delete` | Move it to Trash |
| `Shift+Delete` | Delete it permanently |
| `Ctrl+E` | Export the current view as CSV |
| `Ctrl+N` | New window |

## More than one window

`Ctrl+N` opens another window, and so does **New Window** on the launcher
icon's right-click menu. Two windows share nothing but the settings file: a
scan, its tree and the map drawn from it all belong to the window that started
them, which is the point — one disk in each, side by side.

From outside, one instance serves the lot. A second `fathom` does not start a
second copy; it hands what it was given to the window already open:

```bash
fathom              # show it, or start it
fathom FOLDER       # scan that, in the window already open
fathom -n FOLDER    # scan that, in a window of its own
```

## Under a tiling compositor

The window tiles down to **360px** wide: below 800 an `Adw.Breakpoint` turns
the side-by-side pane vertical, narrows the view switcher and drops every
column but the name and the size — without that the name column collapsed to a
single ellipsis. It also declines to restore a saved maximised state on a
compositor that lays windows out itself, for the same reason the other apps
here do: `set_maximized` under niri takes the whole output away from the
arrangement you made, and `is_maximized()` then saves `true` forever.

## Tests

```bash
python3 tests/test_fathom.py
```

Twenty-six checks. The interesting ones build a small filesystem under a temp
directory — with a hardlink, a symlink loop, a folder with its permissions
removed and an empty folder in it — and check the arithmetic that comes back,
including against `du` where `du` exists. The treemap is checked as geometry:
that a row of rectangles fills its box exactly, that a rectangle's area is
proportional to what it stands for, and that none of them overlap. The last
three build a real window, delete real files inside the scratch directory, and
check that every total above them moved.
