$script:RepoRoot = '__WINDOWS_WORKSPACE__'
$script:Preflight = Join-Path $script:RepoRoot 'scripts\autoexperiment\preflight.ps1'
$script:Contracts = Join-Path $script:RepoRoot '.planning\autoexperiment-control-plane\contracts'

function New-PreflightFixture {
  param([string]$Root)
  New-Item -ItemType Directory -Force -Path (Join-Path $Root '.planning\hermes-master-plan\eval') | Out-Null
  New-Item -ItemType Directory -Force -Path (Join-Path $Root 'output\ops') | Out-Null
  New-Item -ItemType Directory -Force -Path (Join-Path $Root '.claude') | Out-Null

  Set-Content -LiteralPath (Join-Path $Root '.planning\autoexperiment-control-plane-policy.md') -Encoding UTF8 -Value @'
authority frozen evaluator copy-on-write sandbox community subscription GSD Git history source handle
'@
  Set-Content -LiteralPath (Join-Path $Root '.claude\AUTHORITY-CONSTITUTION.md') -Encoding UTF8 -Value 'Authority first; evidence outranks memory.'
  Set-Content -LiteralPath (Join-Path $Root '.planning\hermes-master-plan\eval\eval-run.ps1') -Encoding UTF8 -Value '# frozen evaluator'
  Set-Content -LiteralPath (Join-Path $Root '.planning\hermes-master-plan\eval\baseline.json') -Encoding UTF8 -Value '{"class_means":{"safe":1}}'
  Set-Content -LiteralPath (Join-Path $Root '.planning\hermes-master-plan\eval\fixture.txt') -Encoding UTF8 -NoNewline -Value 'fixture'
  $hash = (Get-FileHash -LiteralPath (Join-Path $Root '.planning\hermes-master-plan\eval\fixture.txt') -Algorithm SHA256).Hash
  Set-Content -LiteralPath (Join-Path $Root '.planning\hermes-master-plan\eval\fixtures.lock') -Encoding UTF8 -Value ("fixture.txt  " + $hash)
  Set-Content -LiteralPath (Join-Path $Root 'output\ops\restore-verify-last-green.txt') -Encoding UTF8 -Value ('GREEN {0} backup=fixture' -f (Get-Date).ToUniversalTime().ToString('o'))
  Set-Content -LiteralPath (Join-Path $Root 'auth.txt') -Encoding UTF8 -Value 'Logged in using ChatGPT'
  Set-Content -LiteralPath (Join-Path $Root 'SURFACE-GRAPH.yaml') -Encoding UTF8 -Value @'
schema:
  required_fields: [id]
surfaces:
  - id: eval-golden-runner
  - id: eval-baseline
  - id: eval-fixtures
  - id: backup-freshness
'@
}

function Invoke-FixturePreflight {
  param(
    [string]$Root,
    [string]$Authority = '',
    [string]$Recovery = '',
    [string]$Evaluator = '',
    [string]$Authorization = '',
    [string]$Invariants = '',
    [string]$SourceTiers = ''
  )
  if (-not $Authority) { $Authority = Join-Path $Root '.claude\AUTHORITY-CONSTITUTION.md' }
  if (-not $Recovery) { $Recovery = Join-Path $Root 'output\ops\restore-verify-last-green.txt' }
  if (-not $Evaluator) { $Evaluator = Join-Path $Root '.planning\hermes-master-plan\eval\eval-run.ps1' }
  if (-not $Authorization) { $Authorization = Join-Path $Root 'auth.txt' }
  if (-not $Invariants) { $Invariants = Join-Path $script:Contracts 'protected-invariants.yaml' }
  if (-not $SourceTiers) { $SourceTiers = Join-Path $script:Contracts 'source-tiers.yaml' }
  $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Preflight `
    -VaultRoot $Root `
    -PolicyPath (Join-Path $Root '.planning\autoexperiment-control-plane-policy.md') `
    -InvariantsPath $Invariants `
    -SourceTiersPath $SourceTiers `
    -AuthorityPath $Authority `
    -GraphPath (Join-Path $Root 'SURFACE-GRAPH.yaml') `
    -EvaluatorPath $Evaluator `
    -BaselinePath (Join-Path $Root '.planning\hermes-master-plan\eval\baseline.json') `
    -FixtureLockPath (Join-Path $Root '.planning\hermes-master-plan\eval\fixtures.lock') `
    -RecoveryMarkerPath $Recovery `
    -AuthorizationEvidencePath $Authorization `
    -Json
  return ($output -join "`n") | ConvertFrom-Json
}

Describe 'AutoExperiment policy contracts' -Tag Policy {
  It 'parses both JSON-compatible YAML contracts' {
    { Get-Content -LiteralPath (Join-Path $script:Contracts 'protected-invariants.yaml') -Raw | ConvertFrom-Json | Out-Null } | Should Not Throw
    { Get-Content -LiteralPath (Join-Path $script:Contracts 'source-tiers.yaml') -Raw | ConvertFrom-Json | Out-Null } | Should Not Throw
  }

  It 'has no blank mandatory invariant fields' {
    $contract = Get-Content -LiteralPath (Join-Path $script:Contracts 'protected-invariants.yaml') -Raw | ConvertFrom-Json
    foreach ($item in @($contract.invariants)) {
      foreach ($field in @($contract.required_invariant_fields)) {
        ($item.PSObject.Properties.Name -contains $field) | Should Be $true
        [string]::IsNullOrWhiteSpace([string]$item.$field) | Should Be $false
      }
    }
  }

  It 'locks community and Indigenous sources by default' {
    $contract = Get-Content -LiteralPath (Join-Path $script:Contracts 'source-tiers.yaml') -Raw | ConvertFrom-Json
    $community = @($contract.tiers | Where-Object { $_.id -eq 'T4-community-indigenous' })[0]
    @($community.allowed_uses).Count | Should Be 0
    $community.quarantine | Should Be 'locked'
    $community.authorization | Should Match 'community_authorization'
  }

  It 'fails closed when a mandatory invariant field is blank' {
    $root = Join-Path $TestDrive 'blank-invariant'
    New-PreflightFixture $root
    $contract = Get-Content -LiteralPath (Join-Path $script:Contracts 'protected-invariants.yaml') -Raw | ConvertFrom-Json
    $contract.invariants[0].rule = ''
    $bad = Join-Path $root 'bad-invariants.yaml'
    $contract | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $bad -Encoding UTF8
    $report = Invoke-FixturePreflight -Root $root -Invariants $bad
    $report.status | Should Be 'FAIL'
    @($report.findings | Where-Object { $_.id -like 'invariant-*-rule' -and $_.level -eq 'FAIL' }).Count | Should BeGreaterThan 0
  }

  It 'fails closed when the source-handle schema is incomplete' {
    $root = Join-Path $TestDrive 'bad-handles'
    New-PreflightFixture $root
    $contract = Get-Content -LiteralPath (Join-Path $script:Contracts 'source-tiers.yaml') -Raw | ConvertFrom-Json
    $contract.required_handle_fields = @('source_id')
    $bad = Join-Path $root 'bad-source-tiers.yaml'
    $contract | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $bad -Encoding UTF8
    $report = Invoke-FixturePreflight -Root $root -SourceTiers $bad
    $report.status | Should Be 'FAIL'
    @($report.findings | Where-Object { $_.id -eq 'source-handles' -and $_.level -eq 'FAIL' }).Count | Should Be 1
  }
}

Describe 'AutoExperiment read-only preflight' {
  It 'passes a valid environment with only the undeclared-target warning' {
    $root = Join-Path $TestDrive 'valid'
    New-PreflightFixture $root
    $report = Invoke-FixturePreflight -Root $root
    $report.status | Should Be 'WARN'
    $report.fail_count | Should Be 0
    $report.persistent_changes | Should Be $false
  }

  It 'does not modify any fixture file' {
    $root = Join-Path $TestDrive 'read-only'
    New-PreflightFixture $root
    $before = Get-ChildItem -LiteralPath $root -Recurse -File | ForEach-Object { $_.FullName + '=' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
    $null = Invoke-FixturePreflight -Root $root
    $after = Get-ChildItem -LiteralPath $root -Recurse -File | ForEach-Object { $_.FullName + '=' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
    (Compare-Object $before $after).Count | Should Be 0
  }

  It 'fails closed when authority is missing' {
    $root = Join-Path $TestDrive 'missing-authority'; New-PreflightFixture $root
    $report = Invoke-FixturePreflight -Root $root -Authority (Join-Path $root 'missing-authority.md')
    $report.status | Should Be 'FAIL'
    @($report.findings | Where-Object { $_.id -eq 'authority' -and $_.level -eq 'FAIL' }).Count | Should Be 1
  }

  It 'fails closed when recovery evidence is missing' {
    $root = Join-Path $TestDrive 'missing-recovery'; New-PreflightFixture $root
    $report = Invoke-FixturePreflight -Root $root -Recovery (Join-Path $root 'missing-recovery.txt')
    $report.status | Should Be 'FAIL'
    @($report.findings | Where-Object { $_.id -eq 'recovery-evidence' -and $_.level -eq 'FAIL' }).Count | Should Be 1
  }

  It 'fails closed when the evaluator is missing' {
    $root = Join-Path $TestDrive 'missing-evaluator'; New-PreflightFixture $root
    $report = Invoke-FixturePreflight -Root $root -Evaluator (Join-Path $root 'missing-evaluator.ps1')
    $report.status | Should Be 'FAIL'
    @($report.findings | Where-Object { $_.id -eq 'evaluator' -and $_.level -eq 'FAIL' }).Count | Should Be 1
  }

  It 'fails closed when subscription authorization is missing' {
    $root = Join-Path $TestDrive 'missing-auth'; New-PreflightFixture $root
    $report = Invoke-FixturePreflight -Root $root -Authorization (Join-Path $root 'missing-auth.txt')
    $report.status | Should Be 'FAIL'
    @($report.findings | Where-Object { $_.id -eq 'authorization' -and $_.level -eq 'FAIL' }).Count | Should Be 1
  }
}
