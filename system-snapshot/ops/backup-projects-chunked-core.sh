#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# PHASE 1 of the chunked Projects backup  (created 2026-07-23)
#
# WHY THIS EXISTS
# The monolithic backup-projects-core.sh needs ~20 minutes of sustained reads
# across 321k files. G: is an external USB drive whose transport resets roughly
# every ~50 minutes (143 UASP resets in 5 days), so a 20-minute job is a coin
# flip -- and losing it discards ALL 20 minutes. Projects/ went 13 days with no
# backup as a result, and 5 projects have never been backed up at all.
#
# WHAT THIS DOES DIFFERENTLY
# Sync ONE PROJECT AT A TIME into a single mutable tree (live/). A reset kills
# one project, not the run; the rest continue and the failed one retries next
# pass. --partial-dir makes retries cumulative.
#
# THIS SCRIPT DOES NOT PRODUCE A RESTORE TARGET.
# live/ is a working tree and is HALF-UPDATED by design at any given moment.
# Never restore from it. Phase 2 (separate, server-side, no USB) is what turns
# live/ into an atomic snapshot and repoints `latest`.
#
# EXIT CODES (derived from the LEDGER, not from the last rsync)
#   0 = every project synced within the freshness window
#   3 = ran fine, but some projects are still stale (they are named)
#   1 = hard failure: source missing, VPS unreachable, mount dead at start
# ---------------------------------------------------------------------------
set -uo pipefail   # deliberately NOT -e: one project failing must not abort the run

SOURCE="__WSL_WORKSPACE__/Projects"
# The wrapper stages a copy of the exclude list onto WSL-local ext4 and points
# here, so a mid-run drive reset cannot make rsync lose its exclusion policy.
# Falls back to the on-drive copy when run standalone.
EXCLUDE_FILE="${EXCLUDE_FILE_OVERRIDE:-__WSL_WORKSPACE__/scripts/ops/projects-backup-excludes.txt}"
SSH_TARGET="root@__TAILSCALE_IPV4__"
SSH_KEY="/home/__LINUX_USER__/.ssh/hermes-hostinger"
DEST_ROOT="/srv/isolatedbackups/projects"
LIVE="${DEST_ROOT}/live"
# ONE TSV, not one file per project. Project names contain spaces ("Quotes App",
# "language pets"), so using them as remote filenames would re-split in the remote
# shell and write to the wrong path. As TSV field values they are never tokens.
LEDGER_TSV="${DEST_ROOT}/.ledger.tsv"

FRESH_HOURS="${FRESH_HOURS:-36}"                    # a project is "fresh" if synced within this
PER_PROJECT_TIMEOUT="${PER_PROJECT_TIMEOUT:-600}"   # seconds; caps one hung chunk
TOTAL_BUDGET_MIN="${TOTAL_BUDGET_MIN:-45}"          # stop starting new chunks past this
ONLY_PROJECT="${ONLY_PROJECT:-}"                    # optional: sync just this one (testing)
DRY_RUN="${DRY_RUN:-0}"                             # 1 = rsync --dry-run, no writes

SSH_TRANSPORT="ssh -i ${SSH_KEY} -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new -o IPQoS=throughput"
SSH=(ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o StrictHostKeyChecking=accept-new -o IPQoS=throughput)

START_EPOCH=$(date +%s)
now() { date +%s; }
say() { printf '%s\n' "$*"; }
mount_alive() { cat __WSL_WORKSPACE__/STATUS.md >/dev/null 2>&1; }

# --- preflight --------------------------------------------------------------
mount_alive || { say "CHUNKED BACKUP FAILED: /mnt/d is stale at start (remount first)"; exit 1; }
[[ -d "$SOURCE" ]]       || { say "CHUNKED BACKUP FAILED: source missing: $SOURCE"; exit 1; }
[[ -f "$EXCLUDE_FILE" ]] || { say "CHUNKED BACKUP FAILED: exclusion policy missing"; exit 1; }
[[ -f "$SSH_KEY" ]]      || { say "CHUNKED BACKUP FAILED: SSH key missing"; exit 1; }
command -v rsync >/dev/null || { say "CHUNKED BACKUP FAILED: rsync unavailable"; exit 1; }
"${SSH[@]}" "$SSH_TARGET" true 2>/dev/null || { say "CHUNKED BACKUP FAILED: VPS unreachable"; exit 1; }

RSYNC_EXTRA=()
if [[ "$DRY_RUN" == "1" ]]; then
  RSYNC_EXTRA+=(--dry-run)
  say "*** DRY RUN -- no data will be written to the VPS ***"
fi

# --- seed live/ from the existing snapshot, ONCE ------------------------------
# Hardlink-seed so the first chunked pass transfers only the DELTA since the last
# good snapshot (2026-07-10) instead of re-sending ~4.6 GB over a failing link.
# Costs ~nothing on disk (hardlinks). Server-side only -- never touches the USB drive.
#
# SAFETY: rsync must NOT be given --inplace. Default rsync writes a temp file and
# renames it, which BREAKS the hardlink and leaves the old snapshot's inode intact.
# With --inplace it would write THROUGH the hardlink and silently corrupt the
# 2026-07-10 snapshot -- currently the ONLY Projects backup in existence.
# Do not add --inplace to the rsync calls below.
if ! "${SSH[@]}" "$SSH_TARGET" test -d "$LIVE"; then
  if [[ "$DRY_RUN" == "1" ]]; then
    say "DRY RUN: would seed live/ from ${DEST_ROOT}/latest (skipping)"
  elif "${SSH[@]}" "$SSH_TARGET" test -e "${DEST_ROOT}/latest"; then
    say "SEEDING live/ from ${DEST_ROOT}/latest via hardlinks (server-side, no USB)"
    "${SSH[@]}" "$SSH_TARGET" "cp -al \"\$(readlink -f '${DEST_ROOT}/latest')\" '${LIVE}'" \
      || { say "CHUNKED BACKUP FAILED: could not seed live/"; exit 1; }
    say "SEED OK"
  else
    say "No prior snapshot to seed from; live/ starts empty (first pass = full transfer)"
    "${SSH[@]}" "$SSH_TARGET" "mkdir -p '${LIVE}'"
  fi
fi

# --- build the chunk list from the LIVE SOURCE -------------------------------
# Deliberately enumerated from the drive, never from the VPS snapshot: the
# snapshot is 13 days old and is missing Aegis, language pets, Quotes App,
# Stocks and tradebot entirely.
mapfile -t PROJECTS < <(find "$SOURCE" -maxdepth 1 -mindepth 1 -type d -printf '%f\n' 2>/dev/null | sort)
if [[ -n "$ONLY_PROJECT" ]]; then
  PROJECTS=("$ONLY_PROJECT")
  say "ONLY_PROJECT set -- restricting this run to: $ONLY_PROJECT"
else
  PROJECTS+=("_root")   # pseudo-chunk: loose files at the Projects/ root
fi
[[ ${#PROJECTS[@]} -gt 0 ]] || { say "CHUNKED BACKUP FAILED: no projects found under $SOURCE"; exit 1; }

# --- read the ledger, order STALEST FIRST ------------------------------------
# Under a flaky link, whatever has gone longest without a backup deserves the
# first and best shot at a stable window. Never-synced projects sort to the top.
declare -A LAST_OK
while IFS=$'\t' read -r e p; do
  [[ -n "${p:-}" && "${e:-}" =~ ^[0-9]+$ ]] && LAST_OK["$p"]="$e"
done < <("${SSH[@]}" "$SSH_TARGET" "cat '${LEDGER_TSV}' 2>/dev/null" 2>/dev/null)

mapfile -t PROJECTS < <(
  for p in "${PROJECTS[@]}"; do printf '%s\t%s\n' "${LAST_OK[$p]:-0}" "$p"; done \
    | sort -n -k1,1 | cut -f2-
)

NEVER=()
for p in "${PROJECTS[@]}"; do [[ -z "${LAST_OK[$p]:-}" ]] && NEVER+=("$p"); done

# Rebuild the whole TSV and ship it as stdin, then rename into place remotely.
# No project name ever appears inside the remote command string, so spaces in
# "Quotes App" / "language pets" cannot re-split. Called after every success.
write_ledger() {
  [[ "$DRY_RUN" == "1" ]] && return 0
  local out="" k
  for k in "${!LAST_OK[@]}"; do out+="${LAST_OK[$k]}"$'\t'"${k}"$'\n'; done
  printf '%s' "$out" | "${SSH[@]}" "$SSH_TARGET" \
    "cat > '${LEDGER_TSV}.tmp' && mv -f '${LEDGER_TSV}.tmp' '${LEDGER_TSV}'" 2>/dev/null
}

say "CHUNKED BACKUP START chunks=${#PROJECTS[@]} fresh_window=${FRESH_HOURS}h budget=${TOTAL_BUDGET_MIN}m"
[[ ${#NEVER[@]} -gt 0 ]] && say "NEVER SYNCED (${#NEVER[@]}): ${NEVER[*]}"
say ""

# --- sync loop ---------------------------------------------------------------
OK_COUNT=0; FAIL_COUNT=0; SKIP_COUNT=0
FAILED_LIST=(); SKIPPED_LIST=()
declare -A NEW_OK

for (( idx=0; idx < ${#PROJECTS[@]}; idx++ )); do
  proj="${PROJECTS[$idx]}"

  elapsed_min=$(( ($(now) - START_EPOCH) / 60 ))
  if (( elapsed_min >= TOTAL_BUDGET_MIN )); then
    say "BUDGET EXHAUSTED after ${elapsed_min}m -- deferring remaining $(( ${#PROJECTS[@]} - idx )) chunk(s)"
    for (( j=idx; j < ${#PROJECTS[@]}; j++ )); do SKIPPED_LIST+=("${PROJECTS[$j]}"); ((SKIP_COUNT++)); done
    break
  fi

  if ! mount_alive; then
    say "MOUNT DIED -- /mnt/d went stale before '$proj'. Aborting remaining chunks."
    for (( j=idx; j < ${#PROJECTS[@]}; j++ )); do SKIPPED_LIST+=("${PROJECTS[$j]}"); ((SKIP_COUNT++)); done
    break
  fi

  t0=$(now)
  if [[ "$proj" == "_root" ]]; then
    # Loose files at the Projects/ root only. '--exclude=/*/' hides every
    # top-level directory, and rsync's --delete does NOT remove excluded files
    # on the receiver, so the per-project trees are protected from deletion here.
    timeout "$PER_PROJECT_TIMEOUT" rsync -azS --delete --partial --partial-dir=.rsync-partial \
      "${RSYNC_EXTRA[@]}" --exclude-from="$EXCLUDE_FILE" --exclude='/*/' \
      -e "$SSH_TRANSPORT" "$SOURCE/" "${SSH_TARGET}:${LIVE}/"
    rc=$?
  else
    if [[ ! -d "$SOURCE/$proj" ]]; then
      say "SKIP  $proj  (not a directory)"; SKIPPED_LIST+=("$proj"); ((SKIP_COUNT++)); continue
    fi
    # The remote path MUST be single-quoted here. ssh concatenates its trailing
    # args into one string and the REMOTE shell re-splits it, so an unquoted
    # "${LIVE}/Quotes App" became `mkdir -p .../live/Quotes App` -- creating a
    # stray `live/Quotes` plus an `App` dir in root's home. (Observed 2026-07-23.)
    # Any embedded single quote is escaped the POSIX way: ' -> '\''
    if [[ "$DRY_RUN" != "1" ]]; then
      esc_proj=${proj//\'/\'\\\'\'}
      "${SSH[@]}" "$SSH_TARGET" "mkdir -p '${LIVE}/${esc_proj}'" 2>/dev/null
    fi
    # -s / --protect-args keeps rsync from letting the remote shell re-split the
    # path too. Default since rsync 3.2.4, stated explicitly so this does not
    # silently regress on an older rsync.
    timeout "$PER_PROJECT_TIMEOUT" rsync -azS -s --delete --partial --partial-dir=.rsync-partial \
      "${RSYNC_EXTRA[@]}" --exclude-from="$EXCLUDE_FILE" \
      -e "$SSH_TRANSPORT" "$SOURCE/$proj/" "${SSH_TARGET}:${LIVE}/${proj}/"
    rc=$?
  fi
  dur=$(( $(now) - t0 ))

  # rsync 24 = "source files vanished during transfer" -- benign on a live tree.
  if [[ $rc -eq 0 || $rc -eq 24 ]]; then
    say "OK    $proj  (${dur}s, rc=$rc)"
    NEW_OK["$proj"]=$(now)
    LAST_OK["$proj"]="${NEW_OK[$proj]}"
    ((OK_COUNT++))
    # Persist AFTER EVERY project, not once at the end. The first real run died
    # mid-loop and the end-of-run write never executed, so 15 completed chunks
    # went unrecorded and had to be redone. Progress must survive the next reset.
    write_ledger
  else
    say "FAIL  $proj  (${dur}s, rc=$rc)  -- partial kept, retries next pass"
    FAILED_LIST+=("$proj"); ((FAIL_COUNT++))
  fi
done

# Final belt-and-braces write (the per-project writes above are the real guarantee).
write_ledger

# --- ghost detection (REPORT ONLY -- never deletes) ---------------------------
# live/ was seeded from the 2026-07-10 snapshot, and per-project syncing never
# deletes at the top level. Directories that were renamed or removed since then
# (Storyboarder->Storyboard, Markets, resume->Resume) therefore LINGER in live/.
# They are harmless but they inflate the tree and would be carried into every
# Phase 2 snapshot. Reported here so the removal stays a human decision.
GHOSTS=()
while IFS= read -r d; do
  [[ -z "$d" ]] && continue
  [[ -d "$SOURCE/$d" ]] || GHOSTS+=("$d")
done < <("${SSH[@]}" "$SSH_TARGET" "find '${LIVE}' -maxdepth 1 -mindepth 1 -type d -printf '%f\n' 2>/dev/null" 2>/dev/null)

# --- verdict from the LEDGER, not from the last rsync ------------------------
say ""
say "CHUNKED BACKUP SUMMARY ok=$OK_COUNT fail=$FAIL_COUNT skipped=$SKIP_COUNT elapsed=$(( ($(now)-START_EPOCH)/60 ))m"
[[ ${#FAILED_LIST[@]}  -gt 0 ]] && say "  failed:  ${FAILED_LIST[*]}"
[[ ${#SKIPPED_LIST[@]} -gt 0 ]] && say "  skipped: ${SKIPPED_LIST[*]}"
[[ ${#GHOSTS[@]}       -gt 0 ]] && say "  ghosts in live/ (present on VPS, gone from source; NOT deleted): ${GHOSTS[*]}"

CUTOFF=$(( $(now) - FRESH_HOURS * 3600 ))
STALE=()
for p in "${PROJECTS[@]}"; do
  e="${LAST_OK[$p]:-0}"
  (( e < CUTOFF )) && STALE+=("$p")
done

if [[ ${#STALE[@]} -gt 0 ]]; then
  say ""
  say "PROJECTS_CHUNKED_STATUS=STALE_REMAIN"
  say "STALE (older than ${FRESH_HOURS}h or never synced): ${STALE[*]}"
  say "live/ is NOT a complete current tree -- do not run a Phase 2 snapshot yet."
  exit 3
fi

say ""
if [[ -n "$ONLY_PROJECT" ]]; then
  # ONLY_PROJECT evaluated freshness over a ONE-ELEMENT list, so a green here says
  # nothing about the other 25 chunks. Emitting ALL_FRESH would be precisely the
  # false-green pattern this whole effort exists to eliminate.
  say "PROJECTS_CHUNKED_STATUS=SINGLE_PROJECT_OK"
  say "'$ONLY_PROJECT' is fresh. This was a targeted run -- it says NOTHING about the other chunks."
  say "Phase 2 requires a full pass reporting ALL_FRESH."
  exit 0
fi
say "PROJECTS_CHUNKED_STATUS=ALL_FRESH"
say "Every chunk is within ${FRESH_HOURS}h. live/ is ready for a Phase 2 snapshot."
exit 0
