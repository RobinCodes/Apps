# Lyndon

A small native browser for Linux. GTK4, libadwaita and WebKitGTK, written in C.
No Electron, no bundled Chromium, no extension runtime — about 200 KB of
browser on top of the WebKit your system already ships.

Four things it optimises for, in this order: **aesthetics, privacy, security,
speed.**

```
$ make && ./lyndon
```

---

## What is actually in here

**Blocking is native.** Filter rules are compiled once into a
`WebKitUserContentFilter` — WebKit's own content-blocker format, the same
machinery Safari uses. WebKit turns the rules into a bytecode DFA and evaluates
it *inside the network process*. A blocked request never reaches the web
process, never allocates, never crosses an IPC boundary, and never runs a line
of JavaScript. This is why it costs measurably less than an extension-based
blocker: there is no extension.

447 rules ship built in across seven categories. Any Adblock Plus or
hosts-format list can be subscribed to on top.

**Dark mode has three settings, and the middle one is the interesting one.**
`Off` leaves pages alone. `Always` inverts everything. `Smart` runs a probe
after each page paints, measures the luminance the site actually rendered, and
only inverts pages that came out light — so a site with a real dark theme is
left exactly as its designers intended, and a site without one stops burning
your retinas. Images, video, canvas and iframes are inverted a second time,
which returns them to their true colours.

**Translucency is a budget, not a switch.** Three effect levels: `Full`
(translucent chrome, shadows, transitions), `Reduced` (opaque, still animated),
`Off` (flat, no transitions anywhere). Translucency is real window alpha, so if
your compositor blurs behind windows you get blur for free; if it does not, it
degrades to a clean tint rather than to something broken. Web content is never
translucent.

**Memory was measured, not reasoned about.** WebKit gives every web view a web
process of its own, and a web process is a complete WebCore and
JavaScriptCore. Read with `ps`, that looks like a per-tab tax of about 175 MB
before a page draws anything — and it is the wrong reading. `RSS` counts every
shared page in full against every process holding it, and five web processes
forked from one binary share nearly all of theirs. `PSS`, which divides shared
pages among their users and is the number that actually adds up to what the
machine has lost, tells a different story.

Five tabs on google.com, same profile, same wait, measured both ways:

| | web processes | total PSS |
|---|---|---|
| one process per tab | 5 | 776 MB |
| one process for all five | 1 | 812 MB |

Consolidating them saves nothing. The per-tab engine was already shared. So
*Share one process between tabs on the same site* exists in Preferences → Web
→ Performance and is **off by default**: it collapses the process count, which
costs a little less in descriptors and scheduling, and it gives no memory
back. It also cannot be combined with unloading — ending a web process ends it
for every view using it, so unloading one idle tab out of a shared process
would blank the tab you are reading — so switching it on switches unloading
off.

What does return memory is giving a page up entirely:

*Idle background tabs are unloaded.* After the configured number of minutes a
background tab drops its page and its whole web process, keeping its place on
the strip, its title and its address, and loads again when you come back to
it. Tabs that are loading, playing audio, holding an unanswered prompt, or
showing an internal page are left alone. Thirty minutes by default; 0 turns it
off.

*The resource cache is sized for a machine with other things to do.* WebKit's
`WEB_BROWSER` cache model budgets as though the browser owns the computer, and
it budgets that inside *every* web process. Lyndon asks for `DOCUMENT_BROWSER`
instead: still a back/forward cache, still resource reuse, a fraction of the
resident size.

*The network process is given a memory ceiling*, an eighth of the machine held
between 128 MB and 512 MB, with no kill threshold — one network process serves
every tab, so killing it would take them all. WebKit leaves this check off
until it is asked for. It is also the only ceiling WebKitGTK 6.0 still offers:
the call that covered web processes went in the API break.

**The start page can be turned down to nothing.** Three settings: `Full` is the
wordmark, the tagline and a summary of what your settings are actually doing;
`Minimal` is the word Lyndon and nothing else; `Blank` is nothing at all. Under
any of them sits an optional artwork — *Aurora*, *Meridian*, *Dune*, *Lattice*,
*Ember* or *Monolith* — and the two settings are independent, so `Blank` with
an artwork chosen is a wallpaper and `Blank` without one is an empty page in
the theme colour. Every artwork is CSS: gradients, and in Meridian's case the
app glyph the page already carries, drawn very large and very faint. There is
no bitmap in the binary, nothing to decode on load and nothing held resident
once it has painted. Which one you get is substituted into the page when the
request is served rather than scripted in afterwards, so a blank start page
paints nothing instead of painting itself and then taking itself away again.

**Whole sites can be kept on disk, and read with no network at all.** Not
"Save Page As" — that already exists and writes one MHTML file. This is a
crawler: it walks a site over plain HTTP, keeps every page and every
stylesheet, image, script and font those pages need, and rewrites the links
between them, so what lands in the folder is an ordinary tree of HTML that
browses offline from `file://`.

Press the disk button in the toolbar (or `Ctrl Shift S`) and the site you are
on is mirrored to `~/Documents/Websites/Saved/<site>/`. Switch the sweep on
and the sites you have *actually been reading* — in history inside the last
few days, and visited enough times to mean it — are mirrored to
`~/Documents/Websites/Automatic/<site>/` in the background, one at a time, a
few hours apart. Both folders are configurable; so is whether you are asked
where to put each one.

The things worth knowing about how it behaves:

*Links are rewritten from what arrived, not from what was hoped for.* A crawl
cannot know while it is running whether a link's target will make it under the
limits, so nothing is rewritten until the fetching stops. Then every saved
page and stylesheet is read back once and relinked from the finished set:
pages that are in the mirror become relative paths, pages that are not stay
absolute URLs that still work online. A mirror that was cancelled halfway, or
that hit its page limit, is therefore still coherent — every local link
resolves, and the rest of the site is one click back onto the network.

*It is polite by construction.* `robots.txt` is fetched before the first page
and honoured, including `Crawl-delay`. Four requests at a time at most, spaced
out, and never more than one site being mirrored at once.

*It stays inside the site.* Pages are followed only within the same
registrable domain. Subresources are taken from wherever the page points,
because a page without its CDN stylesheet is not a copy of anything, and
anything borrowed from another host lands visibly under `_hosts/<host>/`.

*It fetches outside your session, which is the point.* The crawler is libsoup,
not WebKit, and carries none of your cookies. What you get is the logged-out
view of a site, and never a page that needed a password to see — mirroring a
logged-in session would write private pages into a folder with no access
control on it. Private windows do not archive at all.

*Limits are per site and enforced as it goes*: pages, link depth and total
megabytes, 200 / 3 / 200 MB by default, whichever is reached first. An
allow list and a block list decide which sites are eligible, the block list
winning over everything including a site you asked for by hand.

Every mirror carries a `lyndon-archive.ini` saying where it came from, when,
how much of it there is and whether it finished — which is also how the sweep
knows not to fetch a site again the day after it last did. Where a URL lands
on disk and how two of those places link to each other are
[unit tested](tests/test-archive.c), because a mirror whose links are wrong is
a folder of files rather than a copy of a site.

**Passwords live in your keyring, not in a file Lyndon invented.** Logins are
stored through the Secret Service API via libsecret — GNOME Keyring, KWallet,
KeePassXC, whatever your desktop already runs, and Windows Credential Manager
on the Windows build. Lyndon rolls no crypto of its own and writes no password
to its own files. Capture and fill are done by a
script injected into the **top frame only**: a third-party iframe that could
read a filled password would defeat the entire point. Autofill never submits a
form for you.

**One account is filled; several are offered.** A page with one saved login is
filled as soon as its form is found. A page with more than one is not guessed
at — a bar names the accounts and fills the one that is chosen, because filling
the first of them is how somebody ends up signed in as the wrong account, or
locked out by a site counting failures against a password that was never meant
for it. Matching is on the exact origin first; failing that, on saved logins
under the same registrable domain, so a password saved at `example.com` fills
the form at `accounts.example.com`. That fallback is only ever a fallback: a
site that keeps separate logins per subdomain means it, and a login saved over
`https` is never filled into a page served over `http`.

**A box asking for a new password is offered one.** Focus a field the page has
marked `autocomplete="new-password"`, or one sitting beside its own confirmation
field, and Lyndon offers twenty characters from the system's cryptographic
random source, with one of each character class guaranteed. Taking it fills the
confirmation field too. Nothing is stored at that point: the ordinary save
offer, after the form is submitted, is what asks about keeping it — so a
password that was generated and not used leaves nothing behind.

**Checkup**, in Preferences → Passwords, is Chrome's Password Checkup as far as
it can be had without Chrome's server. Weakness and reuse are worked out on the
machine, from the logins already in the keyring: weak means short or one of the
passwords that sit at the top of every breach summary, and reuse is counted per
site, so two accounts of yours on one site are not reported as a shared secret.
The breach check is the only part that reaches the network and it is **off until
you turn it on**. When it is on, the first five characters of each password's
SHA-1 are sent to `api.pwnedpasswords.com`, which answers with every hash it
holds under that prefix — tens of thousands of them — and the comparison
happens here. The password, the username and the site never leave the machine.
That is the same k-anonymity shape Chrome's own checkup is built on, and the
reason this can be asked of a service that is not ours. The
[arithmetic is unit tested](tests/test-pwcheck.c), including the parse of what
comes back, because a mistake there would quietly report every password as safe.

Saved logins can be edited in place — site, username, password and a **note** —
or added by hand, from Preferences → Passwords. The editor says how strong the
password is as you type it and will suggest a better one. A note is kept as its
own item in the keyring beside the login rather than as an attribute of it:
attributes are the login's identity, so adding one would make every noted login
a second row beside the one it was meant to annotate — and a note is as likely
to hold a recovery code as the password field is, which is why it is not in the
history database either. Logins come in from a CSV, a `.xlsx` or a `.ods` file,
or straight out of another browser's own encrypted store, notes included, and
they go back out as the CSV that Chrome, Firefox and Bitwarden all read.

**A half-read login is still offered, with a box to fill in the rest.** When a
form gives up a password but nothing that names the account, the save bar grows
a username field; when a sign-in plainly happened but the password could not be
read — a single-page app that wipes the field in its own submit handler — it
grows a password field instead, and Save stays greyed out until there is
something to save. Two-step logins that ask for the name on one screen and the
password on the next are stitched back together without asking anything.

**Bookmarks can be renamed, from either place they appear.** Right-click one —
in the bookmarks bar or in the Bookmarks dialog — for Open in New Tab, Rename,
Copy Address and Remove. A bookmark is identified by its URL, so renaming one
never moves it, and a name you chose is never written over by the page's own
title on a later visit; a bookmark saved before its title arrived still gets
that title filled in. The rename box opens on what is *stored*, not on what the
row happens to be drawing, which for a bookmark with no label of its own is its
address.

**Files and addresses can be dropped on the window.** Drop a file on the page
and it opens there; drop it on the chrome — the header, the bookmarks bar, the
tab strip — and it opens in a new tab, which is where Chrome puts one too.
Several at once open as several tabs. Dropped text that is not a path or an
address is searched for. The page gets first refusal on anything dropped over
it, so a site's own upload box and its editable fields keep working exactly as
before: this is reached only by drops that nothing inside the window wanted.

**The address bar finishes the address, and knows which one you meant.** Type
`driv` and the rest of `drive.google.com` appears, already selected, so Enter
goes there and the next keystroke replaces it. It only ever extends what you
are typing: never while you are deleting, never with the cursor anywhere but
the end, never over a pasted address, and never once what you have typed is
already somewhere you have been — so pressing Enter cannot land you on a page
deeper inside a site than the one you named.

Which page it offers is decided by three things. Whether you got there by
typing the address, which is the same question the address bar is asking and
so counts for most. How often you have been, on a log scale, because the
hundredth visit says far less than the second. And whether you read the page
or bounced off it.

That last one is the one worth explaining, because the obvious version of it
is wrong. Rank by time spent and an eighteen-hour video outranks the site's
own front page; Discord, sitting on one channel all evening, buries the short
address you actually type. So attention is capped per visit as it is recorded
— ninety seconds, after which a page is simply "read" — and scored as an
average rather than a total. It becomes a quality flag with a ceiling, worth a
tenth either way, rather than a quantity one long session wins outright. The
video and a page you read for a minute end up equally engaged, and the front
page wins on the signals that should decide it. The clock stops when the
window loses focus, and a page nobody chose to record earns nothing.

Ranking is where arithmetic quietly stops being right, so it is
[unit tested](tests/test-store.c) against exactly these cases.

**Privacy defaults are the defaults.** Third-party cookies off, WebGL off,
WebRTC off, autoplay off, passwords not persisted, Intelligent Tracking
Prevention on, Global Privacy Control on, referrers trimmed to the origin, and
a fingerprinting-resistance layer that reports generic CPU and memory values,
masks the GPU model, and adds sub-perceptual noise to canvas readback.

**The rest of what a browser is expected to do**, and not much that it is not.
History with inline address-bar completion, bookmarks with a star and an
optional bookmarks bar, private windows on an ephemeral session, session
restore, windows that reopen at the size they were closed,
reopen-closed-tab, tab pinning, muting and a full tab context menu,
a rich page context menu, view-source, save-page, printing, fullscreen,
remembered per-site zoom, caret browsing, paste-and-go, desktop notifications,
proxy and Accept-Language settings, search keywords, HTTPS-only mode, a site
information panel behind the padlock with per-site permissions, and an importer
for Chrome, Chromium, Brave, Edge, Vivaldi and Firefox.

What is deliberately absent: telemetry, crash reporting, "usage statistics",
sponsored tiles, an account to sign into, a built-in assistant, and any code
path that sends a URL anywhere other than the site you asked for.

**HTTPS-only** is done with WebKit's `make-https` rule, so the upgrade happens
in the network process before a cleartext request is ever sent — not by
retrying after a failure. Loopback, `.local`, `.test` and `.internal` are
exempt, because there is no certificate to upgrade to.

**Site information** lives behind the padlock (or Ctrl+I): the certificate
issuer and expiry, whether blocking is on, per-site permission overrides that
beat the global defaults, and a "clear data for this site" that removes that
one origin's cookies, storage, history and settings, leaving every other site
alone.

**Importing** reads Chrome, Chromium, Brave, Edge, Vivaldi, Opera and Firefox
profiles — bookmarks, history and saved logins, each switchable per profile.
Both families hold an exclusive SQLite lock while running, so the import always
works from a private copy that is deleted afterwards, and it carries the
original visit counts and dates across so imported history ranks properly in
the address bar instead of all looking equally fresh.

Passwords are decrypted where they sit. Chromium keeps its key in the same
keyring Lyndon uses, so its `Login Data` is read directly (AES-128-CBC under a
PBKDF2 key, which is what Chromium's `os_crypt` does on Linux). Firefox keeps
its key in an NSS database, so that import is handed to NSS itself; a profile
sealed with a Primary Password is reported as such rather than half-read. If
either refuses, exporting a CSV from the other browser always works.

**Password files.** `Import from a file` reads CSV and TSV in any of the shapes
the major managers export — Chrome, Firefox, Bitwarden, KeePassXC, 1Password,
LastPass, Dashlane, Safari — matching columns by header name rather than by
position, and sniffing comma, semicolon or tab. It also reads `.xlsx` and `.ods`
directly, with no spreadsheet program installed: both are zip archives of XML,
and Lyndon unpacks the sheet itself. Rows without a site or a password (secure
notes, payment cards) are counted and skipped rather than stored empty.

**Exporting** writes `name,url,username,password,note` at mode `0600`, after a
dialog that says plainly that the file has no encryption of any kind.

---

## Building

Needs GTK 4.10+, libadwaita 1.5+, WebKitGTK 6.0, libsoup 3, SQLite 3, libsecret,
zlib and OpenSSL. NSS is optional and only enables Firefox password import.

| Distro | Command |
|---|---|
| Arch | `sudo pacman -S gtk4 libadwaita webkitgtk-6.0 libsoup3 sqlite libsecret json-glib zlib openssl nss base-devel` |
| Fedora | `sudo dnf install gtk4-devel libadwaita-devel webkitgtk6.0-devel libsoup3-devel sqlite-devel libsecret-devel json-glib-devel zlib-devel openssl-devel nss-devel gcc make` |
| Debian/Ubuntu | `sudo apt install libgtk-4-dev libadwaita-1-dev libwebkitgtk-6.0-dev libsoup-3.0-dev libsqlite3-dev libsecret-1-dev libjson-glib-dev zlib1g-dev libssl-dev libnss3-dev build-essential` |

> **Note the `6.0`.** The older `webkit2gtk-4.1` is the GTK3 generation and will
> not work. `make deps` checks for you.

```sh
make deps      # verify dependencies and print versions
make           # build
make run       # build and launch
make check     # unit tests: filter compiler, URL handling, address-bar
               # ranking, offline mirror paths
make LTO=1     # link-time optimised
make DEBUG=1   # asan + ubsan

make user-install   # ~/.local/bin/lyndon -> lyndon.py, and a .desktop file
sudo make install   # a packaged copy in /usr/local that cannot self-update
```

**Install it with `make user-install`.** It needs no root: it links
`~/.local/bin/lyndon` to `lyndon.py` in this checkout and writes a `.desktop`
file that runs it, which is the same shape as every other app in the
repository. `sudo make install` is still there for packaging, and puts a copy
in `/usr/local` — but a copy is all it is. It has no checkout under it and no
write permission over itself, so it never updates and quietly goes stale.
Which of the two runs is down to `PATH` order and to where the desktop file
is read from, both of which normally prefer the one in your home directory;
`sudo make uninstall` removes the other so there is no question.

---

## Updating itself

Lyndon runs out of this checkout and keeps itself current, the way the Python
apps beside it do. The difference is that it is compiled, so a new version is
not the source but a build of one, and building takes a minute that nobody
should have to wait through.

`lyndon.py` is what the `.desktop` file starts, and it does four things:

1. Swaps in a build finished last time, from `dist-next/` — now, while it is
   not the binary anyone is running.
2. Fast-forwards the checkout to commits fetched earlier. Local git only, a
   few milliseconds.
3. **Starts Lyndon**, from `dist/`. Nothing above this line touches the
   network, so a link clicked in another program opens at the speed it always
   did.
4. Then, behind the running browser: fetches, and if `dist/` was not built
   from the source now checked out, builds that into `dist-next/`.

When step 4 lands, the launcher writes `.update-ready`. The running Lyndon is
watching that one file, and offers a toast — *A newer Lyndon is ready*, with a
**Restart** on it, which does not time out. Restart quits this browser and
asks the launcher to bring the new one up once this process is gone, so the
swap in step 1 is free to happen. Ignore it and the swap happens at the next
start anyway.

`src/update.c` is the whole of the browser's share of this: it watches a file
and spawns the launcher. It never fetches and never runs git — a browser is
the wrong process to be doing either from — and started any way other than
through the launcher it does not exist at all, so `make run` and
`./dist/lyndon` behave exactly as they did.

Only a fast-forward is ever made, so a checkout with commits of its own is
left alone, and uncommitted edits never trigger a build: a checkout being
worked on is rebuilt by hand, as before. What a build was made from is kept
beside it in `.source`, which is the git tree of this folder alone — a commit
touching only the other apps is not a reason to rebuild. `APPS_NO_UPDATE=1`
turns the whole thing off, and anything that goes wrong is written to
`update.log`.

Lyndon is also the default browser, so the launcher runs every time anything
opens a link, not only when someone starts the browser. The fetch is
throttled to once every half hour for that reason; the swap and the
fast-forward, being local, happen every time.

---

## The house style

The chrome wears the same ten themes as the rest of the suite — see
[`house/`](../house/README.md). Lyndon is C and has no Python package to
vendor `theme.py` into, so `house/sync.py` generates `src/house-themes.h`
from it instead: the same numbers, as a C table that `apply_style` emits
libadwaita's whole named palette from. `lyndon.css` is written against those
names, so one table recolours every rule in it.

**Porcelain** is the light default and **Moss** the dark one.
`color-scheme` chooses between them — *Follow system* uses the desktop's
answer — and both are in Preferences → Appearance.

## Configuration

Everything lives in one file: `~/.config/lyndon/config.ini`. Plain GKeyFile —
no GSettings schema to compile, no dconf daemon, no D-Bus round trip on a cold
start. It is meant to be hand-editable.

```ini
[appearance]
color-scheme  = system       ; system | light | dark — which of the pair below
dark-theme    = moss         ; one of the ten house themes
light-theme   = porcelain
effects       = full         ; full | reduced | off
opacity       = 0.82         ; chrome alpha at the "full" level
start-page    = full         ; full | minimal | blank
start-artwork = none         ; none | aurora | meridian | dune |
                             ; lattice | ember | monolith

[web]
force-dark   = smart         ; off | smart | always
webgl        = false
webrtc       = false
hardware-acceleration = auto ; auto | always | never
page-cache   = true          ; keep pages around for back/forward
share-site-process = false   ; fewer processes, no less memory; turns
                             ; unload-background-tabs-after off
unload-background-tabs-after = 30
                             ; minutes; 0 keeps every tab in memory

[privacy]
cookies                 = no-third-party   ; none | no-third-party | all
tracking-prevention     = true
global-privacy-control  = true
trim-referrer           = true
fingerprint-defence     = true
user-agent              = default          ; default | minimal | custom
https-only              = false            ; upgrade http:// to https://
proxy                   = system           ; system | none | custom
proxy-url               = http://127.0.0.1:8080
languages               = en-GB, en        ; Accept-Language; empty sends none
search-keywords         = w=https://en.wikipedia.org/w/index.php?search=%s;
save-passwords          = true             ; offer to save form logins
password-autofill       = true
password-suggest        = true             ; offer a generated one on sign-up
password-leak-check     = false            ; ask a breach service; see Checkup
password-never          = example.com;     ; origins that are never asked
remember-history        = true

[blocker]
enabled       = true
ads           = true
trackers      = true
analytics     = true
social        = true
annoyances    = false        ; most likely to break sites, so off by default
cookie-notices = true
cryptomining  = true
exceptions    = example.com;
subscriptions = https://easylist.to/easylist/easylist.txt;

[permissions]
geolocation   = ask          ; ask | allow | deny
notifications = deny
camera        = ask

[offline]
enabled        = false       ; the whole feature, off until asked for
directory      =             ; empty means ~/Documents/Websites
ask-where      = false       ; choose a folder on every save
automatic      = false       ; mirror the sites you keep reading
days           = 7           ; how far back "keep reading" looks, and how
                             ; stale a mirror has to be before it is retaken
minimum-visits = 3           ; across all of a site's pages
allow          =             ; empty allows any site not blocked
block          = bank.example;
max-pages      = 200         ; per site, whichever limit comes first
max-depth      = 3           ; links from the page you started on
max-megabytes  = 200

[session]
restore          = true      ; reopen last session's tabs on launch
show-home-button = false
per-site-zoom    = true
homepage         = lyndon:start
window-width     = 1180      ; size the last window was closed at
window-height    = 760
window-maximized = false
```

Your own rules go in `~/.config/lyndon/custom-rules.txt`, in Adblock Plus
syntax. Preferences → Blocking → Edit opens it.

---

## Keyboard

| | |
|---|---|
| `Ctrl T` / `Ctrl W` | new tab / close tab |
| `Ctrl Shift T` | reopen the last closed tab |
| `Ctrl N` / `Ctrl Shift N` | new window / new **private** window |
| `Ctrl Tab` / `Ctrl Shift Tab` | next / previous tab |
| `Ctrl Shift O` | tab overview |
| `Ctrl L` | focus the address bar |
| `Alt ←` / `Alt →` | back / forward |
| `Ctrl R` / `Ctrl Shift R` | reload / reload ignoring cache |
| `Ctrl F`, `Ctrl G`, `Ctrl Shift G` | find, next match, previous match |
| `Ctrl +` / `Ctrl −` / `Ctrl 0` | zoom |
| `Ctrl 1`…`Ctrl 8` / `Ctrl 9` | switch to tab N / last tab |
| `Ctrl D` | bookmark this page |
| `Ctrl Shift D` | duplicate tab |
| `Ctrl U` | view page source |
| `Ctrl S` | save page |
| `Ctrl Shift S` | save this whole site for offline reading |
| `Ctrl I` | site information |
| `F7` | caret browsing |
| `Ctrl H` | history |
| `Ctrl P` | print |
| `F11` | full screen — the whole top bar goes with it |
| `Alt Home` | home page |
| `Ctrl Shift P` | pause protection on this site |
| `Ctrl Shift K` | clear cache (also `Ctrl Shift Backspace`) |
| `Ctrl Shift Delete` | clear browsing data — pick what goes |
| `Esc` | close the find bar, leave full screen, or stop loading — in that order |

---

## Full screen

`F11` hides the chrome as well as the window frame: the header, the bookmarks
bar, the tab strip and the progress bar are one widget in the toolbar view, so
all four leave together and the page takes the space. Putting the pointer
against the top edge slides them back without leaving full screen, and takes
them away again when it moves off — so the address bar is still reachable
without a round trip through `F11`. `Esc` leaves too, which matters because
with the chrome gone there is nothing on screen to say how else to.

This hangs off the window's own `fullscreened` property rather than off the
key that toggles it, because `F11` is not the only way in: the menu item, the
compositor's own full-screen binding, and WebKit putting a video up by itself
all arrive at the same place instead of each having to remember to hide the
chrome.

## More than one window

`Ctrl N` and `Ctrl Shift N` open a window and a private window; dragging a tab
off the strip makes a window of it; and each window keeps its own tabs, its own
find bar and its own private session if it has one. Everything they do share —
the engine, the blocker, the history, the one network session — is owned by the
application rather than by any window, so the second window costs a window and
not a browser.

From outside, one instance serves the lot. A second `lyndon` does not start a
second browser; it hands what it was given to the one already running:

```sh
lyndon                       # show the browser, or start it
lyndon URL…                  # open these in the window already in front
lyndon -n  URL…              # …in a new window instead
lyndon -p  URL…              # …in a new private window
```

The launcher icon offers **New Window** and **New Private Window** as right-click
actions, which is the same two flags spelled out in the `.desktop` file.

What comes back is every window's tabs, not just the last one's. Each window
writes the session down on its way out, together with whatever is still open,
because windows close one at a time even when the whole browser is quitting —
so a window that recorded only its own tabs would leave the last one out, always
the smallest, overwriting everything the others had. Closing the final window by
hand used to do exactly that and clear the session outright: the window is
destroyed before shutdown runs, so the save there found nothing open. Quitting
with `Ctrl+Q` happened to work, because that ends the loop with every window
still alive — which is the sort of difference nobody notices until their tabs
are gone.

A window you close on purpose keeps counting until you open another one. That
errs towards bringing back a tab you had rather than losing one, and opening a
window is the signal that the old ones are finished with.

Any of these arriving with the browser shut restores the last session first,
and then does what it was asked on top of it. That is not politeness: the
session is written back at shutdown from whatever is on screen, so opening a
single link from another application and later quitting would otherwise
replace yesterday's eight windows with that one tab, silently and for good.
Restoring costs nothing when there is nothing to restore, and `restore` in
`[session]` still decides whether any of it happens.

---

## How it fits together

```
main.c      entry point
app.c       LyApp — owns config, engine, blocker, downloads; actions and accels
window.c    LyWindow — chrome, tab view, address bar, shield, find bar
tab.c       LyTab — one WebView plus its policy: permissions, navigation, errors
engine.c    the single shared WebContext + NetworkSession; privacy user scripts
blocker.c   rule gathering, compilation and per-tab attachment
store.c     SQLite: history, bookmarks, zoom, permissions, session; and the
            address bar's ranking                                (unit tested)
import.c    bookmarks, history and logins from other browsers' own stores
passwords.c form capture and fill; storage via libsecret
credentials.c the credential struct and the origin rule, shared with Windows
pwfile.c    passwords in and out of CSV, TSV, .xlsx and .ods files
pwcheck.c   weak, reused and breached passwords; the generator (unit tested)
abp.c       Adblock Plus syntax → WebKit content-blocker JSON  (unit tested)
archive.c   the site crawler: fetch, map URLs onto paths, relink (unit tested)
update.c    noticing a newer build and restarting into it (shared)
config.c    the GKeyFile model
prefs.c     the preferences dialog
downloads.c the shared download list
util.c      URL normalisation, eTLD+1, formatting

lyndon.py       the launcher: swap in, fast-forward, start, build behind it
tools/updater.py  the fast-forward itself, the same file every app carries
```

One `WebKitWebContext` and one `WebKitNetworkSession` are shared by every tab in
every window, so the whole browser runs a single network process. Each tab gets
its own `WebKitUserContentManager`, which is what makes a per-site protection
toggle a matter of not installing a filter rather than recompiling one.

`archive.c` is the one part that does not go through WebKit at all. A crawl has
to run without a web view, without a tab and without the browser's cookies, so
it is libsoup and a hand-written tag scanner — tolerant rather than correct,
copying anything it does not understand through untouched, which is the only
behaviour that keeps a document it cannot parse coming out of the mirror
exactly as it went in. Requests outlive the crawl that started them when the
browser is closed mid-mirror, so each one holds a refcounted anchor that a late
callback checks before it touches anything.

### The filter compiler

`abp.c` translates the subset of Adblock Plus syntax that maps cleanly onto
WebKit's rule language, and **deliberately drops everything else**. Dropping a
rule under-blocks; mistranslating one either breaks a page or, worse, makes
WebKit reject the entire list. So `$redirect=`, `$removeparam=`, `$csp=`,
scriptlet injection and cosmetic exceptions are all counted as skipped rather
than guessed at.

What it does handle: `||domain^` anchors, `|` prefix and suffix anchors,
wildcards, literal `/regex/` (validated against WebKit's engine limits),
`$third-party`, `$domain=`, resource-type options and their negations, `@@`
exceptions, `##` element hiding, and hosts-file lines. Rules are emitted in the
order WebKit needs — blocks, then cosmetics, then `ignore-previous-rules` — and
generic hiding selectors are merged into a single rule to keep the compiled DFA
small.

`make check` covers translation, option parsing, ordering, refusal of
unsupported syntax, and JSON escaping.

---

## Honest limitations

- **There is no "12 trackers blocked" counter.** WebKit drops blocked requests
  in the network process before anything observable happens, so a per-page count
  cannot be obtained without giving up exactly the property that makes this
  blocker cheap. The shield shows what is enforced instead of inventing a
  number.
- **Forced dark uses inversion.** A root `filter` establishes a containing
  block, which can disturb `position: fixed` layouts on some sites. This is
  inherent to the technique. `Smart` mode limits the blast radius by only
  inverting pages that need it.
- **Downloads save straight to the download folder** under a name that never
  overwrites an existing file. There is no "where do you want this?" prompt:
  WebKit needs the destination synchronously, and a modal round trip there is
  the classic way to lose a download.
- **Password capture is heuristic, as it is in every browser.** A login form is
  found by locating a visible `input[type=password]` and pairing it with the
  field that names itself, or failing that the nearest text-ish input. Shadow
  roots are searched too, but only a bounded number of times per page. Sites
  that build login flows out of non-standard widgets may still be missed, and
  the offer to type the missing half by hand is the fallback for exactly that.
- **A save offer survives the navigation the login caused**, and only that one:
  a page load more than five seconds after the capture dismisses it, as does
  ninety seconds with no answer.
- **Firefox password import needs NSS at build time.** It is detected
  automatically; without it that one import explains itself and points at the
  CSV route. Everything else builds and runs the same.
- **Autofill fills, it never submits.** By design.
- **The password manager is not Chrome's, and four of Chrome's features are
  not reachable from here.** There is no sync: logins live in the desktop
  keyring, which is the right place for them on this platform, and there is no
  Lyndon account to sync them to. There are no **passkeys** — WebKitGTK exposes
  no credential-provider API for a browser to register itself against, so this
  is a WebKit limitation rather than a decision. There is no **biometric or
  OS re-authentication** before a password is revealed or filled; the keyring's
  own unlock prompt is the only gate, which is the same gate the rest of the
  desktop uses. And there is no **password sharing**. What is here is the rest:
  capture, fill, the account chooser, same-domain matching, generation, notes,
  import and export, and Checkup.
- **Checkup's reuse grouping approximates the Public Suffix List.** Reuse is
  counted per registrable domain, and that domain comes from
  `ly_uri_base_domain`, which carries a short list of multi-label suffixes
  rather than the real PSL. `co.uk` and `com.au` come out right; a hosting
  provider whose customers each own a subdomain does not, and logins there are
  grouped together when they should not be. The same approximation is what
  autofill's same-domain fallback uses, where being wrong this way round costs
  an offer that should not have been made — which is why the fallback only runs
  when the page has no login of its own, and never downgrades `https` to
  `http`.
- **The breach check asks a third party, one request per distinct password.**
  It is off until it is turned on. Nothing identifying goes — five characters
  of a hash, padded responses — but the requests do go to
  `api.pwnedpasswords.com` rather than to anything of Lyndon's, and a store
  with more than 400 distinct passwords stops there.
- **Session restore stores URLs, not scroll position or form state.**
- **No extensions.** There is no WebExtensions runtime and no plan for one; it
  is the single largest source of browser attack surface, and the blocking
  extensions exist for is built in.
- **No form autofill for addresses or payment cards.** Passwords only.
- **A dropped file the page took is the page's.** Anything dropped over the
  content area reaches the web view first, because that is what keeps upload
  boxes and editable fields working. A page that claims a drop and then ignores
  it swallows it, and Lyndon never sees that drop to open the file itself.
  Dropping on the chrome always opens.
- **Save page writes MHTML**, which is the only complete-page format WebKit
  produces.
- **An offline mirror is a copy of the public site, and only roughly a copy.**
  The crawler is not a browser: it does not run JavaScript, so a site that
  builds its pages in the client arrives as whatever its HTML said before the
  scripts ran, and anything reachable only by clicking is not reachable at all.
  Links are found by reading markup, so a URL assembled at runtime is invisible
  to it. Responses larger than 32 MB are skipped, as is video and audio over
  8 MB, because a mirror is for reading rather than for hoarding media.
- **`robots.txt` is read for the site the crawl is rooted on**, and applied to
  every page it follows; subresources are fetched regardless, as a browser
  would.
- **Updating itself needs the launcher.** Started any other way — `make run`,
  `./dist/lyndon`, or a copy put in `/usr/local` by `sudo make install` —
  Lyndon has no update machinery in it, says nothing, and stays at the
  version it is. That is deliberate: the browser does not fetch and does not
  run git, so without something above it doing that there is nothing for it
  to notice.
- **A new version is built behind the running one**, which means a `make -j`
  for about a minute on whatever core is free, once, after a commit that
  touched this folder lands. Nothing waits on it — Lyndon has already
  started — but it is not nothing, and `APPS_NO_UPDATE=1` turns it off.
- **A restart is a restart.** The offer quits the browser and starts it
  again; open tabs come back through session restore, and anything typed
  into a page does not.
- **The sweep is per site, not per page.** It mirrors a whole site starting
  from the busiest page you have read on it, which is not necessarily the page
  you want kept. Ask for that one by hand.
- **The tab context menu acts on the tab you right-clicked**, which libadwaita
  reports through `setup-menu`; if that ever returns nothing the menu falls
  back to the selected tab.

---

## Licence

MIT. See `LICENSE`.

---

## Windows

Lyndon is built on WebKitGTK, and **there is no WebKitGTK for Windows** — no
port exists, and MSYS2 ships no package for it. So the Windows build is not a
recompile. It keeps everything below the toolkit and replaces the two things
that cannot come over: the window is Win32, and the engine is WebView2, the
Edge runtime that is already on every Windows 10 and 11 machine.

```bash
make -f win32/Makefile          # build lyndon.exe
make -f win32/Makefile check    # the blocker's own tests
make -f win32/Makefile dist     # a folder that runs without MSYS2
```

in an **MSYS2 MINGW64** shell, with `mingw-w64-x86_64-gcc`,
`mingw-w64-x86_64-glib2`, `mingw-w64-x86_64-json-glib`,
`mingw-w64-x86_64-sqlite3` and `mingw-w64-x86_64-zlib` installed.
`mingw-w64-x86_64-nss` is optional and enables Firefox password import, the
same way it does on Linux. The result is about 380 KB of executable and a
handful of GLib DLLs; `dist` gathers them, the `data/` directory and the
WebView2 loader into one folder that needs no MSYS2 on the machine it runs on.

Nothing else is needed for the passwords: DPAPI and CNG are part of Windows,
so the Chromium import and the AES-GCM under it cost two more `-l` flags and
no dependency.

`windows\install.ps1`, at the root of the repository, registers the build in
`dist\` as a browser — for `http` and `https` links and for saved pages — so
it is offered in Settings → Default apps, and `-Default Lyndon` opens its page
there.

### What is shared and what is new

`src/lyndon.h` drops the toolkit includes under `_WIN32`, and that is the
whole of what porting the shared half took — **`abp.c`, `config.c`, `store.c`,
`util.c`, `import.c`, `credentials.c`, `pwfile.c` and `update.c` compile from
`src/` unchanged** and are the same code the Linux build runs. The filter translator,
the config file, the SQLite history store, the URL helpers, the browser
importer, the rule that decides what an origin is and the CSV and spreadsheet
readers are therefore not forked, and a change to any of them lands on both.

| | Linux | Windows |
|---|---|---|
| Window, tabs, toolbar | GTK4 + libadwaita | `win32/chrome.c`, drawn |
| Web engine | WebKitGTK 6 | WebView2, `win32/tab.c` |
| Request matching | WebKit's own DFA | `win32/block.c` |
| Filter translation | `src/abp.c` | `src/abp.c` |
| Config, history, URLs | `src/config.c`, `store.c`, `util.c` | the same files |
| Browser import | `src/import.c` | the same file |
| Password files | `src/pwfile.c` | the same file |
| Origin matching | `src/credentials.c` | the same file |
| Offline site mirrors | `src/archive.c` | not ported |
| Noticing a new build | `src/update.c` | the same file |
| Offering the restart | an `AdwToast` | `win32/notice.c`, drawn |

### The blocker

This is the part WebKit was doing for free. It compiles the rules to a DFA and
matches them in its network process; WebView2 offers a callback per request and
nothing else, so the matching had to be written.

It is built for the shape filter lists actually have. Nearly every rule is
`||host^`, so those go in a hash table and are answered by walking the
request's labels right to left — four lookups for a four-label host, no regex.
Everything else is indexed by the longest literal run in the pattern, so a
request only tests rules that share a token with its URL. Patterns are turned
into regexes by `ly_abp_pattern_to_regex()` from `abp.c`, the same function
that produces the Linux build's WebKit JSON, so the two agree about what a
rule means.

`make -f win32/Makefile check` runs it against the lists in `data/rules/`:
that the shipped lists block what they are for, that a page's own resources
are left alone, and that `notdoubleclick.net` is not treated as a subdomain of
a blocked host.

### What is there

Tabs, navigation and the address bar with the same keyword and search
handling as the Linux build. Blocking with a per-tab count and a switch in
the toolbar, element hiding, history and bookmarks in the same SQLite file,
dark chrome that follows the system, per-monitor DPI, and windows that reopen
at the size they were left — written to the same `window-width`,
`window-height` and `window-maximized` keys, as a page area at 96 dpi, so the
same numbers describe the same browser on either platform.

Passwords are at parity too: capture through shadow roots, the two-step
logins that ask for a name on one page and the password on the next, saved
logins edited or added by hand, imported from another browser or from a CSV
or spreadsheet, and exported after the same warning about what a plaintext
file means.

On top of that, the parts that were GTK-bound and had to be built again:

| | Where it lives | Notes |
|---|---|---|
| **Settings** | `win32/prefs.c` | Seven pages, driven by a table bound to `LyConfig` by field offset — adding a setting is one line. |
| **Bookmarks, history, downloads** | `win32/panel.c` | One drop-down with three sections and a filter box, rather than three dialogs. |
| **Downloads** | `win32/downloads.c` | Lyndon's own list and its own file naming, not the Edge bubble. |
| **Saved logins** | `win32/passwords.c` | Windows Credential Manager. |
| **The save-login card** | `win32/login.c` | A popup over the page, because WebView2 owns the whole page area and nothing can be placed inside it. Same three answers, same ninety seconds, same entry box for whichever half the page would not give up. |
| **Importing** | `src/import.c` | The Linux file, compiled here; the profile locations and the way the key is unwrapped differ, nothing else. |
| **Password files** | `src/pwfile.c` | The Linux file, unchanged: the same CSV, TSV, `.xlsx` and `.ods` reader and the same CSV writer. |
| **Session restore** | `win32/chrome.c` | The store's own session table, so both builds read the same rows. |
| **One browser at a time** | `win32/chrome.c` | GApplication's job on Linux. A second start finds the first by a named mutex and hands over what it was asked to open with `WM_COPYDATA`, so a link clicked in another program becomes a tab in the open window. A path — what Explorer passes for a saved page — is opened as its `file://` URL. |
| **The look** | `win32/ui.c` | `data/css/lyndon.css`, followed measurement for measurement. |
| **`lyndon:start`** | `win32/scheme.c` | The same start page, on the same URL, from the same file. |

### How it is drawn

`data/css/lyndon.css` is the specification, and the Windows chrome follows it
rather than approximating it: tabs and buttons at a 9px radius, the address
bar a 32px pill at radius 16 with a two-pixel accent ring when it has the
focus, hover fills at `alpha(currentColor, .07)` and `.09`, the blocked count
in a filled accent pill, libadwaita's own palette down to the hex.

None of that is drawn with GDI. `RoundRect` has no antialiasing, so a 9px
corner comes out as a visible staircase and no amount of correct colour makes
a browser built from staircases look like anything but 2003. `win32/ui.c`
paints into a 32-bit DIB instead and fills the rounded shapes by evaluating a
signed distance field per pixel, which gives a real one-pixel edge; icons are
strokes with round caps at their distance to the segment, and the two filled
ones are supersampled sixteen times a pixel. Text still goes through GDI,
which is better at it than anything worth writing here.

Two things follow from owning the pixels. The address bar shows its URL with
everything but the registrable domain dimmed — the domain is the only part
that says where you are, and an `EDIT` control has one colour for all of its
text, so the entry only exists while it is being typed into and the rest of
the time the URL is painted. And the load bar is a real two-pixel indeterminate
sweep along the bottom of the chrome, because WebView2 reports no fraction and
a bar that invents one is a lie.

### The start page

`lyndon:start` is the homepage in `config.ini` on both builds. WebKit gets
that scheme from `webkit_web_context_register_uri_scheme`; WebView2 will only
navigate to a scheme it was told about before the environment existed, and
telling it means handing it an options object that the SDK provides in C++ and
not at all in C. `win32/scheme.c` implements those four interfaces by hand so
that the config file does not need a Windows-shaped exception in it.

One trap worth writing down: `TargetCompatibleBrowserVersion` defaults, in the
SDK's own C++ helper, to the version the SDK shipped with. Set that on a
machine whose Edge is older and environment creation fails with `E_INVALIDARG`
and no explanation. Lyndon asks the loader what is actually installed and
targets that.

Several of those reuse rather than reimplement. `src/import.c` compiles
unchanged apart from two `#ifdef`s: one over where Chrome, Edge, Brave,
Vivaldi, Opera and Firefox keep their profiles, and one over how the key that
opens their saved logins is found. On Linux that key is a passphrase in the
Secret Service and the values are AES-128-CBC; on Windows it is a random
AES-256 key in the profile's `Local State`, wrapped with DPAPI so that only
the account that saved it can unwrap it, and the values are AES-256-GCM
through CNG. The walk over the profile's SQLite table is written once and is
the same on both — which is the point of splitting it that way rather than
writing the whole import twice. Rows wrapped with Chrome 127's app-bound
`v20` scheme are counted as unreadable rather than guessed at: that key is
held by an elevated service that hands it back only to Chrome.

Firefox is the one import that has to hand the work to the library that owns
the format, and it does so identically on both: NSS is detected with
`pkg-config` at build time and, when it is absent, that one import explains
itself and points at the CSV route.

And the script that finds, fills and captures login forms lives in
`src/password-script.h` and is shared verbatim: only the line that posts a
message back differs, because WebKit gives each handler its own object and
WebView2 gives one `postMessage` for everything. It is security-relevant code,
and two copies of it would be two behaviours. The rule that turns a URL into
the origin autofill matches on lives in `src/credentials.c` for the same
reason — two implementations of it would eventually be two rules, and a saved
login that no longer matches the site it came from.

Passwords go to Credential Manager for the same reason the Linux build uses
libsecret: the browser should not invent its own crypto, and should not write
a password to a file of its own. Credential Manager encrypts per user,
unlocks at logon, and can be reviewed in Control Panel by someone who has
never heard of Lyndon.

### What is honestly missing

* **Find in page.** WebView2 exposes no find API, so this would mean
  highlighting matches from injected script — doable, not done.
* **Translucent chrome.** `.fx-full` leaves the toolbars part-transparent and
  lets the compositor blur what is behind them. Windows 11 has Mica, which is
  the same idea, but wiring it up means compositing the chrome with real alpha
  through DWM and it is not done here: the chrome is opaque at every effects
  setting.
* **A few settings this engine cannot honour.** WebGL, WebRTC, hardware
  acceleration, the proxy and the WebKit-specific privacy switches have no
  WebView2 equivalent. They are still shown, dimmed, under a heading that
  says so, because they are real settings that the Linux build reads from the
  same file — hiding them would make the two look like different products.
* **Per-tab process control.** WebView2 decides that; Lyndon does not.
* **Offline site mirroring.** `archive.c` is libsoup, which the Windows build
  does not link, so there is no crawler here and no Offline page in
  Preferences. The `[offline]` settings still survive a round trip through
  `config.c`, which both builds share, so a profile used on both machines does
  not lose them.
* **File associations.** The Linux build ships a `.desktop` file that claims
  `http`, `https` and `application/pdf`; `windows/install.ps1` only makes Start
  Menu shortcuts, so Lyndon cannot yet be made the default browser from inside
  the installer. Windows' own Default Apps page is the way to do it.

### The vendored header

`win32/webview2/WebView2.h` is Microsoft's SDK header, unmodified, from the
`Microsoft.Web.WebView2` NuGet package. It is included with `-isystem` because
it is MIDL output and warns several hundred times under GCC. The **runtime** is
not vendored — it ships with Edge. `WebView2Loader.dll` is opened by name at
startup rather than linked, because the import library Microsoft ships is
MSVC-format; that also gives somewhere to say "the runtime is missing" instead
of failing to start with no window and no message.

