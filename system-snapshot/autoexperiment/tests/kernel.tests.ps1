$script:RepoRoot = '__WINDOWS_WORKSPACE__'
$script:Kernel = Join-Path $script:RepoRoot 'scripts\autoexperiment\kernel.ps1'
$script:AdapterSchema = Join-Path $script:RepoRoot '.planning\autoexperiment-control-plane\adapters\schema.yaml'
$script:CampaignTemplate = Join-Path $script:RepoRoot '.planning\autoexperiment-control-plane\templates\CAMPAIGN.md'

function New-KernelFixture {
  param([string]$Root, [string]$Hypothesis = 'improve')
  $source = Join-Path $Root 'source'
  $ops = Join-Path $Root 'ops'
  New-Item -ItemType Directory -Force -Path $source, $ops | Out-Null
  Set-Content -LiteralPath (Join-Path $source 'candidate.txt') -Encoding UTF8 -NoNewline -Value 'base'
  Set-Content -LiteralPath (Join-Path $source 'hidden.txt') -Encoding UTF8 -NoNewline -Value 'protected'
  Set-Content -LiteralPath (Join-Path $Root 'baseline.json') -Encoding UTF8 -Value '{"id":"baseline"}'

  Set-Content -LiteralPath (Join-Path $ops 'inspect.ps1') -Encoding UTF8 -Value @'
param($Workspace,$Iteration,$Hypothesis)
'{}'
'@
  Set-Content -LiteralPath (Join-Path $ops 'generate.ps1') -Encoding UTF8 -Value @'
param($Workspace,$Iteration,$Hypothesis)
switch ($Hypothesis) {
  'crash' { exit 9 }
  'undeclared' { Set-Content -LiteralPath (Join-Path $Workspace 'hidden.txt') -NoNewline -Value "changed-$Iteration" }
  'slow' { Start-Sleep -Seconds 2; Set-Content -LiteralPath (Join-Path $Workspace 'candidate.txt') -NoNewline -Value "discard-$Iteration" }
  'simple' { Set-Content -LiteralPath (Join-Path $Workspace 'candidate.txt') -NoNewline -Value 'simple' }
  'discard' { Set-Content -LiteralPath (Join-Path $Workspace 'candidate.txt') -NoNewline -Value "discard-$Iteration" }
  default { Set-Content -LiteralPath (Join-Path $Workspace 'candidate.txt') -NoNewline -Value "improve-$Iteration" }
}
'{}'
'@
  Set-Content -LiteralPath (Join-Path $ops 'evaluate.ps1') -Encoding UTF8 -Value @'
param($Workspace,$Iteration,$Hypothesis)
$value = Get-Content -LiteralPath (Join-Path $Workspace 'candidate.txt') -Raw
if ($value -eq 'base') { @{metric=10;complexity=10;cost=10} | ConvertTo-Json; exit 0 }
if ($value -eq 'simple') { @{metric=10;complexity=5;cost=10} | ConvertTo-Json; exit 0 }
if ($value -match '^improve-(\d+)$') { @{metric=(10+[int]$Matches[1]);complexity=10;cost=10} | ConvertTo-Json; exit 0 }
@{metric=10;complexity=10;cost=10} | ConvertTo-Json
'@
  Set-Content -LiteralPath (Join-Path $ops 'guardrail.ps1') -Encoding UTF8 -Value @'
param($Workspace,$Iteration,$Hypothesis)
'{"pass":true}'
'@
  Set-Content -LiteralPath (Join-Path $ops 'cost.ps1') -Encoding UTF8 -Value @'
param($Workspace,$Iteration,$Hypothesis)
'{"cost":10}'
'@
  Set-Content -LiteralPath (Join-Path $ops 'rollback.ps1') -Encoding UTF8 -Value @'
param($Workspace,$Iteration,$Hypothesis)
'{}'
'@

  $eval = Join-Path $ops 'evaluate.ps1'
  $baseline = Join-Path $Root 'baseline.json'
  $adapter = [ordered]@{
    id='fixture'; version='1.0.0'; description='deterministic fixture'; source_path=$source
    editable_surfaces=@('candidate.txt')
    operations=[ordered]@{
      inspect=(Join-Path $ops 'inspect.ps1'); generate=(Join-Path $ops 'generate.ps1'); evaluate=$eval
      guardrail=(Join-Path $ops 'guardrail.ps1'); cost=(Join-Path $ops 'cost.ps1'); rollback=(Join-Path $ops 'rollback.ps1')
    }
    evaluator=[ordered]@{path=$eval;sha256=(Get-FileHash -LiteralPath $eval -Algorithm SHA256).Hash}
    baseline=[ordered]@{path=$baseline;sha256=(Get-FileHash -LiteralPath $baseline -Algorithm SHA256).Hash}
    primary_metric=[ordered]@{name='score';direction='maximize';minimum_delta=1;equivalence_band=0}
    guardrails=@('fixture-pass'); cost=[ordered]@{basis='subscription-seat'}; rollback=[ordered]@{mode='sandbox-discard'}
  }
  $adapterPath = Join-Path $Root 'adapter.json'
  $adapter | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $adapterPath -Encoding UTF8
  return [pscustomobject]@{ Root=$Root; Source=$source; Adapter=$adapterPath; Ledger=(Join-Path $Root 'experiments.jsonl'); Sandbox=(Join-Path $Root 'sandboxes'); Hypothesis=$Hypothesis }
}

function Invoke-KernelFixture {
  param($Fixture, [int]$MaxIterations = 2, [int]$NoProgressRounds = 2, [int]$WallClockSeconds = 30, [string]$CampaignId = 'test-campaign')
  $before = @(Get-ChildItem -LiteralPath $Fixture.Source -File | ForEach-Object { $_.Name + '=' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash })
  $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Kernel `
    -AdapterPath $Fixture.Adapter -LedgerPath $Fixture.Ledger -CampaignId $CampaignId -Cadence 'test' `
    -BackupCheckpointId 'backup-green' -BaselineId 'baseline-1' -PortfolioId 'test-portfolio' `
    -Hypothesis $Fixture.Hypothesis -MaxIterations $MaxIterations -NoProgressRounds $NoProgressRounds `
    -WallClockSeconds $WallClockSeconds -SandboxRoot $Fixture.Sandbox -Json 2>&1
  $exitCode = $LASTEXITCODE
  $after = @(Get-ChildItem -LiteralPath $Fixture.Source -File | ForEach-Object { $_.Name + '=' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash })
  $report = $null
  if ($exitCode -eq 0) { $report = (($output -join "`n") | ConvertFrom-Json) }
  return [pscustomobject]@{ ExitCode=$exitCode; Output=($output -join "`n"); Report=$report; SourceDiff=@(Compare-Object $before $after) }
}

Describe 'Kernel contracts' -Tag Contract {
  It 'defines complete adapter operations and loop hard stops' {
    $schema = Get-Content -LiteralPath $script:AdapterSchema -Raw | ConvertFrom-Json
    @($schema.required_operations).Count | Should Be 6
    $template = Get-Content -LiteralPath $script:CampaignTemplate -Raw
    foreach ($term in @('trigger:', 'goal:', 'verifier:', 'builder:', 'judge:', 'state:', 'max-iter=', 'no-progress=', 'wall-clock=', 'cost basis:', 'on failure:', 'human checkpoint:')) { $template.Contains($term) | Should Be $true }
  }

  It 'accepts a complete adapter fixture' {
    $fixture = New-KernelFixture (Join-Path $TestDrive 'complete')
    $result = Invoke-KernelFixture $fixture -MaxIterations 1
    $result.ExitCode | Should Be 0
    $result.Report.records.Count | Should Be 1
  }

  It 'rejects a changed evaluator hash' {
    $fixture = New-KernelFixture (Join-Path $TestDrive 'drift')
    Add-Content -LiteralPath ((Get-Content -LiteralPath $fixture.Adapter -Raw | ConvertFrom-Json).evaluator.path) -Value '# drift'
    $result = Invoke-KernelFixture $fixture -MaxIterations 1
    $result.ExitCode | Should Not Be 0
    $result.Output | Should Match 'Evaluator hash drift'
  }

  It 'rejects a blank loop field' {
    $fixture = New-KernelFixture (Join-Path $TestDrive 'blank-loop')
    $result = Invoke-KernelFixture $fixture -MaxIterations 1 -CampaignId ' '
    $result.ExitCode | Should Not Be 0
    $result.Output | Should Match 'Loop field is blank: CampaignId'
  }

  It 'crashes an undeclared surface change' {
    $fixture = New-KernelFixture (Join-Path $TestDrive 'undeclared') -Hypothesis 'undeclared'
    $result = Invoke-KernelFixture $fixture -MaxIterations 1
    $result.ExitCode | Should Be 0
    $result.Report.records[0].status | Should Be 'crash'
    $result.Report.records[0].reason | Should Match 'Undeclared editable surface'
  }
}

Describe 'Karpathy experiment kernel' {
  It 'keeps measured improvements and preserves lineage' {
    $fixture = New-KernelFixture (Join-Path $TestDrive 'improve') -Hypothesis 'improve'
    $result = Invoke-KernelFixture $fixture -MaxIterations 2
    $result.ExitCode | Should Be 0
    @($result.Report.records | Where-Object { $_.status -eq 'keep' }).Count | Should Be 2
    $result.Report.records[1].parent_experiment_id | Should Be 'test-campaign-1'
    $result.SourceDiff.Count | Should Be 0
  }

  It 'discards candidates without worthwhile improvement' {
    $fixture = New-KernelFixture (Join-Path $TestDrive 'discard') -Hypothesis 'discard'
    $result = Invoke-KernelFixture $fixture -MaxIterations 1
    $result.Report.records[0].status | Should Be 'discard'
  }

  It 'keeps an equivalent but simpler candidate' {
    $fixture = New-KernelFixture (Join-Path $TestDrive 'simple') -Hypothesis 'simple'
    $result = Invoke-KernelFixture $fixture -MaxIterations 1
    $result.Report.records[0].status | Should Be 'keep'
    $result.Report.records[0].reason | Should Match 'simpler-or-cheaper'
  }

  It 'records generator failures as crashes' {
    $fixture = New-KernelFixture (Join-Path $TestDrive 'crash') -Hypothesis 'crash'
    $result = Invoke-KernelFixture $fixture -MaxIterations 1
    $result.Report.records[0].status | Should Be 'crash'
  }

  It 'stops at the no-progress ceiling' {
    $fixture = New-KernelFixture (Join-Path $TestDrive 'no-progress') -Hypothesis 'discard'
    $result = Invoke-KernelFixture $fixture -MaxIterations 5 -NoProgressRounds 2
    $result.Report.stop_reason | Should Be 'no-progress'
    $result.Report.iterations | Should Be 2
  }

  It 'stops at the maximum iteration ceiling' {
    $fixture = New-KernelFixture (Join-Path $TestDrive 'max-iterations') -Hypothesis 'improve'
    $result = Invoke-KernelFixture $fixture -MaxIterations 2 -NoProgressRounds 5
    $result.Report.stop_reason | Should Be 'max-iterations'
    $result.Report.iterations | Should Be 2
  }

  It 'stops at the wall-clock ceiling' {
    $fixture = New-KernelFixture (Join-Path $TestDrive 'wall-clock') -Hypothesis 'slow'
    $result = Invoke-KernelFixture $fixture -MaxIterations 5 -NoProgressRounds 5 -WallClockSeconds 1
    $result.Report.stop_reason | Should Be 'wall-clock'
    $result.Report.iterations | Should Be 1
  }

  It 'writes append-only keep/discard/crash JSONL with no Git operations' {
    $fixture = New-KernelFixture (Join-Path $TestDrive 'ledger') -Hypothesis 'discard'
    $result = Invoke-KernelFixture $fixture -MaxIterations 2
    $lines = @(Get-Content -LiteralPath $fixture.Ledger | Where-Object { $_ -match '\S' })
    $lines.Count | Should Be 2
    foreach ($line in $lines) { { $line | ConvertFrom-Json | Out-Null } | Should Not Throw }
    $result.Report.git_operations | Should Be 0
    $result.SourceDiff.Count | Should Be 0
  }
}
