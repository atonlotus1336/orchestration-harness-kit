#!/usr/bin/env bash
# Scan raw tracked and non-ignored untracked bytes before publication.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f .private-denylist ]; then
  echo "BLOCKED: missing local privacy policy"; exit 1
fi
patterns=$(mktemp)
files=$(mktemp)
trap 'rm -f -- "$patterns" "$files"' EXIT
sed 's/\r$//' .private-denylist | grep -vE '^[[:space:]]*(#|$)' > "$patterns" || true
if [ ! -s "$patterns" ]; then
  echo "BLOCKED: empty local privacy policy"; exit 1
fi
# These ignored maintainer inputs are never publishable, even in a test repository.
git ls-files --cached --others --exclude-standard -z -- . ':!/.private-denylist' ':!/.sanitize-rules.local.json' > "$files"
fail=0
count=0
while IFS= read -r tok || [ -n "$tok" ]; do
  count=$((count+1))
  code=0; grep -Eqs -e "$tok" /dev/null || code=$?
  if [ "$code" -gt 1 ]; then echo "BLOCKED: invalid privacy rule $count"; exit 1; fi
  while IFS= read -r -d '' f; do
    [ -f "$f" ] || continue
    code=0; grep -IiqEs -e "$tok" -- "$f" || code=$?
    if [ "$code" -gt 1 ]; then echo "BLOCKED: cannot scan $f"; exit 1; fi
    if [ "$code" -eq 0 ]; then
      echo "LEAK  $f (privacy rule $count)"; fail=1
    fi
  done < "$files"
done < "$patterns"
[ "$fail" -ne 0 ] || echo "full-repo scan clean"
exit "$fail"
