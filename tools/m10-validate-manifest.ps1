param(
  [string]$Manifest = "",
  [switch]$Json
)

$ErrorActionPreference = "Stop"
$kit = Split-Path -Parent $PSScriptRoot
if (-not $Manifest) { $Manifest = Join-Path $kit "installer\modules\module-manifest.json" }
$errors = [System.Collections.Generic.List[string]]::new()

try {
  $root = [System.IO.Path]::GetFullPath($kit).TrimEnd('\', '/')
  $doc = Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json
  $ids = @($doc.modules | ForEach-Object { [string]$_.id })
  if (@($ids | Group-Object | Where-Object Count -gt 1).Count -gt 0) {
    $errors.Add("DUPLICATE_MODULE_ID")
  }
  foreach ($module in @($doc.modules)) {
    if (-not $module.source) { continue }
    $source = [string]$module.source
    if ([System.IO.Path]::IsPathRooted($source) -or $source -match '(^|[\/])\.\.([\/]|$)') {
      $errors.Add("UNSAFE_SOURCE:$($module.id)")
      continue
    }
    $resolved = [System.IO.Path]::GetFullPath((Join-Path $root $source))
    if (-not ($resolved -eq $root -or $resolved.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar))) {
      $errors.Add("SOURCE_ESCAPE:$($module.id)")
    } elseif (-not (Test-Path -LiteralPath $resolved)) {
      $errors.Add("SOURCE_MISSING:$($module.id):$source")
    }
  }
  foreach ($id in @($doc.default_path)) {
    $module = @($doc.modules | Where-Object id -eq $id)
    if ($module.Count -ne 1) { $errors.Add("DEFAULT_MODULE_INVALID:$id") }
    elseif (-not [bool]$module[0].required) { $errors.Add("DEFAULT_MODULE_NOT_REQUIRED:$id") }
  }
} catch {
  $errors.Add("MANIFEST_INVALID:$($_.Exception.Message)")
}

$result = [ordered]@{ status = $(if ($errors.Count) { "RED" } else { "GREEN" }); errors = @($errors) }
if ($Json) { $result | ConvertTo-Json -Depth 6 } else { $result }
if ($errors.Count) { exit 2 }
exit 0
