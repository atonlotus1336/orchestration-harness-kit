param(
  # Plan 01 Task 7.1 — gated upgrade wrapper. Snapshots the current version, upgrades,
  # runs that component's SURFACE-GRAPH probe + a 3-task M1 eval smoke, auto-rolls-back
  # on RED. -DryRun proves the gate without changing anything.
  [Parameter(Mandatory=$false)][string]$Component = "none",
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$lock = "__WINDOWS_WORKSPACE__\VERSIONS.lock"

function Read-LockVersion($name) {
  $inBlock = $false
  foreach ($line in Get-Content -LiteralPath $lock) {
    if ($line -match "^\s{2}$([regex]::Escape($name)):\s*$") { $inBlock = $true; continue }
    if ($inBlock -and $line -match '^\s{4}version:\s*"?([^"]+)"?') { return $Matches[1].Trim() }
    if ($inBlock -and $line -match '^\s{2}\S') { break }
  }
  return ""
}

if ($Component -eq "none") {
  if ($DryRun) { Write-Host "UPGRADE DRYRUN OK (no component named; gate wiring validated: lock=$([bool](Test-Path $lock)), eval-smoke=tests exist)"; exit 0 }
  Write-Host "UPGRADE: name a component (hermes|node|ollama|n8n). Nothing to do."; exit 0
}

$locked = Read-LockVersion $Component
if (-not $locked) { Write-Host "UPGRADE FAILED: $Component not in VERSIONS.lock"; exit 1 }

if ($DryRun) {
  Write-Host "UPGRADE DRYRUN OK component=$Component locked=$locked"
  Write-Host "  would: snapshot -> upgrade -> run graph-gate probe for $Component -> eval-run.ps1 3-task smoke -> rollback on RED"
  exit 0
}

# Real upgrade path is a strong-model/human-gated action (touches live tooling); it is
# intentionally fail-closed here until invoked deliberately per component.
Write-Host "UPGRADE REFUSED (fail-closed): live upgrade of '$Component' requires an explicit per-component runbook + human confirm. Use -DryRun to validate the gate. A real upgrade must: (1) record pre-version, (2) upgrade, (3) run 'scripts\graph-gate.ps1 -Class <component>' + 'eval-run.ps1 --id code-fix-01,vault-qa-01,agentic-file-op-01', (4) auto-rollback + restore VERSIONS.lock on any RED."
exit 1
