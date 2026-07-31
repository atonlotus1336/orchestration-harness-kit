param(
  [string]$Evidence = "",
  [switch]$AllowSourceMachineAudit
)

$ErrorActionPreference = "Continue"
$m11 = $PSScriptRoot
$kit = Split-Path -Parent $m11
$steps = New-Object System.Collections.Generic.List[object]
$overall = "GREEN"

function Add-Step($Name, $Status, $Details) {
  $script:steps.Add([pscustomobject]@{ name = $Name; status = $Status; details = $Details })
  if ($Status -eq "RED" -or $Status -eq "BLOCKED") { $script:overall = "RED" }
  elseif ($Status -eq "BLOCKED_PROVIDER") {
    # Provider/infrastructure failure, not a parity failure. Distinct status + exit 3
    # so a down model endpoint is never misread as a failed clone install.
    if ($script:overall -ne "RED") { $script:overall = "BLOCKED_PROVIDER" }
  }
  elseif ($Status -eq "DECLARED" -or $Status -eq "PENDING") {
    if ($script:overall -eq "GREEN") { $script:overall = "PENDING" }
  }
}

function Test-ProviderHealth($RouteValue) {
  # Self-contained probe: one tiny completion against the OpenAI-compatible endpoint
  # (recipients point PROVIDER_HEALTH_ENDPOINT at their own server; default local Ollama).
  $endpoint = if ($env:PROVIDER_HEALTH_ENDPOINT) { $env:PROVIDER_HEALTH_ENDPOINT } else { "http://127.0.0.1:11434" }
  $model = $RouteValue
  if ($RouteValue -match '^([^:]+):(.+)$') { $model = $Matches[2] }
  try {
    $body = @{ model = $model; messages = @(@{ role = "user"; content = "Reply with the single word OK." }); max_tokens = 12; temperature = 0 } | ConvertTo-Json -Depth 4
    $resp = Invoke-RestMethod -Uri ($endpoint.TrimEnd('/') + "/v1/chat/completions") -Method Post -ContentType "application/json" -Body $body -TimeoutSec 90
    return -not [string]::IsNullOrWhiteSpace([string]$resp.choices[0].message.content)
  } catch {
    return $false
  }
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $m11 "preflight\preflight.ps1") -Json | Out-Null
Add-Step "preflight-windows" $(if ($LASTEXITCODE -eq 0) { "GREEN" } else { "RED" }) @{ exit = $LASTEXITCODE }

$manifestPath = Join-Path $m11 "modules\module-manifest.json"
try {
  $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
  $ids = @($manifest.modules | ForEach-Object { $_.id })
  $missing = @("memory","rag","harness","hermes-snapshot","clone-eval" | Where-Object { $_ -notin $ids })
  Add-Step "module-manifest" $(if ($missing.Count -eq 0) { "GREEN" } else { "RED" }) @{ missing = @($missing) }
} catch {
  Add-Step "module-manifest" "RED" @{ error = $_.Exception.Message }
}

$cloneTasks = @(Get-ChildItem -LiteralPath (Join-Path $m11 "clone-eval\tasks") -Filter "*.yaml" | ForEach-Object { $_.Name })
$badTasks = @($cloneTasks | Where-Object { $_ -notmatch '^(code-fix|agentic-file-op)-' })
Add-Step "clone-eval-public-classes" $(if ($cloneTasks.Count -eq 8 -and $badTasks.Count -eq 0) { "GREEN" } else { "RED" }) @{ task_count = $cloneTasks.Count; bad_tasks = @($badTasks) }

$absPattern = '(?<![A-Za-z0-9_])[A-Za-z]:[\\/](?!n(?:\s|$))[A-Za-z]|/(home|mnt|Users)/'
$privateHits = Select-String -Path (Join-Path $m11 "clone-eval\tasks\*.yaml") -Pattern "vault-qa|research-synthesis|council-reasoning|$absPattern" -ErrorAction SilentlyContinue
Add-Step "clone-eval-private-class-exclusion" $(if (@($privateHits).Count -eq 0) { "GREEN" } else { "RED" }) @{ hit_count = @($privateHits).Count }

$expectedIds = @(
  "agentic-file-op-01","agentic-file-op-02","agentic-file-op-03","agentic-file-op-04",
  "code-fix-01","code-fix-02","code-fix-03","code-fix-04"
)
$baselineRoute = ""
$baselineScores = @{}
try {
  $baseline = Get-Content -Raw -LiteralPath (Join-Path $m11 "clone-eval\baseline.json") | ConvertFrom-Json
  $baselineRoute = [string]$baseline.route
  foreach ($row in @($baseline.results)) { $baselineScores[[string]$row.id] = [double]$row.score }
} catch {}
$effectiveRoute = if ($env:M11_EVAL_ROUTE) { $env:M11_EVAL_ROUTE } elseif ($baselineRoute) { $baselineRoute } else { "ollama-cloud:glm-5.2:cloud" }
$providerOk = $true
if ($env:EVAL_SKIP_PROVIDER_PROBE -ne "1") {
  if (-not (Test-ProviderHealth $effectiveRoute)) {
    $providerOk = $false
    Add-Step "clone-eval-fresh-run" "BLOCKED_PROVIDER" @{
      probe_route = $effectiveRoute
      note = "provider probe failed; infrastructure failure, not clone-parity RED. Verify model endpoint/auth, then re-run."
    }
  }
}
if ($providerOk) {
try {
  $evalArgs = @("--class", "code-fix,agentic-file-op")
  if ($env:M11_EVAL_ROUTE) { $evalArgs += @("--route", $env:M11_EVAL_ROUTE) }
  $evalOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $m11 "clone-eval\eval-run.ps1") @evalArgs 2>&1
  $evalExit = $LASTEXITCODE
  $resultLine = @($evalOutput | Where-Object { [string]$_ -like "result_written=*" } | Select-Object -Last 1)
  $resultPath = if ($resultLine) { ([string]$resultLine).Substring("result_written=".Length) } else { "" }
  $resultHash = ""
  $evalRoute = ""
  $expectedRoute = $baselineRoute
  $missingEvalIds = @()
  $badEvalClasses = @()
  $routeMismatch = @()
  $scoreFailures = @()
  if ($evalExit -eq 0 -and $resultPath -and (Test-Path -LiteralPath $resultPath)) {
    $evalResult = Get-Content -Raw -LiteralPath $resultPath | ConvertFrom-Json
    $actualIds = @($evalResult.results | ForEach-Object { $_.id })
    $missingEvalIds = @($expectedIds | Where-Object { $_ -notin $actualIds })
    $badEvalClasses = @($evalResult.results | Where-Object { $_.class -notin @("code-fix","agentic-file-op") } | ForEach-Object { $_.id })
    foreach ($row in @($evalResult.results)) {
      $id = [string]$row.id
      if ($baselineScores.ContainsKey($id) -and [double]$row.score -lt ($baselineScores[$id] - 5)) {
        $scoreFailures += "$id=$($row.score)<$($baselineScores[$id] - 5)"
      }
    }
    $evalRoute = [string]$evalResult.route
    if ($expectedRoute -and $evalRoute -ne $expectedRoute) { $routeMismatch += "result_route=$evalRoute expected_route=$expectedRoute" }
    $resultHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resultPath).Hash.ToLowerInvariant()
  }
  Add-Step "clone-eval-fresh-run" $(if ($evalExit -eq 0 -and $resultPath -and $missingEvalIds.Count -eq 0 -and $badEvalClasses.Count -eq 0 -and $routeMismatch.Count -eq 0 -and $scoreFailures.Count -eq 0) { "GREEN" } else { "RED" }) @{
    exit = $evalExit
    requested_route = $env:M11_EVAL_ROUTE
    result_route = $evalRoute
    baseline_route = $baselineRoute
    expected_route = $expectedRoute
    result = $resultPath
    sha256 = $resultHash
    missing_ids = @($missingEvalIds)
    bad_classes = @($badEvalClasses)
    route_mismatch = @($routeMismatch)
    score_failures = @($scoreFailures)
  }
} catch {
  Add-Step "clone-eval-fresh-run" "RED" @{ error = $_.Exception.Message }
}
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $kit "tools\m10-scrub-v2.ps1") -Root $m11 -Json | Out-Null
Add-Step "m11-privacy-scrub" $(if ($LASTEXITCODE -eq 0) { "GREEN" } else { "RED" }) @{ exit = $LASTEXITCODE }

$provenance = Join-Path $m11 "PROVENANCE.json"
if (Test-Path -LiteralPath $provenance) {
  try {
    $p = Get-Content -Raw -LiteralPath $provenance | ConvertFrom-Json
    $bad = @()
    $entries = @($p.files | ForEach-Object { [pscustomobject]@{ path = $_.path; sha256 = $_.sha256 } })
    foreach ($entry in $entries) {
      $path = Join-Path $m11 $entry.path
      if (-not (Test-Path -LiteralPath $path)) { $bad += $entry.path; continue }
      $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
      if ($actual -ne [string]$entry.sha256) { $bad += $entry.path }
    }
    $entrySet = @{}
    foreach ($entry in $entries) { $entrySet[([string]$entry.path).Replace('/', '\').ToLowerInvariant()] = $true }
    $extra = @()
    $m11Full = ([System.IO.Path]::GetFullPath($m11)).TrimEnd('\') + '\'
    foreach ($file in Get-ChildItem -LiteralPath $m11 -Recurse -File -Force) {
      $rel = $file.FullName.Substring($m11Full.Length)
      if ($rel -eq 'PROVENANCE.json' -or $rel -eq 'm11-gate-result.json' -or $rel.StartsWith('clone-eval\results\')) { continue }
      if (-not $entrySet.ContainsKey($rel.ToLowerInvariant())) { $extra += $rel }
    }
    Add-Step "provenance-checksums" $(if ($entries.Count -gt 0 -and $bad.Count -eq 0 -and $extra.Count -eq 0) { "GREEN" } else { "RED" }) @{ file_count = $entries.Count; mismatches = @($bad); extra_files = @($extra) }
  } catch {
    Add-Step "provenance-checksums" "RED" @{ error = $_.Exception.Message }
  }
} else {
  Add-Step "provenance-checksums" "RED" @{ missing = $provenance }
}

$cleanTarget = $env:M11_CLEAN_TARGET
if (-not $AllowSourceMachineAudit) {
  if (-not $cleanTarget) {
    Add-Step "clean-target-recipe" "BLOCKED" @{ reason = "M11_CLEAN_TARGET not provided" }
  } elseif (-not (Test-Path -LiteralPath $cleanTarget)) {
    Add-Step "clean-target-recipe" "RED" @{ target = $cleanTarget; reason = "target path not reachable" }
  } else {
    $targetEvidence = Join-Path $cleanTarget "m11-clean-install-evidence.json"
    if (-not (Test-Path -LiteralPath $targetEvidence)) {
      Add-Step "clean-target-recipe" "BLOCKED" @{ target = $cleanTarget; required_evidence = $targetEvidence; reason = "clean install evidence missing" }
    } else {
      try {
        $cleanProof = Get-Content -Raw -LiteralPath $targetEvidence | ConvertFrom-Json
        $currentProvenanceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $m11 "PROVENANCE.json")).Hash.ToLowerInvariant()
        $proofFailures = @()
        if ($cleanProof.status -ne "PASS") { $proofFailures += "status=$($cleanProof.status)" }
        if ([string]$cleanProof.source_kit_sha256 -ne $currentProvenanceHash) { $proofFailures += "source_kit_sha256_mismatch" }
        if ([string]$cleanProof.target_kit_sha256 -ne $currentProvenanceHash) { $proofFailures += "target_kit_sha256_mismatch" }
        if (-not $cleanProof.target_m11 -or -not (Test-Path -LiteralPath $cleanProof.target_m11)) { $proofFailures += "target_m11_missing" }
        if ([int]$cleanProof.copied_file_count -lt 1) { $proofFailures += "copied_file_count=$($cleanProof.copied_file_count)" }
        if ($cleanProof.bundle_gate.status -notin @("PENDING","GREEN")) { $proofFailures += "bundle_gate=$($cleanProof.bundle_gate.status)" }
        if ($cleanProof.bundle_gate.result_route -and $evalRoute -and [string]$cleanProof.bundle_gate.result_route -ne $evalRoute) { $proofFailures += "clean_parent_route_mismatch=$($cleanProof.bundle_gate.result_route)!=$evalRoute" }
        if ($cleanProof.bundle_gate.baseline_route -and $baselineRoute -and [string]$cleanProof.bundle_gate.baseline_route -ne $baselineRoute) { $proofFailures += "clean_baseline_route_mismatch=$($cleanProof.bundle_gate.baseline_route)!=$baselineRoute" }
        if (-not $cleanProof.wsl_preflight.attempted -or $cleanProof.wsl_preflight.exit -ne 0) { $proofFailures += "wsl_preflight_exit=$($cleanProof.wsl_preflight.exit)" }
        if (@($cleanProof.source_marker_hits).Count -ne 0) { $proofFailures += "source_marker_hits=$(@($cleanProof.source_marker_hits).Count)" }
        if (@($cleanProof.failures).Count -ne 0) { $proofFailures += "failures=$(@($cleanProof.failures) -join ';')" }
        Add-Step "clean-target-recipe" $(if ($proofFailures.Count -eq 0) { "GREEN" } else { "RED" }) @{
          target = $cleanTarget
          required_evidence = $targetEvidence
          proof_status = $cleanProof.status
          current_provenance_sha256 = $currentProvenanceHash
          source_kit_sha256 = $cleanProof.source_kit_sha256
          target_kit_sha256 = $cleanProof.target_kit_sha256
          bundle_gate_status = $cleanProof.bundle_gate.status
          bundle_gate_result_route = $cleanProof.bundle_gate.result_route
          bundle_gate_baseline_route = $cleanProof.bundle_gate.baseline_route
          copied_file_count = $cleanProof.copied_file_count
          failures = @($proofFailures)
        }
      } catch {
        Add-Step "clean-target-recipe" "RED" @{ target = $cleanTarget; required_evidence = $targetEvidence; error = $_.Exception.Message }
      }
    }
  }
} else {
  Add-Step "clean-target-recipe" "DECLARED" @{ target = $cleanTarget; note = "Source-machine audit mode only. Closing gate requires M11_CLEAN_TARGET and clean install evidence." }
}

$result = @{
  phase = "M11"
  status = $overall
  steps = [object[]]$steps.ToArray()
}
if (-not $Evidence) { $Evidence = Join-Path ([System.IO.Path]::GetTempPath()) ("m11-gate-result-" + [guid]::NewGuid().ToString() + ".json") }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Evidence) | Out-Null
$result | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Evidence -Encoding UTF8
$result | ConvertTo-Json -Depth 12
if ($overall -eq "BLOCKED_PROVIDER") { exit 3 }
if ($overall -ne "GREEN") { exit 1 }
exit 0
