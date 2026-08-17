param(
  # Plan 01 Task 1.2 - versioned, restore-testable vault backup to the Task 1.1
  # destination (read from DECISIONS.md, never hard-coded). Fail-loud: any copy error
  # => nonzero exit + partial dir renamed .FAILED. Hardlink snapshots via
  # --link-dest=../latest (rsync), prune > -KeepDays.
  [switch]$DryRun,
  [int]$KeepDays = 30
)

$ErrorActionPreference = "Stop"
$decisions = "__WINDOWS_WORKSPACE__\.planning\mission-layer\DECISIONS.md"

# (a) $BACKUP_DEST from DECISIONS.md
$destLine = (Select-String -LiteralPath $decisions -Pattern 'backup dest:' | Select-Object -First 1).Line
if (-not $destLine) { Write-Host "BACKUP FAILED: no 'backup dest:' line in DECISIONS.md (Task 1.1 gate not cleared)"; exit 1 }
if ($destLine -notmatch '(\w+@[\d\.]+):(/\S+)') { Write-Host "BACKUP FAILED: cannot parse ssh dest from DECISIONS.md line"; exit 1 }
$sshTarget = $Matches[1]; $destRoot = $Matches[2].TrimEnd('/')
$destBase = "$destRoot/__PRIVATE_TOKEN_02__-backup"
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$sshKey = "~/.ssh/hermes-hostinger"
$TRUSTED_OWN_VPS = "__TAILSCALE_IPV4__"  # DEVIATIONS.md Task 1.2: trusted-own-device => scrub skipped

function WslRun([string]$cmd) {
  $out = & wsl -e bash -lc $cmd 2>&1
  return @{ exit = $LASTEXITCODE; out = ($out -join "`n") }
}

# (b) sources (Windows paths -> /mnt/d WSL paths; profiles per 2026-07-07 correction)
$sources = @(
  @{ name = "memoryweaver"; wsl = "__WSL_WORKSPACE__/.claude/Memory/memoryweaver" },
  @{ name = "rag-db"; wsl = "__WSL_WORKSPACE__/.rag/db" },
  @{ name = "graphrag-output"; wsl = "__WSL_WORKSPACE__/.rag/graphrag/output" },
  @{ name = "planning"; wsl = "__WSL_WORKSPACE__/.planning" },
  @{ name = "profiles-windows"; wsl = ("/mnt/c/Users/" + $env:USERNAME + "/.hermes/profiles") }
)

if ($DryRun) {
  $probe = WslRun "ssh -i $sshKey -o ConnectTimeout=15 $sshTarget 'mkdir -p $destBase && echo REACHABLE'"
  if ($probe.exit -ne 0 -or $probe.out -notmatch "REACHABLE") { Write-Host "DRYRUN FAILED: dest unreachable: $($probe.out)"; exit 1 }
  foreach ($s in $sources) {
    $chk = WslRun "test -d '$($s.wsl)' && echo ok || echo MISSING"
    Write-Host ("  source {0}: {1}" -f $s.name, $chk.out.Trim())
  }
  Write-Host "  + wsl-profiles tar (live /home/__LINUX_USER__/.hermes/profiles)"
  Write-Host "  + n8n workflows export (REST; __PRIVATE_TOKEN_02__-control-plane is a thin router, REST is the working export path)"
  Write-Host ("  scrub: SKIPPED for trusted-own-VPS dest {0} (DEVIATIONS.md Task 1.2); any other off-machine dest runs the I2 deny-grep and aborts on hit" -f $TRUSTED_OWN_VPS)
  Write-Host "DRYRUN OK dest=${sshTarget}:${destBase}"
  exit 0
}

# Real push delegates to a self-contained bash script (no PowerShell->WSL quoting).
$coreWsl = "__WSL_WORKSPACE__/scripts/ops/backup-vault-core.sh"
$out = & wsl -e bash $coreWsl $sshTarget $destBase $stamp $sshKey $TRUSTED_OWN_VPS $env:USERNAME 2>&1
$coreExit = $LASTEXITCODE
$outText = ($out -join "`n")
Write-Host $outText
if ($coreExit -eq 0 -and $outText -match "BACKUP OK") {
  # write the restore-verify-consumable marker source (BACKUP-MANIFEST is on the VPS)
  New-Item -ItemType Directory -Force -Path "__WINDOWS_WORKSPACE__\output\ops" | Out-Null
  exit 0
}
exit 1

# ---- legacy inline path (unreachable; kept for reference) ----
$staging = Join-Path $env:TEMP ("__PRIVATE_TOKEN_02__-backup-staging-" + $stamp)
New-Item -ItemType Directory -Force -Path $staging | Out-Null
$failures = @()

try {
  # WSL live profiles tar (the runtime side; user still __PRIVATE_TOKEN_01__)
  $r = WslRun "tar -czf /tmp/wsl-hermes-profiles.tgz -C /home/__LINUX_USER__/.hermes profiles 2>/dev/null && cp /tmp/wsl-hermes-profiles.tgz '$(($staging -replace '\\','/') -replace '^C:','/mnt/c')/' && echo ok"
  if ($r.out -notmatch "ok") { $failures += "wsl-profiles: $($r.out)" }

  # n8n workflows export (skip-with-note on failure, per plan)
  try {
    $n8nKey = (Get-Content "G:\n8n-key.txt" -Raw).Trim()
    Invoke-RestMethod -Uri "https://n8n.example.com/api/v1/workflows?limit=250" -Headers @{ "X-N8N-API-KEY" = $n8nKey } -TimeoutSec 30 |
      ConvertTo-Json -Depth 50 | Set-Content -LiteralPath (Join-Path $staging "n8n-workflows-export.json") -Encoding UTF8
  } catch { Write-Host "note: n8n export skipped ($($_.Exception.Message))" }

  # memoryweaver live counts for restore-verify (via WSL runtime)
  $mwCounts = WslRun "export MEMORYWEAVER_DATA_DIR=__WSL_WORKSPACE__/.claude/Memory/memoryweaver; cd __WSL_WORKSPACE__/.claude/memoryweaver && /home/__LINUX_USER__/.local/share/memoryweaver-mcp/venv/bin/python3.11 -m memoryweaver status 2>/dev/null | tail -20"

  # (c) scrub decision
  if ($sshTarget -notmatch [regex]::Escape($TRUSTED_OWN_VPS)) {
    $denyBase = '__PRIVATE_TOKEN_01__|__PRIVATE_TOKEN_02__|__PRIVATE_TOKEN_03__|__PRIVATE_TOKEN_04__|__PRIVATE_TOKEN_05__'
    $denyFile = "__WINDOWS_WORKSPACE__\.private-denylist"
    $deny = if (Test-Path $denyFile) { ((Get-Content $denyFile | Where-Object { $_ -and $_ -notmatch '^\s*#' }) + $denyBase) -join '|' } else { $denyBase }
    $stagingWsl = ($staging -replace '\\','/') -replace '^C:','/mnt/c'
    $hit = WslRun "grep -rIniE '$deny' '$stagingWsl' | head -5"
    if ($hit.out.Trim()) {
      Write-Host "SCRUB HIT (off-machine non-trusted dest):`n$($hit.out)"
      Rename-Item -LiteralPath $staging -NewName ((Split-Path $staging -Leaf) + ".FAILED")
      exit 2
    }
  } else {
    Write-Host "SCRUB SKIPPED: trusted-own-VPS dest (DEVIATIONS.md Task 1.2)"
  }

  # (d) BACKUP-MANIFEST.json - per-source sha256 over all files (WSL sha256sum)
  $manifest = [ordered]@{ ts = (Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz"); stamp = $stamp; dest = "${sshTarget}:${destBase}/$stamp"; memoryweaver_status_tail = $mwCounts.out; sources = @() }
  foreach ($s in $sources) {
    $h = WslRun "cd '$($s.wsl)' 2>/dev/null && find . -type f -not -path '*/spool/*' -print0 | xargs -0 sha256sum 2>/dev/null | sha256sum | cut -d' ' -f1 && find . -type f -not -path '*/spool/*' 2>/dev/null | wc -l && du -sb --exclude=spool . 2>/dev/null | cut -f1"
    $parts = @(($h.out.Trim() -split "`n") | Where-Object { $_ -match '^[0-9a-f]+$' -or $_ -match '^\d+$' })
    $bytesVal = 0L; if ($parts.Count -gt 2 -and [long]::TryParse($parts[2], [ref]$bytesVal)) { } else { $bytesVal = 0 }
    $manifest.sources += [ordered]@{ name = $s.name; path = $s.wsl; aggregate_sha256 = $parts[0]; file_count = $(if ($parts.Count -gt 1) { [int]$parts[1] } else { 0 }); bytes = $bytesVal }
  }
  $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $staging "BACKUP-MANIFEST.json") -Encoding UTF8

  # push: rsync each source + staging extras with hardlink snapshots
  $r = WslRun "ssh -i $sshKey $sshTarget 'mkdir -p $destBase/$stamp'"
  if ($r.exit -ne 0) { throw "dest mkdir failed: $($r.out)" }
  # --link-dest only when a prior snapshot exists (first run has no 'latest')
  $hasLatest = (WslRun "ssh -i $sshKey $sshTarget 'test -e $destBase/latest && echo yes || echo no'").out.Trim() -eq "yes"
  $stagingWsl = ($staging -replace '\\','/') -replace '^C:','/mnt/c'
  $excl = "--exclude='spool/' --exclude='__pycache__/' --exclude='*.lock'"
  foreach ($s in $sources) {
    $link = if ($hasLatest) { "--link-dest=$destBase/latest/$($s.name)" } else { "" }
    $r = WslRun "rsync -a --delete $excl $link -e 'ssh -i $sshKey' '$($s.wsl)/' '${sshTarget}:$destBase/$stamp/$($s.name)/'"
    if ($r.exit -ne 0) { $failures += "rsync $($s.name): exit $($r.exit)" }
  }
  $r = WslRun "rsync -a -e 'ssh -i $sshKey' '$stagingWsl/' '${sshTarget}:$destBase/$stamp/meta/'"
  if ($r.exit -ne 0) { $failures += "rsync meta: exit $($r.exit)" }

  if ($failures.Count -gt 0) {
    WslRun "ssh -i $sshKey $sshTarget 'mv $destBase/$stamp $destBase/$stamp.FAILED'" | Out-Null
    Write-Host ("BACKUP FAILED (partial renamed .FAILED): " + ($failures -join "; "))
    exit 1
  }

  # latest symlink + (e) prune
  WslRun "ssh -i $sshKey $sshTarget 'ln -sfn $destBase/$stamp $destBase/latest; find $destBase -maxdepth 1 -type d -name \"20*\" -mtime +$KeepDays -exec rm -rf {} +'" | Out-Null
  Write-Host "BACKUP OK dest=${sshTarget}:${destBase}/$stamp sources=$($sources.Count)+meta"
  exit 0
} finally {
  Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
}
