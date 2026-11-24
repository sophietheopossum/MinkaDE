#!/usr/bin/env bash
# MinkaDE tester install script.
#
# Installs the Minka desktop environment. Most of it is one binary, minka,
# which is the ShojiWM compositor with Minka's config compiled in, its
# ScreenCast portal backend, the patched xwayland-satellite, the MinkaFX
# overlay, MinkaCap, the MinkaShell shell, the MinkaMon system monitor and
# the other Minka apps, each run through a link named after it. Beside it go
# the compositor's shaders and icons, the QML apps (MinkaConf, MinkaShot and
# MinkaLedger's window) with the shared Proustite / MinkaLink singletons, a
# "Minka" Wayland session entry for your display manager, and the apps'
# launchers.
#
# Usage:
#   ./install.sh                 install the prebuilt x86_64 binary (default)
#   ./install.sh --from-source   build minka here first (needs rust)
#   ./install.sh --no-portal     skip the ScreenCast portal backend
#
# System files go to /usr (sudo is requested for that step only): the
# tarball's usr/ tree, as it stands. Per-user files go to ~/.config.
# Re-running is safe; your settings in ~/.config/minka-settings.json are
# never touched.

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
need qs "quickshell - renders MinkaConf, MinkaShot and MinkaLedger's window"
need python3 "MinkaShell's per-app mute and MinkaConf's cursor previews"
if command -v python3 >/dev/null 2>&1 && ! python3 -c 'import PIL' 2>/dev/null; then
    missing+=("python Pillow (MinkaConf's cursor previews; without it no cursor themes are listed)")
fi
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

# ── minka ────────────────────────────────────────────────────────────────
# The prebuilt binary is in usr/, with everything else that goes to /usr.
# --from-source builds one from src/ to install in its place.
if [[ ! -x usr/bin/minka ]]; then
    echo "usr/bin/minka is missing from the tarball: re-download it" >&2
    exit 1
fi
if [[ $FROM_SOURCE -eq 1 ]]; then
    # minka links the compositor, its portal, xwayland-satellite and every
    # Minka app. Its crates are one Cargo workspace (src/Cargo.toml), which
    # reaches ShojiWM, xwayland-satellite, smithay and AntiWebUI by path.
    echo ">> building minka (this can take a while)"
    (cd src && cargo build --release -p minka)
fi

# ── system install (sudo) ────────────────────────────────────────────────
echo ">> installing system files (sudo)"
# The directories named minka* (/usr/share/minka, /usr/lib/minka and the
# like) are MinkaDE's alone. They go first, so a file an older install left
# in one does not outlive this one: a removed QML file would keep loading.
mapfile -t trees < <(find usr -type d -name 'minka*' -prune -printf '/%p\n')
sudo rm -rf "${trees[@]}"
# And the ones older tarballs installed that this one no longer has:
# MinkaSyu's QML window, up to 0.92.0.
sudo rm -rf /usr/share/minka-syu

# --no-portal leaves out the portal backend: its link, the portal file
# xdg-desktop-portal reads, and the D-Bus and systemd files that start it.
skip=()
if [[ $INSTALL_PORTAL -eq 0 ]]; then
    for f in bin/xdg-desktop-portal-shojiwm share/xdg-desktop-portal/portals/shojiwm.portal \
             share/dbus-1/services/org.freedesktop.impl.portal.desktop.shojiwm.service \
             lib/systemd/user/xdg-desktop-portal-shojiwm.service; do
        skip+=("--exclude=./$f")
    done
fi
# usr/ is copied as it stands, links and all, through tar rather than cp.
# tar replaces a file in use (cp fails on /usr/bin/minka with "Text file
# busy" in a running Minka session) and one an older tarball installed where
# a link goes now. --mode gives every file and directory 755 or 644,
# whatever umask the tarball was unpacked with, --no-same-owner makes them
# root's, and --no-overwrite-dir leaves /usr's own directories as they are
# (cp -a would give /usr itself the unpacking user's owner and mode).
tar -C usr --mode=u=rwX,go=rX "${skip[@]}" -cf - . \
    | sudo tar -C /usr -xf - --no-same-owner --no-overwrite-dir
if [[ $FROM_SOURCE -eq 1 ]]; then
    # In place of the prebuilt one. The links to it are the same.
    sudo install -m755 src/target/release/minka /usr/bin/minka
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

# Older tarballs installed the TypeScript runtime and config, which nothing
# here reads any more. So does ShojiWM's own installer, and ~/.config/shojiwm
# may be the tester's own ShojiWM config: they are named, never removed.
leftovers=()
for old in /usr/lib/shojiwm /usr/share/shojiwm "$XDG_CONFIG_HOME/shojiwm"; do
    if [[ -e "$old" ]]; then
        leftovers+=("$old")
    fi
done
if [[ ${#leftovers[@]} -gt 0 ]]; then
    echo ""
    echo "older MinkaDE tarballs, or a ShojiWM install of its own, put these here;"
    echo "this install does not use them (~/.config/shojiwm may hold your own ShojiWM config):"
    printf '   %s\n' "${leftovers[@]}"
fi

echo ""
echo "done. log out and pick the \"Minka\" session in your display manager."
echo "settings app:   qs -p /usr/share/minka/MinkaConf  (or via the start menu)"
echo "system monitor: minkamon                          (or via the start menu)"
echo "see README.md for the keybinding quickstart."
