# MinkaDE (tester preview)

A high-performance Wayland desktop environment built around the
[ShojiWM](https://github.com/bea4dev/ShojiWM) compositor, with a shell
(MinkaShell, built on AntiWebUI, with its Quickshell version as the
fallback), a settings app (MinkaConf), a freeze-frame screenshot tool
(MinkaShot), a system monitor (MinkaMon), a wgpu overlay process (MinkaFX),
HDR output support, and a compositor config written in Rust and compiled
into the compositor.

This is a **preview for testers** — expect rough edges. Please report what
breaks, along with `~/.local/state/minka/minkashell.log` and
`~/shoji_wm/logs/latest.log`.

## What's in the tarball

| path            | contents                                                        |
|-----------------|-----------------------------------------------------------------|
| `bin/`          | prebuilt x86_64 binaries (shoji_wm with Minka's config compiled in, portal, xwayland-satellite, MinkaFX, MinkaCap) |
| `minka/`        | MinkaShell (prebuilt, plus its Quickshell fallback), MinkaConf + MinkaShot (Quickshell QML), MinkaMon (prebuilt), the compositor's shaders and icons (`shojiwm/`) |
| `src/`          | full sources, for `--from-source` installs                      |
| `dist/`         | session entry, wrappers, portal service files                   |

## Requirements

Required at runtime:

- **quickshell** (`qs`), with its screen-capture support — renders MinkaConf,
  MinkaShot and the shell's fallback
  (Arch: AUR `quickshell`; Fedora COPR / Nix packages exist; otherwise build from source)
- **pipewire + wireplumber** — audio and screen sharing
- **xorg-xwayland** — X11 app support
- **kitty** — default terminal (Super+T)
- **python3** with **Pillow** (Arch: `python-pillow`; Debian/Ubuntu: `python3-pil`) —
  MinkaShell's per-app mute and MinkaConf's cursor previews
- **libseat** (Arch: `seatd`; Debian/Ubuntu: `libseat1`) — the compositor
  will not start without it
- **xdg-desktop-portal** and **xdg-desktop-portal-gtk** — file dialogs etc.
- **polkit-kde-agent** (`/usr/lib/polkit-kde-authentication-agent-1`) — auth prompts
- Mesa / working GPU drivers, **Vulkan** included (the compositor is
  GLES-rendered; MinkaShell, MinkaFX and MinkaMon draw with Vulkan)
- **Noto Sans** (Arch: `noto-fonts`; Debian/Ubuntu: `fonts-noto-core`) — the
  shell's font; without it another is substituted
- shared libraries most desktops already have: libinput, libgbm, libxkbcommon,
  lcms2, xcb-util-cursor, fontconfig and the Vulkan loader.
  `ldd bin/* minka/MinkaShell/minkashell minka/MinkaMon/minkamon` names any
  that are missing, except the Vulkan loader, which is loaded at run time:
  `vulkaninfo --summary` checks that one.

Optional (specific features degrade gracefully without them):

- **hyprshot + swappy** — screenshots (Super+P)
- **cliphist + wl-clipboard** — records clipboard history (there is no picker
  yet; read it with the `cliphist` command)
- **playerctl** — media keys
- **brightnessctl** — brightness keys (only Intel laptop panels and the ASUS
  ScreenPad respond)
- **xdg-utils** — MinkaConf's default-apps page
- **libnotify** (`notify-send`) — notices, e.g. after a session the
  compositor ended because its config hung
- **dolphin** — file manager bind (Super+E)

For `--from-source` installs additionally: **rust** (cargo), **clang**
(libclang, for bindgen), git and network access (cargo fetches pinned git
dependencies), plus the usual Wayland/DRM dev headers (`wayland`,
`wayland-protocols`, `libinput`, `libseat`, `libgbm`, `libEGL`, `libudev`,
`libxkbcommon`, `pixman`, `pipewire`, `xcb-util-cursor`, `fontconfig`
development packages). The `apt-get install` line in
`.github/workflows/release.yml`, which builds the release, is the complete
list for Debian/Ubuntu.

The prebuilt binaries are x86_64, built on Ubuntu 24.04, so they need glibc
2.39 or newer. If they fail to start on your distro, use
`./install.sh --from-source`.

## Install

```sh
tar xf minkade-*.tar.gz
cd minkade-*/
./install.sh                 # prebuilt binaries
# or: ./install.sh --from-source
```

Then **log out and pick the "Minka" session** in your display manager.

Uninstall with `./uninstall.sh` (keeps your settings in
`~/.config/minka-settings.json`, and the logs).

## First steps

- **Super** (tap) or **Super+A** — start menu
- **Super+T** — terminal (kitty)
- **Super+Q** — close window · **Super+M** — maximize
- **Super+S** — toggle tiling mode
- **Super+Left/Right** — focus tiles · **Super+Shift+Left/Right** — move tiles
- **Super+Ctrl+Up/Down** — switch workspace · **Super+Shift+Up/Down** — move window between workspaces
- **Alt+Tab** — cycle windows
- **Print** — MinkaShot: freezes the screen; drag a region (or click WINDOW
  to pick a window), Enter saves to `~/Pictures/Screenshots/`, Esc cancels
- **Super+P** — region screenshot (hyprshot+swappy)

Settings (displays, input, keyboard layout, default apps, wallpaper):

```sh
qs -p /usr/share/minka/MinkaConf
```

They are saved to `~/.config/minka-settings.json`, which the installer never
touches. The compositor config itself is compiled into `shoji_wm`: to change
it, edit `src/ShojiWM/src/shojiwm_rs/examples/default_config/`, reinstall
with `./install.sh --from-source`, and start a new session (there is no live
reload).

## Notes for testers

- HDR: opt-in per display via the display settings (`hdr: true`); needs an
  HDR-capable output.
- X11 apps run through a patched xwayland-satellite that fixes positioning
  and mixed-scale rendering; installed at `/usr/lib/minka/xwayland-satellite`
  so it never conflicts with a distro xwayland-satellite package.
- Screen sharing (Discord/OBS/browsers) goes through the bundled
  `xdg-desktop-portal-shojiwm` ScreenCast backend.
- MinkaMon, the system monitor, starts a few seconds after each login. It is
  made for the ASUS ScreenPad: on other machines it opens as an ordinary
  window, which you can close.
- MinkaShell is also the session's notification daemon and system tray. If
  another notification daemon (mako, dunst, swaync) is installed, D-Bus can
  start it whenever that name is free, at login or while the shell restarts
  after a crash, and you then get its popups for the rest of the session
  (`minkashell.log` says "notifications unavailable"). Uninstall it to keep
  Minka's.
- If the shell crashes three times in a minute, its Quickshell version takes
  over for the rest of the session. To use that one from the start, add
  `export MINKA_SHELL_FRONTEND=quickshell` to `~/.profile` (the compositor
  starts the shell through a login shell, which reads it) and log in again.
- Logs: compositor `~/shoji_wm/logs/latest.log`, shell
  `~/.local/state/minka/minkashell.log`, monitor
  `~/.local/state/minka/minkamon.log`, screenshots `/tmp/minkashot.log`,
  overlay `/tmp/minkafx.log`.
