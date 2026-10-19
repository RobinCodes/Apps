# Hermione as the desktop's file chooser

What this is for: when something says *"select a file on your computer"*, the
window that opens should be the file manager you already know, with your
places, your bookmarks, your search and your thumbnails — not a second file
browser with its own idea of where Downloads is.

## How it works

A program that wants a file does not open a dialog itself. It asks
`xdg-desktop-portal`, which looks up which **backend** the desktop prefers
for `org.freedesktop.impl.portal.FileChooser` and hands the question on.
`hermione --portal` is such a backend. Three files make it one:

| `hermione.portal` | tells the portal that this backend exists and what it can answer |
| `org.freedesktop.impl.portal.desktop.hermione.service` | lets D-Bus start it on demand, so nothing is resident |
| `…-portals.conf` | says to prefer it over the GTK one |

All three go under `$HOME`. Nothing is installed system-wide and nothing
needs root.

## Installing it

```bash
./portal/install.sh          # and  ./portal/install.sh --uninstall
```

Then either log out and back in, or:

```bash
systemctl --user restart xdg-desktop-portal
```

The portal daemon reads the backend list once at startup, so until it is
restarted it will go on using whatever it found last time.

## What it covers, and what it does not

Everything that asks the portal gets Hermione: Flatpak apps (all of them —
inside a sandbox the portal is the *only* way to reach a file), Firefox with
`widget.use-xdg-desktop-portal.file-picker` set to 1, Chromium and Electron
apps, anything Qt with `QT_QPA_PLATFORMTHEME=xdgdesktopportal`, and any GTK
app run with `GTK_USE_PORTAL=1`.

A GTK or Qt program that is **not** sandboxed and has not been told to use
the portal opens its own dialog, and there is nothing a backend can do about
that — the question never reaches the portal. `GTK_USE_PORTAL=1` in the
environment is what fixes it for the GTK ones; the installer offers to put
it in the niri config, where it applies to everything niri starts.

## Checking it by hand

```bash
gdbus call --session --dest org.freedesktop.impl.portal.desktop.hermione \
  --object-path /org/freedesktop/portal/desktop \
  --method org.freedesktop.impl.portal.FileChooser.OpenFile \
  /org/freedesktop/portal/desktop/request/x/y "org.example.App" "" "Pick a file" \
  "{'multiple': <true>}"
```

A window opens; the call sits there until it is answered, and then prints
the response code and the URIs. `0` is a pick, `1` is a cancel.

To see which backend the portal actually chose:

```bash
systemctl --user restart xdg-desktop-portal
journalctl --user -u xdg-desktop-portal -b | grep -i filechooser
```
