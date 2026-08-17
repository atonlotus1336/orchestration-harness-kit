#!/usr/bin/env bash
set -euo pipefail

REPOSITORY="https://github.com/PrimeIntellect-ai/prime-agent.git"
VERSION="v0.7.1"
COMMIT="95afd319a78ae017a41241d50b013d656a0685ce"
TREE="caf6fa88a48d2a4b07b903c327ae3d6b2d1b0466"
LOCK_SHA256="39ee303bca10c0933cf917275613c8f44099f50de1650a5c356e7cda02b701e8"

root="$(git rev-parse --show-toplevel)"
[[ "$root" == "__WSL_WORKSPACE__/hermes-agent-self-evolution" ]] || { echo "wrong repository" >&2; exit 2; }
parent="$root/.git/phase01-gap-lifecycle/sources/prime-agent"
target="$parent/$COMMIT"
mode="acquire"
[[ "${1:-}" == "--check" ]] && mode="check" && shift
[[ $# -eq 0 ]] || { echo "unknown argument" >&2; exit 2; }

verify_cache() {
  [[ -d "$target" && ! -L "$target" ]] || return 1
  [[ ! -e "$target/.git" && ! -e "$target/.gitmodules" ]] || return 1
  [[ -f "$target/.prime-source.json" && -f "$target/package-lock.json" ]] || return 1
  [[ -z "$(find "$target" -type l -print -quit)" ]] || return 1
  [[ "$(sha256sum "$target/package-lock.json" | cut -d ' ' -f1)" == "$LOCK_SHA256" ]] || return 1
  python3 - "$target/.prime-source.json" "$REPOSITORY" "$VERSION" "$COMMIT" "$TREE" "$LOCK_SHA256" <<'PY'
import json, sys
path, repository, version, commit, tree, lock = sys.argv[1:]
value = json.load(open(path, encoding="utf-8"))
expected = {"repository": repository, "version": version, "commit": commit, "tree": tree, "package_lock_sha256": lock}
raise SystemExit(0 if value == expected else 1)
PY
}

if verify_cache; then
  printf 'prime source: READY %s\n' "$target"
  exit 0
fi
[[ "$mode" == "acquire" ]] || { echo "prime source: MISSING_OR_INVALID" >&2; exit 1; }

mkdir -p "$parent"
if [[ -e "$target" || -L "$target" ]]; then
  quarantine="$parent/.quarantine-${COMMIT}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  mv -- "$target" "$quarantine"
fi
temporary="$(mktemp -d "$parent/.fetch-${COMMIT}.XXXXXX")"
trap 'rm -rf -- "$temporary"' EXIT
repo="$temporary/repo"
payload="$temporary/payload"
mkdir -p "$repo" "$payload"

export GIT_CONFIG_NOSYSTEM=1 GIT_LFS_SKIP_SMUDGE=1
git -C "$repo" -c core.hooksPath=/dev/null init -q
git -C "$repo" remote add origin "$REPOSITORY"
git -C "$repo" -c core.hooksPath=/dev/null -c submodule.recurse=false -c filter.lfs.smudge= -c filter.lfs.required=false \
  fetch -q --depth=1 --no-tags origin "refs/tags/$VERSION"
[[ "$(git -C "$repo" rev-parse FETCH_HEAD^{commit})" == "$COMMIT" ]] || { echo "tag commit mismatch" >&2; exit 2; }
[[ "$(git -C "$repo" rev-parse "$COMMIT^{tree}")" == "$TREE" ]] || { echo "tree mismatch" >&2; exit 2; }
git -C "$repo" -c core.hooksPath=/dev/null checkout -q --detach "$COMMIT"
[[ "$(git -C "$repo" remote get-url origin)" == "$REPOSITORY" ]] || { echo "origin mismatch" >&2; exit 2; }
[[ ! -e "$repo/.gitmodules" ]] || { echo "submodules are forbidden" >&2; exit 2; }
[[ -z "$(find "$repo" -path "$repo/.git" -prune -o -type l -print -quit)" ]] || { echo "symlinks are forbidden" >&2; exit 2; }
[[ "$(sha256sum "$repo/package-lock.json" | cut -d ' ' -f1)" == "$LOCK_SHA256" ]] || { echo "package-lock mismatch" >&2; exit 2; }

git -C "$repo" archive --format=tar "$COMMIT" | tar -xf - -C "$payload"
python3 - "$payload/.prime-source.json" "$REPOSITORY" "$VERSION" "$COMMIT" "$TREE" "$LOCK_SHA256" <<'PY'
import json, os, sys
path, repository, version, commit, tree, lock = sys.argv[1:]
value = {"repository": repository, "version": version, "commit": commit, "tree": tree, "package_lock_sha256": lock}
with open(path, "w", encoding="utf-8", newline="\n") as handle:
    json.dump(value, handle, sort_keys=True, separators=(",", ":"))
    handle.write("\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
chmod -R go-w "$payload"
mv -- "$payload" "$target"
trap - EXIT
rm -rf -- "$temporary"
verify_cache || { echo "promoted cache failed reverification" >&2; exit 2; }
printf 'prime source: ACQUIRED %s\n' "$target"
