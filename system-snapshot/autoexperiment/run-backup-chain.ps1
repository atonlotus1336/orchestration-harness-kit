[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][ValidateSet('daily','weekly','monthly')][string]$Cadence,
  # Monthly scheduled tasks use the age gate. Explicit catch-up tasks may force
  # a new verified checkpoint without bypassing the post-backup verification.
  [switch]$Force,
  # Makes the weekday gate testable without changing the production clock.
  [datetime]$Now = (Get-Date)
)
$ErrorActionPreference='Stop'
$root='__WINDOWS_WORKSPACE__'

# Wednesday is reserved for the NUCBOX-owned Hermes/Prime/OMP maintenance
# window.  Keep this guard even though task registration also excludes the day:
# it protects manual starts and stale Task Scheduler definitions.
if($Cadence -eq 'daily' -and $Now.DayOfWeek -eq [System.DayOfWeek]::Wednesday){
  Write-Host 'SKIP: daily backup is disabled on Wednesday; NUCBOX verified maintenance owns this window.'
  exit 0
}

$output=@();$exitCode=1
$priorChainFlag=$env:__PRIVATE_TOKEN_02___AUTOEXPERIMENT_CHAIN
$env:__PRIVATE_TOKEN_02___AUTOEXPERIMENT_CHAIN='1'
try{
  if($Cadence -eq 'daily'){
    $output=& cmd.exe /d /c '"__WINDOWS_WORKSPACE__\.rag\daily-1pm.bat"' 2>&1
    $exitCode=$LASTEXITCODE
  }else{
    $kind=if($Cadence -eq 'weekly'){'Weekly'}elseif($Force){'Monthly'}else{'MonthlyDue'}
    $output=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\ops\backup-projects.ps1') -Kind $kind 2>&1
    $exitCode=$LASTEXITCODE
  }
}finally{
  if($null -eq $priorChainFlag){Remove-Item Env:__PRIVATE_TOKEN_02___AUTOEXPERIMENT_CHAIN -ErrorAction SilentlyContinue}else{$env:__PRIVATE_TOKEN_02___AUTOEXPERIMENT_CHAIN=$priorChainFlag}
}
$text=$output -join "`n";$output|ForEach-Object{Write-Host $_}
if($exitCode -ne 0){Write-Error "BACKUP CHAIN STOPPED cadence=$Cadence exit=$exitCode";exit $exitCode}
if($text -match '(?im)^SKIP:'){
  Write-Host "AUTOEXPERIMENT NOT LAUNCHED: backup is not assigned to this machine."
  exit 0
}
if($Cadence -eq 'monthly' -and $text -match 'MONTHLY BACKUP NOT DUE'){
  Write-Host 'AUTOEXPERIMENT MONTHLY NOT LAUNCHED: no new monthly checkpoint was created.'
  exit 0
}
$scanTask="WorkspaceKit-AutoExperiment-$([char]::ToUpperInvariant($Cadence[0]))$($Cadence.Substring(1))"
Start-ScheduledTask -TaskName $scanTask -ErrorAction Stop
Write-Host "AUTOEXPERIMENT LAUNCHED task=$scanTask policy=sandbox-only"
exit 0
