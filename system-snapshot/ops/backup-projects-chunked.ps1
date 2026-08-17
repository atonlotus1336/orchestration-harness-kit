param(
  # Sync a single project only (testing / targeted retry). Name as it appears
  # under __WINDOWS_WORKSPACE__\Projects, quoted if it contains spaces e.g. -OnlyProject 'Quotes App'.
  [string]$OnlyProject = '',
  # A project counts as fresh if it synced within this many hours.
  [int]$FreshHours = 36,
  # Stop STARTING new chunks past this many minutes (in-flight chunk finishes).
  [int]$BudgetMinutes = 45,
  # Seconds before a single hung chunk is killed and deferred to the next pass.
  [int]$PerProjectTimeout = 600,
  [switch]$DryRun
)

# ---------------------------------------------------------------------------
# PHASE 1 wrapper for the chunked Projects backup (created 2026-07-23).
#
# Responsibilities the core .sh cannot do itself:
#   1. remount /mnt/d as root if the 9p mount went stale (the core script runs
#      as __PRIVATE_TOKEN_01__ and cannot mount)
#   2. write a log to output\ops\ EVEN WHEN THE RUN FAILS
#   3. surface a meaningful exit code to Task Scheduler
#
# EXIT CODES (passed through from the core script)
#   0 = every project synced within the freshness window
#   3 = ran fine, some projects still stale (named in the log)
#   1 = hard failure (mount dead, VPS unreachable, source missing)
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
$core = '__WSL_WORKSPACE__/scripts/ops/backup-projects-chunked-core.sh'
$outputDir = '__WINDOWS_WORKSPACE__\output\ops'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logPath = Join-Path $outputDir ("projects-chunked-{0}.log" -f $stamp)

# See backup-projects.ps1 for why this helper exists: PowerShell 5.1 wraps native
# stderr in a NativeCommandError, and under $ErrorActionPreference='Stop' that
# THROWS before the log is ever written -- which is how failed backups came to
# leave no trace at all in output\ops\.
function Invoke-Native {
  # Parameter is deliberately NOT named $Args -- that collides with PowerShell's
  # automatic $args and the splat silently misbehaves.
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

$transcript = @()
$transcript += "CHUNKED PROJECTS BACKUP  started=$(Get-Date -Format 'o')"

# --- preflight: is /mnt/d alive? remount once if not -------------------------
$check = 'cat __WSL_WORKSPACE__/STATUS.md >/dev/null 2>&1 && echo ALIVE || echo STALE'
$r = Invoke-Native -WslArgs @('-d', 'Ubuntu-restored', '-e', 'bash', '-lc', $check)
if (($r.Output | Out-String) -notmatch 'ALIVE') {
  $transcript += "MOUNT STALE -> attempting remount as root"
  $remount = 'umount -l /mnt/d 2>/dev/null; mount -t drvfs G: /mnt/d -o metadata,uid=1000,gid=1000,umask=22'
  Invoke-Native -WslArgs @('-d', 'Ubuntu-restored', '-u', 'root', '--', 'bash', '-c', $remount) | Out-Null
  Start-Sleep -Seconds 2
  $r2 = Invoke-Native -WslArgs @('-d', 'Ubuntu-restored', '-e', 'bash', '-lc', $check)
  if (($r2.Output | Out-String) -notmatch 'ALIVE') {
    $transcript += "REMOUNT FAILED -- G: is offline or mid-reset. Aborting; nothing was transferred."
    $transcript += "PROJECTS_CHUNKED_EXIT=1"
    $transcript | Tee-Object -FilePath $logPath
    Write-Host "PROJECTS_CHUNKED_EXIT=1 LOG=$logPath"
    exit 1
  }
  $transcript += "REMOUNT OK"
} else {
  $transcript += "MOUNT ALIVE"
}

# --- environment for the core script -----------------------------------------
$envPrefix = "FRESH_HOURS=$FreshHours PER_PROJECT_TIMEOUT=$PerProjectTimeout TOTAL_BUDGET_MIN=$BudgetMinutes"
if ($DryRun) { $envPrefix += " DRY_RUN=1" }
if ($OnlyProject) {
  # Single-quote for the remote shell; escape any embedded single quote.
  $escaped = $OnlyProject -replace "'", "'\''"
  $envPrefix += " ONLY_PROJECT='$escaped'"
}

# --- stage the script OFF the drive it is backing up -------------------------
# 2026-07-23, learned the hard way: the first real run died with
#   "backup-projects-chunked-core.sh: error reading input file: Input/output error"
# Bash reads a script INCREMENTALLY as it executes. The script lived on /mnt/d,
# so when the USB drive reset mid-run bash could not read the remaining lines --
# the loop stopped, and the ledger write and summary (which come AFTER the loop)
# never ran. Every completed chunk went unrecorded.
#
# A backup script for a failing drive must not live on that drive while running.
# Copy it (and the exclude list, which rsync re-reads) to WSL-local ext4 first.
$localCore = '/tmp/__PRIVATE_TOKEN_02__-backup-projects-chunked-core.sh'
$localExcl = '/tmp/__PRIVATE_TOKEN_02__-projects-backup-excludes.txt'
$stage = "cp '$core' '$localCore' && cp '__WSL_WORKSPACE__/scripts/ops/projects-backup-excludes.txt' '$localExcl' && chmod +x '$localCore' && echo STAGED"
$s = Invoke-Native -WslArgs @('-d', 'Ubuntu-restored', '-e', 'bash', '-lc', $stage)
if (($s.Output | Out-String) -notmatch 'STAGED') {
  $transcript += "STAGING FAILED -- could not copy the core script off G:. Aborting."
  $transcript += ($s.Output | Out-String).Trim()
  $transcript += "PROJECTS_CHUNKED_EXIT=1"
  $transcript | Tee-Object -FilePath $logPath
  Write-Host "PROJECTS_CHUNKED_EXIT=1 LOG=$logPath"
  exit 1
}
$transcript += "STAGED core script to $localCore (survives a mid-run drive reset)"
$envPrefix += " EXCLUDE_FILE_OVERRIDE='$localExcl'"

$transcript += "INVOKE: $envPrefix bash $localCore"
$transcript | Tee-Object -FilePath $logPath

$res = Invoke-Native -WslArgs @('-d', 'Ubuntu-restored', '-e', 'bash', '-lc', "$envPrefix bash $localCore")
$exitCode = $res.ExitCode
$res.Output | Tee-Object -FilePath $logPath -Append

Write-Host "PROJECTS_CHUNKED_EXIT=$exitCode LOG=$logPath"
exit $exitCode
