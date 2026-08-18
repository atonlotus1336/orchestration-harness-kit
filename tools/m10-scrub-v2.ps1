param(
  [string]$Root = "",
  [string]$ApprovedTokens = "",
  [string]$Denylist = "",
  [string]$QuarantineDir = "",
  [switch]$Json
)

$ErrorActionPreference = "Stop"
$kitRoot = Split-Path -Parent $PSScriptRoot
if (-not $Root) { $Root = Join-Path $kitRoot "generated" }
if (-not $ApprovedTokens) { $ApprovedTokens = Join-Path $kitRoot "approved-public-tokens.txt" }
if (-not (Test-Path -LiteralPath $ApprovedTokens)) {
  $templateTokens = Join-Path $kitRoot "approved-public-tokens.template.txt"
  if (Test-Path -LiteralPath $templateTokens) { $ApprovedTokens = $templateTokens }
}
if (-not $Denylist) { $Denylist = Join-Path $kitRoot ".private-denylist" }
if (-not $QuarantineDir) { $QuarantineDir = Join-Path $kitRoot ".m10-quarantine" }
if (-not (Test-Path -LiteralPath $Root)) { throw "Scrub root missing: $Root" }
if (-not (Test-Path -LiteralPath $ApprovedTokens)) { throw "Approved token list missing: $ApprovedTokens" }
New-Item -ItemType Directory -Force -Path $QuarantineDir | Out-Null

$approved = @{}
$approvedPrefixes = New-Object System.Collections.Generic.List[string]
Get-Content -LiteralPath $ApprovedTokens | ForEach-Object {
  $line = $_.Trim()
  if ($line -and -not $line.StartsWith("#")) {
    if ($line.StartsWith("prefix:")) {
      $prefix = $line.Substring(7).Trim()
      if ($prefix.Length -lt 8) { throw "Approved prefix too short: $line" }
      $approvedPrefixes.Add($prefix.ToLowerInvariant().TrimEnd([char[]]@('\', '/')))
    } else {
      $approved[$line.ToLowerInvariant()] = $true
    }
  }
}

function Test-ApprovedToken($Token) {
  $lower = $Token.ToLowerInvariant().TrimEnd()
  if ($approved.ContainsKey($lower)) { return $true }
  foreach ($prefix in $approvedPrefixes) {
    if ($lower -eq $prefix -or $lower.StartsWith($prefix + "\") -or $lower.StartsWith($prefix + "/")) {
      return $true
    }
  }
  return $false
}

$denyPatterns = New-Object System.Collections.Generic.List[string]
if (Test-Path -LiteralPath $Denylist) {
  Get-Content -LiteralPath $Denylist | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) { $denyPatterns.Add($line) }
  }
}

$patterns = [ordered]@{
  email = '[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}'
  windows_path = '(?i)\b[A-Z]:[\\/][A-Za-z0-9._$(){}\[\]\-\\/ ]+'
  unix_path = '(?<![A-Za-z0-9])/(?:home|Users|mnt|var|etc|opt|srv|root)/[A-Za-z0-9._$(){}\[\]\-\\/]+'
  ipv4 = '\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b'
  hostname = '\b(?!(?:example|localhost)\b)(?:[A-Za-z0-9-]+\.)+(?:com|net|org|io|dev|app|local|lan|cloud|ai|co|us)\b'
}

$hits = New-Object System.Collections.Generic.List[object]
$files = Get-ChildItem -LiteralPath $Root -Recurse -File -Force |
  Where-Object {
    $_.FullName -notmatch '\\\.git\\' -and
    $_.FullName -notmatch '\\\.m10-quarantine\\' -and
    $_.FullName -notmatch '\\__pycache__\\' -and
    $_.FullName -notmatch '\\\.pytest_cache\\' -and
    $_.Extension -notin @('.pyc', '.png', '.jpg', '.jpeg', '.gif', '.zip')
  }

foreach ($file in $files) {
  $text = Get-Content -Raw -LiteralPath $file.FullName -ErrorAction SilentlyContinue
  if ($null -eq $text) { continue }
  foreach ($deny in $denyPatterns) {
    $denyPattern = if ($deny -match '^[A-Za-z0-9]+$') { '(?<![A-Za-z0-9_-])' + [regex]::Escape($deny) + '(?![A-Za-z0-9_-])' } else { $deny }
    foreach ($match in [regex]::Matches($text, $denyPattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
      $token = $match.Value.TrimEnd(".,);]'`"")
      if (-not (Test-ApprovedToken $token)) {
        $line = 1 + (($text.Substring(0, $match.Index).ToCharArray() | Where-Object { $_ -eq "`n" }).Count)
        $hits.Add([ordered]@{
          file = $file.FullName
          line = $line
          kind = "denylist"
          token = $token
        })
      }
    }
  }
  foreach ($kind in $patterns.Keys) {
    foreach ($match in [regex]::Matches($text, $patterns[$kind])) {
      $token = $match.Value.TrimEnd(".,);]'`"")
      if (-not (Test-ApprovedToken $token)) {
        $line = 1 + (($text.Substring(0, $match.Index).ToCharArray() | Where-Object { $_ -eq "`n" }).Count)
        $hits.Add([ordered]@{
          file = $file.FullName
          line = $line
          kind = $kind
          token = $token
        })
      }
    }
  }
}

$status = "GREEN"
if ($hits.Count -ne 0) { $status = "RED" }
$hitArray = @()
foreach ($hit in $hits) { $hitArray += $hit }
$result = [pscustomobject]@{
  status = $status
  scan_root = $Root
  hit_count = $hits.Count
  hits = $hitArray
  quarantine = $null
}

if ($hits.Count -ne 0) {
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
  $q = Join-Path $QuarantineDir "scrub-v2-$stamp.json"
  $result.quarantine = $q
  $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $q -Encoding UTF8
}

if ($Json) {
  $result | ConvertTo-Json -Depth 8
}
if ($hits.Count -ne 0) { exit 2 }
exit 0
