#!/usr/bin/env bash
# Assemble the MinkaDE tester tarball from this checkout.
#
# Usage: packaging/make-tarball.sh [version]
#
# Uses the WORKING TREES (not git HEADs) of the submodules, so uncommitted
# fixes ship. Expects minka, MinkaDE's one binary, to be built already
# (cargo build --release -p minka), as .github/workflows/release.yml does.
# Run it there: a binary built on this machine needs its CPU (x86-64-v4) and
# glibc, which testers may not have.
#
# The tarball holds usr/, which install.sh copies to /usr, src/ for
# install.sh --from-source, the two scripts and README.md. usr/ is laid out
# by stage-usr.sh, the one description of the installed layout, which the
# minkade package uses as well.

set -euo pipefail

VERSION="${1:-0.1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── what ships ───────────────────────────────────────────────────────────
# MinkaDE's crates are one Cargo workspace (Cargo.toml at the top), so the
# build is in its target/.
MINKA=target/release/minka
# Where the compositor falls back to for its shaders and icons once the
# checkout it was built from is gone, as a CI build's is (INSTALLED_ASSETS
# in Minka's config, minka/session.rs). stage-usr.sh puts them there.
ASSET_FALLBACK=/usr/share/minka/shojiwm

# -> src/, at the same paths, for install.sh --from-source. The workspace's
# Cargo.toml and Cargo.lock ship with every member Cargo.toml lists (some
# nested, as tools/keysend is), since cargo will not load the workspace
# with one missing. minka reaches ShojiWM and xwayland-satellite by path,
# the AntiWebUI apps reach AntiWebUI, and Cargo.toml's `[patch]` points
# smithay at the smithay checkout, so all four have to sit beside the
# members or cargo cannot resolve the build.
WORKSPACE=(Cargo.toml Cargo.lock)
# Read with a TOML parser, not by line: `cargo new` adds a member to the
# end of the last member's line. An empty list prints nothing, so the
# preflight below sees no members.
mapfile -t MEMBERS < <([[ -e Cargo.toml ]] && python3 -I -c '
import sys, tomllib
with open("Cargo.toml", "rb") as f:
    sys.stdout.write("".join(m + "\n" for m in tomllib.load(f)["workspace"]["members"]))')
SOURCES=(ShojiWM xwayland-satellite smithay AntiWebUI "${WORKSPACE[@]}" "${MEMBERS[@]}")

# -> the tarball root
TOP_EXEC=(packaging/install.sh packaging/uninstall.sh)
TOP_DATA=(README.md)

# ── preflight ────────────────────────────────────────────────────────────
# Check this script's own inputs before staging anything, so a file that
# moved or went is reported here, together with any others, instead of
# stopping the build halfway through. What stage-usr.sh reads it checks
# as it goes; the staging dir is removed if it stops.
missing=()
[[ -x "$MINKA" ]] || missing+=("$MINKA (release binary)")
# Built from a ShojiWM commit older than the fallback, the compositor would
# look for its assets only in the CI runner's checkout, and every effect
# would fail on a tester's machine. The path is in the binary when the
# fallback is.
if [[ -x "$MINKA" ]] && ! grep -qaF "$ASSET_FALLBACK" "$MINKA"; then
    missing+=("$MINKA (has no $ASSET_FALLBACK asset fallback: is the ShojiWM gitlink current?)")
fi
for f in packaging/stage-usr.sh "${SOURCES[@]}" "${TOP_EXEC[@]}" "${TOP_DATA[@]}"; do
    [[ -e "$f" ]] || missing+=("$f")
done
# A Cargo.toml that names no members would ship a workspace with none.
if [[ -e Cargo.toml && ${#MEMBERS[@]} -eq 0 ]]; then
    missing+=("Cargo.toml (lists no workspace members)")
fi
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "missing from this checkout:" >&2
    printf '   - %s\n' "${missing[@]}" >&2
    exit 1
fi

# ── stage ────────────────────────────────────────────────────────────────
STAGE_ROOT="$(mktemp -d)"
trap 'rm -rf "$STAGE_ROOT"' EXIT
STAGE="$STAGE_ROOT/minkade-$VERSION"
mkdir -p "$STAGE/src"

packaging/stage-usr.sh "$STAGE" "$MINKA"

echo ">> sources (for --from-source)"
# -R keeps each source's path, so tools/keysend lands in src/tools/keysend.
# A member inside another (MinkaMon/sampler-rs) is copied once. Tree copies
# never carry VCS or IDE state, cargo's builds or node's packages.
rsync -aR --exclude .git --exclude .idea --exclude target --exclude node_modules \
    "${SOURCES[@]}" "$STAGE/src/"

echo ">> scripts"
install -m755 "${TOP_EXEC[@]}" "$STAGE/"
install -m644 "${TOP_DATA[@]}" "$STAGE/"

OUT="$REPO_ROOT/minkade-$VERSION-linux-x86_64.tar.gz"
echo ">> compressing $OUT"
tar -C "$STAGE_ROOT" -czf "$OUT" "minkade-$VERSION"
du -h "$OUT"
