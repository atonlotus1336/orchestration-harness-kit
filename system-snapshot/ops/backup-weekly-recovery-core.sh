#!/usr/bin/env bash
# Build the non-secret recovery bundle that accompanies weekly/monthly Projects
# snapshots. Secrets are excluded by pattern; only configuration/state material
# intended for reconstruction is copied.
set -euo pipefail

KIND="${1:-weekly}"
STAMP="${2:-$(date +%Y%m%d-%H%M%S)}"
case "$KIND" in
  weekly|monthly) ;;
  *) echo "RECOVERY FAILED: kind must be weekly or monthly"; exit 64 ;;
esac

SSH_TARGET="root@__TAILSCALE_IPV4__"
SSH_KEY="/home/__LINUX_USER__/.ssh/hermes-hostinger"
RECOVERY_ROOT="__WSL_WORKSPACE__/.recovery"
CURRENT_ROOT="${RECOVERY_ROOT}/weekly-current"
STAGE="/tmp/__PRIVATE_TOKEN_02__-weekly-recovery-${STAMP}"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new -o IPQoS=throughput)
RSYNC_SSH="ssh -i ${SSH_KEY} -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new -o IPQoS=throughput"

cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

[[ -f "$SSH_KEY" ]] || { echo "RECOVERY FAILED: SSH key missing: $SSH_KEY"; exit 1; }
command -v rsync >/dev/null || { echo "RECOVERY FAILED: rsync is unavailable"; exit 1; }
command -v python3 >/dev/null || { echo "RECOVERY FAILED: python3 is unavailable"; exit 1; }

rm -rf "$STAGE"
mkdir -p "$STAGE/vps-state/opt-data"

# /opt/data is the documented VPS-only application state location. Pull it
# through an explicit secret denylist. Missing/empty directories are valid.
rsync -aS --delete --delete-excluded \
  --exclude='*.env' --exclude='.env' --exclude='.env.*' \
  --exclude='**/auth.json' --exclude='**/*password*' --exclude='**/*secret*' \
  --exclude='**/*token*' --exclude='**/*.key' --exclude='**/*.pem' \
  --exclude='**/id_ed25519*' --exclude='**/id_rsa*' \
  -e "$RSYNC_SSH" "$SSH_TARGET:/opt/data/" "$STAGE/vps-state/opt-data/"

# Redact common inline secret fields from text configuration. Filename excludes
# above handle dedicated secret files; this protects settings such as
# password: value or {"api_key": "value"} embedded in a config document.
STAGE="$STAGE" python3 - <<'PY'
import os
import re
from pathlib import Path

root = Path(os.environ["STAGE"]) / "vps-state"
field = r"(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|access[_-]?key)"
patterns = [
    re.compile(rf"(\b{field}\b\s*[:=]\s*)([^\r\n#}}]+)", re.IGNORECASE),
    re.compile(rf"([\"']{field}[\"']\s*:\s*[\"'])(.*?)([\"'])", re.IGNORECASE),
]
for path in root.rglob("*"):
    if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
        continue
    data = path.read_bytes()
    if b"\x00" in data:
        continue
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        continue
    updated = patterns[0].sub(r"\1<REDACTED>", text)
    updated = patterns[1].sub(r"\1<REDACTED>\3", updated)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
PY

# Keep reconstruction metadata, never credential values. The GitHub kit commit
# is recorded when the local checkout is available; absence is non-fatal.
KIT_COMMIT=""
if [[ -d __WSL_WORKSPACE__/export/orchestration-layer/.git ]]; then
  KIT_COMMIT=$(git -C __WSL_WORKSPACE__/export/orchestration-layer rev-parse HEAD 2>/dev/null || true)
fi

RECOVERY_ROOT="$RECOVERY_ROOT" STAGE="$STAGE" KIND="$KIND" STAMP="$STAMP" KIT_COMMIT="$KIT_COMMIT" python3 - <<'PY'
import json
import os
from pathlib import Path

stage = Path(os.environ["STAGE"])
root = stage / "vps-state"
files = []
total_bytes = 0
for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    rel = path.relative_to(root).as_posix()
    size = path.stat().st_size
    files.append({"path": rel, "bytes": size})
    total_bytes += size

manifest = {
    "schema": "__PRIVATE_TOKEN_02__-recovery-manifest/v1",
    "created_at": os.environ["STAMP"],
    "backup_kind": os.environ["KIND"],
    "source": "Hostinger VPS /opt/data",
    "secret_policy": "plaintext secrets, tokens, passwords, private keys, and env files excluded",
    "github_kit_commit": os.environ.get("KIT_COMMIT") or None,
    "vps_state": {
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
    },
    "credentials_to_reestablish": [
        "Hostinger SSH access",
        "provider/API credentials required by restored services",
        "bot or webhook credentials required by restored workflows",
    ],
}
(stage / "recovery-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY

rm -rf "$CURRENT_ROOT"
mkdir -p "$(dirname "$CURRENT_ROOT")"
mv "$STAGE" "$CURRENT_ROOT"
trap - EXIT
echo "RECOVERY OK kind=$KIND source=/opt/data manifest=${CURRENT_ROOT}/recovery-manifest.json"
