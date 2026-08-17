[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$AdapterPath,
  [Parameter(Mandatory = $true)][string]$LedgerPath,
  [Parameter(Mandatory = $true)][string]$CampaignId,
  [Parameter(Mandatory = $true)][string]$Cadence,
  [Parameter(Mandatory = $true)][string]$BackupCheckpointId,
  [Parameter(Mandatory = $true)][string]$BaselineId,
  [Parameter(Mandatory = $true)][string]$PortfolioId,
  [Parameter(Mandatory = $true)][string]$Hypothesis,
  [ValidateRange(1, 1000)][int]$MaxIterations,
  [ValidateRange(1, 1000)][int]$NoProgressRounds,
  [ValidateRange(1, 86400)][int]$WallClockSeconds,
  [string]$SandboxRoot = (Join-Path ([System.IO.Path]::GetTempPath()) '__PRIVATE_TOKEN_02__-autoexperiment'),
  [switch]$Json
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'sandbox.ps1')
. (Join-Path $PSScriptRoot 'ledger.ps1')

foreach ($loopField in @{
    CampaignId = $CampaignId; Cadence = $Cadence; BackupCheckpointId = $BackupCheckpointId
    BaselineId = $BaselineId; PortfolioId = $PortfolioId; Hypothesis = $Hypothesis
  }.GetEnumerator()) {
  if ([string]::IsNullOrWhiteSpace([string]$loopField.Value)) { throw "Loop field is blank: $($loopField.Key)" }
}

function Get-RequiredHash([string]$Path, [string]$Expected, [string]$Name) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Name is missing: $Path" }
  $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
  if ($actual -ne $Expected) { throw "$Name hash drift: expected=$Expected actual=$actual" }
  return $actual
}

function Invoke-JsonOperation([string]$ScriptPath, [string]$Workspace, [int]$Iteration, [string]$HypothesisText) {
  if (-not (Test-Path -LiteralPath $ScriptPath -PathType Leaf)) { throw "Adapter operation is missing: $ScriptPath" }
  $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath -Workspace $Workspace -Iteration $Iteration -Hypothesis $HypothesisText
  if ($LASTEXITCODE -ne 0) { throw "Adapter operation failed (exit $LASTEXITCODE): $ScriptPath" }
  $text = ($output -join "`n").Trim()
  if (-not $text) { return $null }
  return $text | ConvertFrom-Json
}

function Test-DeclaredChange([string]$Relative, [object[]]$Declared) {
  $r = $Relative.Replace('\', '/').TrimStart('/')
  foreach ($surface in $Declared) {
    $s = ([string]$surface).Replace('\', '/').Trim('/')
    if ($r -eq $s -or $r.StartsWith($s + '/')) { return $true }
  }
  return $false
}

function Test-IsImprovement([double]$Candidate, [double]$Best, [string]$Direction, [double]$Delta) {
  if ($Direction -eq 'maximize') { return $Candidate -ge ($Best + $Delta) }
  return $Candidate -le ($Best - $Delta)
}

$adapter = Get-Content -LiteralPath $AdapterPath -Raw | ConvertFrom-Json
$requiredFields = @('id', 'version', 'description', 'source_path', 'editable_surfaces', 'operations', 'evaluator', 'baseline', 'primary_metric', 'guardrails', 'cost', 'rollback')
foreach ($field in $requiredFields) { if ($adapter.PSObject.Properties.Name -notcontains $field -or $null -eq $adapter.$field) { throw "Adapter missing field: $field" } }
foreach ($operation in @('inspect', 'generate', 'evaluate', 'guardrail', 'cost', 'rollback')) {
  if ($adapter.operations.PSObject.Properties.Name -notcontains $operation -or [string]::IsNullOrWhiteSpace([string]$adapter.operations.$operation)) { throw "Adapter operation missing: $operation" }
}
if (@($adapter.editable_surfaces).Count -eq 0) { throw 'Adapter declares no editable surfaces.' }
if ($adapter.primary_metric.direction -notin @('maximize', 'minimize')) { throw 'Metric direction must be maximize or minimize.' }

$source = [System.IO.Path]::GetFullPath([string]$adapter.source_path)
$evaluatorPath = [System.IO.Path]::GetFullPath([string]$adapter.evaluator.path)
$baselinePath = [System.IO.Path]::GetFullPath([string]$adapter.baseline.path)
$operationPaths = @{}
foreach ($name in @('inspect', 'generate', 'evaluate', 'guardrail', 'cost', 'rollback')) { $operationPaths[$name] = [System.IO.Path]::GetFullPath([string]$adapter.operations.$name) }
$evaluatorHash = Get-RequiredHash $evaluatorPath ([string]$adapter.evaluator.sha256) 'Evaluator'
$baselineHash = Get-RequiredHash $baselinePath ([string]$adapter.baseline.sha256) 'Baseline'
$sourceBefore = Get-AutoExperimentTreeHash $source
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$sandboxes = New-Object System.Collections.Generic.List[string]
$records = New-Object System.Collections.Generic.List[object]
$bestWorkspace = $null
$bestExperiment = 'root'
$bestMetric = $null
$baselineMetric = $null
$bestComplexity = [double]::PositiveInfinity
$bestCost = [double]::PositiveInfinity
$noProgress = 0
$stopReason = 'max-iterations'

try {
  $bestWorkspace = New-AutoExperimentSandbox -Source $source -SandboxRoot $SandboxRoot
  $sandboxes.Add($bestWorkspace)
  $null = Invoke-JsonOperation $operationPaths.inspect $bestWorkspace 0 $Hypothesis
  $baselineEval = Invoke-JsonOperation $operationPaths.evaluate $bestWorkspace 0 $Hypothesis
  if ($null -eq $baselineEval -or $null -eq $baselineEval.metric) { throw 'Baseline evaluator returned no metric.' }
  $bestMetric = [double]$baselineEval.metric
  $baselineMetric = $bestMetric
  if ($null -ne $baselineEval.complexity) { $bestComplexity = [double]$baselineEval.complexity }
  if ($null -ne $baselineEval.cost) { $bestCost = [double]$baselineEval.cost }

  for ($iteration = 1; $iteration -le $MaxIterations; $iteration++) {
    if ($stopwatch.Elapsed.TotalSeconds -ge $WallClockSeconds) { $stopReason = 'wall-clock'; break }
    if ($noProgress -ge $NoProgressRounds) { $stopReason = 'no-progress'; break }
    $experimentId = "$CampaignId-$iteration"
    $parentExperiment = $bestExperiment
    $candidate = $null
    $status = 'crash'; $reason = ''; $candidateMetric = $null; $complexity = $null; $cost = $null; $patchId = 'none'; $rollbackId = 'sandbox-discard'
    try {
      $null = Get-RequiredHash $evaluatorPath $evaluatorHash 'Evaluator'
      $null = Get-RequiredHash $baselinePath $baselineHash 'Baseline'
      $candidate = New-AutoExperimentSandbox -Source $bestWorkspace -SandboxRoot $SandboxRoot
      $sandboxes.Add($candidate)
      $null = Invoke-JsonOperation $operationPaths.generate $candidate $iteration $Hypothesis
      $changed = @(Compare-AutoExperimentTrees -BeforeRoot $bestWorkspace -AfterRoot $candidate)
      if ($changed.Count -ne 1) { throw "One-change contract violated; changed_files=$($changed.Count)." }
      if (-not (Test-DeclaredChange $changed[0] @($adapter.editable_surfaces))) { throw "Undeclared editable surface changed: $($changed[0])" }
      $patchId = (Get-FileHash -LiteralPath (Join-Path $candidate ($changed[0].Replace('/', '\'))) -Algorithm SHA256).Hash.Substring(0, 16).ToLowerInvariant()
      $evaluation = Invoke-JsonOperation $operationPaths.evaluate $candidate $iteration $Hypothesis
      if ($null -eq $evaluation -or $null -eq $evaluation.metric) { throw 'Evaluator returned no primary metric.' }
      $guard = Invoke-JsonOperation $operationPaths.guardrail $candidate $iteration $Hypothesis
      if ($guard -and $guard.PSObject.Properties.Name -contains 'pass' -and -not [bool]$guard.pass) { throw 'Hard guardrail rejected candidate.' }
      $costResult = Invoke-JsonOperation $operationPaths.cost $candidate $iteration $Hypothesis
      $candidateMetric = [double]$evaluation.metric
      $complexity = if ($null -ne $evaluation.complexity) { [double]$evaluation.complexity } else { 0.0 }
      $cost = if ($costResult -and $null -ne $costResult.cost) { [double]$costResult.cost } elseif ($null -ne $evaluation.cost) { [double]$evaluation.cost } else { 0.0 }
      $minimumDelta = [double]$adapter.primary_metric.minimum_delta
      $band = [double]$adapter.primary_metric.equivalence_band
      $improved = Test-IsImprovement $candidateMetric $bestMetric ([string]$adapter.primary_metric.direction) $minimumDelta
      $equivalent = [math]::Abs($candidateMetric - $bestMetric) -le $band
      if ($improved -or ($equivalent -and ($complexity -lt $bestComplexity -or ($complexity -eq $bestComplexity -and $cost -lt $bestCost)))) {
        $status = 'keep'; $reason = if ($improved) { 'primary-metric-improved' } else { 'equivalent-result-simpler-or-cheaper' }
        $bestWorkspace = $candidate; $bestExperiment = $experimentId; $bestMetric = $candidateMetric; $bestComplexity = $complexity; $bestCost = $cost; $noProgress = 0
      } else {
        $status = 'discard'; $reason = 'no-worthwhile-improvement'; $noProgress++
      }
    } catch {
      $status = 'crash'; $reason = $_.Exception.Message; $noProgress++
    }
    $record = [pscustomobject][ordered]@{
      experiment_id = $experimentId; campaign_id = $CampaignId; cadence = $Cadence; backup_checkpoint_id = $BackupCheckpointId
      baseline_id = $BaselineId; portfolio_id = $PortfolioId; parent_experiment_id = $parentExperiment; hypothesis = $Hypothesis
      iteration = $iteration; budget = [ordered]@{ max_iterations = $MaxIterations; no_progress_rounds = $NoProgressRounds; wall_clock_seconds = $WallClockSeconds }
      primary_metric = [string]$adapter.primary_metric.name; baseline_metric = $baselineMetric; candidate_metric = if ($null -eq $candidateMetric) { 'unavailable' } else { $candidateMetric }
      complexity = if ($null -eq $complexity) { 'unavailable' } else { $complexity }; cost = if ($null -eq $cost) { 'unavailable' } else { $cost }
      patch_id = $patchId; status = $status; reason = $reason; evaluator_sha256 = $evaluatorHash; baseline_sha256 = $baselineHash
      rollback_id = $rollbackId; timestamp = (Get-Date -Format o)
    }
    Write-AutoExperimentLedgerRecord -Record $record -LedgerPath $LedgerPath
    $records.Add($record)
    if ($stopwatch.Elapsed.TotalSeconds -ge $WallClockSeconds) { $stopReason = 'wall-clock'; break }
  }
  if ($records.Count -lt $MaxIterations -and $stopReason -eq 'max-iterations' -and $noProgress -ge $NoProgressRounds) { $stopReason = 'no-progress' }
} finally {
  $sourceAfter = Get-AutoExperimentTreeHash $source
  $sourceChanged = @(Compare-Object ($sourceBefore.GetEnumerator() | ForEach-Object { $_.Key + '=' + $_.Value }) ($sourceAfter.GetEnumerator() | ForEach-Object { $_.Key + '=' + $_.Value })).Count -gt 0
  foreach ($sandbox in @($sandboxes.ToArray() | Sort-Object Length -Descending -Unique)) {
    if (Test-Path -LiteralPath $sandbox) { Remove-AutoExperimentSandbox -Path $sandbox -SandboxRoot $SandboxRoot }
  }
  $stopwatch.Stop()
}

if ($sourceChanged) { throw 'Source tree changed during research; invariant violated.' }
$report = [pscustomobject][ordered]@{ status = 'complete'; stop_reason = $stopReason; iterations = $records.Count; best_experiment_id = $bestExperiment; best_metric = $bestMetric; elapsed_seconds = [math]::Round($stopwatch.Elapsed.TotalSeconds, 3); source_unchanged = $true; git_operations = 0; records = $records.ToArray() }
if ($Json) { $report | ConvertTo-Json -Depth 10 } else { $report }
