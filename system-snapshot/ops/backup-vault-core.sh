#!/usr/bin/env bash
# Plan 01 Task 1.2 core (bash, no PowerShell quoting). Args:
#   $1 = ssh target (root@host)   $2 = dest base dir   $3 = stamp   $4 = ssh key path
#   $5 = trusted-own-vps host (scrub skipped when target matches)   $6 = windows user
# Emits BACKUP OK / BACKUP FAILED and exits nonzero on failure (partial renamed .FAILED).
set -u
SSH_TARGET="$1"; DEST_BASE="$2"; STAMP="$3"; SSH_KEY="$4"; TRUSTED="$5"; WINUSER="$6"
SSH="ssh -i $SSH_KEY -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new"
STAGE="/tmp/__PRIVATE_TOKEN_02__-backup-$STAMP"; mkdir -p "$STAGE"
fail() { echo "BACKUP FAILED: $1"; $SSH "$SSH_TARGET" "mv $DEST_BASE/$STAMP $DEST_BASE/$STAMP.FAILED 2>/dev/null"; rm -rf "$STAGE"; exit 1; }

declare -A SRC=(
  [memoryweaver]="__WSL_WORKSPACE__/.claude/Memory/memoryweaver"
  [rag-db]="__WSL_WORKSPACE__/.rag/db"
  [graphrag-output]="__WSL_WORKSPACE__/.rag/graphrag/output"
  [planning]="__WSL_WORKSPACE__/.planning"
  [profiles-windows]="/mnt/c/Users/$WINUSER/.hermes/profiles"
)

# live WSL profiles tar + n8n export into the staging meta dir
tar -czf "$STAGE/wsl-hermes-profiles.tgz" -C /home/__LINUX_USER__/.hermes profiles 2>/dev/null || echo "note: wsl profiles tar skipped"
if [ -f /mnt/d/n8n-key.txt ]; then
  curl -s --max-time 30 -H "X-N8N-API-KEY: $(tr -d '\r\n' < /mnt/d/n8n-key.txt)" \
    "https://n8n.example.com/api/v1/workflows?limit=250" > "$STAGE/n8n-workflows-export.json" 2>/dev/null || echo "note: n8n export skipped"
fi

# memoryweaver status for restore-verify
MW=$(export MEMORYWEAVER_DATA_DIR=__WSL_WORKSPACE__/.claude/Memory/memoryweaver; \
  cd __WSL_WORKSPACE__/.claude/memoryweaver && \
  /home/__LINUX_USER__/.local/share/memoryweaver-mcp/venv/bin/python3.11 -m memoryweaver status 2>/dev/null | tail -20)

# scrub only for non-trusted off-machine dests
case "$SSH_TARGET" in
  *"$TRUSTED"*) echo "SCRUB SKIPPED: trusted-own-VPS dest" ;;
  *)
    DENY='__PRIVATE_TOKEN_01__|__PRIVATE_TOKEN_02__|__PRIVATE_TOKEN_03__|__PRIVATE_TOKEN_04__|__PRIVATE_TOKEN_05__|community-mission|practice-loop|CONSENT-LEDGER-SLOT'
    if grep -rIniE "$DENY" "$STAGE" | head -5 | grep -q .; then echo "SCRUB HIT"; mv "$STAGE" "$STAGE.FAILED"; exit 2; fi ;;
esac

# manifest (per-source aggregate sha256 + counts)
MAN="$STAGE/BACKUP-MANIFEST.json"
{ echo "{"; echo "  \"ts\": \"$(date -Iseconds)\","; echo "  \"stamp\": \"$STAMP\","; \
  echo "  \"dest\": \"$SSH_TARGET:$DEST_BASE/$STAMP\","; \
  printf '  "memoryweaver_status_tail": %s,\n' "$(printf '%s' "$MW" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"; \
  echo "  \"sources\": ["; first=1
  for name in "${!SRC[@]}"; do
    p="${SRC[$name]}"; [ -d "$p" ] || continue
    agg=$(cd "$p" && find . -type f -not -path '*/spool/*' -print0 | xargs -0 sha256sum 2>/dev/null | sha256sum | cut -d' ' -f1)
    cnt=$(cd "$p" && find . -type f -not -path '*/spool/*' 2>/dev/null | wc -l)
    [ $first -eq 1 ] && first=0 || echo ","
    printf '    {"name":"%s","path":"%s","aggregate_sha256":"%s","file_count":%s}' "$name" "$p" "$agg" "$cnt"
  done; echo ""; echo "  ]"; echo "}"; } > "$MAN"

# push
$SSH "$SSH_TARGET" "mkdir -p $DEST_BASE/$STAMP" || fail "dest mkdir"
LINK=""; $SSH "$SSH_TARGET" "test -e $DEST_BASE/latest" && LINK="--link-dest=$DEST_BASE/latest"
for name in "${!SRC[@]}"; do
  p="${SRC[$name]}"; [ -d "$p" ] || continue
  L=""; [ -n "$LINK" ] && L="--link-dest=$DEST_BASE/latest/$name"
  rsync -a --delete --exclude='spool/' --exclude='__pycache__/' --exclude='*.lock' $L \
    -e "$SSH" "$p/" "$SSH_TARGET:$DEST_BASE/$STAMP/$name/" || fail "rsync $name"
done
rsync -a -e "$SSH" "$STAGE/" "$SSH_TARGET:$DEST_BASE/$STAMP/meta/" || fail "rsync meta"
$SSH "$SSH_TARGET" "ln -sfn $DEST_BASE/$STAMP $DEST_BASE/latest; find $DEST_BASE -maxdepth 1 -type d -name '20*' -mtime +30 -exec rm -rf {} +"
rm -rf "$STAGE"
echo "BACKUP OK dest=$SSH_TARGET:$DEST_BASE/$STAMP"
