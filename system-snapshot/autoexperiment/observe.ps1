[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$CheckpointPath,
  [string]$OutputRoot='__WINDOWS_WORKSPACE__\AutoExperiment-Reports'
)
$ErrorActionPreference='Stop'
$root='__WINDOWS_WORKSPACE__'
$base=Join-Path $root 'scripts\autoexperiment'
$scheduleJson=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $base 'scheduler.ps1') -CheckpointPath $CheckpointPath -Json
if($LASTEXITCODE -ne 0){throw 'AutoExperiment scheduler rejected the checkpoint.'}
$schedule=($scheduleJson -join "`n")|ConvertFrom-Json
if($schedule.status -ne 'sandbox-audit-ready' -or $schedule.mutation_policy -ne 'sandbox-only'){throw "Dispatch blocked: $($schedule.status)"}

$cadence=[string]$schedule.kind
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
$reportDir=Join-Path $OutputRoot $cadence
New-Item -ItemType Directory -Force -Path $reportDir|Out-Null
$reportPath=Join-Path $reportDir ("{0}-autoexperiment-{1}.md" -f $cadence,$stamp)
$evidencePath=Join-Path $reportDir ("{0}-evidence-{1}.json" -f $cadence,$stamp)
$auditSandbox=Join-Path $reportDir ("{0}-sandbox-{1}" -f $cadence,$stamp)
New-Item -ItemType Directory -Force -Path $auditSandbox|Out-Null
[pscustomobject][ordered]@{
  schema_version='1.0.0';cadence=$cadence;created_at=(Get-Date -Format o)
  production_root=$root;writable_root=$auditSandbox;production_mutation_allowed=$false
}|ConvertTo-Json -Depth 4|Set-Content -LiteralPath (Join-Path $auditSandbox '.autoexperiment-sandbox.json') -Encoding UTF8
$candidateApproval=if($cadence -eq 'daily'){'automatic-gsd-package'}else{'manual-confirmation-required'}
$productionApproval='manual-confirmation-required'

$preflightOutput=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $base 'preflight.ps1') -Mode Check -WorkspaceMode Sandbox -TargetPaths $auditSandbox -Json 2>&1
$preflightExit=$LASTEXITCODE
$preflightText=$preflightOutput -join "`n"
try{$preflight=$preflightText|ConvertFrom-Json}catch{$preflight=[pscustomobject]@{status='ERROR';raw=$preflightText}}
$tasks=@()
foreach($name in @('WorkspaceKit-Daily-Reindex-Backup','WorkspaceKit-Projects-Weekly-Backup','WorkspaceKit-Projects-Monthly-Backup')){
  $task=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if($task){$info=Get-ScheduledTaskInfo -InputObject $task;$tasks+=[pscustomobject]@{name=$name;state=[string]$task.State;last_run=$info.LastRunTime;last_result=$info.LastTaskResult;next_run=$info.NextRunTime}}
  else{$tasks+=[pscustomobject]@{name=$name;state='MISSING'}}
}
$evidence=[pscustomobject][ordered]@{
  schema_version='1.0.0';cadence=$cadence;generated_at=(Get-Date -Format o)
  checkpoint=$schedule;preflight=$preflight;preflight_exit=$preflightExit;backup_tasks=$tasks
  audit_sandbox=$auditSandbox;candidate_package_approval=$candidateApproval;production_implementation_approval=$productionApproval
  production_mutation=$false;promotion_attempted=$false;git_history_mutation=$false
}
$evidence|ConvertTo-Json -Depth 10|Set-Content -LiteralPath $evidencePath -Encoding UTF8

$scopeText=switch($cadence){
  'daily'{'Minor edge cases and fresh degradations only. Inspect canonical indexes, contracts, recent operational evidence, and backup-covered non-Projects surfaces. Do not perform a broad vault crawl.'}
  'weekly'{'Broader cross-layer analysis across all nine portfolios and shared Codex, Claude Code, and Hermes harness behavior. Use canonical indexes before opening source files.'}
  'monthly'{'System-wide strategic analysis across all nine portfolios, including current agentic-ecosystem research when web tools are available. Prefer primary sources and distinguish external research from local evidence.'}
}
$prompt=@"
You are the sandbox-audit WorkspaceKit AutoExperiment observer for the $cadence cadence.
Read __WINDOWS_WORKSPACE__\.claude\MASTER-INDEX.md first and route narrowly. Read the checkpoint and quantitative evidence at:
- $CheckpointPath
- $evidencePath

The production tree at __WINDOWS_WORKSPACE__ is READ-ONLY for this run. Your only writable workspace is the marker-attested sandbox:
- $auditSandbox

Audit for improvements, and when a finding has a trustworthy deterministic verifier, copy only the minimum required source material into the sandbox, make the candidate change there, and test it there. Preserve source-relative paths under the sandbox and write any proposed patch/evidence beneath the sandbox. Never run a verifier that writes to production. If a safe sandbox verifier is unavailable, report the finding as untested; do not simulate evidence.

$scopeText

Evaluate every one of these portfolios before allocating extra depth: knowledge/retrieval; agent/prompt quality; engineering delivery; reliability/recovery; safety/privacy/governance; learning/memory; product/mission outcomes; efficiency/operational cost; Markdown/token efficiency. Within each portfolio use a bounded mini-loop: clarity gate -> reproduce/measure -> verifier check -> hypothesis -> read-only falsification -> independent critique -> exit clarity. Do not claim an improvement without evidence.

Rank findings after equal portfolio coverage. Priority order: protected-invariant failure, verified degradation, coverage debt/age, downstream leverage, recurring user impact, verifier readiness, benefit-to-cost ratio, then lower duration. For every recommendation give evidence handles, expected benefit, time/rate-limit/complexity costs, regression risk, reversibility, confidence, and whether it is worth implementing.

Approval boundary: candidate-package approval is '$candidateApproval'. For daily only, a low-risk, single-surface, reversible candidate that passes deterministic verification and every protected invariant is automatically approved for creation of a GSD-ready candidate package inside the sandbox. Weekly and monthly candidate packages remain pending explicit manual confirmation. For every cadence, production implementation is '$productionApproval': do not apply a candidate to __WINDOWS_WORKSPACE__ or invoke GSD execution without explicit human confirmation.

Hard boundary: sandbox-only mutation. Do not edit production files, invoke promotion.ps1 against production, change live prompts/skills/configuration, commit, merge, push, send messages, register tasks, or implement recommendations in production. Output one self-contained Markdown report. End with a ranked implementation-candidate table including sandbox evidence paths, candidate-package approval, and production-implementation approval, followed by a clear 'No production changes made' statement.
"@

# The Windows Store app's codex.exe is not executable from this scheduled
# PowerShell context. Use the installed Node CLI shim, which shares the same
# ChatGPT subscription authentication and works both interactively and in tasks.
# Keep that shim current: model routes can require a newer CLI than the one
# installed when this task was registered.
$codex=(Get-Command codex.cmd -ErrorAction Stop).Source
$npm=(Get-Command npm.cmd -ErrorAction Stop).Source
$installedVersionText=((& $codex --version 2>&1) -join "`n")
if($installedVersionText -notmatch '(\d+\.\d+\.\d+)'){throw "AutoExperiment cannot determine the installed Codex CLI version: $installedVersionText"}
$installedVersion=[version]$Matches[1]
$publishedVersionText=((& $npm view '@openai/codex' version 2>&1) -join "`n").Trim()
if($publishedVersionText -notmatch '^(\d+\.\d+\.\d+)$'){throw "AutoExperiment cannot determine the published Codex CLI version: $publishedVersionText"}
$publishedVersion=[version]$Matches[1]
$codexSelfHeal=[ordered]@{attempted=$false;installed_before=$installedVersion.ToString();published=$publishedVersion.ToString();installed_after=$installedVersion.ToString();status='current'}
if($installedVersion -lt $publishedVersion){
  $codexSelfHeal.attempted=$true
  Write-Host "AUTOEXPERIMENT SELF-HEAL: upgrading Codex CLI $installedVersion -> $publishedVersion"
  & $npm install -g ("@openai/codex@{0}" -f $publishedVersion) 2>&1|ForEach-Object{Write-Host $_}
  if($LASTEXITCODE -ne 0){throw "AutoExperiment Codex CLI self-heal failed with exit code $LASTEXITCODE."}
  $installedVersionText=((& $codex --version 2>&1) -join "`n")
  if($installedVersionText -notmatch '(\d+\.\d+\.\d+)'){throw "AutoExperiment cannot verify Codex CLI after self-heal: $installedVersionText"}
  $installedVersion=[version]$Matches[1]
  if($installedVersion -lt $publishedVersion){throw "AutoExperiment Codex CLI self-heal did not reach $publishedVersion (now $installedVersion)."}
  $codexSelfHeal.installed_after=$installedVersion.ToString()
  $codexSelfHeal.status='upgraded'
}
$oldOpenAI=$env:OPENAI_API_KEY;$oldOpenRouter=$env:OPENROUTER_API_KEY
Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:OPENROUTER_API_KEY -ErrorAction SilentlyContinue
try{
  $prompt|& $codex exec -s workspace-write --ephemeral --skip-git-repo-check -C $auditSandbox -o $reportPath -
  $codexExit=$LASTEXITCODE
}finally{
  if($null -ne $oldOpenAI){$env:OPENAI_API_KEY=$oldOpenAI}
  if($null -ne $oldOpenRouter){$env:OPENROUTER_API_KEY=$oldOpenRouter}
}
if($codexExit -ne 0 -or -not(Test-Path -LiteralPath $reportPath -PathType Leaf)){
  @(
    "# AutoExperiment $cadence observation failed",
    '',"Generated: $(Get-Date -Format o)","Codex exit: $codexExit",'','The verified backup checkpoint was accepted, but the read-only research agent did not produce a report.','',"Quantitative evidence: $evidencePath",'','No production changes made.'
  )|Set-Content -LiteralPath $reportPath -Encoding UTF8
  throw "Automatic observation failed; diagnostic report written to $reportPath"
}
$fence=[string]::new([char]96,3)
Add-Content -LiteralPath $reportPath -Encoding UTF8 -Value ("`n`n## Runner health`n`n"+$fence+"json`n"+($codexSelfHeal|ConvertTo-Json -Compress)+"`n"+$fence)
Write-Host "AUTOEXPERIMENT REPORT READY cadence=$cadence path=$reportPath evidence=$evidencePath"
