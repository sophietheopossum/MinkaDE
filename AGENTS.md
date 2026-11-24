# MinkaDE

Sophie's custom Wayland desktop environment. A ShojiWM (smithay) compositor contains
Quickshell apps & Rust helpers, shares palette and IPC through common
submodules. Runs on Zenbook Duo UX482EG (CachyOS, Intel xe + NVIDIA hybrid,
dual-screen "Duo" mode).

## Components (repo's git submodules)

| Submodule      | Lang                       | Role                                                                                                                                                                                                                 |
|----------------|----------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **ShojiWM**    | Rust (smithay) + Rust config | Compositor. **bea4dev's project, not Sophie's** — she maintains patches (e.g. an unmerged HDR pipeline), has good rapport with him, and he is actively developing it. Patching compositor core is fine and is often the only place a fix can live; the caveat is about attribution, not scope. |
| **MinkaShell** | AntiWebUI (Rust)           | Session shell: bar, dock, start menu, calendar/status/battery popovers, notifications. `dist/minkashell` runs it from MinkaDE's one binary (`minka minkashell`), logs to `~/.local/state/minka/minkashell.log`, and restarts it when it dies (after 1, 2, 4, 8, 16, then every 30 s). There is no Quickshell fallback (24/11/2026). |
| **MinkaMon**   | AntiWebUI (Rust)           | System monitor. `sampler-rs/` (`minkamon-sampler`) streams JSON-lines stats; main window is clickable machine schematic opening satellite instrument windows. |
| **MinkaShot**  | Quickshell/QML             | Freeze-frame screenshot tool. Print → frozen frame + loupe crosshair → region/window capture to `~/Pictures/Screenshots/`. Uses MinkaCap for occlusion-free per-window capture.                                      |
| **MinkaConf**  | Quickshell/QML             | Settings utility.                                                                                                                                                                                                    |
| **MinkaCap**   | Rust                       | Per-window Wayland capture via ext-image-copy-capture (occlusion-free toplevel screenshots). Consumed by MinkaShot.                                                                                                  |
| **MinkaFX**    | Rust (wgpu)                | Guido-style overlay process (snap preview, future OSDs).                                                                                                                                                             |
| **Proustite**  | QML singleton + Rust crate | **Shared palette.** Named after "ruby silver" mineral (scarlet that light tarnishes black) — spiritual successor to Eternal Darkness theme. The `proustite` crate gives the AntiWebUI apps the same tokens, read from `Proustite.qml` at build time.                                                                          |
| **MinkaLink**  | QML singleton              | Shared NDJSON IPC client (`ShojiClient`) for ShojiWM socket. QML sibling of MinkaIPC.                                                                                                                                |
| **MinkaIPC**   | Rust                       | Non-blocking NDJSON client crate for the ShojiWM IPC socket.                                                                                                                                                         |
| **bartizan-lsp** | Rust                     | Companion LSP supplying Rust code actions clippy and rust-analyzer leave unfilled (upstream froze IDE assists Dec 2025). A dev tool, not a desktop component. Uses `ra_ap_syntax`, so fixes port to upstream assists later. |
| **MinkaPKG**   | PKGBUILDs                  | Arch packaging for the above.                                                                                                                                                                                        |

`shoji-bar-2` is retired predecessor shell. `xwayland-satellite` is patched
XWayland bridge.

## Theming (Proustite)

- **No literal colors in widgets** — every color goes through `Theme` token. Each
  AntiWebUI app's `theme.rs` adds its own tokens to the `proustite` crate's; each QML
  app still to be ported has a `services/Theme.qml` facade doing the same (MinkaShot scrim).
- `red` is `#FF0000`; the old MinkaMon `glow` token merged into `red`.
- **Shared submodules are consumed via symlink into each QML app's config root**
  (`MinkaShot/Proustite -> ../Proustite`, same for MinkaLink). Quickshell only honours
  qmldir singleton registration for paths *inside* the shell root, so `import "../Proustite"`
  works but `import "../../Proustite"` loads files as plain components (undefined tokens).
- **Never name shared singleton after a Qt type** — QtQuick's built-in `Palette`
  silently shadowed our singleton; that's why it's `Proustite`, not `Palette`.

### Visual direction for AntiWebUI apps

Stated by Sophie on 17/11/2026, for apps as they move from Quickshell to AntiWebUI:
**Material 3 Expressive widgets and motion, in the Proustite palette.**

- **Use `anti_webui_md3e` before hand-drawing a control.** The crate is part of the AntiWebUI
  submodule the apps build, so it needs no toolkit bump. `tools/md3e-trial` shows every widget
  (`target/release/md3e-trial`, or `cargo run --release` there).
- **Colours stay Proustite**, not Material's own purple scheme. `proustite::scheme()`
  (`Proustite/src/lib.rs`) maps the Proustite tokens onto Material's colour roles, and every app
  installs it (`proustite::install()`), md3e-trial included. Near-black text on red is fine with her "for
  some parts of ui" (17/11/2026); the error colours in that mapping are Material's and undecided.
- **Widgets she named:** animated buttons, switches and checkboxes, sliders, chips, the animated
  progress bars (she singled these out), shapes, drag and drop, the sortable list and row, the
  navigation animations, and the date and time pickers (wanted for MinkaLedger once it moves).
- **The rest of the crate is welcome too** (cards, dialogs and sheets, menus, text fields, app
  bars, snackbars): "it's fine to use that, in fact it would make sense, making good use of the
  toolkit".
- **Restyle and retire Quickshell (17/11/2026):** "i would like restyling and for quickshell to
  become obsolete please, i think antiwebui has proven itself enough". MinkaShell, MinkaMon and
  MinkaSyu get restyled this way; MinkaLedger, MinkaShot and MinkaConf move to AntiWebUI. Write
  no new QML.
- **No more Quickshell (24/11/2026, her call):** "port the unported apps to antiwebui, no more
  quickshell". MinkaShot, MinkaConf and MinkaLedger's window are being ported, and then every QML
  tree, fallback path and Quickshell dependency goes, MinkaShell's included ("minkashell should not
  fallback to quickshell").
- **Font: Roboto Flex everywhere** (17/11/2026: "let's go with roboto flex everywhere"), for
  all proportional text in MinkaDE's own UI. Monospace text stays monospace. Noto Sans remains
  the fallback, and Noto's CJK and emoji fonts still cover what Roboto Flex lacks.
- **Keep the density.** Material's defaults are roomy and her UIs are information-rich: use the
  small component sizes and keep existing layouts tight.
- **A widget the toolkit lacks:** tell Sophie instead of hand-rolling around it. She may arrange
  a custom AntiWebUI fork (17/11/2026).

**AntiWebUI is the `AntiWebUI/` submodule** (Sophie's fork; `upstream` is bea4dev) since
18/11/2026. Apps depend on it by path (`../AntiWebUI/src/anti-webui`, `.../anti-webui-md3e`), so
they all build one revision, and a missing or awkward widget is fixed there. MinkaShell, MinkaMon,
MinkaSyu and `tools/md3e-trial` are on it.

**MinkaDE's own Rust crates are one Cargo workspace** (`Cargo.toml` at the top, 23/11/2026), so
they share one `Cargo.lock` and one `target/`, and a dependency is built once for every app that
uses it. Her rule: "if something can be deduplicated, i probably want it".
- **MinkaDE is one binary, `minka`** (`minka/`, 23/11/2026, her call). The apps are libraries,
  and `minka/src/main.rs` links them with the compositor (`shojiwm_minka`, a library crate in
  ShojiWM that builds the config example's files), the portal backend and xwayland-satellite.
  ShojiWM's built-in satellite is patched to the submodule too, so there is one satellite.
  Each process is one app, picked by the name it was started as: `/usr/bin/shoji_wm`,
  `/usr/bin/minkashell` and the rest are links to `/usr/bin/minka`. `minka <name> [args]` runs
  an app for a caller that cannot set argv[0] (a terminal, `/proc/self/exe`), and anything else
  only lists the names. It is still one process per app, so a stalled app cannot stall the
  compositor; what is shared is the code, on disk and in memory.
- **NEVER run a `minka` binary as `shoji_wm`** (a link of that name, or `minka shoji_wm`): it
  starts a compositor, as running a ShojiWM build did on 8/10/2026. Run the app you mean
  (`target/release-fast/minka minkamon`); `minka --names` lists the names and starts nothing.
- **An app that starts another runs the same binary**: MinkaMon's sampler, MinkaSyu's window
  and core, and MinkaThermal's load test start `/proc/self/exe` under the other name. That is
  the build already running, even after an upgrade replaced the file.
- **Installed by the minkade package** (`MinkaPKG/minkade`, `minkarepo publish minkade`, then
  MinkaSyu or `pacman -Syu`), which replaces minka-syu. `packaging/stage-usr.sh` is the one
  description of what lands under `/usr`. The session runs only the package, so a change reaches
  it at the next install and restart; the previous package in pacman's cache is the way back.
  While working, `cargo build --profile release-fast -p minka` (no LTO, incremental) builds
  `target/release-fast/minka`; the shell's and MinkaMon's launchers run it instead of
  `/usr/bin/minka` when `MINKA_BIN` names it. The package builds the release profile (thin LTO).
- **Proustite is a Rust crate too** (`Proustite/`): its `build.rs` reads `Proustite.qml`, so
  the palette is written once for the QML and the AntiWebUI apps. Each app's `theme.rs` keeps
  only its own tokens. `proustite::scheme()` and `proustite::widget_theme()` are the Material
  and toolkit themes every app installs.
- **Profiles and `[patch]` tables live only in the root `Cargo.toml`.** Cargo ignores them in a
  member, and in ShojiWM's workspace when minka builds its crates. The wgpu patch is pinned to
  the revision in `AntiWebUI/Cargo.lock`; the smithay patch mirrors ShojiWM's own.
- **A new crate of hers joins `members`**, and a new app joins the table in `minka/src/main.rs`
  as a library with a `pub fn main`. A directory with a crate that is not hers (an upstream
  project, or makepkg sources under MinkaPKG) goes in `exclude`. Otherwise cargo refuses to build
  it ("believes it's in a workspace when it's not").
- **Log with `minka_note::note!`** (`minka-note/`, 24/11/2026). It takes what `eprintln!` takes
  and writes the line with one write(2); `eprintln!` makes one per piece, costs about four times
  as much, and lets another writer to the file split its line. `log::` and `tracing::` macros
  print nothing in the AntiWebUI apps, which install no logger. The compositor and its Rust
  config log with `tracing`.
- **Upstream workspaces stay separate:** ShojiWM, xwayland-satellite, smithay, AntiWebUI and
  GitComet; minka reaches the crates it needs by path. `.vscode/settings.json` lists them in
  `rust-analyzer.linkedProjects`, because rust-analyzer's discovery stops at the root
  `Cargo.toml`. Claude Code sends the same list, and VS Code's clippy and cache-priming settings,
  through the `rust-analyzer-minka` plugin (`~/.claude/plugins/marketplaces/minka-local`,
  installed at local scope here). The official `rust-analyzer-lsp` and `bartizan` are disabled
  in this checkout: Claude Code gives `.rs` to the first plugin that claims it. The plugin's
  `workspaceFolder` is the MinkaDE root, so every session joins VS Code's lspmux instance and it
  is the same whichever client starts it. The list lives in both places: change them together.
- **Rust dependencies are linked statically**, as in every Rust package on Arch: Rust has no
  stable ABI, and AntiWebUI's own `anti-webui-dylib` says not to ship it. One binary is how
  they are shared instead.

- **Compact sizes exist:** call `anti_webui_md3e::set_density(Density::Compact)` before the first
  view (28 px buttons and icon buttons, 36 px text fields, 32 px menu rows, a 40x24 switch, thin
  sliders). Chips (32 px), checkboxes and radio buttons (48 px touch box), list rows, the search
  bar and the date picker have no compact size yet.
- **The md3e text field cannot be focused from code:** the inner input's `NodeRef` (`field_ref`
  in `components/text_field.rs`) is not public. Wanted: a way to pass one.
- **The X11 backend no longer comes in uninvited:** in the fork, the workspace's `anti-webui`
  dependency has `default-features = false`, and anti-webui-md3e asks for the defaults only for
  its own examples and tests. Worth offering upstream.
- **Component labels are always Roboto Flex** (`typography::FONT_FAMILY`, set on the text node).
  Harmless now that Roboto Flex is MinkaDE's font.

## Build / lint / run

```sh
# Lint QML (MinkaConf, MinkaShot and MinkaLedger's ui/ until their ports land; from the
# app's dir). ABSOLUTE path: qt5-declarative owned /usr/bin/qmllint and was removed
# 29/8/2026, so there is no qmllint on PATH at all now. Never pipe this: like cargo below, `| head` makes $? report head, and the
# Qt5 binary that used to be first on PATH failed with a BARE 255 and no message.
# `find`, not globs: an app that keeps its modules in modules/<sub>/ has no `modules/*.qml`
# (fish refuses to run the glob; bash lints a fraction of the files and exits 255).
# `--absolute-path` is hidden from --help but real: without it Qt 6.12 prints a FALSE
# "not declared as singleton in qmldir" for most `pragma Singleton` files (10/10/2026).
# Qt 6.12's TEXT output also drops every warning a file logged before a function or
# binding with a loop in it (the old QML shell printed 17 of its 28): add `--json -` after
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

# Build MinkaDE's one binary for trying a change: the compositor, the shell and every app.
# The session runs the installed package, so this changes nothing running; try an app with
# `target/release-fast/minka <name>` (never `shoji_wm`), or set MINKA_BIN to it for the shell's
# and MinkaMon's launchers. `cargo test -p <crate>` tests one app's library.
cargo build --profile release-fast -p minka

# Run a Quickshell app still to be ported — ALWAYS use an ABSOLUTE path (relative `.`
# breaks from other cwds)
qs -p /home/seirra/Documents/src/MinkaDE/MinkaConf

# Test MinkaLedger against a THROWAWAY book, never the default one. The default
# (~/.local/share/minka-ledger/book.db) is Sophie's real finances; a probe that
# writes there leaves accounts and imported statements mixed into her data.
env MINKA_LEDGER_BIN=$PWD/target/debug/minka-ledger \
    MINKA_LEDGER_DB=$HOME/.cache/minka-ledger/test.db \
    qs -p /home/seirra/Documents/src/MinkaDE/MinkaLedger/ui
# and for the core alone (target/debug/minka-ledger is the crate's own small binary,
# which exists for its tests and for this; `cargo build -p minka-ledger` makes it):
./target/debug/minka-ledger --db ~/.cache/minka-ledger/test.db    # from MinkaDE/

# MinkaLedger ships a NEW CORE METHOD? Her window runs the package's /usr/bin/minka-ledger,
# so a method added and tested against a checkout build answers "no such method: <name>"
# there until the minkade package is published and installed. She must then RESTART
# MinkaLedger: the running core holds the old inode (readlink /proc/<pid>/exe shows
# /usr/bin/minka with a trailing "(deleted)").
minkarepo publish minkade
```

- **The running compositor is not the worktree.** `/usr/bin/shoji_wm` is a link to
  `/usr/bin/minka`, the last minkade package installed, and the session holds that inode for
  its whole life — so HEAD can be days ahead of what is actually running. Check before
  drawing conclusions from source: `pacman -Qi minkade`, `ls -l /usr/bin/minka`,
  `ps -o lstart= -p $(pgrep -x shoji_wm)`, and `readlink /proc/<pid>/exe` (`/usr/bin/minka`
  for every MinkaDE process) — a trailing `(deleted)` means an upgrade has replaced the
  file and the process is still running the old one. Same for `bartizan-lsp`, which the
  editor launches as `/usr/bin/bartizan-lsp`: every change needs the package **and** an LSP
  restart. A `cargo build` or `cargo test` changes nothing that is running.
- **The Rust config cannot reload in place: there is no Super+Shift+R.** An edit to the Rust
  under `ShojiWM/src/shojiwm_rs/examples/default_config/` (not its `assets/`, which are read
  from disk, see below) takes effect only after the minkade package is published and
  installed (VS Code task "minkade: publish package") and a new session. The way back is the
  previous package in pacman's cache. ShojiWM's own `dist/install.sh` is no longer how the
  session is installed: it writes over the package's `/usr/bin/shoji_wm`. The portal comes
  from the package too. Its shaders and icons are read at runtime from
  `ShojiWM/src/shojiwm_rs/examples/default_config/assets` (an asset root compiled in from the
  build path; a build from before the move to it reads `ShojiWM/packages/config`), so keep the
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
  (`main.rs` plus `minka/*.rs`), compiled into the compositor since 6/10/2026 and into
  `minka` (as the `shojiwm_minka` library) since 23/11/2026.
  `ShojiWM/packages/config/src` (TypeScript) is the rollback config: only the TypeScript
  build reads it, through `$SHOJI_CONFIG`, and the Rust build ignores that variable.
- `shojiwm-env.fish` (symlinked to fish conf.d as `shojiwm.fish`) exports the
  repo-checkout overrides for the QML trees and launchers (`MINKA_SHELL_DIR`,
  `MINKA_SHOT_DIR`, `MINKA_MON_BIN`, `MINKA_MON_DIR`) and points
  `SHOJI_XWAYLAND_SATELLITE_PATH` at the package's `/usr/lib/minka/xwayland-satellite`.
  The binaries themselves are the package's.
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
- **Session logs in `~/.local/state/shoji_wm/logs` (`latest.log`) are UTC**, while
  `journalctl` and `ls` show local time. Comparing them without converting has produced hours
  of phantom timeline. `$SHOJI_LOG_DIR` overrides the directory. Builds without commit
  `59ea204c`, such as the `shoji_wm.previous` kept by the 20/11/2026 install, still write to
  the old `~/shoji_wm/logs`.

### Before syncing ShojiWM with upstream

- **Upstream commit `83e1127e` (part of PR #221) looks like it can freeze the whole compositor.**
  It makes the keyboard-focus hook read the pointer while smithay still holds the pointer's lock.
  Likely trigger: open a menu in Floorp or a Qt or GTK app, press any key while it is open, then
  close it with the mouse. Found by reading the code on 17/11/2026 and checked in three passes;
  nobody has reproduced it. `main` does not have this code, and a rebase or merge brings it in
  without a conflict. Patch or drop that commit before syncing. bea4dev has not been told.
- **A plain rebase drops merge commits.** `acce509d` (the shoji-island merge of 5/11/2026) holds
  hand-made conflict resolutions in `handlers/compositor.rs` and `input.rs`.
- **The running compositor reads shaders and icons from this checkout**, and upstream moved some
  of them, so do a sync in a separate worktree, not in the live one.

## Conventions

- **Dates: `d/M/yyyy`, leading zeros stripped** (e.g. `7/7/2026`).
- Keep the **bar's IPC health dot** — deliberate design detail.
- **Sophie commits edits in real time via GitKraken.** HEAD moves under you;
  every save-point may become a commit. she may format proposed edits
  ("user modified your proposed changes" = intentional; re-read before editing the region again).
- When restarting session apps from the tool shell, **scrub the env first**
  (`SHELL=/bin/fish`, drop `CLAUDE*`/`CODEX*`/`AI_AGENT*`, cwd `$HOME`) — a leaked bash SHELL
  makes her terminals open bash instead of fish. Kill test instances by **exact PID**,
  never `pkill -f "qs -p ."` (the `.` is a regex wildcard that matched her live shell).