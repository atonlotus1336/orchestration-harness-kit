[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$InputPath,
  [string]$PortfoliosPath = '__WINDOWS_WORKSPACE__\.planning\autoexperiment-control-plane\contracts\portfolios.yaml',
  [string]$RankingPath = '__WINDOWS_WORKSPACE__\.planning\autoexperiment-control-plane\contracts\ranking.yaml',
  [switch]$Json
)
$ErrorActionPreference = 'Stop'
$portfolios = Get-Content -LiteralPath $PortfoliosPath -Raw | ConvertFrom-Json
$ranking = Get-Content -LiteralPath $RankingPath -Raw | ConvertFrom-Json
$known = @{}; foreach ($p in @($portfolios.portfolios)) { $known[[string]$p.id] = $true }
$input = Get-Content -LiteralPath $InputPath -Raw | ConvertFrom-Json
$out = New-Object System.Collections.Generic.List[object]
foreach ($item in @($input.opportunities)) {
  foreach ($field in @($ranking.required_opportunity_fields)) {
    if ($item.PSObject.Properties.Name -notcontains $field -or $null -eq $item.$field -or (($item.$field -is [string]) -and [string]::IsNullOrWhiteSpace([string]$item.$field))) { throw "Opportunity '$($item.opportunity_id)' missing field: $field" }
  }
  if (-not $known.ContainsKey([string]$item.portfolio_id)) { throw "Unknown portfolio: $($item.portfolio_id)" }
  if (@($item.evidence_handles).Count -eq 0) { throw "Opportunity '$($item.opportunity_id)' has no attributable evidence handle." }
  $out.Add($item)
}
$report = [pscustomobject]@{ schema_version='1.0.0'; opportunities=$out.ToArray(); broad_scan=$false; generated_at=(Get-Date -Format o) }
if ($Json) { $report | ConvertTo-Json -Depth 8 } else { $report }

