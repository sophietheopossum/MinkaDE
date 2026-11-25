#!/usr/bin/env bash
# Remove a MinkaDE tester install: what the usr/ tree beside this script
# put in /usr, and what older tarballs put there. Leaves what it wrote in
# your home directory: your settings (~/.config/minka-settings.json), the
# compositor's logs (~/.local/state/shoji_wm/, and ~/shoji_wm/ from older
# builds and the hang watchdog), the shell's and MinkaMon's logs
# (~/.local/state/minka/), MinkaConf's cursor previews (~/.cache/minkaconf/)
# and your screenshots (~/Pictures/Screenshots/). Delete those yourself for
# a full wipe.

set -euo pipefail

: "${XDG_CONFIG_HOME:=$HOME/.config}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

if [[ ! -d usr ]]; then
    echo "no usr/ beside uninstall.sh: run it from the unpacked tarball" >&2
    exit 1
fi

echo ">> removing system files (sudo)"
# What install.sh copied is what usr/ holds. The directories named minka*
# are MinkaDE's alone and go whole, with anything an older install left in
# them; outside them, each file and link goes on its own.
mapfile -t trees < <(find usr -type d -name 'minka*' -prune -printf '/%p\n')
mapfile -t files < <(find usr -type d -name 'minka*' -prune -o ! -type d -printf '/%p\n')
sudo rm -rf "${trees[@]}"
sudo rm -f "${files[@]}"
# What older tarballs installed: the binaries one by one, in
# /usr/lib/shojiwm and /usr/share/shojiwm the TypeScript runtime and
# default config, and up to 0.92.0 MinkaSyu's QML window in
# /usr/share/minka-syu.
sudo rm -f /usr/bin/shoji_wm /usr/bin/MinkaFX /usr/bin/MinkaCap /usr/bin/minka-session \
    /usr/bin/minkamon /usr/bin/xdg-desktop-portal-shojiwm \
    /usr/share/wayland-sessions/minka.desktop \
    /usr/share/applications/MinkaConf.desktop \
    /usr/share/applications/MinkaMon.desktop \
    /usr/share/xdg-desktop-portal/portals/shojiwm.portal \
    /usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.shojiwm.service \
    /usr/lib/systemd/user/xdg-desktop-portal-shojiwm.service
sudo rm -rf /usr/lib/shojiwm /usr/lib/minka /usr/share/minka /usr/share/shojiwm \
    /usr/share/minka-syu

echo ">> removing user portal preference"
rm -f "$XDG_CONFIG_HOME/xdg-desktop-portal/shojiwm-portals.conf"

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user restart xdg-desktop-portal 2>/dev/null || true
fi

echo "done. ~/.config/minka-settings.json was kept, with the logs in ~/.local/state/shoji_wm/,"
echo "~/shoji_wm/ and ~/.local/state/minka/ and the previews in ~/.cache/minkaconf/ (remove them"
echo "for a full wipe)."
if [[ -d "$XDG_CONFIG_HOME/shojiwm" ]]; then
    echo "$XDG_CONFIG_HOME/shojiwm is from an older tarball, or a ShojiWM install of its own; MinkaDE does not use it."
fi
