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

**Passwords live in your keyring, not in a file Lyndon invented.** Logins are
stored through the Secret Service API via libsecret — GNOME Keyring, KWallet,
KeePassXC, whatever your desktop already runs, and Windows Credential Manager
on the Windows build. Lyndon rolls no crypto of its own and writes no password
to its own files. Capture and fill are done by a
script injected into the **top frame only**: a third-party iframe that could
read a filled password would defeat the entire point. Autofill requires an
exact origin match, and never submits a form for you.

Saved logins can be edited in place — site, username and password — or added by
hand, from Preferences → Passwords. They come in from a CSV, a `.xlsx` or a
`.ods` file, or straight out of another browser's own encrypted store, and they
go back out as the CSV that Chrome, Firefox and Bitwarden all read.

**A half-read login is still offered, with a box to fill in the rest.** When a
form gives up a password but nothing that names the account, the save bar grows
a username field; when a sign-in plainly happened but the password could not be
read — a single-page app that wipes the field in its own submit handler — it
grows a password field instead, and Save stays greyed out until there is
something to save. Two-step logins that ask for the name on one screen and the
password on the next are stitched back together without asking anything.

**Privacy defaults are the defaults.** Third-party cookies off, WebGL off,
WebRTC off, autoplay off, passwords not persisted, Intelligent Tracking
Prevention on, Global Privacy Control on, referrers trimmed to the origin, and
a fingerprinting-resistance layer that reports generic CPU and memory values,
masks the GPU model, and adds sub-perceptual noise to canvas readback.

**The rest of what a browser is expected to do**, and not much that it is not.
History with ranked address-bar completion, bookmarks with a star and an
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
make check     # unit tests: the filter compiler, and the shared URL handling
make LTO=1     # link-time optimised
make DEBUG=1   # asan + ubsan
sudo make install
```

---

## Configuration

Everything lives in one file: `~/.config/lyndon/config.ini`. Plain GKeyFile —
no GSettings schema to compile, no dconf daemon, no D-Bus round trip on a cold
start. It is meant to be hand-editable.

```ini
[appearance]
color-scheme = system        ; system | light | dark
effects      = full          ; full | reduced | off
opacity      = 0.82          ; chrome alpha at the "full" level

[web]
force-dark   = smart         ; off | smart | always
webgl        = false
webrtc       = false
hardware-acceleration = auto ; auto | always | never

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
| `Ctrl I` | site information |
| `F7` | caret browsing |
| `Ctrl H` | history |
| `Ctrl P` | print |
| `F11` | full screen |
| `Alt Home` | home page |
| `Ctrl Shift P` | pause protection on this site |
| `Ctrl Shift K` | clear cache (also `Ctrl Shift Backspace`) |
| `Ctrl Shift Delete` | clear browsing data — pick what goes |
| `Esc` | stop loading, or close the find bar |

---

## How it fits together

```
main.c      entry point
app.c       LyApp — owns config, engine, blocker, downloads; actions and accels
window.c    LyWindow — chrome, tab view, address bar, shield, find bar
tab.c       LyTab — one WebView plus its policy: permissions, navigation, errors
engine.c    the single shared WebContext + NetworkSession; privacy user scripts
blocker.c   rule gathering, compilation and per-tab attachment
store.c     SQLite: history, bookmarks, per-site zoom and permissions, session
import.c    bookmarks, history and logins from other browsers' own stores
passwords.c form capture and fill; storage via libsecret
credentials.c the credential struct and the origin rule, shared with Windows
pwfile.c    passwords in and out of CSV, TSV, .xlsx and .ods files
abp.c       Adblock Plus syntax → WebKit content-blocker JSON  (unit tested)
config.c    the GKeyFile model
prefs.c     the preferences dialog
downloads.c the shared download list
util.c      URL normalisation, eTLD+1, formatting
```

One `WebKitWebContext` and one `WebKitNetworkSession` are shared by every tab in
every window, so the whole browser runs a single network process. Each tab gets
its own `WebKitUserContentManager`, which is what makes a per-site protection
toggle a matter of not installing a filter rather than recompiling one.

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
- **Session restore stores URLs, not scroll position or form state.**
- **No extensions.** There is no WebExtensions runtime and no plan for one; it
  is the single largest source of browser attack surface, and the blocking
  extensions exist for is built in.
- **No form autofill for addresses or payment cards.** Passwords only.
- **Save page writes MHTML**, which is the only complete-page format WebKit
  produces.
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

### What is shared and what is new

`src/lyndon.h` drops the toolkit includes under `_WIN32`, and that is the
whole of what porting the shared half took — **`abp.c`, `config.c`, `store.c`,
`util.c`, `import.c`, `credentials.c` and `pwfile.c` compile from `src/`
unchanged** and are the same code the Linux build runs. The filter translator,
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

