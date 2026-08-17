# Plan 01 Task 1.5 probe: newest VPS backup age < 8 days AND last restore-verify GREEN
# marker < 30 days. Exit 0 GREEN / 1 RED (reason on stdout).
$ErrorActionPreference = "Stop"
$marker = "__WINDOWS_WORKSPACE__\output\ops\restore-verify-last-green.txt"
if (-not (Test-Path -LiteralPath $marker)) { Write-Host "RED no restore-verify GREEN marker"; exit 1 }
$markerAge = ((Get-Date) - (Get-Item -LiteralPath $marker).LastWriteTime).TotalDays
if ($markerAge -gt 30) { Write-Host ("RED restore-verify marker stale ({0:N1}d > 30d)" -f $markerAge); exit 1 }
$destLine = (Select-String -LiteralPath "__WINDOWS_WORKSPACE__\.planning\mission-layer\DECISIONS.md" -Pattern 'backup dest:' | Select-Object -First 1).Line
if ($destLine -notmatch '(\w+@[\d\.]+):(/\S+)') { Write-Host "RED no backup dest in DECISIONS.md"; exit 1 }
$sshTarget = $Matches[1]; $destBase = "$($Matches[2].TrimEnd('/'))/__PRIVATE_TOKEN_02__-backup"
# Remote mtime via a positional-arg bash helper (no nested PowerShell->ssh quoting).
$raw = (& wsl -e bash __WSL_WORKSPACE__/scripts/ops/probes/backup-age-core.sh $sshTarget $destBase 2>$null)
$mtimeRaw = @($raw | Where-Object { "$_".Trim() -match '^\d+$' } | Select-Object -Last 1)
if (-not $mtimeRaw -or "$mtimeRaw".Trim() -notmatch '^\d+$') { Write-Host "RED cannot reach backup dest (raw='$($raw -join '|')')"; exit 1 }
$ageDays = [int][math]::Floor(((Get-Date).ToUniversalTime() - [DateTimeOffset]::FromUnixTimeSeconds([long]("$mtimeRaw".Trim())).UtcDateTime).TotalDays)
if ($ageDays -ge 8) { Write-Host "RED newest backup is ${ageDays}d old (>= 8d)"; exit 1 }
Write-Host "GREEN newest backup ${ageDays}d old, restore-verify marker $([math]::Round($markerAge,1))d old"
exit 0
