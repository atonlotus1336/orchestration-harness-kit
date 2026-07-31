param(
  [string]$Graph = "SURFACE-GRAPH.yaml",
  [string]$GeneratedDir = "",
  [switch]$Json
)

$ErrorActionPreference = "Continue"
if (-not $GeneratedDir) {
  $GeneratedDir = Join-Path (Split-Path -Parent $PSScriptRoot) "generated"
}
if (-not (Test-Path -LiteralPath $Graph) -and $Graph -eq "SURFACE-GRAPH.yaml") {
  $candidateGraph = Join-Path (Split-Path -Parent $PSScriptRoot) "..\..\SURFACE-GRAPH.yaml"
  if (Test-Path -LiteralPath $candidateGraph) { $Graph = $candidateGraph }
}
$errors = New-Object System.Collections.Generic.List[string]

foreach ($file in @("INSTALL-STAGES.json", "PARITY-VERIFIER.json", "SYNC-ALLOWLIST.json", "SYNC-ALLOWLIST.txt", "MANUAL.md", "PROVENANCE.json", "bindings.local.template.yaml")) {
  if (-not (Test-Path -LiteralPath (Join-Path $GeneratedDir $file))) {
    $errors.Add("GENERATED_MISSING:$file")
  }
}

try {
  $freshDir = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "m10-validate-" + [guid]::NewGuid().ToString("N"))
  $null = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "m10-generate-from-surface-graph.ps1") -Graph $Graph -OutputDir $freshDir
  if ($LASTEXITCODE -ne 0) { $errors.Add("GRAPH_INVALID") }
  foreach ($file in @("INSTALL-STAGES.json", "PARITY-VERIFIER.json", "SYNC-ALLOWLIST.json", "SYNC-ALLOWLIST.txt", "MANUAL.md", "PROVENANCE.json", "bindings.local.template.yaml")) {
    $generatedPath = Join-Path $GeneratedDir $file
    $freshPath = Join-Path $freshDir $file
    if ((Test-Path -LiteralPath $generatedPath) -and (Test-Path -LiteralPath $freshPath)) {
      $generatedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $generatedPath).Hash
      $freshHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $freshPath).Hash
      if ($generatedHash -ne $freshHash) {
        $errors.Add("GENERATED_DRIFT:$file")
      }
    }
  }
} catch {
  $errors.Add("GRAPH_INVALID:$($_.Exception.Message)")
} finally {
  if ($freshDir) {
    Remove-Item -LiteralPath $freshDir -Recurse -Force -ErrorAction SilentlyContinue
  }
}

try {
  $prov = Get-Content -Raw -LiteralPath (Join-Path $GeneratedDir "PROVENANCE.json") | ConvertFrom-Json
  foreach ($entry in $prov.checksums.PSObject.Properties) {
    $path = Join-Path $GeneratedDir $entry.Name
    if (-not (Test-Path -LiteralPath $path)) {
      $errors.Add("CHECKSUM_TARGET_MISSING:$($entry.Name)")
      continue
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
    if ($actual -ne [string]$entry.Value) {
      $errors.Add("CHECKSUM_MISMATCH:$($entry.Name)")
    }
  }
} catch {
  $errors.Add("PROVENANCE_INVALID:$($_.Exception.Message)")
}

$status = "GREEN"
if ($errors.Count -ne 0) { $status = "RED" }
$result = [ordered]@{
  status = $status
  errors = @($errors)
}
if ($Json) {
  $result | ConvertTo-Json -Depth 8
}
if ($errors.Count -ne 0) {
  exit 2
}
exit 0
