#!/usr/bin/env bash
# Remove a MinkaDE tester install. Leaves ~/.config/shojiwm (your config and
# settings) in place; delete that directory yourself if you want a full wipe.

set -euo pipefail

: "${XDG_CONFIG_HOME:=$HOME/.config}"

echo ">> removing system files (sudo)"
sudo rm -f /usr/bin/shoji_wm /usr/bin/MinkaFX /usr/bin/minka-session \
    /usr/bin/xdg-desktop-portal-shojiwm \
    /usr/share/wayland-sessions/minka.desktop \
    /usr/share/applications/MinkaConf.desktop \
    /usr/share/applications/MinkaMon.desktop \
    /usr/share/xdg-desktop-portal/portals/shojiwm.portal \
    /usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.shojiwm.service \
    /usr/lib/systemd/user/xdg-desktop-portal-shojiwm.service
sudo rm -rf /usr/lib/shojiwm /usr/lib/minka /usr/share/minka /usr/share/shojiwm

echo ">> removing user portal preference"
rm -f "$XDG_CONFIG_HOME/xdg-desktop-portal/shojiwm-portals.conf"

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user restart xdg-desktop-portal 2>/dev/null || true
fi

echo "done. ~/.config/shojiwm was kept (remove manually for a full wipe)."