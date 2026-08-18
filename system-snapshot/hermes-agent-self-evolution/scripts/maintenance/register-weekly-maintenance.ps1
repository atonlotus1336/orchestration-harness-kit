[CmdletBinding(SupportsShouldProcess)]
param([switch]$Enable, [string]$TaskName = 'WorkspaceKit-Verified-Weekly-Maintenance')

$script = '__WINDOWS_WORKSPACE__\hermes-agent-self-evolution\scripts\maintenance\run-weekly-maintenance.py'
$argument = '-d __WSL_DISTRO__ -- bash -lc "cd __WSL_WORKSPACE__/hermes-agent-self-evolution && .venv/bin/python scripts/maintenance/run-weekly-maintenance.py --dry-run"'
$action = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument $argument
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Wednesday -At 2:00PM
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew -StartWhenAvailable
if ($PSCmdlet.ShouldProcess($TaskName, 'Register disabled weekly maintenance task')) {
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description 'Disabled-by-default Hermes/Prime/OMP verified maintenance' -Force | Out-Null
  if ($Enable) { Enable-ScheduledTask -TaskName $TaskName | Out-Null } else { Disable-ScheduledTask -TaskName $TaskName | Out-Null }
}
if ($Enable) { Write-Warning 'Enable only after reviewed dry-run evidence; this script still invokes dry-run mode.' }
