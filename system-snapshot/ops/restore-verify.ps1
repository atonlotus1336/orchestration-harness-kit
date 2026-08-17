param(
  # Plan 01 Task 1.3 - "a backup never restored is a hypothesis". Restores the newest
  # backup's memoryweaver store into a temp dir, runs `memoryweaver status` against it
  # (WSL venv), and asserts live-record/FTS counts match BACKUP-MANIFEST.json's recorded
  # status. Prints RESTORE-VERIFY GREEN <counts> or RESTORE-VERIFY RED <mismatch> + exit 1.
  # -SelfTest: tiny synthetic backup exercises the full compare logic without the VPS.
  [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
$sshKey = "~/.ssh/hermes-hostinger"

function WslRun([string]$cmd) {
  $out = & wsl -e bash -lc $cmd 2>&1
  return @{ exit = $LASTEXITCODE; out = ($out -join "`n") }
}

function Get-Counts([string]$statusText) {
  # memoryweaver status tail contains explicit/inferred/FTS counts; extract all integers
  # attached to known labels. Returns ordered label=count map.
  $counts = [ordered]@{}
  foreach ($m in [regex]::Matches($statusText, '(?i)(explicit|inferred|fts|live|records?)\D{0,20}?(\d+)')) {
    $label = $m.Groups[1].Value.ToLowerInvariant()
    if (-not $counts.Contains($label)) { $counts[$label] = [int]$m.Groups[2].Value }
  }
  return $counts
}

if ($SelfTest) {
  $tmp = Join-Path $env:TEMP ("restore-verify-selftest-" + (Get-Date -Format "HHmmss"))
  New-Item -ItemType Directory -Force -Path "$tmp\meta" | Out-Null
  $syntheticStatus = "live records: 42`nexplicit: 10`ninferred: 32`nfts: 42"
  @{ ts = "selftest"; memoryweaver_status_tail = $syntheticStatus } | ConvertTo-Json | Set-Content "$tmp\meta\BACKUP-MANIFEST.json" -Encoding UTF8
  $recorded = Get-Counts (Get-Content -Raw "$tmp\meta\BACKUP-MANIFEST.json" | ConvertFrom-Json).memoryweaver_status_tail
  $restored = Get-Counts $syntheticStatus  # synthetic "restored" status = same text => must match
  $mismatch = @($recorded.Keys | Where-Object { $restored[$_] -ne $recorded[$_] })
  Remove-Item $tmp -Recurse -Force
  if ($mismatch.Count -eq 0 -and $recorded.Count -ge 2) {
    Write-Host ("RESTORE-VERIFY GREEN (selftest) counts=" + (($recorded.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ","))
    exit 0
  }
  Write-Host "RESTORE-VERIFY RED (selftest logic failure)"
  exit 1
}

# --- real path: pull newest backup's current-layout recovery evidence, then
# restore its MemoryWeaver store and compare recorded counts. ---
# Keep this aligned with .rag/isolatedbackup-push.sh. The older mission-layer
# decision records the retired __PRIVATE_TOKEN_02__-vault destination, which is not the
# current daily snapshot tree.
$sshTarget = 'root@__TAILSCALE_IPV4__'
$destBase = '/srv/isolatedbackups/daily/__PRIVATE_TOKEN_02__'

$newest = (WslRun "ssh -i $sshKey $sshTarget 'ls -1d $destBase/20* 2>/dev/null | grep -v FAILED | sort | tail -1'").out.Trim()
if (-not $newest) { Write-Host "RESTORE-VERIFY RED no backup found at $destBase"; exit 1 }

$restoreDir = "/tmp/__PRIVATE_TOKEN_02__-restore-verify"
$r = WslRun "rm -rf $restoreDir && mkdir -p $restoreDir && rsync -a -e 'ssh -i $sshKey' '${sshTarget}:$newest/.claude/memory/memoryweaver/' '$restoreDir/memoryweaver/' && rsync -a -e 'ssh -i $sshKey' '${sshTarget}:$newest/.recovery/daily-current/recovery-manifest.json' '$restoreDir/recovery-manifest.json' && rsync -a -e 'ssh -i $sshKey' '${sshTarget}:$newest/.recovery/daily-current/memoryweaver-fts.db' '$restoreDir/memoryweaver-fts.db'"
if ($r.exit -ne 0) { Write-Host "RESTORE-VERIFY RED restore pull failed: $($r.out)"; exit 1 }

$manifestJson = (WslRun "cat $restoreDir/recovery-manifest.json").out | ConvertFrom-Json
$recorded = Get-Counts $manifestJson.memoryweaver_status_tail
$statusRun = WslRun "export MEMORYWEAVER_DATA_DIR=$restoreDir/memoryweaver; export MEMORYWEAVER_FTS_DB_PATH=$restoreDir/memoryweaver-fts.db; cd __WSL_WORKSPACE__/.claude/memoryweaver && /home/__LINUX_USER__/.local/share/memoryweaver-mcp/venv/bin/python3.11 -m memoryweaver status 2>/dev/null | tail -20"
$restored = Get-Counts $statusRun.out
WslRun "rm -rf $restoreDir" | Out-Null

if ($recorded.Count -lt 1) { Write-Host "RESTORE-VERIFY RED manifest recorded no parsable counts"; exit 1 }
$mismatch = @($recorded.Keys | Where-Object { $restored[$_] -ne $recorded[$_] } | ForEach-Object { "$_ recorded=$($recorded[$_]) restored=$($restored[$_])" })
if ($mismatch.Count -gt 0) { Write-Host ("RESTORE-VERIFY RED " + ($mismatch -join "; ")); exit 1 }
New-Item -ItemType Directory -Force -Path "__WINDOWS_WORKSPACE__\output\ops" | Out-Null
Set-Content -LiteralPath "__WINDOWS_WORKSPACE__\output\ops\restore-verify-last-green.txt" -Value ("GREEN " + (Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz") + " backup=" + [System.IO.Path]::GetFileName($newest)) -Encoding UTF8
Write-Host ("RESTORE-VERIFY GREEN backup=$([System.IO.Path]::GetFileName($newest)) counts=" + (($recorded.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ","))
exit 0
