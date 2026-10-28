# Mnemosyne

The cards written in your notes, scheduled by FSRS, and reviewed on paper or
on screen — with memory palaces and the Mentalist's trainers beside them.

Nothing is made in here. A card is a line in a note (`Front :: Back`, the five
forms of SYSTEM.md §7, in the brain), so writing the note is
writing the cards, and this app is what turns them into a day's work: which
cards, in what order, on which sheet of paper — and, afterwards, what the
answers say about how the learning is going.

```bash
python3 mnemosyne.py                 # today's reviews
python3 mnemosyne.py --study note.md # one note's cards
python3 mnemosyne.py --print         # today's sheet, as a PDF, in the viewer
python3 mnemosyne.py --due           # "12 20": due cards, and today's new ones
```

## Paper first

The method says learning happens on paper (HANDBOOK.md §1.5), so the printed
sheet is not an export: it is the main way a day's reviews get done, and the
software's whole job there is to make the round trip cost nothing.

**The sheet** is A4, folded down the middle. Questions on the left half, each
with its number, where it comes from, and four tick boxes against the fold;
answers on the right half under the same numbers. Folded, only the questions
show. Write the answer, unfold, check, tick: 1 Again, 2 Hard, 3 Good, 4 Easy.
The boxes carry their digits faintly inside, so typing back is reading along a
column. Maths is typeset the way it is on screen, and a picture in a card is
printed with it, small.

**Typing it back** is one field: a digit a row, the list underneath following
along, 0 or a dash for a row left blank, Backspace to go back. Forty cards is
forty keystrokes and an Enter (Ctrl+E from anywhere). The answers go into the
same log as a screen review, marked `via: paper`, and dated the evening the
sheet was answered if it is typed back on a later day — a Monday sheet entered
on Wednesday was still a Monday review.

**What was on each sheet** is written into the brain the moment it is printed
(`learning/sheets/<id>.json`), so a sheet printed at home can be typed back on
the school laptop. Its id — the date and a letter of the machine, `261027-a1`
— is in its corner.

While a sheet printed today is out, **its cards are left off the screen**: they
are being answered in pencil, and the screen asking them again would be the
paper answering a card that had moved on.

**Flashcards**: any deck, ten to a page, fronts then backs with the columns
swapped, so a printer flipping on the long edge puts each back behind its
front. From the Paper page or a deck's menu.

## On screen

One card at a time: the deck and the headings above it in the note, the
question, and after Space the answer, with the four ratings underneath, each
saying when the card would come back — "Good · 9d" is the honest version of
the button.

| | |
|---|---|
| Space | show the answer, then Good |
| 1 2 3 4 | Again, Hard, Good, Easy |
| S | skip, once — it comes back at the end |
| E | open the note at this card (in Notedown when it is beside this app) |
| X | ask Claude to explain the card |
| Esc | stop here |

A card answered **Again comes back** a few cards later, before the session
ends. The end is a calm summary — how many, how long, how many held the first
time, what is due tomorrow — rather than a score.

## What makes a day

`cards.Deck`, shared with Notedown, Hermione and The Eye, says what is due.
`library.py` decides what a day actually is, by the rules HANDBOOK.md §5.2 asks
for:

* **Due cards first**, most at risk first — measured as lost retrievability,
  not days late — then **a ration of new ones**, `new_per_day` of them.
* **Interleaved.** Due and new are each dealt across their decks in turn, on
  screen and on the sheet, so a day is not all of Biology and then all of
  Italian. Mixing is what makes "which of these is it?" part of the recall.
* **No siblings.** The two directions of a `:::` card and the clozes of one
  sentence give each other away, so only one of a family is in a day — and
  none once one has been answered today. The rest come tomorrow.
* **Test dates.** A deck can be given one (its menu on the Decks page). Until
  the day, its new cards come before any others, and the ration rises as far
  as it must to meet them all three days before the test, so the last days are
  reviews and not first meetings. Week counts down to it.
* **Decks switched off** stay out of everything: Preferences → Learning →
  Decks. A `.tex` file has no front matter for `cards: false`, so this is how
  a Digitalization subject you do not take is kept out.
* **A card with an empty side is never shown** — an environment written but
  not filled in yet is a promise, not a question.

## Palaces

A palace is a note in `learning/palaces/` with `kind: palace` and its loci as a
numbered list of `locus :: item` cards (SYSTEM.md §8). Each locus is an
ordinary card and is reviewed with everything else. The Palaces page adds the
**walk**: the loci in order, forwards and then backwards, because the
backwards walk is the one that shows which links are really there. A picture
indented under a locus (`![](Lakás/01-ajto.jpg)`) is that locus's photograph,
shown on the walk and with the card in a review. A walk is a drill, not a
review: it goes into the drill log and does not move the schedule.

## Trainers

The Mentalist curriculum lists its drills in
`learning/curricula/The Mentalist/drills.yaml`; Mnemosyne reads every
curriculum's `drills.yaml` and falls back to a built-in set when there is none.

| trainer | asks | answered by |
|---|---|---|
| observation | a photograph for a few seconds, then the same one with one region mirrored, recoloured or covered by a patch cloned from elsewhere | clicking where |
| deck | a position in the memorised deck, or a card (`QH`, `q♥`, `10d`, `pikk ász`) | the card, or the position |
| doomsday | a date | 0–6, Sunday to Saturday — and on a miss, the method worked through for that date |
| major | a number, or a word from the switched-on Major deck | the word, or the number |
| calc | a product, a square, a cube root, ×11, a square ending in 5 | the number |
| cards | a deck's due cards | the ordinary review |

`params` are as the comment at the top of `drills.yaml` says. Every run ends in
a line of `learning/drills/<machine>.jsonl` — `{t, drill, score, ms, n}`, the
score a fraction right and ms the median answer time — next to the review log
and merged the same way. Today lists the day's runs; Week, the week's.

The observation trainer looks in `~/Pictures` unless the drill or the
preferences say otherwise, decodes each photograph at the size it is shown,
and changes only the region, feathered at its edge, so what is asked about is
the content and not a seam.

## Week

The Sunday review's page (SYSTEM.md §4.3, HANDBOOK.md §1.6): reviews a day,
retention this week and over thirty days against `desired_retention`, new
cards met and waiting, the streak, the tests coming up, and three lists — the
**leeches** (forgotten six times or more after being learned: open one to
rewrite it), what slipped this week, and each deck. The printer button puts it
all on one A4 page, the leeches with their file and line, to rewrite with the
sheet beside you. Leeches are also marked on the daily sheet and on screen.

**The schedule fits itself.** Once the log holds 400 spaced reviews, FSRS's
nineteen weights are fitted to it (`fit.py`): a bounded coordinate search that
lowers the log loss of FSRS's own predictions — every review a day or more
after the previous one is a prediction the log can check — with each weight
kept within a factor of four of its default and a light pull towards the
defaults where the log has little to say. The result goes into
`learning.yaml`, so every machine schedules with the same numbers, and it is
refitted whenever the log has grown by a quarter. Week says which weights are
in use and has a button to fit now.

## Claude

When Claude Code is installed (`house/claude.py`), three things use it, and the
answer streams into a panel at the side:

* **Explain** (X while reviewing): why the answer is what it is, and one hook
  to hang it on. Claude is given the card and its note.
* **Make Cards from a Note…** (menu): Claude proposes cards in the §7 format;
  each is a tick box, and only the ticked ones are added to the end of the
  note, under "Cards". Claude itself runs read-only; the note is written by this
  app, after the approval.
* **Ask Claude…** (Ctrl+K): anything, with the card being reviewed attached.
  Follow-ups continue the conversation; *Continue in Claude Desk* opens it there.

## For other apps

| | |
|---|---|
| `mnemosyne --due` | prints `due new` — today's due cards and today's ration of new ones — and exits. No GTK is loaded, so it is cheap enough for a badge. `0 0` where there is no brain. |
| `mnemosyne --study FILE` | opens the window (or hands to the one open) and starts on that note's cards: the due ones, then every new one, whatever the day's ration. A note outside the learning roots works too. This is Notedown's "study this note". |
| `mnemosyne --print [--no-open]` | prints today's sheet and the PDF's path; exit 1 if nothing is due. |

On Windows the same through `mnemosyne.bat`, which runs these three through
`python.exe` so they print to the console that asked.

## Settings

Two files, because they follow you to different extents.

**`SecondBrain/config/learning.yaml`** holds what the schedule depends on, so
every machine schedules the same log the same way. Preferences → Learning
writes it; so can you. Keys this app does not know are kept.

| | |
|---|---|
| `roots`, `extensions` | where cards are looked for (SYSTEM.md §7) |
| `new_per_day` | the daily ration of new cards; 20 is about 120–140 reviews a day once it settles |
| `desired_retention` | the chance of recall FSRS aims for when a card comes back; 0.9 |
| `"off"` | decks switched off, by name or folder: `["Digitalization/DataHU/Humán tárgyak"]`. Quoted, because YAML 1.1 reads a bare `off:` as *false* — a bare one is understood too |
| `tests` | `deck: YYYY-MM-DD` |
| `weights`, `weights_reviews` | the fitted FSRS weights, and the reviews they came from |

**`~/.config/mnemosyne/config.json`** (`%APPDATA%\mnemosyne` on Windows) is
this machine's: the theme, the card text size, the sheet's text size, whether
new cards go on the sheet, where the observation photographs are, the window.

## On a tiling desktop

Written on niri, on a 1366-wide screen. Below 720px the page switcher moves
from the header to a bar along the bottom and the Claude panel folds over the
content instead of beside it, so the whole window works in a 455px column; the
test suite checks it can be made 360px wide. The size is remembered and the
maximise is not, under a tiling compositor — see `housestyle.Geometry`.

The window's content stands on its own: every shortcut is on a controller
inside it as well as on the application, and every dialog is presented on a
widget inside it, so Mind Palace can host it in a window of its own.

## The house style

The look is the house's, from [`house/`](../house/README.md): the same themes,
the same Look page in Preferences, the same glass. The brain's modules —
`brain.py`, `cards.py`, `fsrs.py`, `claude.py` — are vendored from there too,
byte for byte, by `house/sync.py`.

## Running it

Needs GTK4, libadwaita, PyGObject and pycairo. On Arch:

```bash
sudo pacman -S gtk4 libadwaita python-gobject python-cairo
```

PyYAML is used for the YAML files when it is there (`python-yaml`), and a small
reader of the same subset when it is not.

To put it on the menu and on `$PATH`:

```bash
ln -sf "$PWD/mnemosyne.py" ~/.local/bin/mnemosyne
cp mnemosyne.desktop ~/.local/share/applications/
cp mnemosyne.svg ~/.local/share/icons/hicolor/scalable/apps/
update-desktop-database ~/.local/share/applications
```

On Windows, see [the shared README](../README.md#on-windows); `windows/install.ps1`
makes the Start menu shortcut, and `mnemosyne.bat` starts it on MSYS2's
Python (UCRT64, or MINGW64 on an older install).

## Tests

```bash
python3 tests/test_mnemosyne.py
```

The first half needs no display: the queue rules, paper sheets and their PDFs,
the trainers' arithmetic, the week, the fitter, the YAML reader, the command
line — all against a small home built in a scratch folder and pointed at with
`ORDER_HOME`, so nothing real is read or written. The second half opens the
real window on the same kind of home and drives it: a review by its keys, a
sheet printed and typed back, a palace walked, two drills run, and the content
hosted in another window the way Mind Palace hosts it. Nothing is launched —
the viewer a printed sheet would open is replaced by one that only records.

## How it is put together

| | |
|---|---|
| `library.py` | the day: the queue's rules, paper, siblings, tests, decks off, leeches |
| `session.py` | one sitting at the screen: the order, Again coming back, the summary |
| `paper.py` | sheets: what was on them, the ticks, and the PDFs (sheet, flashcards, week) |
| `trainers.py` | the drills' logic: drills.yaml, the drill log, Doomsday, the stack, the Major system, sums, where to change a photograph |
| `observe.py` | the observation trainer's photographs: found, shrunk, changed |
| `stats.py` | the week, from the log |
| `fit.py` | FSRS fitted to the log |
| `cardtext.py` | a card's Markdown and maths as Pango markup; its pictures |
| `latex.py` | maths drawn with Unicode and Pango, the copy Notedown and Claude Desk carry |
| `yamlite.py` | the YAML people write by hand, without PyYAML |
| `cli.py` | `--due`, `--print`: answered before GTK is imported |
| `window.py` | the pages, the activities, the menus, the keys |
| `today.py` `decks.py` `paperpage.py` `palaces.py` `drills.py` `week.py` | the six pages |
| `study.py` | the review itself |
| `assistant.py` | Claude: explain, make cards, ask |
| `prefs.py` | the Look page, then Learning and Cards and paper |
| `config.py` | the two kinds of settings |
| `winenv.py` | the Windows differences, so nothing else has to know |

## Licence

MIT. See [LICENSE](../LICENSE).
