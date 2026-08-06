#!/usr/bin/env bash
# MinkaDE tester install script.
#
# Installs the Minka desktop environment: the ShojiWM compositor + TypeScript
# runtime, the ScreenCast portal backend, the patched xwayland-satellite, the
# MinkaFX overlay, and the MinkaShell / MinkaConf / MinkaMon Quickshell trees
# (with their shared Proustite / MinkaLink singletons), plus a "Minka" Wayland
# session entry for your display manager and launchers for the settings and
# system-monitor apps.
#
# Usage:
#   ./install.sh                 install the prebuilt x86_64 binaries (default)
#   ./install.sh --from-source   build everything here first (needs rust + npm)
#   ./install.sh --no-portal     skip the ScreenCast portal backend
#
# System files go to /usr (sudo is requested for that step only). Per-user
# files go to ~/.config. Re-running is safe; an existing ~/.config/shojiwm
# user config is never overwritten.

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
need node "runs the TypeScript config runtime"
need qs "quickshell - renders MinkaShell/MinkaConf/MinkaMon"
need python3 "MinkaMon's stats sampler (stdlib only, no pip packages)"
need Xwayland "X11 application support"
need kitty "default terminal (Super+T)"
command -v pipewire >/dev/null 2>&1 || [ -S "${XDG_RUNTIME_DIR:-/run/user/$UID}/pipewire-0" ] || missing+=("pipewire (audio + screen sharing)")
if [[ $FROM_SOURCE -eq 1 ]]; then
    need cargo "rust toolchain, required for --from-source"
    need npm "required for --from-source"
fi
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "!! missing dependencies:" >&2
    printf '   - %s\n' "${missing[@]}" >&2
    echo "   see README.md for per-distro package names." >&2
    read -rp "continue anyway? [y/N] " reply
    [[ "$reply" == y* || "$reply" == Y* ]] || exit 1
fi

# ── resolve binaries + runtime ───────────────────────────────────────────
if [[ $FROM_SOURCE -eq 1 ]]; then
    echo ">> building ShojiWM + portal (this can take a while)"
    (cd src/ShojiWM && cargo build --release -p shoji_wm -p xdg-desktop-portal-shojiwm)
    echo ">> building xwayland-satellite"
    (cd src/xwayland-satellite && cargo build --release)
    echo ">> building MinkaFX"
    (cd src/MinkaFX && cargo build --release)

    SHOJI_BIN="src/ShojiWM/target/release/shoji_wm"
    PORTAL_BIN="src/ShojiWM/target/release/xdg-desktop-portal-shojiwm"
    SATELLITE_BIN="src/xwayland-satellite/target/release/xwayland-satellite"
    MINKAFX_BIN="src/MinkaFX/target/release/MinkaFX"

    echo ">> staging TypeScript runtime (npm ci)"
    RUNTIME_SRC="$(mktemp -d)/shojiwm-runtime"
    trap 'rm -rf "$(dirname "$RUNTIME_SRC")"' EXIT
    mkdir -p "$RUNTIME_SRC/packages" "$RUNTIME_SRC/tools"
    cp src/ShojiWM/package.json src/ShojiWM/package-lock.json src/ShojiWM/tsconfig.json "$RUNTIME_SRC/"
    cp -a src/ShojiWM/packages/shoji_wm "$RUNTIME_SRC/packages/"
    cp -a src/ShojiWM/packages/config "$RUNTIME_SRC/packages/"
    cp src/ShojiWM/tools/decoration-runtime.ts src/ShojiWM/tools/evaluate-decoration.ts "$RUNTIME_SRC/tools/"
    npm --prefix "$RUNTIME_SRC" ci
else
    for f in bin/shoji_wm bin/xdg-desktop-portal-shojiwm bin/xwayland-satellite bin/MinkaFX runtime/package.json; do
        if [[ ! -e "$f" ]]; then
            echo "prebuilt file missing from tarball: $f (re-download, or use --from-source)" >&2
            exit 1
        fi
    done
    SHOJI_BIN="bin/shoji_wm"
    PORTAL_BIN="bin/xdg-desktop-portal-shojiwm"
    SATELLITE_BIN="bin/xwayland-satellite"
    MINKAFX_BIN="bin/MinkaFX"
    RUNTIME_SRC="runtime"
fi

# ── system install (sudo) ────────────────────────────────────────────────
echo ">> installing system files (sudo)"
sudo install -Dm755 "$SHOJI_BIN" /usr/bin/shoji_wm
sudo install -Dm755 "$MINKAFX_BIN" /usr/bin/MinkaFX
sudo install -Dm755 "$SATELLITE_BIN" /usr/lib/minka/xwayland-satellite
sudo install -Dm755 dist/minka-session /usr/bin/minka-session
sudo install -Dm644 dist/minka.desktop /usr/share/wayland-sessions/minka.desktop

sudo rm -rf /usr/lib/shojiwm
sudo mkdir -p /usr/lib/shojiwm
sudo cp -a "$RUNTIME_SRC/." /usr/lib/shojiwm/

sudo rm -rf /usr/share/shojiwm/default-config
sudo mkdir -p /usr/share/shojiwm/default-config
sudo cp -a "$RUNTIME_SRC/packages/config/." /usr/share/shojiwm/default-config/

sudo rm -rf /usr/share/minka/MinkaShell /usr/share/minka/MinkaConf \
    /usr/share/minka/MinkaMon /usr/share/minka/Proustite /usr/share/minka/MinkaLink
sudo mkdir -p /usr/share/minka
sudo cp -a minka/MinkaShell /usr/share/minka/
sudo cp -a minka/MinkaConf /usr/share/minka/
sudo cp -a minka/MinkaMon /usr/share/minka/
# Shared singletons must land as siblings: each app tree carries a
# `Proustite`/`MinkaLink` symlink to `../Proustite`, which only resolves from
# /usr/share/minka/<app>/ if these sit next to it.
sudo cp -a minka/Proustite /usr/share/minka/
sudo cp -a minka/MinkaLink /usr/share/minka/

# Application launchers (the session entry is separate, in wayland-sessions).
sudo install -Dm644 dist/MinkaConf.desktop /usr/share/applications/MinkaConf.desktop
sudo install -Dm644 dist/MinkaMon.desktop /usr/share/applications/MinkaMon.desktop
# MinkaMon.desktop execs this rather than qs directly, so its output reaches a
# log file instead of /dev/null.
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
USER_CONFIG_DIR="$XDG_CONFIG_HOME/shojiwm"
CREATED_CONFIG=0
if [[ ! -e "$USER_CONFIG_DIR/src/index.tsx" ]]; then
    echo ">> creating user config at $USER_CONFIG_DIR"
    mkdir -p "$USER_CONFIG_DIR"
    cp -a "$RUNTIME_SRC/packages/config/." "$USER_CONFIG_DIR/"
    CREATED_CONFIG=1
else
    echo ">> keeping existing user config at $USER_CONFIG_DIR"
fi

mkdir -p "$USER_CONFIG_DIR/node_modules"
ln -sfn /usr/lib/shojiwm/packages/shoji_wm "$USER_CONFIG_DIR/node_modules/shoji_wm"
if [[ $CREATED_CONFIG -eq 1 || ! -e "$USER_CONFIG_DIR/package.json" ]]; then
    cat > "$USER_CONFIG_DIR/package.json" <<'EOF'
{
  "name": "shojiwm-user-config",
  "private": true,
  "type": "module",
  "dependencies": {
    "shoji_wm": "file:/usr/lib/shojiwm/packages/shoji_wm"
  }
}
EOF
fi
if [[ $CREATED_CONFIG -eq 1 || ! -e "$USER_CONFIG_DIR/tsconfig.json" ]]; then
    cat > "$USER_CONFIG_DIR/tsconfig.json" <<'EOF'
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "jsxImportSource": "shoji_wm",
    "strict": true,
    "verbatimModuleSyntax": true,
    "noEmit": true
  }
}
EOF
fi

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
echo "system monitor: qs -p /usr/share/minka/MinkaMon   (or via the start menu)"
echo "see README.md for the keybinding quickstart."