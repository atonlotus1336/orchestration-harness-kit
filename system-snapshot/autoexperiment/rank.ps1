[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$OpportunitiesPath,
  [ValidateSet('daily','weekly','monthly')][string]$Cadence,
  [ValidateRange(1,1000)][int]$TotalBudget,
  [string]$PortfoliosPath = '__WINDOWS_WORKSPACE__\.planning\autoexperiment-control-plane\contracts\portfolios.yaml',
  [string]$RankingPath = '__WINDOWS_WORKSPACE__\.planning\autoexperiment-control-plane\contracts\ranking.yaml',
  [switch]$Json
)
$ErrorActionPreference = 'Stop'
$portfolios = Get-Content -LiteralPath $PortfoliosPath -Raw | ConvertFrom-Json
$ranking = Get-Content -LiteralPath $RankingPath -Raw | ConvertFrom-Json
$input = Get-Content -LiteralPath $OpportunitiesPath -Raw | ConvertFrom-Json
$due = @($portfolios.portfolios | Where-Object { $Cadence -in @($_.cadences) } | Sort-Object order)
if ($TotalBudget -lt $due.Count) { throw "Budget $TotalBudget cannot provide coverage for all $($due.Count) due portfolios." }
$known = @{}; foreach ($p in $due) { $known[[string]$p.id] = $true }
$opportunities = @($input.opportunities)
foreach ($o in $opportunities) {
  if (-not $known.ContainsKey([string]$o.portfolio_id)) { throw "Opportunity targets an unknown or non-due portfolio: $($o.portfolio_id)" }
  foreach ($field in @($ranking.required_opportunity_fields)) { if ($o.PSObject.Properties.Name -notcontains $field -or $null -eq $o.$field) { throw "Opportunity '$($o.opportunity_id)' missing field: $field" } }
  $cost = [double]$o.estimated_cost
  $ratio = if ($cost -eq 0) { [double]$o.expected_benefit } else { [double]$o.expected_benefit / $cost }
  $o | Add-Member -NotePropertyName benefit_cost_ratio -NotePropertyValue $ratio -Force
}

$ranked = @($opportunities | Sort-Object `
  @{Expression={[int][bool]$_.invariant_failure};Descending=$true},
  @{Expression={[int][bool]$_.verified_degradation};Descending=$true},
  @{Expression={[int]$_.coverage_debt};Descending=$true},
  @{Expression={[int]$_.age_rounds};Descending=$true},
  @{Expression={[int]$_.downstream_leverage};Descending=$true},
  @{Expression={[int]$_.recurrence_user_impact};Descending=$true},
  @{Expression={[int]$_.verifier_readiness};Descending=$true},
  @{Expression={[double]$_.benefit_cost_ratio};Descending=$true},
  @{Expression={[double]$_.estimated_duration_minutes};Descending=$false},
  @{Expression={[string]$_.opportunity_id};Descending=$false})

$queue = New-Object System.Collections.Generic.List[object]
foreach ($p in $due) {
  $bestForPortfolio = @($ranked | Where-Object { $_.portfolio_id -eq $p.id }) | Select-Object -First 1
  $coverageOpportunity = if($bestForPortfolio){$bestForPortfolio.opportunity_id}else{'coverage-scan:'+$p.id}
  $queue.Add([pscustomobject][ordered]@{
      position=$queue.Count+1; allocation='coverage'; portfolio_id=$p.id
      opportunity_id=$coverageOpportunity
      experiment_ready=[bool]($bestForPortfolio -and [int]$bestForPortfolio.verifier_readiness -ge [int]$ranking.rules.minimum_verifier_readiness_for_experiment)
    })
}
$remaining = $TotalBudget - $queue.Count
for ($i=0; $i -lt $remaining -and $ranked.Count -gt 0; $i++) {
  $o = $ranked[$i % $ranked.Count]
  $queue.Add([pscustomobject][ordered]@{position=$queue.Count+1;allocation='surplus';portfolio_id=$o.portfolio_id;opportunity_id=$o.opportunity_id;experiment_ready=([int]$o.verifier_readiness -ge [int]$ranking.rules.minimum_verifier_readiness_for_experiment)})
}
$report=[pscustomobject][ordered]@{schema_version='1.0.0';cadence=$Cadence;budget=$TotalBudget;due_portfolios=$due.Count;coverage_units=$due.Count;surplus_units=$queue.Count-$due.Count;queue=$queue.ToArray()}
if($Json){$report|ConvertTo-Json -Depth 8}else{$report}
