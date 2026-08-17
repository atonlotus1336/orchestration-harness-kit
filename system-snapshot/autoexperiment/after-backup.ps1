[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][ValidateSet('daily','weekly','monthly')][string]$Cadence,
  [switch]$TrustedBackupChain
)
$ErrorActionPreference='Stop'
$root='__WINDOWS_WORKSPACE__'
$stateRoot=Join-Path $root '.planning\autoexperiment-control-plane\state'
$taskName=switch($Cadence){'daily'{'WorkspaceKit-Daily-Reindex-Backup'}'weekly'{'WorkspaceKit-Projects-Weekly-Backup'}'monthly'{'WorkspaceKit-Projects-Monthly-Backup'}}
$task=Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
$info=Get-ScheduledTaskInfo -InputObject $task
if(-not $TrustedBackupChain){
  if($info.LastTaskResult -ne 0){throw "Backup task $taskName did not finish green (result=$($info.LastTaskResult))."}
  if(((Get-Date)-$info.LastRunTime).TotalHours -gt 3){throw "Backup completion event is stale for $taskName."}
}

if($Cadence -eq 'monthly'){
  $latest=Get-ChildItem -LiteralPath (Join-Path $root 'output\ops') -Filter 'projects-backup-*.log' -File|Sort-Object LastWriteTime -Descending|Select-Object -First 1
  $skip=Get-ChildItem -LiteralPath (Join-Path $root 'output\ops') -Filter 'projects-backup-skipped-*.log' -File|Sort-Object LastWriteTime -Descending|Select-Object -First 1
  if($skip -and $skip.LastWriteTime -ge $info.LastRunTime.AddMinutes(-2) -and (-not $latest -or $skip.LastWriteTime -gt $latest.LastWriteTime)){
    Write-Host 'AUTOEXPERIMENT MONTHLY NOT DUE: no new monthly backup checkpoint was created.'
    exit 0
  }
}

$verifyScript=if($Cadence -eq 'daily'){Join-Path $root 'scripts\ops\restore-verify.ps1'}else{Join-Path $root 'scripts\ops\restore-verify-projects.ps1'}
if($Cadence -eq 'daily'){
  $verifyOutput=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verifyScript 2>&1
}else{
  $verifyOutput=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $verifyScript -Kind $Cadence 2>&1
}
$verifyExit=$LASTEXITCODE
$verifyOutput|ForEach-Object{Write-Host $_}
if($verifyExit -ne 0){throw "Restore verification failed for $Cadence; AutoExperiment was not dispatched."}

New-Item -ItemType Directory -Force -Path $stateRoot|Out-Null
$completedAt=if($TrustedBackupChain){Get-Date}else{$info.LastRunTime}
$runStamp=$completedAt.ToString('yyyyMMdd-HHmmss')
$manifestPath=Join-Path $stateRoot ("backup-manifest-$Cadence-$runStamp.json")
$scope=if($Cadence -eq 'daily'){
  @('__WINDOWS_WORKSPACE__/.claude','__WINDOWS_WORKSPACE__/.codex','__WINDOWS_WORKSPACE__/scripts','__WINDOWS_WORKSPACE__/.planning','__WINDOWS_WORKSPACE__/.rag')
}else{@('__WINDOWS_WORKSPACE__/Projects')}
$manifest=[pscustomobject][ordered]@{task=$taskName;cadence=$Cadence;last_run=$completedAt.ToString('o');backup_chain_attested=[bool]$TrustedBackupChain;restore_verifier=$verifyScript;restore_output=($verifyOutput -join "`n");scope=$scope}
$manifest|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $manifestPath -Encoding UTF8
$evidence=[pscustomobject][ordered]@{
  backup_id=("$Cadence-$runStamp");kind=$Cadence;status='green';completed_at=$completedAt.ToString('o')
  manifest_path=$manifestPath;manifest_sha256=(Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash
  included_surfaces=$scope;excluded_surfaces=@('secrets','credentials','private-keys');restore_status='green';eligible_scope=$scope
}
$evidencePath=Join-Path $stateRoot ("backup-evidence-$Cadence-$runStamp.json")
$checkpointPath=Join-Path $stateRoot ("checkpoint-$Cadence-$runStamp.json")
$evidence|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $evidencePath -Encoding UTF8
$checkpointOutput=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\autoexperiment\checkpoint.ps1') -EvidencePath $evidencePath -StatePath (Join-Path $stateRoot 'checkpoints.jsonl') -Json
if($LASTEXITCODE -ne 0){throw 'Checkpoint adapter failed.'}
$checkpointOutput -join "`n"|Set-Content -LiteralPath $checkpointPath -Encoding UTF8
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\autoexperiment\observe.ps1') -CheckpointPath $checkpointPath
if($LASTEXITCODE -ne 0){throw "Automatic $Cadence observation failed after the verified backup."}
