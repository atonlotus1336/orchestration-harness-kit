Set-StrictMode -Version 2.0

function Write-AutoExperimentLedgerRecord {
  param([Parameter(Mandatory = $true)]$Record, [Parameter(Mandatory = $true)][string]$LedgerPath)
  $required = @('experiment_id', 'campaign_id', 'cadence', 'backup_checkpoint_id', 'baseline_id', 'portfolio_id', 'parent_experiment_id', 'hypothesis', 'iteration', 'budget', 'primary_metric', 'baseline_metric', 'candidate_metric', 'complexity', 'cost', 'patch_id', 'status', 'reason', 'evaluator_sha256', 'baseline_sha256', 'rollback_id', 'timestamp')
  foreach ($field in $required) {
    if ($Record.PSObject.Properties.Name -notcontains $field) { throw "Ledger record missing field: $field" }
    if ($null -eq $Record.$field -or (($Record.$field -is [string]) -and [string]::IsNullOrWhiteSpace([string]$Record.$field))) { throw "Ledger record field is blank: $field" }
  }
  if ($Record.status -notin @('keep', 'discard', 'crash')) { throw "Invalid experiment status: $($Record.status)" }
  $directory = Split-Path -Parent ([System.IO.Path]::GetFullPath($LedgerPath))
  if (-not (Test-Path -LiteralPath $directory)) { New-Item -ItemType Directory -Force -Path $directory | Out-Null }
  Add-Content -LiteralPath $LedgerPath -Encoding UTF8 -Value ($Record | ConvertTo-Json -Depth 8 -Compress)
}

