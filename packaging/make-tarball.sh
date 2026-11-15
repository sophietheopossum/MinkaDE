#!/usr/bin/env bash
# Assemble the MinkaDE tester tarball from this checkout.
#
# Usage: packaging/make-tarball.sh [version]
#
# Uses the WORKING TREES (not git HEADs) of the submodules, so uncommitted
# fixes ship. Expects the BINARIES below to already be built (release), as
# .github/workflows/release.yml does. Run it there: binaries built on this
# machine need its CPU (x86-64-v4) and glibc, which testers may not have.

set -euo pipefail

VERSION="${1:-0.1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── what ships ───────────────────────────────────────────────────────────
# install.sh and uninstall.sh name these files one by one, so a change here
# usually needs a matching change there.

# -> bin/shoji_wm: the compositor with Minka's Rust config compiled in,
# shojiwm_rs's default_config example
# (cargo build --release -p shojiwm_rs --example default_config).
COMPOSITOR=ShojiWM/target/release/examples/default_config
# Where it falls back to for its shaders and icons once the checkout it was
# built from is gone (INSTALLED_ASSETS in the example's minka/session.rs),
# and so where install.sh puts minka/shojiwm.
ASSET_FALLBACK=/usr/share/minka/shojiwm

# -> bin/
BINARIES=(
    ShojiWM/target/release/xdg-desktop-portal-shojiwm
    xwayland-satellite/target/release/xwayland-satellite
    MinkaFX/target/release/MinkaFX
    MinkaCap/target/release/MinkaCap
)

# -> minka/shojiwm/, at the same paths under it as under ASSET_ROOT. The
# compositor reads its shaders and icons from disk; when the checkout it
# was built from is gone, as a CI build's is, it reads this copy in
# ASSET_FALLBACK.
ASSET_ROOT=ShojiWM/packages/config
ASSETS=("$ASSET_ROOT"/src/effect/*.frag "$ASSET_ROOT"/assets/*.svg)

# -> minka/
QS_APPS=(MinkaShell MinkaConf MinkaMon MinkaShot)
# Shared singletons, as SIBLINGS of the app trees. Each app contains a
# `Proustite`/`MinkaLink` symlink pointing at `../Proustite`, so they only
# resolve if these ship alongside — both here and once installed under
# /usr/share/minka. Without them the app trees carry dangling symlinks and
# every theme token comes back undefined.
QS_SHARED=(Proustite MinkaLink)

# -> minka/<App>/, the top of each app's tree, where its launcher looks for
# them: the AntiWebUI shell that dist/minkashell runs, and the AntiWebUI
# MinkaMon that dist/minkamon runs, with the sampler it looks for beside
# itself. The trees ship without their target/, so these are the only
# builds of them in the tarball.
APP_BINARIES=(
    MinkaShell/target/release/minkashell
    MinkaMon/target/release/minkamon
    MinkaMon/sampler-rs/target/release/minkamon-sampler
)
# -> minka/, at the same path: launchers the compositor runs from inside an
# app's tree. (dist/ is otherwise left out of the trees; see the app trees
# below.) Without it the session runs the Quickshell shell.
APP_LAUNCHERS=(MinkaShell/dist/minkashell)

# -> src/, for install.sh --from-source. MinkaIPC and smithay are path
# dependencies (MinkaFX's, MinkaMon's and MinkaShell's, and ShojiWM's
# `[patch]` on ../smithay), so they have to sit next to their dependents or
# cargo cannot resolve the build.
SOURCES=(ShojiWM xwayland-satellite MinkaFX MinkaCap MinkaMon MinkaShell MinkaIPC smithay)

# -> dist/
DIST_EXEC=(
    packaging/minka-session
    MinkaMon/dist/minkamon
)
DIST_DATA=(
    packaging/minka.desktop
    MinkaConf/dist/MinkaConf.desktop
    MinkaMon/dist/MinkaMon.desktop
    ShojiWM/dist/shojiwm.portal
    ShojiWM/dist/org.freedesktop.impl.portal.desktop.shojiwm.service
    ShojiWM/dist/xdg-desktop-portal-shojiwm.service
)

# -> the tarball root
TOP_EXEC=(packaging/install.sh packaging/uninstall.sh)
TOP_DATA=(README.md)

# ── preflight ────────────────────────────────────────────────────────────
# Check every input before staging anything, so a file that upstream moved
# or deleted is reported here, together with any others, instead of
# stopping the build halfway through. An ASSETS pattern that matches
# nothing stays as written, and is reported like a missing file.
missing=()
for f in "$COMPOSITOR" "${BINARIES[@]}" "${APP_BINARIES[@]}"; do
    [[ -x "$f" ]] || missing+=("$f (release binary)")
done
# Built from a ShojiWM commit older than the fallback, the compositor would
# look for its assets only in the CI runner's checkout, and every effect
# would fail on a tester's machine. The path is in the binary when the
# fallback is.
if [[ -x "$COMPOSITOR" ]] && ! grep -qaF "$ASSET_FALLBACK" "$COMPOSITOR"; then
    missing+=("$COMPOSITOR (has no $ASSET_FALLBACK asset fallback: is the ShojiWM gitlink current?)")
fi
for f in "${ASSETS[@]}" "${QS_APPS[@]}" "${QS_SHARED[@]}" "${APP_LAUNCHERS[@]}" \
         "${SOURCES[@]}" "${DIST_EXEC[@]}" "${DIST_DATA[@]}" \
         "${TOP_EXEC[@]}" "${TOP_DATA[@]}"; do
    [[ -e "$f" ]] || missing+=("$f")
done
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "missing from this checkout:" >&2
    printf '   - %s\n' "${missing[@]}" >&2
    exit 1
fi

# ── stage ────────────────────────────────────────────────────────────────
STAGE_ROOT="$(mktemp -d)"
trap 'rm -rf "$STAGE_ROOT"' EXIT
STAGE="$STAGE_ROOT/minkade-$VERSION"
mkdir -p "$STAGE"/{bin,dist,minka/shojiwm,src}

# Tree copies never carry VCS or IDE state. The sources have no trailing
# slash, so rsync puts each tree in the destination under its own name.
VCS_EXCLUDES=(--exclude .git --exclude .idea)

echo ">> binaries"
install -m755 "$COMPOSITOR" "$STAGE/bin/shoji_wm"
install -m755 "${BINARIES[@]}" "$STAGE/bin/"

echo ">> compositor assets"
(cd "$ASSET_ROOT" && cp --parents "${ASSETS[@]#"$ASSET_ROOT"/}" "$STAGE/minka/shojiwm/")

echo ">> app trees"
# `dist` is excluded: MinkaConf's and MinkaMon's files there are installed
# separately (see the dist files section below), and MinkaShell's launcher
# ships as APP_LAUNCHERS. `target` is cargo's: the builds the apps need ship
# as APP_BINARIES.
rsync -a "${VCS_EXCLUDES[@]}" --exclude dist --exclude target "${QS_APPS[@]}" "$STAGE/minka/"
rsync -a "${VCS_EXCLUDES[@]}" "${QS_SHARED[@]}" "$STAGE/minka/"
for f in "${APP_BINARIES[@]}"; do
    install -m755 "$f" "$STAGE/minka/${f%%/*}/"
done
for f in "${APP_LAUNCHERS[@]}"; do
    install -Dm755 "$f" "$STAGE/minka/$f"
done

echo ">> sources (for --from-source)"
rsync -a "${VCS_EXCLUDES[@]}" --exclude target --exclude node_modules \
    "${SOURCES[@]}" "$STAGE/src/"

echo ">> dist files"
install -m755 "${DIST_EXEC[@]}" "$STAGE/dist/"
install -m644 "${DIST_DATA[@]}" "$STAGE/dist/"
install -m755 "${TOP_EXEC[@]}" "$STAGE/"
install -m644 "${TOP_DATA[@]}" "$STAGE/"

OUT="$REPO_ROOT/minkade-$VERSION-linux-x86_64.tar.gz"
echo ">> compressing $OUT"
tar -C "$STAGE_ROOT" -czf "$OUT" "minkade-$VERSION"
du -h "$OUT"
