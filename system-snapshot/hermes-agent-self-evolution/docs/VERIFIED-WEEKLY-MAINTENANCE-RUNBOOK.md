# Verified Weekly Maintenance

The NUCBOX maintenance task is scheduled for Wednesday 14:00 America/New_York (2:00 PM Eastern) and is disabled by default. It currently runs only the deterministic dry-run controller; a live update adapter is deliberately unavailable until separately reviewed.

1. Run the controller manually on NUCBOX: `[WSL/bash] cd __WSL_WORKSPACE__/hermes-agent-self-evolution && .venv/bin/python scripts/maintenance/run-weekly-maintenance.py --dry-run`.
2. Register the disabled task only: `[PowerShell] .\scripts\maintenance\register-weekly-maintenance.ps1`.
3. Inspect it: `[PowerShell] Get-ScheduledTask -TaskName WorkspaceKit-Verified-Weekly-Maintenance` and inspect the JSON/SQLite ledger path printed by the controller.
4. Run it on demand after registration: `[PowerShell] Start-ScheduledTask -TaskName WorkspaceKit-Verified-Weekly-Maintenance`.
5. Disable it immediately: `[PowerShell] Disable-ScheduledTask -TaskName WorkspaceKit-Verified-Weekly-Maintenance`.

The ledger records `PROMOTED`, `FAILED_RESTORED`, or `SKIPPED` per component. It halts on unverified rollback, integrity, credential, or shared-infrastructure failure. Automatic commits are local and allowlisted; remote push, provider changes, credentials, memory, permissions, and network policy are never automatic. A later live adapter must be independently reviewed before any enabled task can update production.
