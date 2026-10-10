# MinkaDE

Sophie's custom Wayland desktop environment. A ShojiWM (smithay) compositor contains
Quickshell apps & Rust helpers, shares palette and IPC through common
submodules. Runs on Zenbook Duo UX482EG (CachyOS, Intel xe + NVIDIA hybrid,
dual-screen "Duo" mode).

## Components (repo's git submodules)

| Submodule      | Lang                       | Role                                                                                                                                                                                                                 |
|----------------|----------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ShojiWM**    | Rust (smithay) + Rust config | Compositor. **bea4dev's project, not Sophie's** — she maintains patches (e.g. an unmerged HDR pipeline), has good rapport with him, and he is actively developing it. Patching compositor core is fine and is often the only place a fix can live; the caveat is about attribution, not scope. |
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
# Lint QML (from the app's dir — MinkaLedger's is ui/). ABSOLUTE path: qt5-declarative
# owned /usr/bin/qmllint and was removed 29/8/2026, so there is no qmllint on PATH at
# all now. Never pipe this: like cargo below, `| head` makes $? report head, and the
# Qt5 binary that used to be first on PATH failed with a BARE 255 and no message.
# `find`, not globs: MinkaShell keeps its modules in modules/<sub>/, so `modules/*.qml`
# matches nothing there (fish refuses to run it; bash lints 6 of 26 files, exits 255).
# `--absolute-path` is hidden from --help but real: without it Qt 6.12 prints a FALSE
# "not declared as singleton in qmldir" for most `pragma Singleton` files (10/10/2026).
# Qt 6.12's TEXT output also drops every warning a file logged before a function or
# binding with a loop in it (MinkaShell prints 17 of its 28): add `--json -` after
# `-I .` for the complete list. Warnings alone exit 0; a syntax error exits 1.
find shell.qml services modules -name '*.qml' \
    -exec /usr/lib/qt6/bin/qmllint --absolute-path -I . {} +

# Check the live Rust config, its tests included, without building (from ShojiWM/)
cargo check -p shojiwm_rs --example default_config --profile test

# Test the Rust config and the compositor core (from ShojiWM/). Hide the live session
# first (mkdir the dir once): a config runtime under test binds the IPC socket named
# after $WAYLAND_DISPLAY and can replace the session's own (15/9/2026). A piped
# `| tail` masks cargo's exit code.
env -u WAYLAND_DISPLAY XDG_RUNTIME_DIR=$HOME/.cache/claude-builds/xdg-test \
    cargo test -p shojiwm_rs --lib --tests --example default_config
env -u WAYLAND_DISPLAY XDG_RUNTIME_DIR=$HOME/.cache/claude-builds/xdg-test \
    cargo test -p shojiwm_lib --lib

# The TypeScript config is the rollback (see "Config paths" below). Type-check it, and
# test its runtime crate, with:
./node_modules/.bin/tsc --noEmit -p packages/config
env -u WAYLAND_DISPLAY XDG_RUNTIME_DIR=$HOME/.cache/claude-builds/xdg-test \
    cargo test -p shoji_wm --lib evaluator

# Syntax-check the sampler
python3 -m py_compile MinkaMon/scripts/sampler.py

# Run a Quickshell app — ALWAYS use an ABSOLUTE path (relative `.` breaks from other cwds)
qs -p /home/seirra/Documents/src/MinkaDE/MinkaShell

# Test MinkaLedger against a THROWAWAY book, never the default one. The default
# (~/.local/share/minka-ledger/book.db) is Sophie's real finances; a probe that
# writes there leaves accounts and imported statements mixed into her data.
env MINKA_LEDGER_BIN=$PWD/MinkaLedger/target/debug/minka-ledger \
    MINKA_LEDGER_DB=$HOME/.cache/minka-ledger/test.db \
    qs -p /home/seirra/Documents/src/MinkaDE/MinkaLedger/ui
# and for the core alone:
./target/debug/minka-ledger --db ~/.cache/minka-ledger/test.db

# MinkaLedger ships a NEW CORE METHOD? cargo build --release, or her app will not have it.
# Her launcher runs target/release/minka-ledger; `cargo build` and `cargo test` only touch
# the debug binary, so a method added and tested against debug answers
# "no such method: <name>" in her window. She must then RESTART MinkaLedger: the running
# core holds the old inode (readlink /proc/<pid>/exe shows a trailing "(deleted)").
cargo build --release
```

- **The running compositor is not the worktree.** `/usr/bin/shoji_wm` is whatever was
  last installed (`/usr/bin/shoji_wm --version` ends in `(minka)` for the Rust config
  build), and the session holds that inode for its whole life — so HEAD can be
  days ahead of what is actually running. Check before drawing conclusions from source:
  `ls -l /usr/bin/shoji_wm`, `ps -o lstart= -p $(pgrep -x shoji_wm)`, and
  `readlink /proc/<pid>/exe` — a trailing `(deleted)` means a rebuild has replaced the
  file and the process is still running the old one. Same for `bartizan-lsp`, which the
  editor launches straight out of `target/release/` with no copy on `PATH`: every change
  needs `cargo build --release` **and** an LSP restart. `cargo test` alone builds only
  the test binary and changes nothing that is running.
- **The Rust config cannot reload in place: there is no Super+Shift+R.** An edit under
  `ShojiWM/src/shojiwm_rs/examples/default_config/` takes effect only after
  `dist/install.sh --dev --no-config --no-portal --rust-config --expect-runtime=minka`
  (VS Code task "shojiwm: install") and a new session. The compositor serving the session it runs from
  is kept as `/usr/lib/shojiwm/shoji_wm.previous` (from a tty, the existing one is kept). The portal is not part of it: it runs
  from `ShojiWM/target/release` through a systemd user `override.conf`. Its shaders and icons are read at runtime from
  `ShojiWM/packages/config` (an asset root compiled in from the build path), so keep the
  checkout where it is. `dist/install.sh --dev` without `--rust-config` puts the
  TypeScript build back. That build hot-reloads on
  Super+Shift+R from `packages/config/**`, while `packages/shoji_wm/**` and
  `tools/decoration-runtime.ts` come from the *installed* `/usr/lib/shojiwm`.
- Quickshell **live-reloads on every file save.** A broken intermediate QML save wedges
  the running instance (dead clicks) until restart — keep every save-point valid.
- Component-*file* edits (new/renamed QML components) may need a full restart, not just
  a live-reload. Don't promise live-reload immediacy.

## Config paths & environment

- **Live ShojiWM config = the Rust port in `ShojiWM/src/shojiwm_rs/examples/default_config/`**
  (`main.rs` plus `minka/*.rs`), compiled into `/usr/bin/shoji_wm` since 6/10/2026.
  `ShojiWM/packages/config/src` (TypeScript) is the rollback config: only the TypeScript
  build reads it, through `$SHOJI_CONFIG`, and the Rust build ignores that variable.
- `shojiwm-env.fish` (symlinked to fish conf.d as `shojiwm.fish`) exports the
  repo-checkout overrides: `MINKA_SHELL_DIR`, `MINKA_SHOT_DIR`, `MINKA_FX_BIN`,
  `MINKA_CAP_BIN`, `MINKA_MON_BIN`, `MINKA_MON_DIR`, `SHOJI_XWAYLAND_SATELLITE_PATH`.
  Its `SHOJI_CONFIG` line is commented out, for a TypeScript rollback.
- The ShojiWM IPC is an NDJSON Unix socket at
  `$XDG_RUNTIME_DIR/shojiwm-$WAYLAND_DISPLAY.sock`. Query it live with `wayland-info`
  for protocol support (ask the running compositor, don't grep source).
  `{"id":1,"method":"workspaces.get"}` returns every window with its `focused`,
  `maximized` and `minimized` flags — the only way to observe focus from outside, since
  the compositor logs focus changes at `debug!` only. Read in a loop: the server drops a
  half-closed connection, so a bare `socat` often returns nothing. Methods are registered
  in the Rust config's `minka/workspace_ipc.rs` (`server.handle_with_client` and
  `ipc.command`; `settings.*` in `minka/settings.rs`); there is no `windows.list`.
  `main.rs` only wires the `minka/` modules together, and the order of its `configure_*`
  calls is significant.
- **Session logs in `~/shoji_wm/logs` are UTC**, while `journalctl` and `ls` show local
  time. Comparing them without converting has produced hours of phantom timeline.

## Conventions

- **Dates: `d/M/yyyy`, leading zeros stripped** (e.g. `7/7/2026`).
- Keep the **bar's IPC health dot** — deliberate design detail.
- **Sophie commits edits in real time via GitKraken.** HEAD moves under you;
  every save-point may become a commit. she may format proposed edits
  ("user modified your proposed changes" = intentional; re-read before editing the region again).
- When restarting session apps from the tool shell, **scrub the env first**
  (`SHELL=/bin/fish`, drop `Codex*`/`AI_AGENT*`, cwd `$HOME`) — a leaked bash SHELL
  makes her terminals open bash instead of fish. Kill test instances by **exact PID**,
  never `pkill -f "qs -p ."` (the `.` is a regex wildcard that matched her live shell).