# ShojiWM session environment. Symlinked to ~/.config/fish/conf.d/shojiwm.fish;
# ly's setup.sh runs a fish login shell and imports its exports into the
# session, so everything here reaches the compositor at startup.

# The session runs the Rust config, compiled into MinkaDE's one binary (the
# minkade package's /usr/bin/minka; /usr/bin/shoji_wm is a link to it), which
# ignores SHOJI_CONFIG. Uncomment this when rolling back to the TypeScript
# build (ShojiWM's dist/install.sh --dev without --rust-config), so it loads
# the repo checkout instead of the installed copy in ~/.config/shojiwm
# (super+shift+r reloads from here).
# set -gx SHOJI_CONFIG $HOME/Documents/src/MinkaDE/ShojiWM/packages/config/src/index.tsx

# The submodule's xwayland-satellite (fixes non-(0,0) layout origins; upstream
# 33c344f, > 0.8.1), as a process of its own: part of minka, and installed by
# the minkade package out of PATH, so it never shadows a distro
# xwayland-satellite. Without this, ShojiWM runs the copy built into it on a
# thread.
set -gx SHOJI_XWAYLAND_SATELLITE_PATH /usr/lib/minka/xwayland-satellite

# Run the Minka apps from the repo checkouts. Without these the config falls
# back to the tarball-install locations (/usr/share/minka, /usr/bin).
set -gx MINKA_SHELL_DIR $HOME/Documents/src/MinkaDE/MinkaShell
# The shell's launcher (MinkaShell/dist/minkashell) runs the shell from the
# installed /usr/bin/minka (MINKA_BIN names another build to try).
set -gx MINKA_SHOT_DIR $HOME/Documents/src/MinkaDE/MinkaShot
# MinkaFX and MinkaCap are part of minka too: the config and MinkaShot run
# the package's /usr/bin/MinkaFX and /usr/bin/MinkaCap unless MINKA_FX_BIN or
# MINKA_CAP_BIN names something else.
# MinkaMon's launcher (which owns the logging) from the checkout, and the
# directory it is given, from which it finds a checkout build to try
# ($MINKA_MON_DIR/../target/...) when MINKA_BIN is unset and there is no
# /usr/bin/minka. The package installs the same launcher as /usr/bin/minkamon.
set -gx MINKA_MON_BIN $HOME/Documents/src/MinkaDE/MinkaMon/dist/minkamon
set -gx MINKA_MON_DIR $HOME/Documents/src/MinkaDE/MinkaMon

# KDE apps (Dolphin's open-with, kickoff-style menus) build their service
# cache from ${XDG_MENU_PREFIX}applications.menu; only the plasma- one is
# installed, and without the prefix kbuildsycoca6 silently builds a cache
# with no application tree.
set -gx XDG_MENU_PREFIX plasma-

#prevents duplicate hypercat work
set -gx HC_SHARED_WORKSPACE 1

# The satellite at HEAD only supersamples X11 apps (render at the largest output's scale, let
# the compositor downscale) when this is set; unset it picks the SMALLEST scale instead.
set -gx XWAYLAND_SATELLITE_COMPOSITOR_SCALING 1
# ShojiWM sends the satellite's stdout/stderr to /dev/null unless this is set; with it they go
# to ~/shoji_wm/logs/ (path override: SHOJI_XWAYLAND_SATELLITE_LOG_PATH).
set -gx SHOJI_XWAYLAND_SATELLITE_LOG 1
