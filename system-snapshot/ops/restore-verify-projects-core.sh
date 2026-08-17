#!/usr/bin/env bash
# Restore a representative cross-project sample from the latest VPS snapshot,
# then compare every restored file byte-for-byte with the live source.
set -euo pipefail

KIND="${1:-weekly}"
case "$KIND" in
  weekly) DEST_ROOT="/srv/isolatedbackups/weekly/projects" ;;
  monthly) DEST_ROOT="/srv/isolatedbackups/monthly/projects" ;;
  *) echo "RESTORE-VERIFY FAILED: kind must be weekly or monthly"; exit 64 ;;
esac

SOURCE="__WSL_WORKSPACE__/Projects"
SSH_TARGET="root@__TAILSCALE_IPV4__"
SSH_KEY="/home/__LINUX_USER__/.ssh/hermes-hostinger"
EXCLUDE_FILE="__WSL_WORKSPACE__/scripts/ops/projects-backup-excludes.txt"
SSH_TRANSPORT="ssh -i ${SSH_KEY} -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new)
LIST="/tmp/__PRIVATE_TOKEN_02__-projects-restore-list.$$.bin"
RESTORE="/tmp/__PRIVATE_TOKEN_02__-projects-restore.$$.dir"

cleanup() { rm -rf -- "$LIST" "$RESTORE"; }
trap cleanup EXIT
mkdir -p "$RESTORE"
: > "$LIST"

snapshot=$("${SSH[@]}" "$SSH_TARGET" readlink -f "${DEST_ROOT}/latest")
[[ "$snapshot" == "${DEST_ROOT}/"* ]] || { echo "RESTORE-VERIFY FAILED: invalid latest target: $snapshot"; exit 1; }

declare -A chosen_top=()
declare -A chosen_path=()
count=0
for top_dir in "$SOURCE"/*/; do
  [[ -d "$top_dir" ]] || continue
  while IFS= read -r -d '' absolute; do
    relative="${absolute#${SOURCE}/}"
    top="${relative%%/*}"
    if [[ -z "${chosen_top[$top]+x}" ]]; then
      printf '%s\0' "$relative" >> "$LIST"
      chosen_top[$top]=1
      chosen_path[$relative]=1
      ((count+=1))
    fi
  done < <(find "$top_dir" -type f -size -64M \
    -not -path '*/node_modules/*' -not -path '*/.venv/*' -not -path '*/venv/*' \
    -not -path '*/__pycache__/*' -not -path '*/.cache/*' -not -path '*/Library/*' \
    -not -path '*/Temp/*' -not -path '*/Logs/*' -not -path '*/bin/*' -not -path '*/obj/*' \
    -not -path '*/build/*' -not -path '*/dist/*' -not -name '.env' -not -name '.env.*' \
    -not -name 'auth.json' -not -name 'id_ed25519*' -not -name 'id_rsa*' \
    -not -name '*.key' -not -name '*.pem' -print0 -quit)
done

while (( count < 100 )) && IFS= read -r -d '' absolute; do
  relative="${absolute#${SOURCE}/}"
  if [[ -z "${chosen_path[$relative]+x}" ]]; then
    printf '%s\0' "$relative" >> "$LIST"
    chosen_path[$relative]=1
    ((count+=1))
  fi
done < <(find "$SOURCE" -type f -size -64M \
  -not -path '*/node_modules/*' -not -path '*/.venv/*' -not -path '*/venv/*' \
  -not -path '*/__pycache__/*' -not -path '*/.cache/*' -not -path '*/Library/*' \
  -not -path '*/Temp/*' -not -path '*/Logs/*' -not -path '*/bin/*' -not -path '*/obj/*' \
  -not -path '*/build/*' -not -path '*/dist/*' -not -name '.env' -not -name '.env.*' \
  -not -name 'auth.json' -not -name 'id_ed25519*' -not -name 'id_rsa*' \
  -not -name '*.key' -not -name '*.pem' -print0 | head -z -n $((100 - count)))

(( count > 0 )) || { echo "RESTORE-VERIFY FAILED: no sample files selected"; exit 1; }
rsync -a --from0 --files-from="$LIST" -e "$SSH_TRANSPORT" \
  "${SSH_TARGET}:${snapshot}/" "$RESTORE/"

verified=0
bytes=0
while IFS= read -r -d '' relative; do
  src="${SOURCE}/${relative}"
  restored="${RESTORE}/${relative}"
  [[ -f "$restored" ]] || { echo "RESTORE-VERIFY FAILED: missing restored file: $relative"; exit 2; }
  cmp -s -- "$src" "$restored" || { echo "RESTORE-VERIFY FAILED: content mismatch: $relative"; exit 2; }
  size=$(stat -c %s -- "$restored")
  bytes=$((bytes + size))
  ((verified+=1))
done < "$LIST"

echo "RESTORE-VERIFY OK snapshot=$snapshot files=$verified bytes=$bytes"
