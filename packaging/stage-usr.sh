#!/usr/bin/env bash
# Lay out everything MinkaDE installs, under DEST/usr, from this checkout.
#
# Usage: packaging/stage-usr.sh DEST [MINKA]
#
# MINKA is MinkaDE's one binary (default: target/release/minka, as the
# minkade package builds it). This is the one description of the installed
# layout: MinkaPKG/minkade's package() runs it into $pkgdir.
#
#   /usr/bin/minka                  the binary: compositor, portal, satellite, apps
#   /usr/bin/<name>                 a link to it for every name it answers to
#                                   (`minka --names`), except these two:
#   /usr/bin/minkamon               MinkaMon's launcher, which execs
#                                   minka-keep --once to log its output
#   /usr/lib/minka/xwayland-satellite
#                                   out of PATH, so it never shadows a distro
#                                   xwayland-satellite (minka-session and
#                                   shojiwm-env.fish point ShojiWM at it)
#   /usr/bin/minka-session, /usr/share/wayland-sessions/minka.desktop
#   /usr/lib/minka/minka-keep       the launcher that logs a session app's
#                                   output and restarts it if it dies (the
#                                   session config starts MinkaShot's daemon
#                                   through it, and the shell's and
#                                   MinkaMon's launchers installed here exec
#                                   it)
#   /usr/share/minka/shojiwm        the compositor's shaders and icons, read
#                                   once the checkout it was built from is gone
#   /usr/share/minka/<App>          the shell's and MinkaMon's assets (and the
#                                   shell's scripts and launcher), and the QML
#                                   trees still installed (MinkaConf and
#                                   MinkaLedger's window) beside Proustite and
#                                   MinkaLink: MinkaConf's symlink reaches
#                                   ../Proustite, and MinkaLedger's window has
#                                   its own copies of both
#   the portal backend's D-Bus, portal and systemd files, MinkaSyu's units,
#   MinkaThermal's unit, and the apps' desktop entries.
set -euo pipefail

DEST="${1:?usage: packaging/stage-usr.sh DEST [MINKA]}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MINKA="${2:-$REPO_ROOT/target/release/minka}"
DEST="$(mkdir -p "$DEST" && cd "$DEST" && pwd)"
cd "$REPO_ROOT"

[[ -x "$MINKA" ]] || { echo "stage-usr.sh: no minka binary at $MINKA" >&2; exit 1; }
mapfile -t NAMES < <("$MINKA" --names)
[[ ${#NAMES[@]} -gt 0 ]] || { echo "stage-usr.sh: $MINKA --names listed nothing" >&2; exit 1; }

USR="$DEST/usr"
SHARE="$USR/share/minka"
# Tree copies never carry VCS state, IDE state or cargo's builds.
EXCLUDES=(--exclude .git --exclude .idea --exclude target)

echo ">> the binary and its names"
install -Dm755 "$MINKA" "$USR/bin/minka"
install -d "$USR/lib/minka"
for name in "${NAMES[@]}"; do
    case "$name" in
        minkamon) ;;
        xwayland-satellite) ln -sfn ../../bin/minka "$USR/lib/minka/xwayland-satellite" ;;
        *) ln -sfn minka "$USR/bin/$name" ;;
    esac
done
install -Dm755 MinkaMon/dist/minkamon "$USR/bin/minkamon"

echo ">> session"
install -Dm755 packaging/minka-session "$USR/bin/minka-session"
install -Dm755 packaging/minka-keep "$USR/lib/minka/minka-keep"
install -Dm644 packaging/minka.desktop "$USR/share/wayland-sessions/minka.desktop"

echo ">> compositor assets"
ASSET_ROOT=ShojiWM/src/shojiwm_rs/examples/default_config/assets
install -d "$SHARE/shojiwm"
(cd "$ASSET_ROOT" && cp --parents shaders/*.frag icons/*.svg "$SHARE/shojiwm/")

echo ">> app trees"
# The shell's and MinkaMon's trees now hold only what the Rust apps read at
# run time (assets/, the shell's scripts/); MinkaConf's is still QML.
# MinkaShot has no tree: its Rust reads nothing from here. dist/ is left out
# (what is needed from it is installed below), and so is the Rust of the apps
# that are in minka. Anchored patterns: each tree's own top level only.
RUST=(--exclude '/*/src' --exclude '/*/sampler-rs' --exclude '/*/Cargo.toml' --exclude '/*/build.rs')
rsync -a "${EXCLUDES[@]}" "${RUST[@]}" --exclude '/*/dist' \
    MinkaShell MinkaConf MinkaMon "$SHARE/"
rsync -a "${EXCLUDES[@]}" "${RUST[@]}" Proustite "$SHARE/"
rsync -a "${EXCLUDES[@]}" MinkaLink "$SHARE/"
install -Dm755 MinkaShell/dist/minkashell "$SHARE/MinkaShell/dist/minkashell"
# MinkaLedger's window sits elsewhere than beside the shared singletons, so
# -L copies what its Proustite/MinkaLink links point at.
cp -rL MinkaLedger/ui "$SHARE/MinkaLedger"
find "$SHARE/MinkaLedger" -name .git -delete
# Its Proustite copy came with the crate that minka builds from it.
rm -rf "$SHARE/MinkaLedger/Proustite/"{Cargo.toml,build.rs,src}
find "$SHARE/MinkaLedger" -type d -exec chmod 755 {} +
find "$SHARE/MinkaLedger" -type f -exec chmod 644 {} +

echo ">> desktop entries, units, portal"
APPS="$USR/share/applications"
install -Dm644 MinkaConf/dist/MinkaConf.desktop "$APPS/MinkaConf.desktop"
install -Dm644 MinkaMon/dist/MinkaMon.desktop "$APPS/MinkaMon.desktop"
install -Dm644 MinkaLedger/dist/MinkaLedger.desktop "$APPS/MinkaLedger.desktop"
install -Dm644 MinkaSyu/dist/minka-syu.desktop "$APPS/minka-syu.desktop"
install -Dm644 MinkaThermal/dist/minka-thermal.desktop "$APPS/minka-thermal.desktop"
UNITS="$USR/lib/systemd/user"
install -Dm644 MinkaSyu/dist/minka-syu-check.service "$UNITS/minka-syu-check.service"
install -Dm644 MinkaSyu/dist/minka-syu-check.timer "$UNITS/minka-syu-check.timer"
install -Dm644 MinkaThermal/dist/minka-thermal.service "$UNITS/minka-thermal.service"
install -Dm644 ShojiWM/dist/xdg-desktop-portal-shojiwm.service "$UNITS/xdg-desktop-portal-shojiwm.service"
install -Dm644 ShojiWM/dist/shojiwm.portal "$USR/share/xdg-desktop-portal/portals/shojiwm.portal"
install -Dm644 ShojiWM/dist/org.freedesktop.impl.portal.desktop.shojiwm.service \
    "$USR/share/dbus-1/services/org.freedesktop.impl.portal.desktop.shojiwm.service"
install -Dm644 MinkaSyu/README.md "$USR/share/doc/minkade/MinkaSyu.md"
