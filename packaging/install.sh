#!/usr/bin/env bash
# MinkaDE tester install script.
#
# Installs the Minka desktop environment: the ShojiWM compositor with Minka's
# config compiled in (plus the shaders and icons it reads), the ScreenCast
# portal backend, the patched xwayland-satellite, the MinkaFX overlay, the
# MinkaShell / MinkaConf Quickshell trees (with their shared Proustite /
# MinkaLink singletons), the MinkaMon system monitor, a "Minka" Wayland
# session entry for your display manager, and launchers for the settings and
# system-monitor apps.
#
# Usage:
#   ./install.sh                 install the prebuilt x86_64 binaries (default)
#   ./install.sh --from-source   build everything here first (needs rust)
#   ./install.sh --no-portal     skip the ScreenCast portal backend
#
# System files go to /usr (sudo is requested for that step only). Per-user
# files go to ~/.config. Re-running is safe; your settings in
# ~/.config/minka-settings.json are never touched.

set -euo pipefail

: "${XDG_CONFIG_HOME:=$HOME/.config}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

FROM_SOURCE=0
INSTALL_PORTAL=1
for arg in "$@"; do
    case "$arg" in
        --from-source) FROM_SOURCE=1 ;;
        --no-portal) INSTALL_PORTAL=0 ;;
        -h|--help)
            awk 'NR == 1{next} /^#/{sub(/^# ?/, ""); print; next} {exit}' "$0"
            exit 0
            ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

# ── dependency sanity check ──────────────────────────────────────────────
missing=()
need() { command -v "$1" >/dev/null 2>&1 || missing+=("$1${2:+ ($2)}"); }
need qs "quickshell - renders MinkaShell and MinkaConf"
need python3 "MinkaShell's per-app mute and MinkaConf's cursor previews (stdlib only)"
need Xwayland "X11 application support"
need kitty "default terminal (Super+T)"
command -v pipewire >/dev/null 2>&1 || [ -S "${XDG_RUNTIME_DIR:-/run/user/$UID}/pipewire-0" ] || missing+=("pipewire (audio + screen sharing)")
if [[ $FROM_SOURCE -eq 1 ]]; then
    need cargo "rust toolchain, required for --from-source"
fi
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "!! missing dependencies:" >&2
    printf '   - %s\n' "${missing[@]}" >&2
    echo "   see README.md for per-distro package names." >&2
    read -rp "continue anyway? [y/N] " reply
    [[ "$reply" == y* || "$reply" == Y* ]] || exit 1
fi

# ── resolve binaries ─────────────────────────────────────────────────────
if [[ $FROM_SOURCE -eq 1 ]]; then
    # The compositor is shojiwm_rs's default_config example: Minka's config
    # compiled in. --example limits a build to examples, so the portal gets
    # a build of its own.
    echo ">> building ShojiWM + portal (this can take a while)"
    (cd src/ShojiWM && cargo build --release -p shojiwm_rs --example default_config \
        && cargo build --release -p xdg-desktop-portal-shojiwm)
    echo ">> building xwayland-satellite"
    (cd src/xwayland-satellite && cargo build --release)
    echo ">> building MinkaFX"
    (cd src/MinkaFX && cargo build --release)
    echo ">> building MinkaMon + sampler"
    (cd src/MinkaMon && cargo build --release)
    (cd src/MinkaMon/sampler-rs && cargo build --release)

    SHOJI_BIN="src/ShojiWM/target/release/examples/default_config"
    PORTAL_BIN="src/ShojiWM/target/release/xdg-desktop-portal-shojiwm"
    SATELLITE_BIN="src/xwayland-satellite/target/release/xwayland-satellite"
    MINKAFX_BIN="src/MinkaFX/target/release/MinkaFX"
    MINKAMON_BIN="src/MinkaMon/target/release/minkamon"
    SAMPLER_BIN="src/MinkaMon/sampler-rs/target/release/minkamon-sampler"
else
    for f in bin/shoji_wm bin/xdg-desktop-portal-shojiwm bin/xwayland-satellite bin/MinkaFX \
             minka/MinkaMon/minkamon minka/MinkaMon/minkamon-sampler; do
        if [[ ! -e "$f" ]]; then
            echo "prebuilt file missing from tarball: $f (re-download, or use --from-source)" >&2
            exit 1
        fi
    done
    SHOJI_BIN="bin/shoji_wm"
    PORTAL_BIN="bin/xdg-desktop-portal-shojiwm"
    SATELLITE_BIN="bin/xwayland-satellite"
    MINKAFX_BIN="bin/MinkaFX"
    MINKAMON_BIN="minka/MinkaMon/minkamon"
    SAMPLER_BIN="minka/MinkaMon/minkamon-sampler"
fi

# ── system install (sudo) ────────────────────────────────────────────────
echo ">> installing system files (sudo)"
sudo install -Dm755 "$SHOJI_BIN" /usr/bin/shoji_wm
sudo install -Dm755 "$MINKAFX_BIN" /usr/bin/MinkaFX
sudo install -Dm755 "$SATELLITE_BIN" /usr/lib/minka/xwayland-satellite
sudo install -Dm755 dist/minka-session /usr/bin/minka-session
sudo install -Dm644 dist/minka.desktop /usr/share/wayland-sessions/minka.desktop

sudo rm -rf /usr/share/minka/MinkaShell /usr/share/minka/MinkaConf \
    /usr/share/minka/MinkaMon /usr/share/minka/Proustite /usr/share/minka/MinkaLink \
    /usr/share/minka/shojiwm
sudo mkdir -p /usr/share/minka
# The compositor's shaders and icons. It reads them from the checkout it was
# built from while that exists, and from here otherwise.
sudo cp -a minka/shojiwm /usr/share/minka/
sudo cp -a minka/MinkaShell /usr/share/minka/
sudo cp -a minka/MinkaConf /usr/share/minka/
sudo cp -a minka/MinkaMon /usr/share/minka/
# Shared singletons must land as siblings: each app tree carries a
# `Proustite`/`MinkaLink` symlink to `../Proustite`, which only resolves from
# /usr/share/minka/<app>/ if these sit next to it.
sudo cp -a minka/Proustite /usr/share/minka/
sudo cp -a minka/MinkaLink /usr/share/minka/
# MinkaMon looks for its sampler beside itself. A source build replaces the
# prebuilt pair that came with the tree.
sudo install -m755 "$MINKAMON_BIN" "$SAMPLER_BIN" /usr/share/minka/MinkaMon/

# Application launchers (the session entry is separate, in wayland-sessions).
sudo install -Dm644 dist/MinkaConf.desktop /usr/share/applications/MinkaConf.desktop
sudo install -Dm644 dist/MinkaMon.desktop /usr/share/applications/MinkaMon.desktop
# MinkaMon.desktop execs this rather than the binary directly, so its output
# reaches a log file instead of /dev/null.
sudo install -Dm755 dist/minkamon /usr/bin/minkamon

if [[ $INSTALL_PORTAL -eq 1 ]]; then
    sudo install -Dm755 "$PORTAL_BIN" /usr/bin/xdg-desktop-portal-shojiwm
    sudo install -Dm644 dist/shojiwm.portal /usr/share/xdg-desktop-portal/portals/shojiwm.portal
    sudo install -Dm644 dist/org.freedesktop.impl.portal.desktop.shojiwm.service \
        /usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.shojiwm.service
    sudo install -Dm644 dist/xdg-desktop-portal-shojiwm.service \
        /usr/lib/systemd/user/xdg-desktop-portal-shojiwm.service
fi

# ── per-user setup (no sudo) ─────────────────────────────────────────────
if [[ $INSTALL_PORTAL -eq 1 ]]; then
    echo ">> writing user portals.conf"
    mkdir -p "$XDG_CONFIG_HOME/xdg-desktop-portal"
    if [[ ! -e "$XDG_CONFIG_HOME/xdg-desktop-portal/shojiwm-portals.conf" ]]; then
        cat > "$XDG_CONFIG_HOME/xdg-desktop-portal/shojiwm-portals.conf" <<'EOF'
[preferred]
default=gtk
org.freedesktop.impl.portal.ScreenCast=shojiwm
EOF
    fi
    if command -v systemctl >/dev/null 2>&1 && systemctl --user is-enabled default.target >/dev/null 2>&1; then
        systemctl --user daemon-reload || true
        systemctl --user stop xdg-desktop-portal-shojiwm.service 2>/dev/null || true
        systemctl --user restart xdg-desktop-portal 2>/dev/null || true
    fi
fi

echo ""
echo "done. log out and pick the \"Minka\" session in your display manager."
echo "settings app:   qs -p /usr/share/minka/MinkaConf  (or via the start menu)"
echo "system monitor: minkamon                          (or via the start menu)"
echo "see README.md for the keybinding quickstart."
