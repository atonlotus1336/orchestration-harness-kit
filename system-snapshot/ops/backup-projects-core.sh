#!/usr/bin/env bash
# Full file-level Projects snapshots to the trusted VPS, accompanied by a
# sanitized VPS recovery bundle and non-secret manifest. This does not create or
# push Git repositories. Unchanged files are hard-linked to the prior snapshot.
set -euo pipefail

KIND="${1:-}"
case "$KIND" in
  weekly|monthly) ;;
  *) echo "BACKUP FAILED: kind must be weekly or monthly"; exit 64 ;;
esac

SOURCE="__WSL_WORKSPACE__/Projects"
RECOVERY_CORE="__WSL_WORKSPACE__/scripts/ops/backup-weekly-recovery-core.sh"
RECOVERY_SOURCE="__WSL_WORKSPACE__/.recovery/weekly-current"
EXCLUDE_FILE="__WSL_WORKSPACE__/scripts/ops/projects-backup-excludes.txt"
SSH_TARGET="root@__TAILSCALE_IPV4__"
SSH_KEY="/home/__LINUX_USER__/.ssh/hermes-hostinger"
RESERVE_BYTES=$((20 * 1024 * 1024 * 1024))
WEEKLY_KEEP=4
MONTHLY_KEEP=3
case "$KIND" in
  weekly) DEST_ROOT="/srv/isolatedbackups/weekly/projects"; KEEP="$WEEKLY_KEEP" ;;
  monthly) DEST_ROOT="/srv/isolatedbackups/monthly/projects"; KEEP="$MONTHLY_KEEP" ;;
esac
STAMP="$(date +%Y%m%d-%H%M%S)"
SNAPSHOT="${KIND}-${STAMP}"
PARTIAL=".partial-${SNAPSHOT}"
FINAL_PATH="${DEST_ROOT}/${SNAPSHOT}"
PARTIAL_PATH="${DEST_ROOT}/${PARTIAL}"
SSH_TRANSPORT="ssh -i ${SSH_KEY} -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new -o IPQoS=throughput"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new -o IPQoS=throughput)
LOCK_HELD=0
PARTIAL_ACTIVE=0

cleanup() {
  if [[ "$PARTIAL_ACTIVE" -eq 1 ]]; then
    case "$PARTIAL_PATH" in
      "$DEST_ROOT"/.partial-*)
        "${SSH[@]}" "$SSH_TARGET" rm -rf -- "$PARTIAL_PATH" >/dev/null 2>&1 || true
        ;;
      *) echo "BACKUP CLEANUP REFUSED: unsafe partial path: $PARTIAL_PATH" >&2 ;;
    esac
  fi
  if [[ "$LOCK_HELD" -eq 1 ]]; then
    "${SSH[@]}" "$SSH_TARGET" rmdir "${DEST_ROOT}/.backup.lock" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

[[ -d "$SOURCE" ]] || { echo "BACKUP FAILED: source missing: $SOURCE"; exit 1; }
[[ -f "$EXCLUDE_FILE" ]] || { echo "BACKUP FAILED: exclusion policy missing: $EXCLUDE_FILE"; exit 1; }
[[ -x "$RECOVERY_CORE" ]] || { echo "BACKUP FAILED: recovery exporter missing or not executable: $RECOVERY_CORE"; exit 1; }
[[ -f "$SSH_KEY" ]] || { echo "BACKUP FAILED: SSH key missing: $SSH_KEY"; exit 1; }
command -v rsync >/dev/null || { echo "BACKUP FAILED: rsync is unavailable"; exit 1; }

"${SSH[@]}" "$SSH_TARGET" mkdir -p "$DEST_ROOT"
if ! "${SSH[@]}" "$SSH_TARGET" mkdir "${DEST_ROOT}/.backup.lock"; then
  echo "BACKUP FAILED: another Projects backup holds ${DEST_ROOT}/.backup.lock"
  exit 1
fi
LOCK_HELD=1

# A failed run must not become permanent retention. The lock ensures no current
# backup owns one of these staging directories while they are removed.
"${SSH[@]}" "$SSH_TARGET" bash -s -- "$DEST_ROOT" <<'REMOTE_PARTIAL_CLEANUP'
set -euo pipefail
root="$1"
case "$root" in
  /srv/isolatedbackups/weekly/projects|/srv/isolatedbackups/monthly/projects) ;;
  *) echo "BACKUP CLEANUP REFUSED: $root"; exit 65 ;;
esac
find "$root" -maxdepth 1 -mindepth 1 -type d -name '.partial-*' -print -exec rm -rf -- {} +
REMOTE_PARTIAL_CLEANUP

echo "RECOVERY EXPORT START kind=$KIND"
"$RECOVERY_CORE" "$KIND" "$STAMP"
[[ -f "$RECOVERY_SOURCE/recovery-manifest.json" ]] || { echo "BACKUP FAILED: recovery manifest missing"; exit 1; }
echo "RECOVERY EXPORT OK source=$RECOVERY_SOURCE"

"${SSH[@]}" "$SSH_TARGET" rm -rf "$PARTIAL_PATH"
"${SSH[@]}" "$SSH_TARGET" mkdir -p "$PARTIAL_PATH"
PARTIAL_ACTIVE=1

LINK_ARGS=()
if "${SSH[@]}" "$SSH_TARGET" test -e "${DEST_ROOT}/latest"; then
  LINK_ARGS+=("--link-dest=${DEST_ROOT}/latest")
fi

echo "BACKUP PREFLIGHT kind=$KIND snapshot=$SNAPSHOT"
estimate_output=$(LC_ALL=C rsync -aSni --delete --stats --exclude-from="$EXCLUDE_FILE" "${LINK_ARGS[@]}" -e "$SSH_TRANSPORT" \
  "$SOURCE/" "${SSH_TARGET}:${PARTIAL_PATH}/")
estimate_bytes=$(printf '%s\n' "$estimate_output" | awk -F': ' '/Total transferred file size:/ {gsub(/,/ , "", $2); print $2}' | awk '{print $1}' | tail -1)
[[ "$estimate_bytes" =~ ^[0-9]+$ ]] || { echo "BACKUP FAILED: could not estimate transfer size"; exit 1; }
RECOVERY_LINK_ARGS=()
if "${SSH[@]}" "$SSH_TARGET" test -e "${DEST_ROOT}/latest/recovery"; then
  RECOVERY_LINK_ARGS+=("--link-dest=../latest/recovery")
fi
recovery_estimate_output=$(LC_ALL=C rsync -aSni --delete --stats "${RECOVERY_LINK_ARGS[@]}" -e "$SSH_TRANSPORT" \
  "$RECOVERY_SOURCE/" "${SSH_TARGET}:${PARTIAL_PATH}/recovery/")
recovery_estimate_bytes=$(printf '%s\n' "$recovery_estimate_output" | awk -F': ' '/Total transferred file size:/ {gsub(/,/ , "", $2); print $2}' | awk '{print $1}' | tail -1)
[[ "$recovery_estimate_bytes" =~ ^[0-9]+$ ]] || { echo "BACKUP FAILED: could not estimate recovery bundle transfer size"; exit 1; }
estimate_bytes=$((estimate_bytes + recovery_estimate_bytes))
remote_free=$("${SSH[@]}" "$SSH_TARGET" df -B1 --output=avail "$DEST_ROOT" | awk 'NR==2 {print $1}')
[[ "$remote_free" =~ ^[0-9]+$ ]] || { echo "BACKUP FAILED: could not read VPS free space"; exit 1; }
required=$((estimate_bytes + RESERVE_BYTES))
echo "BACKUP SPACE estimate_bytes=$estimate_bytes remote_free_bytes=$remote_free reserve_bytes=$RESERVE_BYTES"
if (( remote_free < required )); then
  echo "BACKUP FAILED: VPS would fall below the 20 GiB reserve"
  exit 1
fi

# 2026-07-25 TOCTOU FIX: verify-after-copy on a LIVE tree fails whenever anything writes between
# the copy and the checksum pass (seen: .git/FETCH_HEAD + refs/remotes churn, an in-flight
# .planning/evidence run json stamped DURING the backup). That is drift, not corruption — but
# silently ignoring paths would weaken the gate. Instead: converge. Re-copy picks up the drifted
# files; verify re-checks everything. Only a tree that will not quiesce across MAX_SYNC_PASSES
# fails — and that failure names its input: a file being continuously rewritten.
MAX_SYNC_PASSES=3
pass=1
while :; do
  echo "BACKUP COPY START pass=$pass source=$SOURCE dest=${SSH_TARGET}:${PARTIAL_PATH}"
  rsync -azS --delete --partial --partial-dir=.rsync-partial --exclude-from="$EXCLUDE_FILE" "${LINK_ARGS[@]}" \
    -e "$SSH_TRANSPORT" "$SOURCE/" "${SSH_TARGET}:${PARTIAL_PATH}/"

  echo "RECOVERY COPY START pass=$pass source=$RECOVERY_SOURCE dest=${SSH_TARGET}:${PARTIAL_PATH}/recovery"
  rsync -azS --delete --partial --partial-dir=.rsync-partial "${RECOVERY_LINK_ARGS[@]}" \
    -e "$SSH_TRANSPORT" "$RECOVERY_SOURCE/" "${SSH_TARGET}:${PARTIAL_PATH}/recovery/"

  echo "BACKUP CHECKSUM VERIFY START pass=$pass"
  verify_output=$(rsync -rlcni --delete --exclude='/recovery/' --exclude-from="$EXCLUDE_FILE" -e "$SSH_TRANSPORT" \
    "$SOURCE/" "${SSH_TARGET}:${PARTIAL_PATH}/")
  recovery_verify_output=$(rsync -rlcni --delete -e "$SSH_TRANSPORT" \
    "$RECOVERY_SOURCE/" "${SSH_TARGET}:${PARTIAL_PATH}/recovery/")
  verify_output="${verify_output}${recovery_verify_output}"
  [[ -z "$verify_output" ]] && break

  if (( pass >= MAX_SYNC_PASSES )); then
    printf '%s\n' "$verify_output" | head -100
    echo "BACKUP FAILED: tree still changing after $MAX_SYNC_PASSES copy+verify passes (files above kept mutating throughout)"
    exit 2
  fi
  drift_count=$(printf '%s\n' "$verify_output" | wc -l)
  echo "BACKUP VERIFY DRIFT pass=$pass files=$drift_count — live writes during copy; re-syncing:"
  printf '%s\n' "$verify_output" | head -20
  pass=$((pass+1))
done

"${SSH[@]}" "$SSH_TARGET" mv "$PARTIAL_PATH" "$FINAL_PATH"
PARTIAL_ACTIVE=0
"${SSH[@]}" "$SSH_TARGET" ln -sfn "$SNAPSHOT" "${DEST_ROOT}/latest"

manifest_line="completed=$(date -Iseconds) kind=${KIND} snapshot=${SNAPSHOT} transfer_bytes=${estimate_bytes} policy=exclude-rebuildable-and-plaintext-secrets checksum_verify=clean"
printf '%s\n' "$manifest_line" | "${SSH[@]}" "$SSH_TARGET" tee "${DEST_ROOT}/${SNAPSHOT}.manifest.txt" >/dev/null

"${SSH[@]}" "$SSH_TARGET" bash -s -- "$DEST_ROOT" "$KIND" "$KEEP" <<'REMOTE_RETENTION'
set -euo pipefail
root="$1"; kind="$2"; keep="$3"
case "$root" in
  /srv/isolatedbackups/weekly/projects|/srv/isolatedbackups/monthly/projects) ;;
  *) echo "BACKUP RETENTION REFUSED: $root"; exit 65 ;;
esac
mapfile -t snapshots < <(find "$root" -maxdepth 1 -mindepth 1 -type d -name "${kind}-*" -printf '%f\n' | sort -r)
for ((i=keep; i<${#snapshots[@]}; i++)); do
  rm -rf -- "$root/${snapshots[$i]}"
  rm -f -- "$root/${snapshots[$i]}.manifest.txt"
done
REMOTE_RETENTION

remote_free_after=$("${SSH[@]}" "$SSH_TARGET" df -B1 --output=avail "$DEST_ROOT" | awk 'NR==2 {print $1}')
echo "BACKUP OK snapshot=$SNAPSHOT transfer_bytes=$estimate_bytes remote_free_bytes=$remote_free_after checksum_verify=clean"
