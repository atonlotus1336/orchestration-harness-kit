# Plan 01 Task 5.5 probe: newest LEARNING-DIGEST < 8 days AND last canary run = CAUGHT
# (all quarantined) AND active filter/seed hashes match active-hashes.json (M4 mutation
# pattern - an unauthorized in-place edit to the filter prompt or a seed pack trips RED).
# Exit 0 GREEN / 1 RED.
$ErrorActionPreference = "Stop"
$loop = "__WINDOWS_WORKSPACE__\output\practice-loop"

# 1. freshest weekly digest age (exclude dry-run/canary digests)
$digests = @(Get-ChildItem -LiteralPath $loop -Filter "LEARNING-DIGEST-*.md" -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -notmatch 'DRYRUN|CANARY' } | Sort-Object LastWriteTime -Descending)
if ($digests.Count -eq 0) { Write-Host "RED no weekly digest"; exit 1 }
$ageDays = ((Get-Date) - $digests[0].LastWriteTime).TotalDays
if ($ageDays -ge 8) { Write-Host ("RED newest digest {0:N1}d old (>=8d) - a weekly run was missed" -f $ageDays); exit 1 }

# 2. hash pinning (Task 5.3): active filter prompt + seed packs unchanged since pinned
$pinPath = Join-Path $loop "active-hashes.json"
if (-not (Test-Path -LiteralPath $pinPath)) { Write-Host "RED no active-hashes.json pin file"; exit 1 }
$pins = Get-Content -Raw -LiteralPath $pinPath | ConvertFrom-Json
$fp = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $loop "filter-prompt-v1.txt")).Hash.ToLowerInvariant()
if ($fp -ne [string]$pins.filter_prompt) { Write-Host "RED filter-prompt hash drift (unauthorized in-place edit) - re-pin via swap-filter, never by hand"; exit 1 }
foreach ($prop in $pins.PSObject.Properties) {
  if ($prop.Name -notlike "seed_pack_*") { continue }
  $slug = $prop.Name.Substring("seed_pack_".Length)
  $spPath = Join-Path $loop "seed-packs\$slug.yaml"
  if (-not (Test-Path -LiteralPath $spPath)) { Write-Host "RED seed pack missing: $slug"; exit 1 }
  $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $spPath).Hash.ToLowerInvariant()
  if ($h -ne [string]$prop.Value) { Write-Host "RED seed-pack hash drift: $slug"; exit 1 }
}

# 3. last canary run caught everything (canary-result.json written by the weekly run's
# canary check; absent = never verified = RED, fail-closed)
$canaryResult = Join-Path $loop "canary-result.json"
if (-not (Test-Path -LiteralPath $canaryResult)) { Write-Host "RED no canary-result.json (weekly canary never ran)"; exit 1 }
$cr = Get-Content -Raw -LiteralPath $canaryResult | ConvertFrom-Json
if ([int]$cr.missed -ne 0) { Write-Host "RED last canary run MISSED $($cr.missed) - injection defense degraded"; exit 1 }

Write-Host "GREEN digest $([math]::Round($ageDays,1))d old, all hashes pinned, last canary CAUGHT $($cr.caught)/$($cr.total)"
exit 0
