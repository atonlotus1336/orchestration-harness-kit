param(
  [ValidateSet('Weekly', 'Monthly', 'MonthlyDue')]
  [string]$Kind,
  # How old the last good VPS snapshot may be before a "not due" skip is treated
  # as an alarm instead of a success. Weekly cadence is 7d, so anything past ~10d
  # means at least one weekly run was missed or failed.
  [int]$StaleAfterDays = 10,
  # A monthly checkpoint becomes eligible again after four weeks, even if it
  # was created late in the previous calendar month.
  [ValidateRange(1, 366)]
  [int]$MonthlyDueAfterDays = 28
)

$ErrorActionPreference = 'Stop'

# 2026-07-25 MACHINE GUARD: nucbox-only (Ubuntu-restored distro + WSL backup key + physical G:).
# The task gets registered on both PCs by the drive-watchdog; on DESKTOP-2S7VQDO every run can only
# fail. Exit 0 = wrong machine, not success-of-a-backup. See MACHINE-IDENTITY.md.
# NOTE: the REAL computer name is NUCBOX_M6ULTRA (underscore); Tailscale shows it as
# nucbox-m6ultra (hyphen) because Tailscale rewrites underscores. Accept both.
if ($env:COMPUTERNAME -notmatch '^NUCBOX[-_]M6ULTRA$') {
  Write-Output "SKIP: Projects backup runs only on NUCBOX-M6ULTRA; this is $env:COMPUTERNAME."
  exit 0
}

$core = '__WSL_WORKSPACE__/scripts/ops/backup-projects-core.sh'
$outputDir = '__WINDOWS_WORKSPACE__\output\ops'
$monthMarker = Join-Path $outputDir ("projects-monthly-{0}.ok" -f (Get-Date -Format 'yyyy-MM'))

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

# Invoke a native command without letting a stderr write blow up the script.
# 2026-07-23: PowerShell 5.1 wraps native stderr in a NativeCommandError, and with
# $ErrorActionPreference='Stop' that THREW before the Tee-Object log line ever ran --
# so every failed backup wrote NO log at all. Silent failure, by construction.
function Invoke-Native {
  # NOTE: the parameter is deliberately NOT named $Args -- that collides with
  # PowerShell's automatic $args variable and the splat silently misbehaves.
  param([string[]]$WslArgs)
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $out = & wsl.exe @WslArgs 2>&1
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $prev
  }
  [PSCustomObject]@{ Output = $out; ExitCode = $code }
}

# ---------------------------------------------------------------------------
# 2026-07-23 FALSE-GREEN FIX (FG-1)
#
# The MonthlyDue skip path used to `exit 0` and write NO log, so Task Scheduler
# reported "Result 0 / success" for a run that did nothing -- while the weekly
# job had been failing for 13 days and the whole Projects stream was stale since
# 2026-07-10. Nothing anywhere raised a hand.
#
# "Not due" is only legitimately a success if a RECENT snapshot actually exists.
# A calendar marker is not enough on its own: once that marker is 28 days old,
# the task must create a fresh monthly checkpoint rather than skip forever.
# So the skip path now verifies freshness on the VPS before agreeing to be green:
#
#   exit 0 = backed up OK, or skipped AND last snapshot verified fresh
#   exit 3 = skipped, and the last snapshot is PROVEN STALE   <-- the alarm
#
# If the freshness probe itself cannot run (VPS unreachable, WSL down, /mnt/d
# stale) we log UNKNOWN and still exit 0. An unreachable prober must never
# manufacture a false RED -- only a positively-proven stale backup earns one.
# ---------------------------------------------------------------------------
if ($Kind -eq 'MonthlyDue' -and (Test-Path -LiteralPath $monthMarker -PathType Leaf) -and
    ((Get-Date) - (Get-Item -LiteralPath $monthMarker).LastWriteTime).TotalDays -lt $MonthlyDueAfterDays) {
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $skipLog = Join-Path $outputDir ("projects-backup-skipped-{0}.log" -f $stamp)
  $markerAgeDays = [math]::Floor(((Get-Date) - (Get-Item -LiteralPath $monthMarker).LastWriteTime).TotalDays)

  # NOTE: `stat -c` on the SYMLINK ITSELF -- deliberately NOT `stat -Lc`.
  # Dereferencing gives the snapshot directory's mtime, which rsync -a preserved
  # from the SOURCE tree (reads as 2026-04-20, ~95 days, wildly wrong). The
  # `latest` symlink is recreated by `ln -sfn` at the moment a backup completes,
  # so its own mtime is the true completion time. Verified 2026-07-23.
  $probe = 'timeout 45 ssh -i /home/__LINUX_USER__/.ssh/hermes-hostinger -o BatchMode=yes ' +
           '-o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new root@__TAILSCALE_IPV4__ ' +
           'stat -c %Y /srv/isolatedbackups/projects/latest'

  $res = Invoke-Native -WslArgs @('--distribution', 'Ubuntu-restored', '-e', 'bash', '-lc', $probe)
  $mtime = ($res.Output | Out-String).Trim() -split '\r?\n' | Where-Object { $_ -match '^\d+$' } | Select-Object -Last 1

  $lines = @()
  $lines += "MONTHLY BACKUP NOT DUE marker=$monthMarker marker_age_days=$markerAgeDays due_after_days=$MonthlyDueAfterDays"

  if ($res.ExitCode -ne 0 -or -not $mtime) {
    $lines += "FRESHNESS UNKNOWN probe_exit=$($res.ExitCode) -- could not reach the VPS snapshot."
    $lines += "PROJECTS_BACKUP_STATUS=SKIPPED_FRESHNESS_UNKNOWN"
    $lines += ($res.Output | Out-String).Trim()
    $lines | Tee-Object -FilePath $skipLog
    Write-Host "PROJECTS_BACKUP_EXIT=0 (skipped, freshness unknown) LOG=$skipLog"
    exit 0
  }

  $ageDays = [math]::Floor(((Get-Date) - ([DateTimeOffset]::FromUnixTimeSeconds([int64]$mtime)).LocalDateTime).TotalDays)
  $lines += "LAST GOOD SNAPSHOT age_days=$ageDays threshold_days=$StaleAfterDays"

  if ($ageDays -gt $StaleAfterDays) {
    $lines += "PROJECTS_BACKUP_STATUS=SKIPPED_BUT_STALE"
    $lines += "ALARM: monthly backup declined to run, but the last good Projects snapshot is $ageDays days old."
    $lines += "The weekly job is not keeping this stream current. Investigate before trusting any green."
    $lines | Tee-Object -FilePath $skipLog
    Write-Host "PROJECTS_BACKUP_EXIT=3 (skipped, STALE) LOG=$skipLog"
    exit 3
  }

  $lines += "PROJECTS_BACKUP_STATUS=SKIPPED_AND_FRESH"
  $lines | Tee-Object -FilePath $skipLog
  Write-Host "PROJECTS_BACKUP_EXIT=0 (skipped, fresh) LOG=$skipLog"
  exit 0
}

$effectiveKind = if ($Kind -eq 'Weekly') { 'weekly' } else { 'monthly' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logPath = Join-Path $outputDir ("projects-backup-{0}-{1}.log" -f $effectiveKind, $stamp)

$res = Invoke-Native -WslArgs @('--distribution', 'Ubuntu-restored', '--exec', 'bash', $core, $effectiveKind)
$exitCode = $res.ExitCode
$res.Output | Tee-Object -FilePath $logPath

if ($exitCode -eq 0 -and $effectiveKind -eq 'monthly') {
  Set-Content -LiteralPath $monthMarker -Encoding UTF8 -Value ("OK {0} log={1}" -f (Get-Date -Format 'o'), $logPath)
}

if ($exitCode -eq 0) {
  $reindexMode = if ($effectiveKind -eq 'monthly') { 'Standard' } else { 'Fast' }
  $queueScript = '__WINDOWS_WORKSPACE__\.rag\queue-graphrag-maintenance.ps1'
  if (Test-Path -LiteralPath $queueScript -PathType Leaf) {
    Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @(
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $queueScript, '-Mode', $reindexMode
    ) | Out-Null
    Write-Host "GRAPHRAG_MAINTENANCE_QUEUED=$reindexMode"
  }
  # Compatibility handoff for NUC tasks registered before the integrated
  # wrapper existed. The wrapper sets __PRIVATE_TOKEN_02___AUTOEXPERIMENT_CHAIN=1 and launches
  # the named on-demand task itself, preventing duplicate observation runs.
  if ($env:__PRIVATE_TOKEN_02___AUTOEXPERIMENT_CHAIN -ne '1') {
    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
      '__WINDOWS_WORKSPACE__\scripts\autoexperiment\after-backup.ps1',
      '-Cadence', $effectiveKind, '-TrustedBackupChain'
    ) | Out-Null
    Write-Host "AUTOEXPERIMENT LAUNCHED cadence=$effectiveKind policy=sandbox-only"
  }
}

Write-Host "PROJECTS_BACKUP_EXIT=$exitCode LOG=$logPath"
exit $exitCode
