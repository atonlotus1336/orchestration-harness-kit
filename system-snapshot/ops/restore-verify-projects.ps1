[CmdletBinding()]
param([ValidateSet('weekly','monthly')][string]$Kind = 'weekly')

$ErrorActionPreference = 'Stop'
$core = '__WSL_WORKSPACE__/scripts/ops/restore-verify-projects-core.sh'
$outputDir = '__WINDOWS_WORKSPACE__\output\ops'
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
$logPath = Join-Path $outputDir ("projects-restore-verify-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
# 2026-07-21: was `--distribution Ubuntu` — that distro's vhdx is CORRUPT (HCS 0x80070570),
# so every restore-verify silently failed. The live distro is Ubuntu-restored.
$output = & wsl.exe --distribution Ubuntu-restored --exec bash $core $Kind 2>&1
$exitCode = $LASTEXITCODE
$output | Tee-Object -FilePath $logPath
Write-Host "PROJECTS_RESTORE_VERIFY_EXIT=$exitCode LOG=$logPath"
exit $exitCode
