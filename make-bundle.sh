#!/usr/bin/env bash
# Offline bundle = git archive of the committed (already scrubbed + guarded) HEAD.
# This is "Option B" for free: no second allowlist, no second scrub — the repo IS the
# source of truth, the bundle is just a frozen export of it.
#   bash make-bundle.sh            # writes ../orchestration-layer-bundle.{zip,tar.gz}
#   bash make-bundle.sh /tmp/out   # custom prefix
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
OUT="${1:-$ROOT/../orchestration-layer-bundle}"

if ! git -C "$ROOT" diff --quiet || ! git -C "$ROOT" diff --cached --quiet; then
  echo "WARNING: uncommitted changes — the bundle reflects the last COMMIT, not your working tree."
  echo "         run 'bash sync-shareable.sh && git add -A && git commit' first for a current bundle."
fi

git -C "$ROOT" archive --format=zip    -o "$OUT.zip"    HEAD
git -C "$ROOT" archive --format=tar.gz -o "$OUT.tar.gz" HEAD
echo "bundle written from committed HEAD:"
ls -la "$OUT.zip" "$OUT.tar.gz"
