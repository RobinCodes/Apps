#!/usr/bin/env bash
# Make Hermione the desktop's file chooser — or put back whatever was there.
#
# Everything lands under $HOME. xdg-desktop-portal has searched
# ~/.local/share/xdg-desktop-portal/portals since 1.19, so a backend no
# longer has to be installed into /usr to be found.
#
# The one file this does not simply write is the portals.conf: the system
# ships one per desktop and the first one found wins outright rather than
# merging, so a user copy has to carry over every line of the system's or
# it quietly drops the desktop's other preferences.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
launcher="${HERMIONE:-}"
if [ -z "$launcher" ]; then
    if [ -x "$HOME/.local/bin/hermione" ]; then
        launcher="$HOME/.local/bin/hermione"
    else
        launcher="$(dirname "$here")/hermione.py"
    fi
fi

data="${XDG_DATA_HOME:-$HOME/.local/share}"
conf="${XDG_CONFIG_HOME:-$HOME/.config}"
portals="$data/xdg-desktop-portal/portals"
services="$data/dbus-1/services"
service_name="org.freedesktop.impl.portal.desktop.hermione.service"
desktop="${XDG_CURRENT_DESKTOP:-}"
desktop="${desktop%%:*}"
desktop="$(printf '%s' "${desktop:-niri}" | tr '[:upper:]' '[:lower:]')"
config_file="$conf/xdg-desktop-portal/${desktop}-portals.conf"

INTERFACE=org.freedesktop.impl.portal.FileChooser

uninstall() {
    rm -fv "$portals/hermione.portal" "$services/$service_name"
    if [ -f "$config_file" ]; then
        if grep -q '^# written by hermione' "$config_file"; then
            rm -fv "$config_file"
            echo "removed $config_file (the system's own is used again)"
        else
            sed -i "/^$INTERFACE=/d" "$config_file"
            echo "took the FileChooser line out of $config_file"
        fi
    fi
    echo
    echo "Now: systemctl --user restart xdg-desktop-portal"
}

if [ "${1:-}" = "--uninstall" ]; then
    uninstall
    exit 0
fi

mkdir -p "$portals" "$services" "$(dirname "$config_file")"

install -m 644 "$here/hermione.portal" "$portals/hermione.portal"
echo "wrote $portals/hermione.portal"

sed "s|@HERMIONE@|$launcher|" \
    "$here/$service_name.in" > "$services/$service_name"
chmod 644 "$services/$service_name"
echo "wrote $services/$service_name  (Exec=$launcher --portal)"

# The preference list. Start from the system's file for this desktop so its
# other choices survive, and put ours at the head of the FileChooser line
# with gtk still behind it as the fallback.
if [ ! -f "$config_file" ]; then
    system=""
    for dir in $(printf '%s' "${XDG_DATA_DIRS:-/usr/local/share:/usr/share}" | tr ':' ' '); do
        if [ -f "$dir/xdg-desktop-portal/${desktop}-portals.conf" ]; then
            system="$dir/xdg-desktop-portal/${desktop}-portals.conf"
            break
        fi
    done
    {
        echo "# written by hermione/portal/install.sh"
        if [ -n "$system" ]; then
            echo "# from $system, with the FileChooser line added"
            grep -v "^$INTERFACE=" "$system"
        else
            echo "[preferred]"
            echo "default=gtk;"
        fi
    } > "$config_file"
fi

if grep -q "^$INTERFACE=" "$config_file"; then
    sed -i "s|^$INTERFACE=.*|$INTERFACE=hermione;gtk;|" "$config_file"
else
    # Under [preferred], which is the only section there is.
    if grep -q '^\[preferred\]' "$config_file"; then
        sed -i "0,/^\[preferred\]/s||[preferred]\n$INTERFACE=hermione;gtk;|" \
            "$config_file"
    else
        printf '[preferred]\n%s=hermione;gtk;\n' "$INTERFACE" >> "$config_file"
    fi
fi
echo "wrote $config_file"

# D-Bus caches the list of activatable services, so without this the first
# thing to ask for a file gets "service is not activatable" until the next
# login.
if gdbus call --session --dest org.freedesktop.DBus \
       --object-path /org/freedesktop/DBus \
       --method org.freedesktop.DBus.ReloadConfig >/dev/null 2>&1; then
    echo "told D-Bus to re-read its services"
fi

# And write the bytecode now rather than on the first dialog. The portal
# gives a backend 25 seconds to take its bus name before giving up — and
# when it gives up it exports no FileChooser at all until it is restarted,
# so a cold Python start is not merely slow, it is the difference between
# a file dialog and none.
if python3 -m compileall -q "$(dirname "$here")/hermione" >/dev/null 2>&1; then
    echo "compiled the bytecode, so the first dialog is not the slow one"
fi

echo
sed 's/^/    /' "$config_file"
echo
echo "Now: systemctl --user restart xdg-desktop-portal"
echo "     (or log out and back in — the daemon reads the backends once)"
