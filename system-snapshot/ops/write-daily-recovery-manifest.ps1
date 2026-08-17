param(
  [string]$OutputRoot = '__WINDOWS_WORKSPACE__\.recovery\daily-current'
)

$ErrorActionPreference = 'Stop'

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$kitPath = '__WINDOWS_WORKSPACE__\export\orchestration-layer'
$kitCommit = $null
if (Test-Path -LiteralPath (Join-Path $kitPath '.git') -PathType Container) {
  $gitOutput = & git -C $kitPath rev-parse HEAD 2>$null
  if ($LASTEXITCODE -eq 0) {
    $kitCommit = ($gitOutput | Select-Object -Last 1).ToString().Trim()
  }
}

$weeklyRoot = '__WINDOWS_WORKSPACE__\.recovery\weekly-current'
$weeklyManifest = Join-Path $weeklyRoot 'recovery-manifest.json'
$lastWeeklyExport = $null
if (Test-Path -LiteralPath $weeklyManifest -PathType Leaf) {
  $lastWeeklyExport = (Get-Item -LiteralPath $weeklyManifest).LastWriteTime.ToString('o')
}

$memoryweaverFts = '__WSL_WORKSPACE__/.recovery/daily-current/memoryweaver-fts.db'
& wsl.exe --distribution Ubuntu-restored --exec bash -lc "cp /home/__LINUX_USER__/.local/share/memoryweaver-runtime/fts.db $memoryweaverFts"
if ($LASTEXITCODE -ne 0) {
  throw 'MemoryWeaver FTS export failed; refusing to create an unverifiable daily recovery manifest.'
}

$memoryweaverStatus = & wsl.exe --distribution Ubuntu-restored --exec bash -lc "export MEMORYWEAVER_DATA_DIR=__WSL_WORKSPACE__/.claude/Memory/memoryweaver; export MEMORYWEAVER_FTS_DB_PATH=$memoryweaverFts; cd __WSL_WORKSPACE__/.claude/memoryweaver && /home/__LINUX_USER__/.local/share/memoryweaver-mcp/venv/bin/python3.11 -m memoryweaver status 2>&1 | tail -20"
if ($LASTEXITCODE -ne 0) {
  throw 'MemoryWeaver status failed; refusing to create an unverifiable daily recovery manifest.'
}

$manifest = [ordered]@{
  schema = '__PRIVATE_TOKEN_02__-daily-recovery-manifest/v1'
  created_at = (Get-Date).ToString('o')
  computer = $env:COMPUTERNAME
  source = '__WINDOWS_WORKSPACE__'
  daily_backup_scope = @(
    'WorkspaceKit configuration, skills, hooks, memory, RAG sources, MCP and Hermes configuration',
    'Non-secret recovery metadata'
  )
  intentionally_not_in_daily_backup = @(
    'Projects tree (weekly and monthly stream)',
    'Plaintext passwords, tokens, private keys, and environment files',
    'Rebuildable virtual environments, WSL distribution, model weights, caches, and generated artifacts'
  )
  vps_state = [ordered]@{
    included_in_this_daily_snapshot = $false
    export_cadence = 'weekly and monthly'
    last_weekly_export_manifest = $lastWeeklyExport
  }
  memoryweaver_status_tail = ($memoryweaverStatus -join "`n")
  github_kit_commit = $kitCommit
  credentials_to_reestablish = @(
    'Hostinger SSH access',
    'Provider and API credentials required by restored services',
    'Bot or webhook credentials required by restored workflows'
  )
  secret_policy = 'No secret values are stored in this manifest or the backup.'
}

$path = Join-Path $OutputRoot 'recovery-manifest.json'
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $path -Encoding UTF8
Write-Output "DAILY MANIFEST OK path=$path last_weekly_vps_export=$lastWeeklyExport"
