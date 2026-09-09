param(
  [string]$Allowlist = "",
  [string]$Graph = "SURFACE-GRAPH.yaml",
  [string]$Destination = "",
  [string]$SanitizerRules = "",
  [switch]$RequireSanitizerRules,
  [switch]$NoScrub
)

$ErrorActionPreference = "Stop"
if ($NoScrub) {
  throw "NoScrub is not permitted for publishable exports"
}
$kitRoot = $PSScriptRoot
if (-not $Allowlist) { $Allowlist = Join-Path $kitRoot "generated\SYNC-ALLOWLIST.json" }
if (-not $Destination) { $Destination = Join-Path $kitRoot ".shareable-preview" }
if (-not $SanitizerRules) { $SanitizerRules = Join-Path $kitRoot ".sanitize-rules.local.json" }
if (-not (Test-Path -LiteralPath $Graph) -and $Graph -eq "SURFACE-GRAPH.yaml") {
  $candidateGraph = Join-Path $kitRoot "..\SURFACE-GRAPH.yaml"
  if (Test-Path -LiteralPath $candidateGraph) { $Graph = $candidateGraph }
}
if (-not (Test-Path -LiteralPath $Allowlist)) {
  throw "Generated allowlist missing. Run tools/m10-generate-from-surface-graph.ps1 first."
}

$data = Get-Content -Raw -LiteralPath $Allowlist | ConvertFrom-Json
$rules = @()
if (Test-Path -LiteralPath $SanitizerRules) {
  $loadedRules = Get-Content -Raw -LiteralPath $SanitizerRules | ConvertFrom-Json
  foreach ($loadedRule in $loadedRules) {
    if ($loadedRule.From) {
      $rules += [pscustomobject]@{
        From = [string]$loadedRule.From
        To = [string]$loadedRule.To
      }
    }
  }
}
if ($rules.Count -eq 0) {
  throw "Sanitizer rules required but none loaded from $SanitizerRules"
}
function Resolve-SafeChildPath {
  param([string]$Root, [string]$Relative)
  $rel = ([string]$Relative).Replace('/', '\')
  if (-not $rel) { throw "Empty relative target path" }
  if ([System.IO.Path]::IsPathRooted($rel) -or $rel -match '(^|[\\/])\.\.([\\/]|$)') {
    throw "Unsafe relative target path: $Relative"
  }
  $rootFull = [System.IO.Path]::GetFullPath($Root)
  $targetFull = [System.IO.Path]::GetFullPath((Join-Path $rootFull $rel))
  $rootPrefix = $rootFull.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
  if (-not $targetFull.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Target escapes destination root: $Relative"
  }
  return $targetFull
}

$parser = @'
import json
import sys
import yaml
with open(sys.argv[1], "r", encoding="utf-8") as f:
    data = yaml.safe_load(f) or {}
print(json.dumps({str(r.get("id")): str(r.get("config_path")) for r in data.get("surfaces", [])}))
'@
$tmpParser = [System.IO.Path]::GetTempFileName() + ".py"
try {
  Set-Content -LiteralPath $tmpParser -Value $parser -Encoding UTF8
  $pathMap = (& python $tmpParser $Graph | ConvertFrom-Json)
  if ($LASTEXITCODE -ne 0) { throw "Could not parse graph for sync-shareable-v2" }
} finally {
  Remove-Item -LiteralPath $tmpParser -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $Destination -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Destination | Out-Null

foreach ($row in @($data.shareable)) {
  $prop = $pathMap.PSObject.Properties[[string]$row.id]
  if (-not $prop) { continue }
  $source = [string]$prop.Value
  $targetRel = [string]$row.target_path
  $preferred = Join-Path $kitRoot ($targetRel -replace '/', '\')
  if (Test-Path -LiteralPath $preferred -PathType Leaf) {
    $sourceWin = $preferred
  } else {
    $sourceWin = $source -replace '/', '\'
  }
  if (-not (Test-Path -LiteralPath $sourceWin -PathType Leaf)) {
    throw "Shareable source missing for $($row.id): $sourceWin"
  }
  $target = Resolve-SafeChildPath $Destination $targetRel
  $parent = Split-Path -Parent $target
  New-Item -ItemType Directory -Force -Path $parent | Out-Null
  Copy-Item -LiteralPath $sourceWin -Destination $target -Force
  $copied = Get-Content -Raw -LiteralPath $target -ErrorAction SilentlyContinue
  if ($null -ne $copied) {
    foreach ($rule in @($rules)) {
      $from = [string]$rule.From
      $to = [string]$rule.To
      if ($from) {
        $copied = $copied.Replace($from, $to)
      }
    }
    Set-Content -LiteralPath $target -Value $copied -Encoding UTF8
  }
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $kitRoot "tools\m10-scrub-v2.ps1") -Root $Destination -Json
if ($LASTEXITCODE -ne 0) {
  throw "M10 scrub-v2 failed for shareable preview"
}

[ordered]@{
  status = "GREEN"
  destination = $Destination
} | ConvertTo-Json -Depth 4
