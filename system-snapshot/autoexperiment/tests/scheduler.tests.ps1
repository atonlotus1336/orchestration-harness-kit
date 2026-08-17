$script:RepoRoot = '__WINDOWS_WORKSPACE__'
$script:Rank = Join-Path $script:RepoRoot 'scripts\autoexperiment\rank.ps1'
$script:Scout = Join-Path $script:RepoRoot 'scripts\autoexperiment\scout.ps1'
$script:Portfolios = Join-Path $script:RepoRoot '.planning\autoexperiment-control-plane\contracts\portfolios.yaml'
$script:Ranking = Join-Path $script:RepoRoot '.planning\autoexperiment-control-plane\contracts\ranking.yaml'
$script:Campaign = Join-Path $script:RepoRoot 'scripts\autoexperiment\campaign.ps1'
$script:Converge = Join-Path $script:RepoRoot 'scripts\autoexperiment\converge.ps1'
$script:Schedules = Join-Path $script:RepoRoot '.planning\autoexperiment-control-plane\contracts\schedules.yaml'
$script:Observer = Join-Path $script:RepoRoot 'scripts\autoexperiment\observe.ps1'

function New-Opportunity {
  param([string]$Id,[string]$Portfolio,[bool]$Invariant=$false,[bool]$Degraded=$false,[int]$Debt=0,[int]$Age=0,[int]$Leverage=1,[int]$Impact=1,[int]$Ready=1,[double]$Benefit=1,[double]$Cost=1,[double]$Duration=10)
  [pscustomobject][ordered]@{
    opportunity_id=$Id; portfolio_id=$Portfolio; summary="Opportunity $Id"; evidence_handles=@("handle:$Id")
    invariant_failure=$Invariant; verified_degradation=$Degraded; coverage_debt=$Debt; age_rounds=$Age
    downstream_leverage=$Leverage; recurrence_user_impact=$Impact; verifier_readiness=$Ready
    expected_benefit=$Benefit; estimated_cost=$Cost; estimated_duration_minutes=$Duration
  }
}

function Invoke-RankFixture {
  param([object[]]$Opportunities,[int]$Budget=12)
  $path=Join-Path $TestDrive ('opportunities-'+[guid]::NewGuid().ToString('N')+'.json')
  [pscustomobject]@{opportunities=$Opportunities}|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $path -Encoding UTF8
  $output=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Rank -OpportunitiesPath $path -Cadence weekly -TotalBudget $Budget -PortfoliosPath $script:Portfolios -RankingPath $script:Ranking -Json
  (($output-join "`n")|ConvertFrom-Json)
}

Describe 'Coverage-first portfolio ranking' -Tag Ranking {
  It 'defines exactly the nine agreed portfolios' {
    $contract=Get-Content -LiteralPath $script:Portfolios -Raw|ConvertFrom-Json
    @($contract.portfolios).Count|Should Be 9
    @($contract.portfolios.id|Sort-Object -Unique).Count|Should Be 9
  }

  It 'allocates equal visibility before any surplus depth' {
    $ops=@(New-Opportunity 'critical' 'reliability-recovery' -Invariant $true)
    $report=Invoke-RankFixture $ops -Budget 12
    @($report.queue|Select-Object -First 9|Where-Object {$_.allocation -eq 'coverage'}).Count|Should Be 9
    @($report.queue|Select-Object -First 9|ForEach-Object {$_.portfolio_id}|Sort-Object -Unique).Count|Should Be 9
    @($report.queue|Select-Object -Skip 9|Where-Object {$_.portfolio_id -eq 'reliability-recovery'}).Count|Should Be 3
  }

  It 'gives surplus to the lexicographically highest need' {
    $ops=@(
      New-Opportunity 'normal' 'knowledge-retrieval' -Leverage 5 -Benefit 20
      New-Opportunity 'degraded' 'engineering-delivery' -Degraded $true
      New-Opportunity 'invariant' 'safety-privacy-governance' -Invariant $true
    )
    $report=Invoke-RankFixture $ops -Budget 10
    $report.queue[9].opportunity_id|Should Be 'invariant'
  }

  It 'uses age to prevent starvation among otherwise eligible work' {
    $ops=@(
      New-Opportunity 'newer' 'agent-prompt-quality' -Age 0 -Leverage 5
      New-Opportunity 'waiting' 'learning-memory' -Age 8 -Leverage 1
    )
    $report=Invoke-RankFixture $ops -Budget 10
    $report.queue[9].opportunity_id|Should Be 'waiting'
  }

  It 'breaks complete ties deterministically by opportunity id' {
    $ops=@(
      New-Opportunity 'z-last' 'knowledge-retrieval'
      New-Opportunity 'a-first' 'engineering-delivery'
    )
    $report=Invoke-RankFixture $ops -Budget 10
    $report.queue[9].opportunity_id|Should Be 'a-first'
  }

  It 'refuses a budget that cannot cover every due portfolio' {
    $ops=@(New-Opportunity 'one' 'knowledge-retrieval')
    $path=Join-Path $TestDrive 'too-small.json';[pscustomobject]@{opportunities=$ops}|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $path -Encoding UTF8
    $output=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Rank -OpportunitiesPath $path -Cadence weekly -TotalBudget 8 -PortfoliosPath $script:Portfolios -RankingPath $script:Ranking -Json 2>&1
    $LASTEXITCODE|Should Not Be 0
    ($output-join "`n")|Should Match 'cannot provide coverage'
  }

  It 'scouts only attributable opportunities in registered portfolios' {
    $input=Join-Path $TestDrive 'scout.json'
    [pscustomobject]@{opportunities=@(New-Opportunity 'scoped' 'markdown-token-efficiency')}|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $input -Encoding UTF8
    $output=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Scout -InputPath $input -PortfoliosPath $script:Portfolios -RankingPath $script:Ranking -Json
    $report=(($output-join "`n")|ConvertFrom-Json)
    $report.broad_scan|Should Be $false
    $report.opportunities[0].evidence_handles[0]|Should Be 'handle:scoped'
  }
}

function New-CampaignFixture {
  param([string]$Root,[hashtable]$SignalOverrides=@{},[bool]$Coupled=$false,[int]$MaxTransitions=20,[int]$NoProgress=2)
  New-Item -ItemType Directory -Force -Path $Root|Out-Null
  $contract=Join-Path $Root 'frozen.txt';Set-Content -LiteralPath $contract -Encoding UTF8 -Value 'frozen'
  $signals=[ordered]@{clarity_pass=$true;reproduced=$true;verifier_ready=$true;falsifiable=$true;kernel_complete=$true;independent_verified=$true;integration_pass=$true;ablation_pass=$true;exit_clear=$true}
  foreach($key in $SignalOverrides.Keys){$signals[$key]=$SignalOverrides[$key]}
  $campaign=[ordered]@{campaign_id='loop-test';builder_id='builder';verifier_id='verifier';builder_context_id='builder-context';verifier_context_id='fresh-verifier-context';frozen_contract_path=$contract;frozen_contract_sha256=(Get-FileHash -LiteralPath $contract -Algorithm SHA256).Hash;max_transitions=$MaxTransitions;no_progress_limit=$NoProgress;wall_clock_seconds=30;coupled_package=$Coupled;signals=$signals}
  $path=Join-Path $Root 'campaign.json';$campaign|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $path -Encoding UTF8
  [pscustomobject]@{Campaign=$path;State=(Join-Path $Root 'state.json');Contract=$contract}
}

function Invoke-CampaignFixture($Fixture){
  $output=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Campaign -CampaignPath $Fixture.Campaign -StatePath $Fixture.State -Json 2>&1
  $exitCode=$LASTEXITCODE
  $report=if($exitCode -eq 0){(($output-join "`n")|ConvertFrom-Json)}else{$null}
  [pscustomobject]@{ExitCode=$exitCode;Output=($output-join "`n");Report=$report}
}

Describe 'Nested category and convergence loops' -Tag Loops {
  It 'passes every gated mini-loop and writes complete exit clarity' {
    $fixture=New-CampaignFixture (Join-Path $TestDrive 'complete-loop')
    $result=Invoke-CampaignFixture $fixture
    $result.Report.status|Should Be 'complete'
    (@($result.Report.history.from)-join ',')|Should Be 'entry,clarity,reproduction,verifier-checkpoint,falsification,kernel,independent-verification,integration,exit-clarity'
    $result.Report.exit_record.exit_clear|Should Be $true
    (Test-Path -LiteralPath $fixture.State)|Should Be $true
  }

  It 'requires builder-verifier separation and fresh context' {
    $fixture=New-CampaignFixture (Join-Path $TestDrive 'same-verifier')
    $c=Get-Content -LiteralPath $fixture.Campaign -Raw|ConvertFrom-Json;$c.verifier_id=$c.builder_id;$c|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $fixture.Campaign -Encoding UTF8
    $result=Invoke-CampaignFixture $fixture
    $result.ExitCode|Should Not Be 0
    $result.Output|Should Match 'Builder and verifier must be separate'
  }

  It 'detects frozen-contract drift' {
    $fixture=New-CampaignFixture (Join-Path $TestDrive 'contract-drift');Add-Content -LiteralPath $fixture.Contract -Value 'drift'
    $result=Invoke-CampaignFixture $fixture
    $result.ExitCode|Should Not Be 0
    $result.Output|Should Match 'contract drift'
  }

  It 'returns failed clarity to a bounded clarity mini-loop' {
    $fixture=New-CampaignFixture (Join-Path $TestDrive 'bounded') -SignalOverrides @{clarity_pass=$false} -MaxTransitions 20 -NoProgress 2
    $result=Invoke-CampaignFixture $fixture
    $result.Report.status|Should Be 'stopped'
    $result.Report.stop_reason|Should Be 'no-progress'
    $result.Report.transitions|Should Be 3
  }

  It 'requires ablation before a coupled package can integrate' {
    $fixture=New-CampaignFixture (Join-Path $TestDrive 'ablation') -SignalOverrides @{ablation_pass=$false} -Coupled $true -NoProgress 1
    $result=Invoke-CampaignFixture $fixture
    $result.Report.status|Should Be 'stopped'
    $result.Report.history[-1].reason|Should Be 'ablation-required'
  }

  It 'routes cross-category effects back to affected clarity loops' {
    $path=Join-Path $TestDrive 'converge.json'
    [pscustomobject]@{results=@([pscustomobject]@{experiment_id='e1';portfolio_id='knowledge-retrieval';affected_portfolios=@('agent-prompt-quality','markdown-token-efficiency');coupled_package=$true;ablation_pass=$false;verified=$true})}|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $path -Encoding UTF8
    $output=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Converge -ResultsPath $path -Json
    $report=(($output-join "`n")|ConvertFrom-Json)
    @($report.feedback_edges).Count|Should Be 2
    @($report.feedback_edges|Where-Object {$_.state -eq 'clarity'}).Count|Should Be 2
    $report.packages[0].status|Should Be 'ablation-required'
    $report.packages[0].promotable|Should Be $false
  }
}

Describe 'Scheduled sandbox audit approval boundary' -Tag Policy {
  It 'automatically approves the daily audit package but never production implementation' {
    $schedule=Get-Content -LiteralPath $script:Schedules -Raw|ConvertFrom-Json
    $daily=@($schedule.chains|Where-Object {$_.id -eq 'daily'})|Select-Object -First 1
    $schedule.activation|Should Be 'automatic_sandbox_audit'
    $daily.audit_dispatch_approval|Should Be 'automatic'
    $daily.candidate_package_approval|Should Be 'automatic-gsd-package'
    $daily.production_implementation_approval|Should Be 'manual-confirmation-required'
  }

  It 'keeps weekly and monthly candidate packages behind manual confirmation' {
    $schedule=Get-Content -LiteralPath $script:Schedules -Raw|ConvertFrom-Json
    foreach($cadence in @('weekly','monthly')){
      $chain=@($schedule.chains|Where-Object {$_.id -eq $cadence})|Select-Object -First 1
      $chain.candidate_package_approval|Should Be 'manual-confirmation-required'
      $chain.production_implementation_approval|Should Be 'manual-confirmation-required'
      $chain.improvement_clock_trigger|Should Be $false
    }
  }

  It 'runs Codex with write access only to the marker-attested audit sandbox' {
    $observer=Get-Content -LiteralPath $script:Observer -Raw
    $observer|Should Match 'autoexperiment-sandbox.json'
    $observer|Should Match 'codex exec -s workspace-write'
    $observer|Should Match '-C \$auditSandbox'
    $observer|Should Match "productionApproval='manual-confirmation-required'"
    $observer|Should Not Match 'codex exec -s danger-full-access'
  }
}
