#!/usr/bin/env bash
# Prints the newest VPS backup dir's epoch mtime (or nothing). Positional args avoid
# the PowerShell->wsl->ssh quoting trap. $1=ssh target  $2=dest base dir
set -u
TARGET="$1"; DESTBASE="$2"
ssh -i ~/.ssh/hermes-hostinger -o ConnectTimeout=15 -o StrictHostKeyChecking=no -o LogLevel=ERROR "$TARGET" \
  "ls -1dt '$DESTBASE'/20*/ 2>/dev/null | grep -v FAILED | head -1 | xargs -r stat -c %Y"
