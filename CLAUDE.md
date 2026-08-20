# MinkaDE

Sophie's custom Wayland desktop environment. A ShojiWM (smithay) compositor contains
Quickshell apps & Rust helpers, shares palette and IPC through common
submodules. Runs on Zenbook Duo UX482EG (CachyOS, Intel xe + NVIDIA hybrid,
dual-screen "Duo" mode).

## Components (repo's git submodules)

| Submodule      | Lang                       | Role                                                                                                                                                                                                                 |
|----------------|----------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ShojiWM**    | Rust (smithay) + TS config | Compositor. **bea4dev's project, not Sophie's** — she maintains patches (e.g. an unmerged HDR pipeline), has good rapport with him, and he is actively developing it. Patching compositor core is fine and is often the only place a fix can live; the caveat is about attribution, not scope. |
| **MinkaShell** | Quickshell/QML             | Session shell: bar, dock, start menu, calendar/status/battery popovers, notifications.                                                                                                                               |
| **MinkaMon**   | Quickshell/QML + Python    | System monitor. `scripts/sampler.py` streams JSON-lines stats; main window is clickable machine schematic opening satellite instrument windows.                                                                      |
| **MinkaShot**  | Quickshell/QML             | Freeze-frame screenshot tool. Print → frozen frame + loupe crosshair → region/window capture to `~/Pictures/Screenshots/`. Uses MinkaCap for occlusion-free per-window capture.                                      |
| **MinkaConf**  | Quickshell/QML             | Settings utility.                                                                                                                                                                                                    |
| **MinkaCap**   | Rust                       | Per-window Wayland capture via ext-image-copy-capture (occlusion-free toplevel screenshots). Consumed by MinkaShot.                                                                                                  |
| **MinkaFX**    | Rust (wgpu)                | Guido-style overlay process (snap preview, future OSDs).                                                                                                                                                             |
| **Proustite**  | QML singleton              | **Shared palette.** Named after "ruby silver" mineral (scarlet that light tarnishes black) — spiritual successor to Eternal Darkness theme.                                                                          |
| **MinkaLink**  | QML singleton              | Shared NDJSON IPC client (`ShojiClient`) for ShojiWM socket. QML sibling of MinkaIPC.                                                                                                                                |
| **MinkaIPC**   | Rust                       | Non-blocking NDJSON client crate for the ShojiWM IPC socket.                                                                                                                                                         |
| **bartizan-lsp** | Rust                     | Companion LSP supplying Rust code actions clippy and rust-analyzer leave unfilled (upstream froze IDE assists Dec 2025). A dev tool, not a desktop component. Uses `ra_ap_syntax`, so fixes port to upstream assists later. |
| **MinkaPKG**   | PKGBUILDs                  | Arch packaging for the above.                                                                                                                                                                                        |

`shoji-bar-2` is retired predecessor shell. `xwayland-satellite` is patched
XWayland bridge.

## Theming (Proustite)

- **No literal colors in widgets** — every color goes through `Theme` token. Each
  app's `services/Theme.qml` is a thin facade re-exporting `Proustite` tokens plus
  app-specific extras (MinkaShell barBg/barHeight, MinkaMon seriesPalette, MinkaShot scrim).
- `red` is `#FF0000`; the old MinkaMon `glow` token merged into `red`.
- **Shared submodules are consumed via symlink into each app's config root**
  (`MinkaShell/Proustite -> ../Proustite`, same for MinkaLink). Quickshell only honours
  qmldir singleton registration for paths *inside* the shell root, so `import "../Proustite"`
  works but `import "../../Proustite"` loads files as plain components (undefined tokens).
- **Never name shared singleton after a Qt type** — QtQuick's built-in `Palette`
  silently shadowed our singleton; that's why it's `Proustite`, not `Palette`.

## Build / lint / run

```sh
# Lint QML (from app's dir; uses system qmllint)
qmllint shell.qml services/*.qml modules/*.qml

# Type-check ShojiWM config (from ShojiWM/) — currently exits 0 clean
./node_modules/.bin/tsc --noEmit -p packages/config

# Test the compositor. shoji_wm is a BINARY crate: `--lib` fails with
# "no library targets found", and a piped `| tail` masks cargo's exit code.
cargo test -p shoji_wm --bins ssd::evaluator

# Syntax-check the sampler
python3 -m py_compile MinkaMon/scripts/sampler.py

# Run a Quickshell app — ALWAYS use an ABSOLUTE path (relative `.` breaks from other cwds)
qs -p /home/seirra/Documents/src/MinkaDE/MinkaShell
```

- **The running compositor is not the worktree.** `/usr/bin/shoji_wm` is whatever was
  last installed, and the session holds that inode for its whole life — so HEAD can be
  days ahead of what is actually running. Check before drawing conclusions from source:
  `ls -l /usr/bin/shoji_wm`, `ps -o lstart= -p $(pgrep -x shoji_wm)`, and
  `readlink /proc/<pid>/exe` — a trailing `(deleted)` means a rebuild has replaced the
  file and the process is still running the old one. Same for `bartizan-lsp`, which the
  editor launches straight out of `target/release/` with no copy on `PATH`: every change
  needs `cargo build --release` **and** an LSP restart. `cargo test` alone builds only
  the test binary and changes nothing that is running.
- **ShojiWM config reload is manual: Super+Shift+R.** There is no IPC reload path; a
  config edit does not take effect until Sophie reloads. `packages/config/**` is live
  from the repo, but `packages/shoji_wm/**` and `tools/decoration-runtime.ts` are
  consumed from the *installed* `/usr/lib/shojiwm` — those need a reinstall, and the
  binary and runtime should be installed together.
- Quickshell **live-reloads on every file save.** A broken intermediate QML save wedges
  the running instance (dead clicks) until restart — keep every save-point valid.
- Component-*file* edits (new/renamed QML components) may need a full restart, not just
  a live-reload. Don't promise live-reload immediacy.

## Config paths & environment

- **Live ShojiWM config = `ShojiWM/packages/config/src/index.tsx`** via `$SHOJI_CONFIG`.
- `shojiwm-env.fish` (symlinked to fish conf.d) exports the repo-checkout overrides:
  `SHOJI_CONFIG`, `MINKA_SHELL_DIR`, `MINKA_SHOT_DIR`, `MINKA_FX_BIN`,
  `SHOJI_XWAYLAND_SATELLITE_PATH`, `XWLS_LOGICAL_GEOMETRY`.
- The ShojiWM IPC is an NDJSON Unix socket at
  `$XDG_RUNTIME_DIR/shojiwm-$WAYLAND_DISPLAY.sock`. Query it live with `wayland-info`
  for protocol support (ask the running compositor, don't grep source).
  `{"id":1,"method":"workspaces.get"}` returns every window with its `focused`,
  `maximized` and `minimized` flags — the only way to observe focus from outside, since
  the compositor logs focus changes at `debug!` only. Read in a loop: the server drops a
  half-closed connection, so a bare `socat` often returns nothing. Methods are registered
  in `packages/config/src/index.tsx` (`WORKSPACE_IPC.handle`); there is no `windows.list`.
- **Session logs in `~/shoji_wm/logs` are UTC**, while `journalctl` and `ls` show local
  time. Comparing them without converting has produced hours of phantom timeline.

## Conventions

- **Dates: `d/M/yyyy`, leading zeros stripped** (e.g. `7/7/2026`).
- Keep the **bar's IPC health dot** — deliberate design detail.
- **Sophie commits edits in real time via GitKraken.** HEAD moves under you;
  every save-point may become a commit. she may format proposed edits
  ("user modified your proposed changes" = intentional; re-read before editing the region again).
- When restarting session apps from the tool shell, **scrub the env first**
  (`SHELL=/bin/fish`, drop `CLAUDE*`/`AI_AGENT*`, cwd `$HOME`) — a leaked bash SHELL
  makes her terminals open bash instead of fish. Kill test instances by **exact PID**,
  never `pkill -f "qs -p ."` (the `.` is a regex wildcard that matched her live shell).