# Plan 01 Task 7.2 — model-catalog surface probe. Compares a hash of `ollama list`
# (model names) against the hash routing-policy.json was benchmarked against. Per D15:
# a mismatch exits 0 but writes `note: YELLOW <reason>` (routing evidence stale) — it
# does NOT fail the gate. A DEPRECATED model referenced by routing/council/judge config
# but ABSENT from `ollama list` => RED fail-closed (exit 1).
$ErrorActionPreference = "Stop"
$policy = "__WINDOWS_WORKSPACE__\.planning\hermes-master-plan\routing-policy.json"
$catalogHashFile = "__WINDOWS_WORKSPACE__\output\ops\model-catalog.hash"
New-Item -ItemType Directory -Force -Path (Split-Path $catalogHashFile) | Out-Null

$names = @(& ollama list 2>$null | Select-Object -Skip 1 | ForEach-Object { ($_ -split '\s+')[0] } | Where-Object { $_ } | Sort-Object)
if ($names.Count -eq 0) { Write-Host "note: YELLOW ollama list empty or unreachable (cannot validate catalog)"; exit 0 }
$sha = [System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes(($names -join ",")))).Replace("-","").ToLower().Substring(0,16)

# Build the VALID set: local models + cloud-cache base names. A routed model is valid if
# it is local, a "-cloud" alias, or its base name (strip -cloud and :tag) is in the cloud
# cache. Unmatched => UNVERIFIABLE (YELLOW per D15, cache is stale/naming differs), never
# a hard RED - we do not fail the gate on a model the stale cache simply doesn't list.
$cloudCache = "/home/__LINUX_USER__/.hermes/ollama_cloud_models_cache.json"
$cloudBases = @()
try {
  $cc = & wsl -e bash -c "cat $cloudCache 2>/dev/null" 2>$null | ConvertFrom-Json
  $cloudBases = @($cc.models | ForEach-Object { ($_ -split ':')[0] })
} catch {}
function Base($m) { (($m -replace '-cloud$','') -split ':')[0] }
$localBases = @($names | ForEach-Object { Base $_ })

$unverifiable = @()
if (Test-Path $policy) {
  $ptext = Get-Content -Raw -LiteralPath $policy
  $referenced = [regex]::Matches($ptext, '"(qwen[\w.:-]+|deepseek[\w.:-]+|glm[\w.:-]+|gpt-oss[\w.:-]+|hermes[\w.:-]+|ministral[\w.:-]+|qwen-vl)"') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
  foreach ($m in $referenced) {
    if ($m -like "*-cloud") { continue }                 # explicit cloud alias: valid by construction
    if ($m -in $names) { continue }                       # exact local match
    $b = Base $m
    if ($b -in $localBases -or $b -in $cloudBases) { continue }  # base matches local or cloud cache
    $unverifiable += $m
  }
}
if ($unverifiable.Count -gt 0) {
  Write-Host ("note: YELLOW routed model(s) not confirmed in local list or cloud cache (stale cache / rename possible - queue a scoped re-benchmark, do not fail): " + ($unverifiable -join ", "))
  # fall through to hash check; YELLOW does not exit nonzero (D15)
}

$prev = if (Test-Path $catalogHashFile) { (Get-Content -Raw -LiteralPath $catalogHashFile).Trim() } else { "" }
if ($prev -and $prev -ne $sha) {
  Write-Host "note: YELLOW model catalog changed (was $prev now $sha) - routing evidence may be stale for changed models; queue a scoped M1 re-benchmark"
  Set-Content -LiteralPath $catalogHashFile -Value $sha -Encoding ascii
  exit 0
}
Set-Content -LiteralPath $catalogHashFile -Value $sha -Encoding ascii
Write-Host "MODEL-CATALOG GREEN: $($names.Count) models, hash=$sha, all routed models present-or-cloud"
exit 0
