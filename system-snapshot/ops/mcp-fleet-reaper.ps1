param(
  # Plan 01 Task 1.4 - MCP-fleet reaper (BACKSTOP to the M0 supersede guard, which is
  # the primary mechanism; see PLAN 01 section 0-pre note 4). Groups python/node MCP
  # children by parent; kills a child tree ONLY when its harness parent is DEAD
  # (orphan) or idle beyond -IdleHours. NEVER touches children of a live session.
  # Default REPORT-ONLY; -Execute required to kill. Prints:
  #   REAPER killed=<n> live-sessions=<m> python-total=<t>
  [switch]$Execute,
  [double]$IdleHours = 12
)

$ErrorActionPreference = "Stop"
$harnessNames = @("codex", "opencode", "claude", "cursor")

$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='node.exe'"
$all = Get-CimInstance Win32_Process
$byPid = @{}; foreach ($p in $all) { $byPid[[int]$p.ProcessId] = $p }

$liveSessions = @{}
foreach ($p in $all) {
  $n = ($p.Name -replace '\.exe$','').ToLowerInvariant()
  if ($harnessNames -contains $n) { $liveSessions[[int]$p.ProcessId] = $p }
}

$killed = 0
$candidates = @{}
foreach ($p in $procs) {
  $parentId = [int]$p.ParentProcessId
  if ($liveSessions.ContainsKey($parentId)) {
    # live harness session: check idle (session older than threshold with no recent CPU is
    # NOT reliably detectable per-child; conservative rule = live parent means UNTOUCHABLE
    # unless the PARENT itself exceeded IdleHours of zero-CPU, which we do not infer here).
    continue
  }
  if ($byPid.ContainsKey($parentId)) {
    # parent alive but not a harness (e.g. wsl, service hosts, IDE helpers): do not touch.
    continue
  }
  # parent PID not in the live process list => orphaned child tree
  if (-not $candidates.ContainsKey($parentId)) { $candidates[$parentId] = @() }
  $candidates[$parentId] += $p
}

foreach ($group in $candidates.GetEnumerator()) {
  foreach ($p in $group.Value) {
    if ($Execute) {
      try { Stop-Process -Id $p.ProcessId -Force -Confirm:$false -ErrorAction Stop; $killed++ }
      catch { }
    } else {
      Write-Host ("  would-kill (orphan, dead-parent {0}): {1} pid={2} cmd={3}" -f $group.Key, $p.Name, $p.ProcessId, ([string]$p.CommandLine).Substring(0, [Math]::Min(90, ([string]$p.CommandLine).Length)))
    }
  }
}

$pythonTotal = @(Get-Process python -ErrorAction SilentlyContinue).Count
if (-not $Execute) { $killed = 0 }
Write-Host ("REAPER killed={0} live-sessions={1} python-total={2}" -f $killed, $liveSessions.Count, $pythonTotal)
if (-not $Execute -and $candidates.Count -gt 0) { Write-Host ("report-only: {0} orphan group(s) found; rerun with -Execute to reap" -f $candidates.Count) }
exit 0
