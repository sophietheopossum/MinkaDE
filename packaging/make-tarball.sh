#!/usr/bin/env bash
# Assemble the MinkaDE tester tarball from this checkout.
#
# Usage: packaging/make-tarball.sh [version]
#
# Uses the WORKING TREES (not git HEADs) of the submodules, so uncommitted
# fixes ship. Expects the BINARIES below to already be built (release).
# Runs `npm ci` to stage the TypeScript runtime with node_modules included
# (contains esbuild's native binary, hence the -x86_64 tarball suffix).

set -euo pipefail

VERSION="${1:-0.1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── what ships ───────────────────────────────────────────────────────────
# install.sh and uninstall.sh name these files one by one, so a change here
# usually needs a matching change there.

# -> bin/
BINARIES=(
    ShojiWM/target/release/shoji_wm
    ShojiWM/target/release/xdg-desktop-portal-shojiwm
    xwayland-satellite/target/release/xwayland-satellite
    MinkaFX/target/release/MinkaFX
)

# -> runtime/, relative to ShojiWM/ and kept at the same relative paths.
# install.sh --from-source stages the same list from src/ShojiWM.
RUNTIME_FILES=(
    package.json package-lock.json tsconfig.json
    packages/shoji_wm packages/config
    tools/decoration-runtime.ts
)

# -> minka/
QS_APPS=(MinkaShell MinkaConf MinkaMon)
# Shared singletons, as SIBLINGS of the app trees. Each app contains a
# `Proustite`/`MinkaLink` symlink pointing at `../Proustite`, so they only
# resolve if these ship alongside — both here and once installed under
# /usr/share/minka. Without them the app trees carry dangling symlinks and
# every theme token comes back undefined.
QS_SHARED=(Proustite MinkaLink)

# -> src/, for install.sh --from-source
SOURCES=(ShojiWM xwayland-satellite MinkaFX MinkaIPC)

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
# stopping the build halfway through.
missing=()
for f in "${BINARIES[@]}"; do
    [[ -x "$f" ]] || missing+=("$f (release binary)")
done
for f in "${RUNTIME_FILES[@]/#/ShojiWM/}" "${QS_APPS[@]}" "${QS_SHARED[@]}" \
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
mkdir -p "$STAGE"/{bin,dist,minka,runtime,src}

# Tree copies never carry VCS or IDE state. The sources have no trailing
# slash, so rsync puts each tree in the destination under its own name.
VCS_EXCLUDES=(--exclude .git --exclude .idea)

echo ">> binaries"
install -m755 "${BINARIES[@]}" "$STAGE/bin/"

echo ">> TypeScript runtime (npm ci)"
(cd ShojiWM && cp -a --parents "${RUNTIME_FILES[@]}" "$STAGE/runtime/")
npm --prefix "$STAGE/runtime" ci --silent

echo ">> quickshell trees"
# `dist` is excluded: each app owns its launcher there, but it is installed to
# /usr/share/applications separately (see the dist files section below), so
# copying it into the app tree as well would just duplicate it.
rsync -a "${VCS_EXCLUDES[@]}" --exclude dist "${QS_APPS[@]}" "$STAGE/minka/"
rsync -a "${VCS_EXCLUDES[@]}" "${QS_SHARED[@]}" "$STAGE/minka/"

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
