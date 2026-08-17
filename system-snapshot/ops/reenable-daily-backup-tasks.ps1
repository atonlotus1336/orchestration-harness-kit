$ErrorActionPreference = 'Stop'

Enable-ScheduledTask -TaskName 'WorkspaceKit-Daily-Reindex-Backup' | Out-Null
Enable-ScheduledTask -TaskName 'WorkspaceKit-AutoExperiment-Daily' | Out-Null

Unregister-ScheduledTask -TaskName 'WorkspaceKit-Daily-Actions-Reenable' -Confirm:$false -ErrorAction SilentlyContinue
