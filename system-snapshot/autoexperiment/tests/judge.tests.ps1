$script:RepoRoot='__WINDOWS_WORKSPACE__'
$script:Judge=Join-Path $script:RepoRoot 'scripts\autoexperiment\judge.ps1'
$script:Ablation=Join-Path $script:RepoRoot 'scripts\autoexperiment\ablation.ps1'
$script:CostModel=Join-Path $script:RepoRoot '.planning\autoexperiment-control-plane\contracts\cost-model.yaml'
$script:Invariants=Join-Path $script:RepoRoot '.planning\autoexperiment-control-plane\contracts\protected-invariants.yaml'

function New-JudgeCandidate {
  param([string]$Path,[double]$Baseline=10,[double]$Candidate=12,[double]$Noise=.5,[bool]$Repeat=$true,[bool]$Holdout=$true,[bool]$Compatibility=$true,[bool]$InvariantsPass=$true,[double]$Complexity=0,[double]$Dependencies=0,[bool]$CostJustified=$false)
  $contract=Get-Content -LiteralPath $script:Invariants -Raw|ConvertFrom-Json
  $invariantResults=@($contract.invariants|ForEach-Object{[pscustomobject]@{id=$_.id;pass=$InvariantsPass}})
  $object=[ordered]@{
    candidate_id='candidate';metric=[ordered]@{baseline=$Baseline;candidate=$Candidate;direction='maximize';noise_band=$Noise}
    repeat_pass=$Repeat;holdout_pass=$Holdout;compatibility_results=@([ordered]@{id='whole-system';pass=$Compatibility})
    invariant_results=$invariantResults;cost_justified=$CostJustified
    measured=[ordered]@{wall_clock_delta_seconds=0;rate_limit_pressure_delta=0;context_tokens_delta=0;concurrency_delta=0;dependency_count_delta=$Dependencies;maintenance_steps_delta=0;complexity_delta=$Complexity}
  }
  $object|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $Path -Encoding UTF8
}

function Invoke-JudgeFixture([string]$Path){
  $output=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Judge -CandidatePath $Path -CostModelPath $script:CostModel -InvariantsPath $script:Invariants -Json
  (($output-join "`n")|ConvertFrom-Json)
}

Describe 'Compatibility and economic judge' -Tag Judge {
  It 'rejects a local gain that breaks a protected invariant' {
    $path=Join-Path $TestDrive 'invariant.json';New-JudgeCandidate $path -InvariantsPass $false
    $report=Invoke-JudgeFixture $path
    $report.decision|Should Be 'reject';(@($report.reasons) -contains 'protected-invariant-regression')|Should Be $true
  }

  It 'rejects a local gain with whole-system compatibility loss' {
    $path=Join-Path $TestDrive 'compatibility.json';New-JudgeCandidate $path -Compatibility $false
    $report=Invoke-JudgeFixture $path
    $report.decision|Should Be 'reject';(@($report.reasons) -contains 'whole-system-regression')|Should Be $true
  }

  It 'leaves a gain inside measurement noise unproven' {
    $path=Join-Path $TestDrive 'noise.json';New-JudgeCandidate $path -Candidate 10.2 -Noise .5
    $report=Invoke-JudgeFixture $path
    $report.decision|Should Be 'unproven';$report.uncertainty.within_noise|Should Be $true
  }

  It 'leaves an unreproduced gain unproven' {
    $path=Join-Path $TestDrive 'repeat.json';New-JudgeCandidate $path -Repeat $false
    (Invoke-JudgeFixture $path).decision|Should Be 'unproven'
  }

  It 'rejects meaningful gain with unjustified added dependency cost' {
    $path=Join-Path $TestDrive 'cost.json';New-JudgeCandidate $path -Dependencies 1
    $report=Invoke-JudgeFixture $path
    $report.decision|Should Be 'reject';(@($report.increased_cost_factors) -contains 'dependency_count_delta')|Should Be $true
  }

  It 'accepts an equivalent result only when it is strictly simpler without other cost increases' {
    $path=Join-Path $TestDrive 'simple.json';New-JudgeCandidate $path -Candidate 10 -Complexity -2
    $report=Invoke-JudgeFixture $path
    $report.decision|Should Be 'accept';$report.complexity.simplification_win|Should Be $true
  }

  It 'accepts a reproducible Pareto improvement and keeps factors separate' {
    $path=Join-Path $TestDrive 'pareto.json';New-JudgeCandidate $path
    $report=Invoke-JudgeFixture $path
    $report.decision|Should Be 'accept';(@($report.reasons) -contains 'pareto-improvement')|Should Be $true
    $report.single_scalar_score|Should Be $null
    $report.costs.dependency_count_delta|Should Be 0
  }
}

function New-AblationFixture {
  param([string]$Root,[bool]$Unresolved=$false)
  $sandboxRoot=Join-Path $Root 'sandboxes';$source=Join-Path $sandboxRoot 'sandbox-source';New-Item -ItemType Directory -Force -Path $source|Out-Null
  Set-Content -LiteralPath (Join-Path $source '.autoexperiment-sandbox.json') -Encoding UTF8 -Value '{"id":"source"}'
  Set-Content -LiteralPath (Join-Path $source 'a.txt') -Encoding UTF8 -Value 'a';Set-Content -LiteralPath (Join-Path $source 'b.txt') -Encoding UTF8 -Value 'b'
  $eval=Join-Path $Root 'evaluate.ps1'
  if($Unresolved){Set-Content -LiteralPath $eval -Encoding UTF8 -Value "param(`$Workspace,`$ComponentId); @{metric=12}|ConvertTo-Json"}
  else{Set-Content -LiteralPath $eval -Encoding UTF8 -Value @'
param($Workspace,$ComponentId)
$a=Test-Path -LiteralPath (Join-Path $Workspace 'a.txt');$b=Test-Path -LiteralPath (Join-Path $Workspace 'b.txt')
$metric=if($a -and $b){12}elseif($b){10}elseif($a){11}else{8}
@{metric=$metric}|ConvertTo-Json
'@}
  $manifest=[ordered]@{experiment_id='coupled';sandbox_path=$source;sandbox_root=$sandboxRoot;evaluator_path=$eval;evaluator_sha256=(Get-FileHash -LiteralPath $eval -Algorithm SHA256).Hash;evaluate_script=$eval;components=@([ordered]@{id='a';relative_path='a.txt'},[ordered]@{id='b';relative_path='b.txt'});budget_max_components=2;full_metric=12;metric_direction='maximize';minimum_delta=1}
  $path=Join-Path $Root 'manifest.json';$manifest|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $path -Encoding UTF8
  [pscustomobject]@{Manifest=$path;Source=$source;Root=$sandboxRoot}
}

function Invoke-AblationFixture($Fixture){
  $output=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Ablation -ManifestPath $Fixture.Manifest -Json 2>&1
  $exit=$LASTEXITCODE;$report=if($exit -eq 0){(($output-join "`n")|ConvertFrom-Json)}else{$null}
  [pscustomobject]@{ExitCode=$exit;Output=($output-join "`n");Report=$report}
}

Describe 'Sandbox-confined coupled-change ablation' -Tag Ablation {
  It 'attributes component contribution without changing the source sandbox' {
    $fixture=New-AblationFixture (Join-Path $TestDrive 'attributed')
    $before=@(Get-ChildItem -LiteralPath $fixture.Source -File|ForEach-Object{$_.Name+'='+(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash})
    $result=Invoke-AblationFixture $fixture
    $after=@(Get-ChildItem -LiteralPath $fixture.Source -File|ForEach-Object{$_.Name+'='+(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash})
    $result.Report.status|Should Be 'attributed';$result.Report.promotable|Should Be $true
    @($result.Report.records|Where-Object{$_.classification -eq 'contributor'}).Count|Should Be 2
    @(Compare-Object $before $after).Count|Should Be 0
  }

  It 'records unresolved interaction when individual removals explain no gain' {
    $fixture=New-AblationFixture (Join-Path $TestDrive 'unresolved') -Unresolved $true
    $report=(Invoke-AblationFixture $fixture).Report
    $report.status|Should Be 'unresolved-interaction';$report.promotable|Should Be $false
  }

  It 'rejects a source without a sandbox marker' {
    $fixture=New-AblationFixture (Join-Path $TestDrive 'no-marker');Remove-Item -LiteralPath (Join-Path $fixture.Source '.autoexperiment-sandbox.json')
    $result=Invoke-AblationFixture $fixture
    $result.ExitCode|Should Not Be 0;$result.Output|Should Match 'marker is missing'
  }

  It 'rejects path traversal before creating a component copy' {
    $fixture=New-AblationFixture (Join-Path $TestDrive 'traversal')
    $m=Get-Content -LiteralPath $fixture.Manifest -Raw|ConvertFrom-Json;$m.components[0].relative_path='..\outside.txt';$m|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $fixture.Manifest -Encoding UTF8
    $result=Invoke-AblationFixture $fixture
    $result.ExitCode|Should Not Be 0;$result.Output|Should Match 'Unsafe ablation component path'
  }

  It 'rejects component sets above the declared budget' {
    $fixture=New-AblationFixture (Join-Path $TestDrive 'budget')
    $m=Get-Content -LiteralPath $fixture.Manifest -Raw|ConvertFrom-Json;$m.budget_max_components=1;$m|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $fixture.Manifest -Encoding UTF8
    $result=Invoke-AblationFixture $fixture
    $result.ExitCode|Should Not Be 0;$result.Output|Should Match 'budget exceeded'
  }

  It 'rejects frozen evaluator drift' {
    $fixture=New-AblationFixture (Join-Path $TestDrive 'evaluator-drift')
    $m=Get-Content -LiteralPath $fixture.Manifest -Raw|ConvertFrom-Json;Add-Content -LiteralPath $m.evaluator_path -Value '# drift'
    $result=Invoke-AblationFixture $fixture
    $result.ExitCode|Should Not Be 0;$result.Output|Should Match 'Frozen evaluator hash drift'
  }

  It 'rejects a source outside its declared sandbox root' {
    $fixture=New-AblationFixture (Join-Path $TestDrive 'wrong-root')
    $m=Get-Content -LiteralPath $fixture.Manifest -Raw|ConvertFrom-Json;$m.sandbox_root=Join-Path $TestDrive 'different-root';$m|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $fixture.Manifest -Encoding UTF8
    $result=Invoke-AblationFixture $fixture
    $result.ExitCode|Should Not Be 0;$result.Output|Should Match 'outside the declared sandbox root'
  }
}
