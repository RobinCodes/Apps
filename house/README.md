# The house style

The look every app in this repository is drawn in, in one place.

| | |
|---|---|
| `theme.py` | The ten palettes, and the glass each one is made of. No GTK import, so a settings file can read it before a toolkit exists. |
| `housestyle.py` | libadwaita's named palette restated in a theme's colours, the shared idiom on top of that, the widgets CSS cannot draw, the preference rows, `aurora_texture` (the light, baked once) and `Geometry` (remembering a window's size). |
| `sync.py` | Copies both into every app, and `--check` says whether a copy has drifted. |
| `test_house.py` | Measures the palettes, parses the sheet, and runs `sync.py --check`. |

## Why it is copied rather than imported

Each app folder here is a thing you can copy to another machine and run on its
own. An app that imported its colours from a sibling directory would not be,
so these are vendored into each app's package the way `updater.py` has been
since the beginning. The cost is that nine copies can drift, and `sync.py` is
what stops them: it copies byte for byte, and the test suite fails on a drift.

After editing anything here:

```bash
python3 house/sync.py           # copy into every app
python3 house/sync.py --check   # or just say which are stale
python3 house/test_house.py     # and check the palettes still measure up
```

Lyndon Browser is C and has no Python package to vendor into, so `sync.py`
generates `Lyndon Browser/src/house-themes.h` from the same `theme.py`
instead — the same numbers, as a C table its `apply_style` emits
`@define-color` from.

## The two halves of a theme

A theme here carries more than the colour of text. It carries the material
the chrome is made of: the tint of the glass, how much light comes through
it, how bright its edges catch that light, and the four hues of the slow
light behind it. Both halves have to be designed together — a rose-tinted
glass over a cold blue palette looks like two themes fighting, and the amount
of light a dark glass can take before text stops being readable is not the
same as for a light one.

Four fields exist for one app each and are filled in for every theme anyway,
because a theme that only half exists in one of the apps is not a house
style: `ansi` (Prism), `matte` (The Eye), `cursor`, and `dim`.

## Adding a theme

One `_add(Theme(...))` in `theme.py`, then `sync.py`, then `test_house.py`.
The suite will tell you if the palette is not readable; it measures every
colour that carries meaning against that theme's own background, and both the
light themes and the dark ones have to pass the same bar. The light themes
need much darker colours than the dark ones, which is why they are not the
same numbers with the background swapped.

## How an app joins

```python
from . import housestyle

# In the window, once the content is built:
self.glazing = housestyle.Glazing(self, self.config, extra=widgets.CSS,
                                  app_class="fathom")
self.glazing.wrap(self.toasts)

# And in the preferences:
dialog.add(housestyle.look_page(self.config, self.glazing.restyle))
```

`Glazing` works out which theme is in force, installs the sheet, follows the
desktop between light and dark while it is being followed, and gives the
window back as one rounded translucent slab with a highlight that follows the
pointer.

Its sibling is `Geometry`, which is the other thing every window wants and had
nine copies of:

```python
self.geometry = housestyle.Geometry(self, config)   # in the constructor
self.geometry.restore()
...
self.geometry.save()                                 # in close-request
```

The settings `Glazing` reads are `theming.SETTINGS`, which a config spreads
into its own defaults:

```python
DEFAULTS = {
    **theming.SETTINGS,
    ...
}
```

Any of them an app has not adopted falls back to the house default rather
than raising, which is what lets an app take them on one at a time.
`Geometry` reads `window_width`, `window_height` and `window_maximized`, by
subscript or by attribute, so a config that keeps its settings as fields works
without changing shape.

## Two rules worth not relearning

**The focus ring has to name its widgets.** GTK sets `:focus-visible` on the
focused widget *and on every one of its ancestors* — measured, not assumed. A
bare `:focus-visible { outline }` therefore draws a nested stack of rings out
to the window edge rather than one ring around the control. `:focus` is no
help either, because a composite widget keeps the real focus on an inner node.
So the sheet lists the controls that get a ring, and containers are not on the
list.

**The aurora is quieter than it looks like it should be.** It is the
background of every empty area in a window, and a warm hue reads far stronger
than a cold one at the same alpha: 0.30 turned Hearth's warm ground into milky
coffee. 0.22 on a dark theme, 0.14 on a light one.

**Decoration goes in a texture, not in `background-image`.** GSK re-blends
every layer of a CSS `background-image` over the whole box on every repaint
and caches nothing, and the slab is the biggest surface in the window.
Measured at 1100×700: a flat slab 6.3 ms a frame, the two sheen gradients
took it to 23.2, and each aurora blob added about 13 more — 62 ms for the
layer. `aurora_texture()` draws the lot once with Cairo at 256×160 and
`Slab.do_snapshot` blits it scaled: 11.2 ms, and nothing in it is sharp
enough to notice the upscale.

**A soft shadow costs by its blur radius, not by the size of the box.**
Twelve popover-sized boxes measured 16.8 ms a frame at `0 10px 34px` and
6.2 ms at `0 4px 12px`. Every popover, toast, tooltip and dialog in nine apps
had the big one, and that was what "the popups are laggy" meant. A shadow
says "this floats"; it does not have to be the deepest one that can be drawn.
