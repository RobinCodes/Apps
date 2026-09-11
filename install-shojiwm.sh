#!/usr/bin/env bash
# One-shot: build and install ShojiWM plus the liquid-glass config.
#
# Run:  bash ~/Projects/Programming/Apps/install-shojiwm.sh
#
# This exists because the upstream instructions are slightly out of date and
# because this machine needs the build tuned down. Everything it does is
# reversible — see "Undoing it" at the bottom.
#
#
# WHAT IS ACTUALLY BEING INSTALLED
#
# Not a terminal. ShojiWM is a Wayland compositor, and the liquid glass is a
# GLSL shader it applies to terminal windows. It does not run alongside niri:
# both are compositors, and you pick one at the login screen. niri stays
# installed and stays the default until you choose otherwise.
#
#
# WHERE THIS DIFFERS FROM THE UPSTREAM GUIDE
#
#   xwayland-satellite  The guide says to build it from source with cargo.
#                       It is packaged in Arch's `extra` repo, so this
#                       installs the package instead — same thing, no build.
#
#   --dev               The guide's default is a full --release build with
#                       LTO. On 3.4 GB of RAM that is the step most likely to
#                       thrash or get OOM-killed, because LTO holds the whole
#                       program in memory at link time. --dev selects the
#                       release-fast profile: no LTO, incremental. Slightly
#                       slower compositor, dramatically cheaper build.
#
#   CARGO_BUILD_JOBS    Cargo defaults to one rustc per core. Four concurrent
#                       rustc processes on a 3.4 GB machine is how you meet
#                       your own oom-defender. Two is the compromise.
set -euo pipefail

SHOJI_DIR="$HOME/Projects/Programming/Apps/ShojiWM"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/shojiwm"

# The build is the expensive part; keep it off the small end of the machine.
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-2}"

say() { printf '\n>> %s\n' "$*"; }

# --- preflight -------------------------------------------------------------
# Checked rather than assumed, because the failure modes are all slow: a build
# that runs for twenty minutes and then cannot find a header is a bad way to
# learn a package was missing.

[[ -d $SHOJI_DIR ]] || { echo "missing $SHOJI_DIR — clone ShojiWM there first" >&2; exit 1; }
[[ -x $SHOJI_DIR/dist/install.sh ]] || { echo "missing $SHOJI_DIR/dist/install.sh" >&2; exit 1; }

free_gb=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc '0-9')
if (( free_gb < 10 )); then
    echo "Only ${free_gb}G free on \$HOME. A release-fast build of 786 crates" >&2
    echo "wants several gigabytes in target/. Free some space first." >&2
    exit 1
fi
say "disk: ${free_gb}G free, enough to build in"

# --- 1. packages -----------------------------------------------------------
# rust             the toolchain (302 MB installed; no rustup needed)
# xorg-xwayland    the X server ShojiWM drives for X11 apps
# xwayland-satellite  what drives it — in `extra`, despite the guide
# kitty            the config applies its shader to app_id kitty or ghostty
#                  ONLY. Without one of them the glass never appears, which
#                  is a very confusing way for this to fail.
say "installing packages (sudo)"
sudo pacman -S --needed rust xorg-xwayland xwayland-satellite kitty

# --- 2. build and install --------------------------------------------------
# install.sh prompts for sudo itself, partway through, after the build. It
# keeps an existing ~/.config/shojiwm rather than overwriting it, which is why
# the liquid-glass config was cloned into place first — the installer adopts
# it and symlinks node_modules/shoji_wm at /usr/lib/shojiwm for it. There is
# no npm install step; that symlink is what satisfies the file: dependency in
# package.json.
say "building ShojiWM (this is the long part — 786 crates, 2 jobs)"
cd "$SHOJI_DIR"
./dist/install.sh --dev

# --- 3. verify -------------------------------------------------------------
say "checking the install"
fail=0
for path in /usr/bin/shoji_wm /usr/lib/shojiwm /usr/share/wayland-sessions/shojiwm.desktop; do
    if [[ -e $path ]]; then
        echo "  ok      $path"
    else
        echo "  MISSING $path" >&2
        fail=1
    fi
done

if [[ -L "$CONFIG_DIR/node_modules/shoji_wm" ]]; then
    echo "  ok      $CONFIG_DIR/node_modules/shoji_wm -> $(readlink "$CONFIG_DIR/node_modules/shoji_wm")"
else
    echo "  MISSING the node_modules symlink in $CONFIG_DIR" >&2
    fail=1
fi

if [[ -f "$CONFIG_DIR/src/water-terminal.frag" ]]; then
    echo "  ok      liquid-glass config is the active one"
else
    echo "  WARNING $CONFIG_DIR is not the liquid-glass config" >&2
fi

(( fail == 0 )) || { echo; echo "something did not land — see above" >&2; exit 1; }

cat <<'EOF'

done.

Log out of niri and pick "ShojiWM" at the greetd login screen. Or, to try it
without logging out at all, from a TTY (Ctrl+Alt+F2):

    shoji_wm --tty

Super+T opens kitty, which is the window the glass shader is applied to.
Super alone is the start menu, Super+Q closes a window, Super+S toggles tiling.

Undoing it, completely:

    cd ~/Projects/Programming/Apps/ShojiWM
    ./dist/uninstall.sh                  # compositor + portal
    ./dist/uninstall.sh --user-config    # and ~/.config/shojiwm too

niri is untouched throughout and stays available at the login screen either
way.
EOF
