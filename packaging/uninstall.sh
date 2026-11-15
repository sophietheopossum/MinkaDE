#!/usr/bin/env bash
# Remove a MinkaDE tester install. Leaves what it wrote in your home
# directory: your settings (~/.config/minka-settings.json), the compositor's
# logs (~/shoji_wm/), the shell's and MinkaMon's logs (~/.local/state/minka/),
# MinkaConf's cursor previews (~/.cache/minkaconf/) and your screenshots
# (~/Pictures/Screenshots/). Delete those yourself for a full wipe.

set -euo pipefail

: "${XDG_CONFIG_HOME:=$HOME/.config}"

echo ">> removing system files (sudo)"
sudo rm -f /usr/bin/shoji_wm /usr/bin/MinkaFX /usr/bin/MinkaCap /usr/bin/minka-session \
    /usr/bin/minkamon /usr/bin/xdg-desktop-portal-shojiwm \
    /usr/share/wayland-sessions/minka.desktop \
    /usr/share/applications/MinkaConf.desktop \
    /usr/share/applications/MinkaMon.desktop \
    /usr/share/xdg-desktop-portal/portals/shojiwm.portal \
    /usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.shojiwm.service \
    /usr/lib/systemd/user/xdg-desktop-portal-shojiwm.service
# /usr/lib/shojiwm and /usr/share/shojiwm hold the TypeScript runtime and
# default config that older tarballs installed.
sudo rm -rf /usr/lib/shojiwm /usr/lib/minka /usr/share/minka /usr/share/shojiwm

echo ">> removing user portal preference"
rm -f "$XDG_CONFIG_HOME/xdg-desktop-portal/shojiwm-portals.conf"

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user restart xdg-desktop-portal 2>/dev/null || true
fi

echo "done. ~/.config/minka-settings.json was kept, with the logs in ~/shoji_wm/ and"
echo "~/.local/state/minka/ and the previews in ~/.cache/minkaconf/ (remove them for a full wipe)."
if [[ -d "$XDG_CONFIG_HOME/shojiwm" ]]; then
    echo "$XDG_CONFIG_HOME/shojiwm is from an older tarball, or a ShojiWM install of its own; MinkaDE does not use it."
fi
