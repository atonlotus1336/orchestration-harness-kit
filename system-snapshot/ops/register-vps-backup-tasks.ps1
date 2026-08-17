param(
  [switch]$DailyOnly,
  # Preserve the established live task time while converting its schedule from
  # daily to every day except Wednesday.
  [string]$DailyAt = '2:59 PM'
)

$ErrorActionPreference = 'Stop'

$dailyScript = '__WINDOWS_WORKSPACE__\.rag\daily-1pm.bat'
$projectsScript = '__WINDOWS_WORKSPACE__\scripts\ops\backup-projects.ps1'
$autoExperimentChain = '__WINDOWS_WORKSPACE__\scripts\autoexperiment\run-backup-chain.ps1'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$userId = $identity.Name

if (-not (Test-Path -LiteralPath $dailyScript -PathType Leaf)) {
  throw "Daily backup entry point is missing: $dailyScript"
}

$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 8) `
  -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal `
  -UserId $userId `
  -LogonType Interactive `
  -RunLevel Limited

$dailyAction = if (Test-Path -LiteralPath $autoExperimentChain -PathType Leaf) {
  New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File "__WINDOWS_WORKSPACE__\scripts\autoexperiment\run-backup-chain.ps1" -Cadence daily' -WorkingDirectory '__WINDOWS_WORKSPACE__'
} else {
  New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/d /c ""__WINDOWS_WORKSPACE__\.rag\daily-1pm.bat""' -WorkingDirectory '__WINDOWS_WORKSPACE__\.rag'
}
$dailyTrigger = @(
  'Sunday', 'Monday', 'Tuesday', 'Thursday', 'Friday', 'Saturday' |
    ForEach-Object { New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $_ -At $DailyAt }
)
Register-ScheduledTask `
  -TaskName 'WorkspaceKit-Daily-Reindex-Backup' `
  -Description 'WorkspaceKit recovery manifest every day except Wednesday; Wednesday is reserved for NUCBOX Hermes/Prime/OMP verified maintenance.' `
  -Action $dailyAction `
  -Trigger $dailyTrigger `
  -Settings $settings `
  -Principal $principal `
  -Force | Out-Null

if (-not $DailyOnly) {
  if (-not (Test-Path -LiteralPath $projectsScript -PathType Leaf)) {
    throw "Projects backup entry point is missing: $projectsScript"
  }

  $weeklyAction = if (Test-Path -LiteralPath $autoExperimentChain -PathType Leaf) {
    New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File "__WINDOWS_WORKSPACE__\scripts\autoexperiment\run-backup-chain.ps1" -Cadence weekly' -WorkingDirectory '__WINDOWS_WORKSPACE__'
  } else {
    New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File "__WINDOWS_WORKSPACE__\scripts\ops\backup-projects.ps1" -Kind Weekly' -WorkingDirectory '__WINDOWS_WORKSPACE__'
  }
  $weeklyTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Saturday -At '11:59 AM'
  Register-ScheduledTask `
    -TaskName 'WorkspaceKit-Projects-Weekly-Backup' `
    -Description 'Weekly hard-linked Projects snapshot plus sanitized VPS recovery bundle and manifest to the VPS with checksum verification.' `
    -Action $weeklyAction `
    -Trigger $weeklyTrigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

  # The due-check runs on Monday after the Saturday weekly backup and its
  # following weekly AutoExperiment window. The marker still limits actual
  # monthly snapshots to one per calendar month.
  $monthlyAction = if (Test-Path -LiteralPath $autoExperimentChain -PathType Leaf) {
    New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File "__WINDOWS_WORKSPACE__\scripts\autoexperiment\run-backup-chain.ps1" -Cadence monthly' -WorkingDirectory '__WINDOWS_WORKSPACE__'
  } else {
    New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File "__WINDOWS_WORKSPACE__\scripts\ops\backup-projects.ps1" -Kind MonthlyDue' -WorkingDirectory '__WINDOWS_WORKSPACE__'
  }
  $monthlyTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Monday -At '11:59 AM'
  Register-ScheduledTask `
    -TaskName 'WorkspaceKit-Projects-Monthly-Backup' `
    -Description 'Monday due-check that creates one monthly Projects checkpoint after the preceding weekly backup and AutoExperiment window.' `
    -Action $monthlyAction `
    -Trigger $monthlyTrigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null
}

$names = @('WorkspaceKit-Daily-Reindex-Backup')
if (-not $DailyOnly) {
  $names += 'WorkspaceKit-Projects-Weekly-Backup', 'WorkspaceKit-Projects-Monthly-Backup'
}

# Install the on-demand report-only followers after their backup tasks exist.
# The backup wrappers launch these only after the underlying command succeeds.
$scanRegistration = '__WINDOWS_WORKSPACE__\scripts\autoexperiment\register-scheduled-scans.ps1'
if (Test-Path -LiteralPath $scanRegistration -PathType Leaf) {
  if ($DailyOnly) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scanRegistration -Cadences daily
  } else {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scanRegistration
  }
  if ($LASTEXITCODE -ne 0) { throw 'AutoExperiment scan task registration failed.' }
}

Get-ScheduledTask -TaskName $names | ForEach-Object {
  $info = Get-ScheduledTaskInfo -InputObject $_
  [pscustomobject]@{
    TaskName = $_.TaskName
    State = $_.State.ToString()
    NextRunTime = $info.NextRunTime
    Execute = $_.Actions.Execute
    Arguments = $_.Actions.Arguments
    WorkingDirectory = $_.Actions.WorkingDirectory
  }
}
