# ShojiWM session environment. Symlinked to ~/.config/fish/conf.d/shojiwm.fish;
# ly's setup.sh runs a fish login shell and imports its exports into the
# session, so everything here reaches the compositor at startup.

# Load the ShojiWM TypeScript config straight from the repo checkout instead
# of the installed copy in ~/.config/shojiwm (super+shift+r reloads from here).
set -gx SHOJI_CONFIG $HOME/Documents/src/MinkaDE/ShojiWM/packages/config/src/index.tsx

# Uncomment to run the submodule build of xwayland-satellite instead of the
# distro package (fixes non-(0,0) layout origins; upstream 33c344f, > 0.8.1):
set -gx SHOJI_XWAYLAND_SATELLITE_PATH $HOME/Documents/src/MinkaDE/xwayland-satellite/target/release/xwayland-satellite

# Run MinkaShell + MinkaFX from the repo checkouts. Without these the config
# falls back to the tarball-install locations (/usr/share/minka, /usr/bin).
set -gx MINKA_SHELL_DIR $HOME/Documents/src/MinkaDE/MinkaShell
set -gx MINKA_SHOT_DIR $HOME/Documents/src/MinkaDE/MinkaShot
set -gx MINKA_FX_BIN $HOME/Documents/src/MinkaDE/MinkaFX/target/release/MinkaFX
set -gx MINKA_CAP_BIN $HOME/Documents/src/MinkaDE/MinkaCap/target/release/MinkaCap

# KDE apps (Dolphin's open-with, kickoff-style menus) build their service
# cache from ${XDG_MENU_PREFIX}applications.menu; only the plasma- one is
# installed, and without the prefix kbuildsycoca6 silently builds a cache
# with no application tree.
set -gx XDG_MENU_PREFIX plasma-

