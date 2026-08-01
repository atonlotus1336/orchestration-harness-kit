#!/usr/bin/env bash
# Full-repo leak scan — the companion the pre-commit hook cannot be.
#
# WHY THIS EXISTS: .githooks/pre-commit scans only STAGED files
# (git diff --cached). That catches new leaks, but anything committed
# BEFORE a token was added to .private-denylist stays invisible forever —
# the scanner never re-reads untouched files. Proven 2026-08-01: two
# project-name leaks survived four scrub passes this way.
#
# Run after ANY denylist addition, and periodically. Read-only.
set -uo pipefail
cd "$(dirname "$0")/.."
[ -f .private-denylist ] || { echo "no .private-denylist (recipients: add your own tokens)"; exit 0; }
fail=0
while IFS= read -r tok; do
  [ -z "$tok" ] && continue
  case "$tok" in \#*) continue ;; esac
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    if grep -Iiqs -e "$tok" -- "$f"; then
      echo "LEAK  $f  <- token: $tok"
      fail=1
    fi
  done < <(git ls-files)
done < .private-denylist
[ "$fail" -eq 0 ] && echo "full-repo scan clean ($(git ls-files | wc -l) tracked files)"
exit $fail
