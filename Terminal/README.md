# Prism

A terminal made of glass.

```bash
python3 prism.py                 # reopens the tabs you had last time
python3 prism.py ~/Projects      # starting there
python3 prism.py -n              # in a window of its own
python3 prism.py -e htop         # running something
```

VTE does the emulation; everything else here is about what it looks like and
how fast you can get at it. Ctrl+Shift+P lists every command there is, which
is the quickest way to find out what it does.

## It needs one package

Prism is the only app in this folder with a dependency the others do not
have:

```bash
sudo pacman -S vte4                   # Arch
sudo apt install libvte-2.91-gtk4-0   # Debian, Ubuntu
sudo dnf install vte291-gtk4          # Fedora
```

Without it the app prints that line and exits rather than failing somewhere
inside GTK. This is also why Prism is Linux-only while the rest of the folder
runs on Windows too: VTE is a Unix pseudo-terminal widget and there is no
build of it for Windows to port to.

## The glass is drawn, not borrowed

Most "transparent" terminals ask the compositor to blur what is behind the
window. niri has no blur to ask for, and there is no wallpaper daemon running
here to frost, so a translucent window would show whatever happened to be
underneath it — which is not a material, it is a hole.

So Prism paints its own light. Behind every pane is an `Ambient` layer: four
soft blobs of colour drifting on sixty- to hundred-second cycles, which is
slow enough that you never catch it moving and fast enough that the window is
never quite the same twice. Each pane then samples *that same light*, at its
own position in the window, and paints it again inside itself — larger and
much fainter, the way glass diffuses what passes through it. A glow half
under the edge of a pane appears on both sides of that edge, in the right
place, slightly blurred where it crosses. That continuity is the whole trick:
give each pane its own glow instead and it reads as a row of separate lamps.

On top of that each pane gets the things that make a rounded rectangle read
as a slab rather than a painted-on panel — a wide soft shadow, a body
gradient, a grain that doubles as dithering, a bright inner edge at the top
that is the *thickness* of the material, a one-pixel rim that is brightest
where the light is, and a specular highlight that follows the pointer.

It costs very little. The light animates only while you are using the window
and settles to a still gradient twelve seconds after the last keystroke; the
pointer highlight repaints at 30fps rather than at the display's rate; and
every effect can be turned off, after which the whole thing is a handful of
gradients drawn once per resize. On a 1366×768 laptop that matters.

### Where the blur is, and where it is not

The command palette and the find bar really are frosted: GTK 4.22 honours CSS
`backdrop-filter`, and what it blurs is what the app has already drawn
underneath — which under a floating panel is the terminal's text. Behind a
*pane*, though, is the ambient layer, and that is four soft gradients:
blurring a gradient gives you the same gradient and costs an offscreen pass
for nothing. So the panels frost and nothing else does, and a panel that is
frosted is drawn much thinner than one that is not, because the blur is what
stops the text underneath competing and the fill no longer has to.

Set **Panel blur** to 0 in the preferences and the panels go back to being
nearly solid, which is the same idea done the cheap way.

None of this reaches the desktop behind the window. That is the compositor's
to blur, and niri has none.

## Ten themes, and why the light ones look different

The themes are the house's, from [`house/theme.py`](../house/README.md), and
they are the same ten in every app here — so choosing Porcelain in the
terminal is choosing the same Porcelain as in the file manager.

Every theme is a palette *and* a glass: the sixteen ANSI colours, the tint of
the material, how bright its edges get, and the colours of the light behind
it. Both halves are designed together, because rose-tinted glass over a cold
blue palette looks like two themes fighting.

| | |
|---|---|
| **Moss** | Forest black under sage. The quiet one, and the dark default. |
| **Lacquer** | Black lacquer and brass. Porcelain with the light turned down. |
| **Petrol** | Deep blue-green glass lit by copper. A cold room and a warm lamp. |
| **Aurora** | Deep blue glass over slow northern light. |
| **Obsidian** | Neutral black glass. Nothing competes with the text. |
| **Ember** | Warm smoked glass, lit from below. |
| **Studio** | An 18% neutral grey surround, with the panels kept dark. |
| **Monochrome** | Grey glass. Colour only where output depends on it. |
| **Porcelain** | Warm paper under glass. The light default. |
| **Daylight** | Frosted white glass. For a bright room. |

The palettes are drawn rather than borrowed, and the test suite measures
them: the foreground has to reach 7:1 against its own background, and every
colour that carries meaning has to reach 4.5:1. The light themes are not the
dark ones with the background swapped — they need much darker colours to get
there, and their "white" is a mid grey, because a program that asks for
ANSI 7 on a white background is not asking to be invisible. One end of the
grey ramp *is* allowed to vanish into the background: ANSI 0 on a dark theme
and ANSI 15 on a light one both mean "the colour of the background".

With **Follow the desktop** on, Prism uses one theme in light mode and
another in dark, and switches when the desktop does.

## Tabs, splits and panes

Splits are a tree of two-way splits: `Ctrl+Shift+\` puts a terminal beside
the current one, `Ctrl+Shift+-` puts one below it, and both start in the same
directory as the one they came from. Alt+arrow moves between them
geometrically — the nearest pane in that direction on the screen, not the
next one in the tree, which are different answers as soon as the splits are
uneven.

The pane with the keyboard keeps its full brightness and the others are
dimmed and pushed back. That is said with light rather than with a coloured
border, which is what real glass does when one piece is nearer the lamp, and
it survives being seen out of the corner of an eye. Dimmed text still reads
at about 10:1.

The gaps between panes are not drawn. They are the light showing through.

## Knowing where you are

"New tab in the same directory" normally depends on OSC 7, an escape sequence
the shell emits on every prompt. On Arch that hook lives in
`/etc/profile.d/vte.sh`, which is sourced by *login* shells — and a terminal
emulator starts an ordinary interactive one, so nothing sources it and the
terminal is never told anything.

So Prism asks twice: OSC 7 when it is there (the only one of the two that can
be right about a shell that has sshed somewhere), and `/proc/<pid>/cwd`
otherwise, which needs nothing from the shell at all. New tabs, new splits
and the subtitle in the header all work out of the box, with any shell.

## Pasting

Anything with a newline in the middle of it is held up and shown to you
first. A newline in a terminal is not punctuation, it is the Enter key, and
text that arrives with one already in it runs before it has been read — which
is the oldest trick there is, a copy button that quietly puts a second line
on the clipboard. A single trailing newline goes straight through, because
that is how everybody copies a command.

Turn it off in Preferences → Behaviour if you would rather it did not ask.

## Keyboard

| | |
|---|---|
| `Ctrl+Shift+P` | Every command, by name |
| `Ctrl+Shift+T` / `W` | New tab / close tab |
| `Ctrl+Shift+N` | New window |
| `Ctrl+Page Up/Down` | Previous / next tab |
| `Alt+1`…`9`, `Alt+0` | That tab, and the last one |
| `Ctrl+Shift+Space` | Tab overview |
| `Ctrl+Shift+\` | Split to the right |
| `Ctrl+Shift+-` | Split downwards |
| `Ctrl+Shift+X` | Close this pane |
| `Alt+←↑↓→` | The pane that way |
| `Ctrl+Shift+O` | The next pane |
| `Ctrl+Shift+C` / `V` | Copy / paste |
| `Ctrl+Shift+A` | Select all |
| `Ctrl+Shift+F` | Find in the scrollback |
| `Ctrl+Shift+G` | Find again |
| `Ctrl +` / `-` / `0` | Zoom this pane |
| `F11` | Full screen |
| `Ctrl+,` | Preferences |
| `Ctrl+Shift+?` | This list, in the app |

`Ctrl+Click` opens a link — both real OSC 8 hyperlinks and bare URLs in the
output. `file.c:42` is a link too.

## Finding things

`Ctrl+Shift+F` searches the scrollback, with a case toggle and a regex
toggle. There is no "3 of 47" counter: VTE will find the next match but it
will not count them, so a counter would mean pulling ten thousand lines out
as a string and re-scanning them on every keystroke. The bar says whether the
last search found anything, which is the part anyone acts on.

## On a tiling desktop

The window goes down to 360px, which is narrower than niri's narrowest preset
column on this screen (about 455), and the suite asserts it on every run.
Below 620px the header gives up its two optional buttons and the subtitle;
both buttons are in the menu.

A maximise remembered from a previous session is taken back once, before the
first frame, if the compositor turns out to be a tiling one — niri honours
`set_maximized` by handing the window the whole output, which overrides the
column width you chose, and then reads back as maximised and saves it again
forever. Geometry is not saved at all while tiled, so the size you last chose
on a floating desktop survives.

## Updating itself

The app runs out of this checkout, so the newest version of it is whatever
`origin` has. The launcher fast-forwards to anything fetched last time before
a single module is imported — local git, a few milliseconds — and a few
seconds after the window is up a worker thread fetches; if the upstream has
moved, every window offers a *Restart*. Only a fast-forward is ever made, so a
checkout with work of its own in it is left exactly as it is, and
`APPS_NO_UPDATE=1` turns the whole thing off. Same `updater.py` as every other
app here; see the [top-level README](../README.md#updates).

## Settings

`~/.config/prism/config.json`, written whenever something changes. The
preferences dialog is also the preview: everything applies as you move it,
and there is nothing to confirm.

The session — which tabs were open and where they were — goes in
`~/.local/share/prism/session.json`. Splits come back as splits, in the right
directories, but not at the exact sizes: the nesting is not worth a file
format.

## Running it

It runs straight out of this folder; there is nothing to install.

```bash
python3 prism.py
```

For a launcher, a menu entry and an icon, the same as the other apps here:

```bash
ln -s "$PWD/prism.py" ~/.local/bin/prism
cp prism.desktop ~/.local/share/applications/
cp prism.svg ~/.local/share/icons/hicolor/scalable/apps/
update-desktop-database ~/.local/share/applications
```

### Being the terminal

Four separate things decide what "open a terminal" means on this machine,
and they do not talk to each other.

**The key binding.** In `~/.config/niri/config.kdl`. The absolute path is not
optional — niri's children do not get `~/.local/bin` on their PATH:

```kdl
Mod+T hotkey-overlay-title="Open a Terminal: Prism" { spawn "/home/robin/.local/bin/prism"; }
Mod+Shift+T hotkey-overlay-title="Open a Terminal: alacritty" { spawn "alacritty"; }
```

The second line is worth keeping. A terminal is how you fix things, and a
terminal that is also the thing that is broken is no use at the moment you
need it.

**`$TERMINAL`,** which scripts and anything i3-sensible-terminal-shaped read:

```kdl
environment {
    TERMINAL "/home/robin/.local/bin/prism"
}
```

**Desktop entries with `Terminal=true`.** GLib has a hard-coded list of
terminals it will use, and Prism is not on it — but the first name on that
list is `xdg-terminal-exec`, a program the spec says should choose for you.
Nothing on Arch provides it outside the AUR, so `~/.local/bin/xdg-terminal-exec`
is a short stand-in that reads `~/.config/xdg-terminals.list`:

```
prism.desktop
```

Put another terminal's desktop entry at the top of that file and everything
follows it; the script needs no editing.

**Thunar's "Open Terminal Here",** which uses Xfce's helper system rather
than any of the above: `~/.local/share/xfce4/helpers/custom-TerminalEmulator.desktop`
pointing at Prism, named by `TerminalEmulator=custom-TerminalEmulator` in
`~/.config/xfce4/helpers.rc`. Xfce passes the command as `-x`, which Prism
takes as another spelling of `-e`.

## Tests

```bash
python3 tests/test_prism.py
```

The first half needs nothing but Python: the palettes and their contrast, the
settings file, the command matcher, the shell command and the environment it
is given. The second half opens a real window and drives it — splitting
panes, switching every theme, opening the palette — and the last of those
starts a shell on a real pseudo-terminal and reads back what it printed,
because every other test here would pass on a terminal that could not run
anything.

Two of them are there for bugs that were real. GSK's GL renderer draws a
repeat node at full strength however much opacity it is wrapped in, which
turned the frosting into television static — the Cairo renderer composed it
correctly, so the fade is baked into the tile and the test checks the tile.
And the window-width test is the tiling floor above.

## How it is put together

| | |
|---|---|
| `glass.py` | The material: the ambient light, the slabs, the cards |
| `theme.py` | The six palettes, their glass, and the contrast maths |
| `terminal.py` | One VTE widget, its colours, its links, its shell |
| `shell.py` | What to run and in what environment — no VTE, so it is testable without one |
| `panes.py` | The split tree |
| `window.py` | The chrome, the tabs, and everything with a shortcut |
| `command.py` | The palette and its matcher |
| `find.py` | The search bar |
| `prefs.py` | The preferences |
| `widgets.py` | Shared pieces, and the stylesheet — generated, because the colours come from the theme |

## Licence

MIT, with the rest of this repository.
