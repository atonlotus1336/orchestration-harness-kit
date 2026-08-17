[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ManifestPath,[switch]$Json)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'sandbox.ps1')
$manifest=Get-Content -LiteralPath $ManifestPath -Raw|ConvertFrom-Json
$required=@('experiment_id','sandbox_path','sandbox_root','evaluator_path','evaluator_sha256','evaluate_script','components','budget_max_components','full_metric','metric_direction','minimum_delta')
foreach($field in $required){if($manifest.PSObject.Properties.Name -notcontains $field -or $null -eq $manifest.$field){throw "Ablation manifest field missing: $field"}}
$source=[System.IO.Path]::GetFullPath([string]$manifest.sandbox_path).TrimEnd('\')
$root=[System.IO.Path]::GetFullPath([string]$manifest.sandbox_root).TrimEnd('\')
if(-not $source.StartsWith($root+'\')){throw 'Source sandbox is outside the declared sandbox root.'}
if(-not(Test-Path -LiteralPath (Join-Path $source '.autoexperiment-sandbox.json') -PathType Leaf)){throw 'Source sandbox marker is missing.'}
if(@($manifest.components).Count -gt [int]$manifest.budget_max_components){throw 'Ablation component budget exceeded.'}
$evaluatorHash=(Get-FileHash -LiteralPath ([string]$manifest.evaluator_path) -Algorithm SHA256).Hash
if($evaluatorHash -ne [string]$manifest.evaluator_sha256){throw 'Frozen evaluator hash drift.'}
$sourceBefore=Get-AutoExperimentTreeHash $source
$records=New-Object System.Collections.Generic.List[object]
foreach($component in @($manifest.components)){
  $relative=[string]$component.relative_path
  if([System.IO.Path]::IsPathRooted($relative) -or $relative -match '(^|[\\/])\.\.([\\/]|$)'){throw "Unsafe ablation component path: $relative"}
  $copy=New-AutoExperimentSandbox -Source $source -SandboxRoot $root
  try{
    $target=[System.IO.Path]::GetFullPath((Join-Path $copy $relative))
    if(-not $target.StartsWith($copy.TrimEnd('\')+'\')){throw "Ablation target escaped sandbox: $relative"}
    if(-not(Test-Path -LiteralPath $target)){throw "Ablation component missing: $relative"}
    $renamed=$target+'.ablated-'+[guid]::NewGuid().ToString('N')
    Move-Item -LiteralPath $target -Destination $renamed
    $output=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File ([string]$manifest.evaluate_script) -Workspace $copy -ComponentId ([string]$component.id)
    if($LASTEXITCODE -ne 0){throw "Ablation evaluator failed for component: $($component.id)"}
    $evaluation=(($output-join "`n")|ConvertFrom-Json);$metric=[double]$evaluation.metric
    $contribution=if([string]$manifest.metric_direction -eq 'minimize'){$metric-[double]$manifest.full_metric}else{[double]$manifest.full_metric-$metric}
    $classification=if($contribution -ge [double]$manifest.minimum_delta){'contributor'}elseif($contribution -le -[double]$manifest.minimum_delta){'harmful'}else{'no-independent-effect'}
    $records.Add([pscustomobject][ordered]@{component_id=$component.id;relative_path=$relative;ablated_metric=$metric;contribution=$contribution;classification=$classification})
  }finally{if($copy -and (Test-Path -LiteralPath $copy)){Remove-AutoExperimentSandbox -Path $copy -SandboxRoot $root}}
}
$sourceAfter=Get-AutoExperimentTreeHash $source
$beforeLines=@($sourceBefore.GetEnumerator()|ForEach-Object{$_.Key+'='+$_.Value});$afterLines=@($sourceAfter.GetEnumerator()|ForEach-Object{$_.Key+'='+$_.Value})
if(@(Compare-Object $beforeLines $afterLines).Count){throw 'Source sandbox changed during ablation.'}
$contributors=@($records|Where-Object{$_.classification -eq 'contributor'})
$unresolved=($records.Count -gt 1 -and $contributors.Count -eq 0)
$status=if($unresolved){'unresolved-interaction'}elseif($contributors.Count){'attributed'}else{'no-causal-gain'}
$report=[pscustomobject][ordered]@{schema_version='1.0.0';experiment_id=$manifest.experiment_id;status=$status;promotable=($status -eq 'attributed');source_unchanged=$true;components_tested=$records.Count;records=$records.ToArray()}
if($Json){$report|ConvertTo-Json -Depth 8}else{$report}
