[CmdletBinding()]
param([ValidateSet('daily','weekly','monthly')][string[]]$Cadences=@('daily','weekly','monthly'))
$ErrorActionPreference='Stop'
$root='__WINDOWS_WORKSPACE__'
$runner=Join-Path $root 'scripts\autoexperiment\after-backup.ps1'
if(-not(Test-Path -LiteralPath $runner -PathType Leaf)){throw "Missing AutoExperiment runner: $runner"}
$chain=Join-Path $root 'scripts\autoexperiment\run-backup-chain.ps1'
if(-not(Test-Path -LiteralPath $chain -PathType Leaf)){throw "Missing backup chain: $chain"}
$userId=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal=New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$definitions=@(
  @{Name='WorkspaceKit-AutoExperiment-Daily';Cadence='daily';Backup='WorkspaceKit-Daily-Reindex-Backup';Limit=(New-TimeSpan -Hours 1)},
  @{Name='WorkspaceKit-AutoExperiment-Weekly';Cadence='weekly';Backup='WorkspaceKit-Projects-Weekly-Backup';Limit=(New-TimeSpan -Hours 6)},
  @{Name='WorkspaceKit-AutoExperiment-Monthly';Cadence='monthly';Backup='WorkspaceKit-Projects-Monthly-Backup';Limit=(New-TimeSpan -Hours 24)}
)
foreach($d in @($definitions|Where-Object{$_.Cadence -in $Cadences})){
  if(-not(Get-ScheduledTask -TaskName $d.Backup -ErrorAction SilentlyContinue)){throw "Required backup task is not registered: $($d.Backup)"}
  $scanSettings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit $d.Limit -MultipleInstances IgnoreNew
  $scanAction=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ("-NoProfile -ExecutionPolicy Bypass -File `"{0}`" -Cadence {1} -TrustedBackupChain" -f $runner,$d.Cadence) -WorkingDirectory $root
  # Recreate our own task so a stale trigger from an older registration cannot
  # survive -Force. These tasks must be on-demand only; the green backup wrapper
  # is their sole dispatcher.
  if(Get-ScheduledTask -TaskName $d.Name -ErrorAction SilentlyContinue){Unregister-ScheduledTask -TaskName $d.Name -Confirm:$false}
  Register-ScheduledTask -TaskName $d.Name -Description ("Visible on-demand sandbox-only AutoExperiment audit launched only after {0} succeeds; production implementation always requires manual confirmation." -f $d.Backup) -Action $scanAction -Settings $scanSettings -Principal $principal -Force|Out-Null
}
Get-ScheduledTask -TaskName 'WorkspaceKit-AutoExperiment-*'|ForEach-Object{$i=Get-ScheduledTaskInfo -InputObject $_;[pscustomobject]@{TaskName=$_.TaskName;State=$_.State;NextRunTime=$i.NextRunTime;Execute=$_.Actions.Execute;Arguments=$_.Actions.Arguments}}
