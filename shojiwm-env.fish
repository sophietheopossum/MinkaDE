# ShojiWM session environment. Symlinked to ~/.config/fish/conf.d/shojiwm.fish;
# ly's setup.sh runs a fish login shell and imports its exports into the
# session, so everything here reaches the compositor at startup.

# Load the ShojiWM TypeScript config straight from the repo checkout instead
# of the installed copy in ~/.config/shojiwm (super+shift+r reloads from here).
set -gx SHOJI_CONFIG $HOME/Documents/src/MinkaDE/ShojiWM/packages/config/src/index.tsx

# Uncomment to run the submodule build of xwayland-satellite instead of the
# distro package (fixes non-(0,0) layout origins; upstream 33c344f, > 0.8.1):
set -gx SHOJI_XWAYLAND_SATELLITE_PATH $HOME/Documents/src/MinkaDE/xwayland-satellite/target/release/xwayland-satellite
# activate mixed mode scaling
set -gx XWLS_LOGICAL_GEOMETRY 1
