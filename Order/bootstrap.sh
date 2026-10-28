#!/bin/sh
# A new Arch machine, brought into the Order in one command:
#
#     curl -fsSL https://raw.githubusercontent.com/RobinCodes/Apps/main/Order/bootstrap.sh | sh
#
# Each step is skipped when it is already done, so running it again is how a
# half-finished machine is finished. Root is needed once, for pacman.
#
#   1. the packages the apps and the brain run on (pacman)
#   2. a name for this machine (ORDER_MACHINE, in ~/.config/environment.d)
#   3. the apps (public) and the brain (private: needs a git credential helper
#      that can reach github.com — `gh auth login` or git-credential-manager)
#   4. `order sync`: every repository in the manifest, in its place
#   5. ~/.local/bin links and .desktop entries for every app
#   6. the sync timer (a systemd user timer, every ten minutes)
#   7. Claude Code, for "Ask Claude…" in every app
#   8. `order doctor`
set -eu

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

say "packages"
sudo pacman -S --needed --noconfirm \
    git python python-gobject python-cairo python-yaml python-numpy python-pillow \
    python-charset-normalizer python-beautifulsoup4 python-lxml python-pymupdf \
    gtk4 libadwaita poppler-glib librsvg vte4 webkitgtk-6.0 \
    gst-plugins-base gst-plugins-good gst-plugins-bad gst-plugins-ugly gst-libav \
    gst-python ffmpeg tesseract tesseract-data-eng tesseract-data-hun tesseract-data-ita \
    make gcc pkgconf
python3 -c "import docx" 2>/dev/null || \
    python3 -m pip install --user --break-system-packages -q python-docx

say "this machine's name"
name="${ORDER_MACHINE:-}"
if [ -z "$name" ]; then
    default="$(hostname | tr 'A-Z' 'a-z')"
    if [ -t 0 ] || [ -r /dev/tty ]; then
        printf "Name this machine [Enter = %s]: " "$default"
        read -r name </dev/tty || name=""
    fi
    name="${name:-$default}"
fi
name="$(printf '%s' "$name" | tr 'A-Z' 'a-z' | tr -cs 'a-z0-9-' '-' | sed 's/^-*//; s/-*$//')"
mkdir -p "$HOME/.config/environment.d"
printf 'ORDER_MACHINE=%s\n' "$name" > "$HOME/.config/environment.d/order.conf"
export ORDER_MACHINE="$name"
echo "  $name"

say "the apps and the brain"
apps="$HOME/Projects/Programming/Apps"
[ -d "$apps/.git" ] || git clone https://github.com/RobinCodes/Apps.git "$apps"
[ -d "$HOME/SecondBrain/.git" ] || git clone https://github.com/RobinCodes/SecondBrain.git "$HOME/SecondBrain"

say "every repository in the manifest"
python3 "$apps/Order/order.py" adopt || true
python3 "$apps/Order/order.py" sync

say "launchers and menu entries"
python3 "$apps/Order/order.py" apps install
mkdir -p "$HOME/.local/bin"
ln -sf "$apps/Order/order.py" "$HOME/.local/bin/order"
ln -sf "$HOME/SecondBrain/anam" "$HOME/.local/bin/anam"
make -C "$apps/Lyndon Browser" user-install >/dev/null 2>&1 || echo "  (Lyndon Browser not built: make -C \"$apps/Lyndon Browser\" user-install)"

say "the sync timer"
python3 "$apps/Order/order.py" timer install

say "Claude Code"
command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ] || {
    curl -fsSL https://claude.ai/install.sh | bash
    echo "  Run 'claude' once in a terminal to sign in."
}

say "where things stand"
python3 "$apps/Order/order.py" doctor || true
printf '\nThis machine is in the Order. Log out and in once for ORDER_MACHINE to reach every app.\n'
