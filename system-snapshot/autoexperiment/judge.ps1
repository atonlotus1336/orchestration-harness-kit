[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$CandidatePath,
  [string]$CostModelPath='__WINDOWS_WORKSPACE__\.planning\autoexperiment-control-plane\contracts\cost-model.yaml',
  [string]$InvariantsPath='__WINDOWS_WORKSPACE__\.planning\autoexperiment-control-plane\contracts\protected-invariants.yaml',
  [switch]$Json
)
$ErrorActionPreference='Stop'
$candidate=Get-Content -LiteralPath $CandidatePath -Raw|ConvertFrom-Json
$costModel=Get-Content -LiteralPath $CostModelPath -Raw|ConvertFrom-Json
$invariantContract=Get-Content -LiteralPath $InvariantsPath -Raw|ConvertFrom-Json
$required=@('candidate_id','metric','repeat_pass','holdout_pass','compatibility_results','invariant_results','cost_justified','measured')
foreach($field in $required){if($candidate.PSObject.Properties.Name -notcontains $field -or $null -eq $candidate.$field){throw "Candidate field missing: $field"}}
foreach($field in @($costModel.measured_factors|Where-Object{$_ -ne 'primary_metric_delta'})){if($candidate.measured.PSObject.Properties.Name -notcontains $field -or $null -eq $candidate.measured.$field){throw "Measured factor missing: $field"}}
$knownInvariants=@{};foreach($i in @($invariantContract.invariants)){$knownInvariants[[string]$i.id]=$true}
$declaredResults=@{};$failedInvariants=New-Object System.Collections.Generic.List[string]
foreach($r in @($candidate.invariant_results)){
  if(-not $knownInvariants.ContainsKey([string]$r.id)){throw "Unknown invariant result: $($r.id)"}
  $declaredResults[[string]$r.id]=$true
  if(-not [bool]$r.pass){$failedInvariants.Add([string]$r.id)}
}
$missingInvariantResults=@($knownInvariants.Keys|Where-Object{-not $declaredResults.ContainsKey($_)})
$failedCompatibility=@($candidate.compatibility_results|Where-Object{-not [bool]$_.pass}|ForEach-Object{[string]$_.id})
$baseline=[double]$candidate.metric.baseline;$value=[double]$candidate.metric.candidate;$noise=[double]$candidate.metric.noise_band
$delta=if([string]$candidate.metric.direction -eq 'minimize'){$baseline-$value}else{$value-$baseline}
$meaningful=$delta -gt $noise
$equivalent=[math]::Abs($value-$baseline) -le $noise
$factorNames=@($costModel.measured_factors|Where-Object{$_ -ne 'primary_metric_delta'})
$increased=@($factorNames|Where-Object{[double]$candidate.measured.$_ -gt 0})
$nonIncreasing=@($factorNames|Where-Object{[double]$candidate.measured.$_ -le 0}).Count -eq $factorNames.Count
$simpler=$equivalent -and [double]$candidate.measured.complexity_delta -lt 0 -and $nonIncreasing
$reasons=New-Object System.Collections.Generic.List[string]
$decision='unproven'
if($missingInvariantResults.Count){$decision='reject';$reasons.Add('missing-invariant-results')}
elseif($failedInvariants.Count){$decision='reject';$reasons.Add('protected-invariant-regression')}
elseif($failedCompatibility.Count){$decision='reject';$reasons.Add('whole-system-regression')}
elseif(-not [bool]$candidate.repeat_pass -or -not [bool]$candidate.holdout_pass){$decision='unproven';$reasons.Add('repeat-or-holdout-missing')}
elseif($simpler){$decision='accept';$reasons.Add('equivalent-result-strictly-simpler')}
elseif(-not $meaningful){$decision='unproven';$reasons.Add('gain-within-noise')}
elseif($increased.Count -and -not [bool]$candidate.cost_justified){$decision='reject';$reasons.Add('unjustified-cost-increase')}
else{$decision='accept';$reasons.Add($(if($increased.Count){'meaningful-gain-with-explicit-cost-justification'}else{'pareto-improvement'}))}
$report=[pscustomobject][ordered]@{
  schema_version='1.0.0';candidate_id=$candidate.candidate_id;decision=$decision;reasons=$reasons.ToArray()
  hard_gates=[ordered]@{all_invariants_pass=($failedInvariants.Count -eq 0 -and $missingInvariantResults.Count -eq 0);compatibility_pass=($failedCompatibility.Count -eq 0);repeat_pass=[bool]$candidate.repeat_pass;holdout_pass=[bool]$candidate.holdout_pass}
  benefit=[ordered]@{baseline=$baseline;candidate=$value;direction=$candidate.metric.direction;delta=$delta;noise_band=$noise;meaningful=$meaningful;equivalent=$equivalent}
  costs=[ordered]@{};complexity=[ordered]@{delta=[double]$candidate.measured.complexity_delta;simplification_win=$simpler}
  uncertainty=[ordered]@{repeat_pass=[bool]$candidate.repeat_pass;holdout_pass=[bool]$candidate.holdout_pass;within_noise=(-not $meaningful)}
  failed_invariants=$failedInvariants.ToArray();failed_compatibility=$failedCompatibility;increased_cost_factors=$increased;single_scalar_score=$null
}
foreach($name in $factorNames){$report.costs[$name]=[double]$candidate.measured.$name}
if($Json){$report|ConvertTo-Json -Depth 10}else{$report}

