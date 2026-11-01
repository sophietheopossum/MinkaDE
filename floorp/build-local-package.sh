#!/usr/bin/env bash
# Build the Arch Floorp package from the committed HEADs of the MinkaDE
# floorp/* clones. The PKGBUILD's normal upstream-tarball mode is unchanged.
set -euo pipefail

check_only=false
if [[ ${1:-} == --check ]]; then
  check_only=true
  shift
fi
if [[ ${1:-} == -- ]]; then
  shift
fi

workspace=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
floorp_dir="$workspace/floorp/Floorp"
runtime_dir="$workspace/floorp/Floorp-Runtime"
core_dir="$workspace/floorp/Floorp-core"
pkgbuild_dir="$workspace/MinkaPKG/floorp"

die() {
  echo "floorp local build: $*" >&2
  exit 1
}

repo_head() {
  local name="$1"
  local dir="$2"
  local dirty

  [[ -d "$dir" ]] || die "missing $name clone: $dir"
  git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "$name is not a Git working tree: $dir"

  dirty=$(git -C "$dir" status --porcelain)
  if [[ -n "$dirty" ]]; then
    {
      echo "floorp local build: $name has uncommitted changes; this build uses committed HEAD only:"
      printf '%s\n' "$dirty" | sed -n '1,50p'
    } >&2
    exit 1
  fi

  git -C "$dir" rev-parse HEAD
}

floorp_head=$(repo_head Floorp "$floorp_dir")
runtime_head=$(repo_head Floorp-Runtime "$runtime_dir")
core_head=$(repo_head Floorp-core "$core_dir")

pkgver=$(sed -n 's/^pkgver=//p' "$pkgbuild_dir/PKGBUILD" | head -n 1)
[[ -n "$pkgver" ]] || die "could not read pkgver from $pkgbuild_dir/PKGBUILD"

floorp_version=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["version"])' \
  "$floorp_dir/package.json")
if [[ "$floorp_version" != "$pkgver" ]]; then
  die "Floorp clone is version $floorp_version, but the PKGBUILD packages $pkgver; update the PKGBUILD first"
fi

lock_commit=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["source"]["commit"])' \
  "$floorp_dir/floorp-runtime.lock.json")
if [[ "$lock_commit" != "$runtime_head" ]]; then
  echo "floorp local build: warning: Floorp locks Runtime $lock_commit, but the clone is at $runtime_head" >&2
fi

printf '%s\n' \
  "Floorp:         $floorp_head" \
  "Floorp-Runtime: $runtime_head" \
  "Floorp-core:    $core_head"

if [[ "$check_only" == true ]]; then
  exit 0
fi

export _build_local_repos=true
export _local_repos_root="$workspace/floorp"
export _local_floorp_ref="commit=$floorp_head"
export _local_core_ref="commit=$core_head"
export _runtime_commit="$runtime_head"

cd "$pkgbuild_dir"
exec makepkg -f "$@"
