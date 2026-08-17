[CmdletBinding()]
param(
  [ValidateSet('Check')]
  [string]$Mode = 'Check',
  [string]$VaultRoot = '__WINDOWS_WORKSPACE__',
  [string]$PolicyPath = '',
  [string]$InvariantsPath = '',
  [string]$SourceTiersPath = '',
  [string]$AuthorityPath = '',
  [string]$GraphPath = '',
  [string]$EvaluatorPath = '',
  [string]$BaselinePath = '',
  [string]$FixtureLockPath = '',
  [string]$RecoveryMarkerPath = '',
  [string]$AuthorizationEvidencePath = '',
  [string[]]$TargetPaths = @(),
  [ValidateSet('ReadOnly', 'Sandbox', 'Production')]
  [string]$WorkspaceMode = 'ReadOnly',
  [int]$MaxRecoveryAgeDays = 30,
  [switch]$Json
)

$ErrorActionPreference = 'Stop'
$findings = New-Object System.Collections.Generic.List[object]

function Add-Finding {
  param([string]$Id, [ValidateSet('PASS', 'WARN', 'FAIL')][string]$Level, [string]$Message, [string]$Evidence = '')
  $findings.Add([pscustomobject][ordered]@{
      id = $Id
      level = $Level
      message = $Message
      evidence = $Evidence
    })
}

function Resolve-InputPath {
  param([string]$Value, [string]$DefaultRelative)
  $candidate = if ($Value) { $Value } else { Join-Path $VaultRoot $DefaultRelative }
  return [System.IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($candidate))
}

function Read-JsonYamlContract {
  param([string]$Path, [string]$FindingId)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    Add-Finding $FindingId 'FAIL' 'Required contract is missing.' $Path
    return $null
  }
  try {
    $value = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    Add-Finding $FindingId 'PASS' 'Contract parsed as JSON-compatible YAML.' $Path
    return $value
  } catch {
    Add-Finding $FindingId 'FAIL' ('Contract parse failed: ' + $_.Exception.Message) $Path
    return $null
  }
}

function Test-RequiredProperties {
  param($Object, [string[]]$Names, [string]$Prefix)
  if ($null -eq $Object) { return }
  foreach ($name in $Names) {
    $property = $Object.PSObject.Properties[$name]
    $blank = ($null -eq $property) -or ($null -eq $property.Value) -or
      (($property.Value -is [string]) -and [string]::IsNullOrWhiteSpace([string]$property.Value))
    if ($blank) {
      Add-Finding ($Prefix + '-' + $name) 'FAIL' "Mandatory field '$name' is missing or blank."
    }
  }
}

function Read-SurfaceGraph {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    Add-Finding 'surface-graph' 'FAIL' 'Surface graph is missing.' $Path
    return $null
  }
  $python = Get-Command python -ErrorAction SilentlyContinue
  if (-not $python) {
    Add-Finding 'surface-graph-parser' 'FAIL' 'Python is unavailable; surface graph cannot be parsed safely.'
    return $null
  }
  try {
    $parser = "import json,sys,yaml; data=yaml.safe_load(open(sys.argv[1],encoding='utf-8')) or {}; print(json.dumps(data))"
    $jsonText = & $python.Source -c $parser $Path
    if ($LASTEXITCODE -ne 0 -or -not $jsonText) { throw 'Python YAML parser returned no valid document.' }
    $graph = $jsonText | ConvertFrom-Json
    Add-Finding 'surface-graph' 'PASS' 'Surface graph parsed without invoking its report-writing gate.' $Path
    return $graph
  } catch {
    Add-Finding 'surface-graph' 'FAIL' ('Surface graph parse failed: ' + $_.Exception.Message) $Path
    return $null
  }
}

function Test-FixtureLock {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    Add-Finding 'fixture-lock' 'FAIL' 'Frozen evaluator fixture lock is missing.' $Path
    return
  }
  $base = Split-Path -Parent $Path
  $checked = 0
  $bad = New-Object System.Collections.Generic.List[string]
  foreach ($line in (Get-Content -LiteralPath $Path)) {
    if ($line -match '^\s*#' -or $line -notmatch '\S') { continue }
    $parts = $line -split '\s+', 2
    if ($parts.Count -ne 2 -or $parts[1].Trim() -notmatch '^[a-fA-F0-9]{64}$') {
      $bad.Add('malformed:' + $line.Trim())
      continue
    }
    $fixture = Join-Path $base $parts[0]
    if (-not (Test-Path -LiteralPath $fixture -PathType Leaf)) {
      $bad.Add('missing:' + $parts[0])
      continue
    }
    $actual = (Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash
    if ($actual -ne $parts[1].Trim()) { $bad.Add('hash-drift:' + $parts[0]); continue }
    $checked++
  }
  if ($checked -eq 0 -or $bad.Count -gt 0) {
    Add-Finding 'fixture-lock' 'FAIL' ("Frozen fixtures are not trustworthy; checked=$checked problems=$($bad.Count).") ($bad -join ';')
  } else {
    Add-Finding 'fixture-lock' 'PASS' "Frozen fixture hashes verified; checked=$checked." $Path
  }
}

function Test-RecoveryEvidence {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    Add-Finding 'recovery-evidence' 'FAIL' 'Restore-verification evidence is missing.' $Path
    return
  }
  $content = Get-Content -LiteralPath $Path -Raw
  if ($content -notmatch '^GREEN\s+(?<recorded_at>\S+)') {
    Add-Finding 'recovery-evidence' 'FAIL' 'Recovery marker is not GREEN with a recorded timestamp.' $Path
    return
  }
  try {
    $recordedAt = [DateTimeOffset]::Parse($Matches.recorded_at, [Globalization.CultureInfo]::InvariantCulture)
  } catch {
    Add-Finding 'recovery-evidence' 'FAIL' 'Recovery marker timestamp is invalid.' $Path
    return
  }
  $age = ([DateTimeOffset]::Now - $recordedAt).TotalDays
  if ($age -lt -0.01) {
    Add-Finding 'recovery-evidence' 'FAIL' 'Recovery marker timestamp is in the future.' $Path
  } elseif ($age -gt $MaxRecoveryAgeDays) {
    Add-Finding 'recovery-evidence' 'FAIL' ("Recovery evidence is stale: {0:N1} days > $MaxRecoveryAgeDays days." -f $age) $Path
  } else {
    Add-Finding 'recovery-evidence' 'PASS' ("Restore evidence is GREEN and {0:N1} days old." -f $age) $Path
  }
}

function Get-CanonicalPath {
  param([string]$Path)
  try { return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/').ToLowerInvariant() } catch { return '' }
}

function Get-AttestedSandboxRoot {
  param([string]$TargetPath)
  $target = Get-CanonicalPath $TargetPath
  if (-not $target) { return '' }
  $cursor = $target
  while ($cursor) {
    $markerPath = Join-Path $cursor '.autoexperiment-sandbox.json'
    if (Test-Path -LiteralPath $markerPath -PathType Leaf) {
      try {
        $marker = Get-Content -Raw -LiteralPath $markerPath | ConvertFrom-Json
        $writableRoot = Get-CanonicalPath ([string]$marker.writable_root)
        if ($writableRoot -eq $cursor -and
            $marker.production_mutation_allowed -eq $false -and
            ($target -eq $cursor -or $target.StartsWith($cursor + '\'))) {
          return $cursor
        }
      } catch { return '' }
    }
    if ($cursor -match '^[a-z]:$') { break }
    $volumeRoot = Get-CanonicalPath ([System.IO.Path]::GetPathRoot($cursor))
    if ($cursor -eq $volumeRoot) { break }
    try { $parent = [System.IO.Directory]::GetParent($cursor) } catch { $parent = $null }
    if (-not $parent) { break }
    $next = Get-CanonicalPath $parent.FullName
    if (-not $next -or $next -eq $cursor) { break }
    $cursor = $next
  }
  return ''
}

$PolicyPath = Resolve-InputPath $PolicyPath '.planning\autoexperiment-control-plane\RESEARCH-POLICY.md'
$InvariantsPath = Resolve-InputPath $InvariantsPath '.planning\autoexperiment-control-plane\contracts\protected-invariants.yaml'
$SourceTiersPath = Resolve-InputPath $SourceTiersPath '.planning\autoexperiment-control-plane\contracts\source-tiers.yaml'
$AuthorityPath = Resolve-InputPath $AuthorityPath '.claude\AUTHORITY-CONSTITUTION.md'
$GraphPath = Resolve-InputPath $GraphPath 'SURFACE-GRAPH.yaml'
$EvaluatorPath = Resolve-InputPath $EvaluatorPath '.planning\hermes-master-plan\eval\eval-run.ps1'
$BaselinePath = Resolve-InputPath $BaselinePath '.planning\hermes-master-plan\eval\baseline.json'
$FixtureLockPath = Resolve-InputPath $FixtureLockPath '.planning\hermes-master-plan\eval\fixtures.lock'
$RecoveryMarkerPath = Resolve-InputPath $RecoveryMarkerPath 'output\ops\restore-verify-last-green.txt'

if (Test-Path -LiteralPath $PolicyPath -PathType Leaf) {
  $policyText = Get-Content -LiteralPath $PolicyPath -Raw
  $requiredPolicyTerms = @('authority', 'frozen evaluator', 'copy-on-write sandbox', 'community', 'subscription', 'GSD', 'Git history', 'source handle')
  $missingTerms = @($requiredPolicyTerms | Where-Object { $policyText -notmatch [regex]::Escape($_) })
  if ($missingTerms.Count -eq 0) {
    Add-Finding 'research-policy' 'PASS' 'Research constitution contains every mandatory policy domain.' $PolicyPath
  } else {
    Add-Finding 'research-policy' 'FAIL' ('Research constitution omits: ' + ($missingTerms -join ', ')) $PolicyPath
  }
} else {
  Add-Finding 'research-policy' 'FAIL' 'Research constitution is missing.' $PolicyPath
}

$invariants = Read-JsonYamlContract $InvariantsPath 'invariants-contract'
$sourceTiers = Read-JsonYamlContract $SourceTiersPath 'source-tiers-contract'
Test-RequiredProperties $invariants @('schema_version', 'policy_id', 'authority_path', 'required_invariant_fields', 'protected_roots', 'invariants') 'invariants'
Test-RequiredProperties $sourceTiers @('schema_version', 'required_tier_fields', 'required_handle_fields', 'handles_only_in_working_context', 'raw_content_store', 'tiers') 'sources'

if ($invariants) {
  $ids = @{}
  foreach ($item in @($invariants.invariants)) {
    Test-RequiredProperties $item @($invariants.required_invariant_fields) ('invariant-' + [string]$item.id)
    if ($ids.ContainsKey([string]$item.id)) { Add-Finding 'invariants-duplicate' 'FAIL' ('Duplicate invariant: ' + $item.id) }
    else { $ids[[string]$item.id] = $true }
  }
  $mandatory = @('authority-precedence', 'working-behavior-preserved', 'immutable-evaluator', 'sandbox-only-mutation', 'recovery-not-weakened', 'community-data-authorization', 'external-content-quarantine', 'subscription-only-routing', 'gsd-only-promotion', 'no-automatic-history-mutation', 'no-global-skill-exposure-drift')
  $missing = @($mandatory | Where-Object { -not $ids.ContainsKey($_) })
  if ($missing.Count) { Add-Finding 'invariants-mandatory-set' 'FAIL' ('Missing mandatory invariants: ' + ($missing -join ', ')) }
  else { Add-Finding 'invariants-mandatory-set' 'PASS' 'All mandatory protected invariants are present.' }
}

if ($sourceTiers) {
  $priorities = @{}
  foreach ($tier in @($sourceTiers.tiers)) {
    Test-RequiredProperties $tier @($sourceTiers.required_tier_fields) ('source-' + [string]$tier.id)
    if ($priorities.ContainsKey([string]$tier.priority)) { Add-Finding 'source-priority' 'FAIL' ('Duplicate source priority: ' + $tier.priority) }
    else { $priorities[[string]$tier.priority] = $true }
  }
  $community = @($sourceTiers.tiers | Where-Object { $_.id -eq 'T4-community-indigenous' }) | Select-Object -First 1
  if (-not $community -or @($community.allowed_uses).Count -ne 0 -or $community.quarantine -ne 'locked' -or $community.authorization -notmatch 'community_authorization') {
    Add-Finding 'community-source-lock' 'FAIL' 'Community/Indigenous tier is not fail-closed.'
  } else {
    Add-Finding 'community-source-lock' 'PASS' 'Community/Indigenous sources are locked pending community authorization.'
  }
  if (@($sourceTiers.required_handle_fields).Count -lt 7) { Add-Finding 'source-handles' 'FAIL' 'Source-handle schema is incomplete.' }
  else { Add-Finding 'source-handles' 'PASS' 'Source handles require provenance, freshness, hash, privacy, and quarantine state.' }
}

if (-not (Test-Path -LiteralPath $AuthorityPath -PathType Leaf)) {
  Add-Finding 'authority' 'FAIL' 'Canonical authority file is missing.' $AuthorityPath
} else {
  $authorityText = Get-Content -LiteralPath $AuthorityPath -Raw
  if ($authorityText -match 'Authority' -and $authorityText -match 'evidence') {
    Add-Finding 'authority' 'PASS' 'Canonical authority source is present and recognizable.' $AuthorityPath
  } else {
    Add-Finding 'authority' 'FAIL' 'Authority source does not contain its mandatory authority/evidence contract.' $AuthorityPath
  }
}

$graph = Read-SurfaceGraph $GraphPath
if ($graph) {
  $requiredIds = @('eval-golden-runner', 'eval-baseline', 'eval-fixtures', 'backup-freshness')
  $graphIds = @(@($graph.surfaces) | ForEach-Object { [string]$_.id })
  $missingGraphIds = @($requiredIds | Where-Object { $_ -notin $graphIds })
  if ($missingGraphIds.Count) { Add-Finding 'surface-graph-critical' 'FAIL' ('Critical surfaces are unregistered: ' + ($missingGraphIds -join ', ')) }
  else { Add-Finding 'surface-graph-critical' 'PASS' 'Evaluator and recovery surfaces are registered.' }
}

foreach ($pair in @(@('evaluator', $EvaluatorPath), @('baseline', $BaselinePath))) {
  if (Test-Path -LiteralPath $pair[1] -PathType Leaf) { Add-Finding $pair[0] 'PASS' ($pair[0] + ' is available.') $pair[1] }
  else { Add-Finding $pair[0] 'FAIL' ($pair[0] + ' is missing.') $pair[1] }
}
if (Test-Path -LiteralPath $BaselinePath -PathType Leaf) {
  try { $null = Get-Content -LiteralPath $BaselinePath -Raw | ConvertFrom-Json }
  catch { Add-Finding 'baseline-parse' 'FAIL' ('Baseline JSON is invalid: ' + $_.Exception.Message) $BaselinePath }
}
Test-FixtureLock $FixtureLockPath
Test-RecoveryEvidence $RecoveryMarkerPath

if ($AuthorizationEvidencePath) {
  $authPath = [System.IO.Path]::GetFullPath($AuthorizationEvidencePath)
  if (-not (Test-Path -LiteralPath $authPath -PathType Leaf)) {
    Add-Finding 'authorization' 'FAIL' 'Authorization evidence is missing.' $authPath
  } elseif ((Get-Content -LiteralPath $authPath -Raw) -match 'Logged in using ChatGPT') {
    Add-Finding 'authorization' 'PASS' 'Subscription authorization evidence is valid.' $authPath
  } else {
    Add-Finding 'authorization' 'FAIL' 'Authorization evidence does not prove ChatGPT subscription login.' $authPath
  }
} else {
  try {
    # Windows PowerShell promotes native stderr to an ErrorRecord when the script-wide
    # preference is Stop. `codex login status` writes its success line there, so relax
    # error handling only for this read-only probe and still require exit code zero.
    $previousErrorPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $authOutput = & codex login status 2>&1 | Out-String
    $authExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorPreference
    if ($authExitCode -eq 0 -and $authOutput -match 'Logged in using ChatGPT') { Add-Finding 'authorization' 'PASS' 'Codex reports ChatGPT subscription login.' }
    else { Add-Finding 'authorization' 'FAIL' 'Codex subscription authorization could not be proved.' }
  } catch {
    if ($previousErrorPreference) { $ErrorActionPreference = $previousErrorPreference }
    Add-Finding 'authorization' 'FAIL' ('Codex authorization probe failed: ' + $_.Exception.Message)
  }
}
if ($env:OPENAI_API_KEY -or $env:OPENROUTER_API_KEY) {
  Add-Finding 'metered-route-guard' 'FAIL' 'A prohibited metered-route credential is present in the process environment.'
} else {
  Add-Finding 'metered-route-guard' 'PASS' 'No prohibited metered-route credential is present in the process environment.'
}

$resolvedTargets = @($TargetPaths | Where-Object { $_ } | ForEach-Object { Get-CanonicalPath $_ } | Where-Object { $_ })
if ($resolvedTargets.Count -eq 0) {
  Add-Finding 'targets' 'WARN' 'No candidate targets were declared; environment eligibility only was checked.'
} else {
  $runtimeProtectedRoots = if ($env:USERPROFILE) {
    @('.codex', '.claude', '.hermes') | ForEach-Object {
      Get-CanonicalPath (Join-Path $env:USERPROFILE $_)
    }
  } else { @() }
  $vaultProtectedRoots = @(
    Get-CanonicalPath (Join-Path $VaultRoot '.codex')
  )
  $protectedRoots = @(
    @($invariants.protected_roots) + @($runtimeProtectedRoots) + @($vaultProtectedRoots) |
      ForEach-Object { Get-CanonicalPath $_ } |
      Where-Object { $_ } |
      Select-Object -Unique
  )
  foreach ($target in $resolvedTargets) {
    $overlap = @($protectedRoots | Where-Object { $target -eq $_ -or $target.StartsWith($_ + '\') -or $_.StartsWith($target + '\') })
    $attestedSandbox = if ($WorkspaceMode -eq 'Sandbox') { Get-AttestedSandboxRoot $target } else { '' }
    if ($WorkspaceMode -eq 'Sandbox' -and -not $attestedSandbox) {
      Add-Finding 'sandbox-attestation' 'FAIL' 'Sandbox targets must be inside a marker-attested writable root.' $target
    } elseif ($WorkspaceMode -eq 'Sandbox') {
      Add-Finding 'sandbox-attestation' 'PASS' 'Target is inside the marker-attested writable root.' $attestedSandbox
    }
    if ($overlap.Count) {
      Add-Finding 'protected-target-overlap' 'FAIL' 'Target overlaps a protected production root.' $target
    } else {
      Add-Finding 'target-scope' 'PASS' 'Target does not overlap a protected production root.' $target
    }
  }
  $previousErrorPreference = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  $gitRoot = & git -C $VaultRoot rev-parse --show-toplevel 2>$null
  $gitExitCode = $LASTEXITCODE
  $ErrorActionPreference = $previousErrorPreference
  if ($gitExitCode -ne 0 -or -not $gitRoot) {
    if ($WorkspaceMode -eq 'Sandbox') {
      Add-Finding 'dirty-targets' 'WARN' 'Vault is not a Git worktree; sandbox-path isolation is the applicable non-overlap guard.'
    } else {
      Add-Finding 'dirty-targets' 'FAIL' 'Target overlap with existing work cannot be determined because the vault is not a valid Git worktree.'
    }
  } else {
    $dirty = @(& git -C $VaultRoot status --porcelain -- @($TargetPaths) 2>$null)
    if ($dirty.Count) { Add-Finding 'dirty-targets' 'FAIL' 'One or more declared targets contain existing uncommitted work.' ($dirty -join '; ') }
    else { Add-Finding 'dirty-targets' 'PASS' 'Declared targets do not overlap uncommitted work.' }
  }
}

$failCount = @($findings | Where-Object { $_.level -eq 'FAIL' }).Count
$warnCount = @($findings | Where-Object { $_.level -eq 'WARN' }).Count
$status = if ($failCount) { 'FAIL' } elseif ($warnCount) { 'WARN' } else { 'PASS' }
$report = [pscustomobject][ordered]@{
  schema_version = '1.0.0'
  mode = $Mode
  status = $status
  pass_count = @($findings | Where-Object { $_.level -eq 'PASS' }).Count
  warn_count = $warnCount
  fail_count = $failCount
  persistent_changes = $false
  generated_at = (Get-Date -Format o)
  findings = $findings.ToArray()
}

if ($Json) { $report | ConvertTo-Json -Depth 8 }
else {
  Write-Output ("AUTOEXPERIMENT PREFLIGHT: {0} | pass={1} warn={2} fail={3}" -f $status, $report.pass_count, $warnCount, $failCount)
  foreach ($finding in $findings) { Write-Output ("[{0}] {1}: {2}" -f $finding.level, $finding.id, $finding.message) }
}
if ($failCount) { exit 1 }
exit 0
