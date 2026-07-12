# MinkaDE (tester preview)

A high-performance Wayland desktop environment built around the
[ShojiWM](https://github.com/bea4dev/ShojiWM) compositor, with a
Quickshell-based shell (MinkaShell), a settings app (MinkaConf), a wgpu
overlay process (MinkaFX), HDR output support, and a TypeScript-based
compositor config you can edit live.

This is a **preview for testers** — expect rough edges. Please report what
breaks, along with `/tmp/minkashell.log` and `~/shoji_wm/logs/latest.log`.

## What's in the tarball

| path       | contents                                                        |
|------------|-----------------------------------------------------------------|
| `bin/`     | prebuilt x86_64 binaries (shoji_wm, portal, xwayland-satellite, MinkaFX) |
| `runtime/` | the TypeScript config runtime, dependencies pre-installed       |
| `minka/`   | MinkaShell + MinkaConf (Quickshell QML)                         |
| `src/`     | full sources, for `--from-source` installs                      |
| `dist/`    | session entry, wrapper, portal service files                    |

## Requirements

Required at runtime:

- **nodejs** — runs the TypeScript config runtime
- **quickshell** (`qs`) — renders MinkaShell and MinkaConf
  (Arch: AUR `quickshell`; Fedora COPR / Nix packages exist; otherwise build from source)
- **pipewire + wireplumber** — audio and screen sharing
- **xorg-xwayland** — X11 app support
- **kitty** — default terminal (Super+T)
- **xdg-desktop-portal** and **xdg-desktop-portal-gtk** — file dialogs etc.
- **polkit-kde-agent** (`/usr/lib/polkit-kde-authentication-agent-1`) — auth prompts
- Mesa / working GPU drivers (the compositor is GLES-rendered, MinkaFX uses Vulkan)

Optional (specific features degrade gracefully without them):

- **hyprshot + swappy** — screenshots (Super+P)
- **cliphist + wl-clipboard** — clipboard history
- **playerctl** — media keys
- **dolphin** — file manager bind (Super+E)

For `--from-source` installs additionally: **rust** (cargo), **npm**, git and
network access (cargo fetches pinned git dependencies), plus the usual
Wayland/DRM dev headers (`libinput`, `libseat`, `libgbm`, `libEGL`, `libudev`,
`libxkbcommon`, `pipewire` development packages).

The prebuilt binaries are x86_64, built against a current rolling-release
glibc/Mesa (July 2026). If they fail to start on your distro, use
`./install.sh --from-source`.

## Install

```sh
tar xf minkade-*.tar.gz
cd minkade-*/
./install.sh                 # prebuilt binaries
# or: ./install.sh --from-source
```

Then **log out and pick the "Minka" session** in your display manager.

Uninstall with `./uninstall.sh` (keeps `~/.config/shojiwm`).

## First steps

- **Super** (tap) or **Super+A** — start menu
- **Super+T** — terminal (kitty)
- **Super+Q** — close window · **Super+M** — maximize
- **Super+S** — toggle tiling mode
- **Super+Left/Right** — focus tiles · **Super+Shift+Left/Right** — move tiles
- **Super+Ctrl+Up/Down** — switch workspace · **Super+Shift+Up/Down** — move window between workspaces
- **Alt+Tab** — cycle windows
- **Super+P** — region screenshot (hyprshot+swappy)
- **Super+Shift+R** — reload the compositor config

Settings (displays, input, keyboard layout, default apps, wallpaper):

```sh
qs -p /usr/share/minka/MinkaConf
```

Your compositor config lives at `~/.config/shojiwm/src/index.tsx` — it's
TypeScript, reloads live with Super+Shift+R, and the installer never
overwrites it once created.

## Notes for testers

- HDR: opt-in per display via the display settings (`hdr: true`); needs an
  HDR-capable output.
- X11 apps run through a patched xwayland-satellite that fixes positioning
  and mixed-scale rendering; installed at `/usr/lib/minka/xwayland-satellite`
  so it never conflicts with a distro xwayland-satellite package.
- Screen sharing (Discord/OBS/browsers) goes through the bundled
  `xdg-desktop-portal-shojiwm` ScreenCast backend.
- Logs: compositor `~/shoji_wm/logs/latest.log`, shell `/tmp/minkashell.log`,
  overlay `/tmp/minkafx.log`.