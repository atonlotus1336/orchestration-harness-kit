[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ResultsPath,[switch]$Json)
$ErrorActionPreference='Stop'
$input=Get-Content -LiteralPath $ResultsPath -Raw|ConvertFrom-Json
$feedback=New-Object System.Collections.Generic.List[object]
$packages=New-Object System.Collections.Generic.List[object]
foreach($result in @($input.results)){
  $coupled=@($result.affected_portfolios).Count -gt 0
  $ablationRequired=$coupled -or [bool]$result.coupled_package
  $ablationPass=($result.PSObject.Properties.Name -contains 'ablation_pass') -and [bool]$result.ablation_pass
  foreach($affected in @($result.affected_portfolios|Where-Object{$_ -and $_ -ne $result.portfolio_id}|Sort-Object -Unique)){
    $feedback.Add([pscustomobject][ordered]@{from_portfolio=$result.portfolio_id;to_portfolio=$affected;experiment_id=$result.experiment_id;reason='cross-category-effect';state='clarity'})
  }
  $packageStatus=if($ablationRequired -and -not $ablationPass){'ablation-required'}elseif([bool]$result.verified){'verified'}else{'verification-required'}
  $packages.Add([pscustomobject][ordered]@{experiment_id=$result.experiment_id;portfolio_id=$result.portfolio_id;coupled=$ablationRequired;ablation_required=$ablationRequired;ablation_pass=$ablationPass;promotable=([bool]$result.verified -and (-not $ablationRequired -or $ablationPass));status=$packageStatus})
}
$report=[pscustomobject][ordered]@{schema_version='1.0.0';feedback_edges=$feedback.ToArray();packages=$packages.ToArray();generated_at=(Get-Date -Format o)}
if($Json){$report|ConvertTo-Json -Depth 8}else{$report}
