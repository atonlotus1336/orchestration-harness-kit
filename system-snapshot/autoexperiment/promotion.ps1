[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$WinnerPath,[Parameter(Mandatory=$true)][string]$OutputDirectory,[switch]$Json)
$ErrorActionPreference='Stop';$w=Get-Content -LiteralPath $WinnerPath -Raw|ConvertFrom-Json
$required=@('winner_id','title','outcome','evidence_paths','patch_path','baseline_path','verifier_path','economics','invariant_results','rollback_path','judge_decision','ablation_status')
foreach($f in $required){if($w.PSObject.Properties.Name -notcontains $f -or $null -eq $w.$f){throw "Winner field missing: $f"}}
if($w.judge_decision -ne 'accept'){throw 'Winner was not accepted by the compatibility/economic judge.'}
if($w.ablation_status -notin @('attributed','not-coupled')){throw 'Winner lacks resolved ablation evidence.'}
foreach($p in @($w.evidence_paths)+@($w.patch_path,$w.baseline_path,$w.verifier_path,$w.rollback_path)){if(-not(Test-Path -LiteralPath ([string]$p))){throw "Promotion evidence missing: $p"}}
$id=([string]$w.winner_id -replace '[^A-Za-z0-9._-]','-');$dir=Join-Path ([System.IO.Path]::GetFullPath($OutputDirectory)) $id;New-Item -ItemType Directory -Force -Path $dir|Out-Null
$brief=Get-Content -LiteralPath '__WINDOWS_WORKSPACE__\.planning\autoexperiment-control-plane\templates\IMPLEMENTATION-BRIEF.md' -Raw
$map=@{'{{TITLE}}'=$w.title;'{{OUTCOME}}'=$w.outcome;'{{EVIDENCE}}'=(@($w.evidence_paths)-join "`n");'{{PATCH}}'=$w.patch_path;'{{BASELINE_VERIFIER}}'="baseline=$($w.baseline_path)`nverifier=$($w.verifier_path)";'{{ECONOMICS}}'=($w.economics|ConvertTo-Json -Depth 6);'{{INVARIANTS}}'=($w.invariant_results|ConvertTo-Json -Depth 6);'{{ROLLBACK}}'=$w.rollback_path}
foreach($k in $map.Keys){$brief=$brief.Replace($k,[string]$map[$k])}
$briefPath=Join-Path $dir 'IMPLEMENTATION-BRIEF.md';$brief|Set-Content -LiteralPath $briefPath -Encoding UTF8
$manifest=[pscustomobject][ordered]@{schema_version='1.0.0';winner_id=$w.winner_id;brief=$briefPath;production_mutation=$false;git_history_mutation=$false;skill_activation=$false;next_commands=@('gsd-capture','gsd-plan-phase')};$manifestPath=Join-Path $dir 'promotion.json';$manifest|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $manifestPath -Encoding UTF8
if($Json){$manifest|ConvertTo-Json -Depth 6}else{$manifest}
