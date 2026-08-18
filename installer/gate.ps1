param([switch]$Json)

$ErrorActionPreference = "Continue"
$kit = Split-Path -Parent $PSScriptRoot
$steps = [System.Collections.Generic.List[object]]::new()
function Add-Step($name, $exitCode) {
  $steps.Add([ordered]@{ name = $name; status = $(if ($exitCode -eq 0) { "PASS" } else { "FAIL" }); exit = $exitCode })
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $kit "tools\m10-validate-manifest.ps1") -Json | Out-Null
Add-Step "module-manifest" $LASTEXITCODE

& python (Join-Path $kit "modules\guards\test-dry-run-gate.py") | Out-Null
Add-Step "destructive-command-guard" $LASTEXITCODE

& python (Join-Path $kit "modules\topology\configure-topology.py") --selftest | Out-Null
Add-Step "solo-topology" $LASTEXITCODE

& python (Join-Path $kit "tools\restore-private-system-snapshot.py") --selftest | Out-Null
Add-Step "optional-snapshot-integrity" $LASTEXITCODE

& python (Join-Path $kit "tools\verify-prime-omp-client-path.py") --selftest | Out-Null
Add-Step "prime-omp-client-path" $LASTEXITCODE

$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("orchestration-gate-" + [guid]::NewGuid().ToString("N"))
& python (Join-Path $PSScriptRoot "kit.py") build --output $stage --apply --yes | Out-Null
Add-Step "client-payload-build" $LASTEXITCODE
if ($stage.StartsWith([System.IO.Path]::GetTempPath()) -and (Test-Path -LiteralPath $stage)) {
  Remove-Item -LiteralPath $stage -Recurse -Force
}

$status = if (@($steps | Where-Object status -eq "FAIL").Count) { "FAIL" } else { "PASS" }
$result = [ordered]@{ status = $status; checks = @($steps) }
$result | ConvertTo-Json -Depth 6
if ($status -ne "PASS") { exit 2 }
exit 0
