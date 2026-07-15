#!/usr/bin/env bash
# Assemble the MinkaDE tester tarball from this checkout.
#
# Usage: packaging/make-tarball.sh [version]
#
# Uses the WORKING TREES (not git HEADs) of the submodules, so uncommitted
# fixes ship. Expects release binaries to already exist:
#   ShojiWM/target/release/{shoji_wm,xdg-desktop-portal-shojiwm}
#   xwayland-satellite/target/release/xwayland-satellite
#   MinkaFX/target/release/MinkaFX
# Runs `npm ci` to stage the TypeScript runtime with node_modules included
# (contains esbuild's native binary, hence the -x86_64 tarball suffix).

set -euo pipefail

VERSION="${1:-0.1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

for f in ShojiWM/target/release/shoji_wm \
         ShojiWM/target/release/xdg-desktop-portal-shojiwm \
         xwayland-satellite/target/release/xwayland-satellite \
         MinkaFX/target/release/MinkaFX; do
    [[ -x "$f" ]] || { echo "missing release binary: $f" >&2; exit 1; }
done

STAGE_ROOT="$(mktemp -d)"
trap 'rm -rf "$STAGE_ROOT"' EXIT
STAGE="$STAGE_ROOT/minkade-$VERSION"
mkdir -p "$STAGE"/{bin,dist,minka,src}

echo ">> binaries"
install -m755 ShojiWM/target/release/shoji_wm "$STAGE/bin/"
install -m755 ShojiWM/target/release/xdg-desktop-portal-shojiwm "$STAGE/bin/"
install -m755 xwayland-satellite/target/release/xwayland-satellite "$STAGE/bin/"
install -m755 MinkaFX/target/release/MinkaFX "$STAGE/bin/"

echo ">> TypeScript runtime (npm ci)"
RUNTIME="$STAGE/runtime"
mkdir -p "$RUNTIME/packages" "$RUNTIME/tools"
cp ShojiWM/package.json ShojiWM/package-lock.json ShojiWM/tsconfig.json "$RUNTIME/"
cp -a ShojiWM/packages/shoji_wm "$RUNTIME/packages/"
cp -a ShojiWM/packages/config "$RUNTIME/packages/"
cp ShojiWM/tools/decoration-runtime.ts ShojiWM/tools/evaluate-decoration.ts "$RUNTIME/tools/"
npm --prefix "$RUNTIME" ci --silent

echo ">> quickshell trees"
rsync -a --exclude .git --exclude .idea MinkaShell/ "$STAGE/minka/MinkaShell/"
rsync -a --exclude .git --exclude .idea MinkaConf/ "$STAGE/minka/MinkaConf/"

echo ">> sources (for --from-source)"
EXCLUDES=(--exclude .git --exclude .idea --exclude target --exclude node_modules)
rsync -a "${EXCLUDES[@]}" ShojiWM/ "$STAGE/src/ShojiWM/"
rsync -a "${EXCLUDES[@]}" xwayland-satellite/ "$STAGE/src/xwayland-satellite/"
rsync -a "${EXCLUDES[@]}" MinkaFX/ "$STAGE/src/MinkaFX/"
rsync -a "${EXCLUDES[@]}" MinkaIPC/ "$STAGE/src/MinkaIPC/"

echo ">> dist files"
install -m755 packaging/minka-session "$STAGE/dist/"
install -m644 packaging/minka.desktop "$STAGE/dist/"
install -m644 ShojiWM/dist/shojiwm.portal "$STAGE/dist/"
install -m644 ShojiWM/dist/org.freedesktop.impl.portal.desktop.shojiwm.service "$STAGE/dist/"
install -m644 ShojiWM/dist/xdg-desktop-portal-shojiwm.service "$STAGE/dist/"
install -m755 packaging/install.sh "$STAGE/"
install -m755 packaging/uninstall.sh "$STAGE/"
install -m644 README.md "$STAGE/"

OUT="$REPO_ROOT/minkade-$VERSION-linux-x86_64.tar.gz"
echo ">> compressing $OUT"
tar -C "$STAGE_ROOT" -czf "$OUT" "minkade-$VERSION"
du -h "$OUT"