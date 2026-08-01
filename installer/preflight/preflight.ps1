param(
  [switch]$Json
)

$ErrorActionPreference = "Continue"

function Test-Cmd($Name) {
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($cmd) { return [string]$cmd.Source }
  return $null
}

function New-Check($Name, $Required, $Status, $Detail) {
  [ordered]@{
    name = $Name
    required = [bool]$Required
    status = $Status
    detail = $Detail
  }
}

$checks = New-Object System.Collections.Generic.List[object]
$checks.Add((New-Check "os" $true "PASS" ([System.Environment]::OSVersion.Platform.ToString())))
$checks.Add((New-Check "powershell" $true "PASS" ($PSVersionTable.PSVersion.ToString())))

$python = Test-Cmd "python"
$pyStatus = "FAIL"
$pyDetail = "python not found"
if ($python) {
  $version = (& python --version 2>&1)
  $pyStatus = if ($version -match 'Python 3\.(1[1-9]|[2-9][0-9])\.') { "PASS" } else { "FAIL" }
  $pyDetail = "$version at $python"
}
$checks.Add((New-Check "python3.11+" $true $pyStatus $pyDetail))

$node = Test-Cmd "node"
$checks.Add((New-Check "node" $true $(if ($node) { "PASS" } else { "FAIL" }) $(if ($node) { (& node --version 2>&1) } else { "node not found" })))

$ollama = Test-Cmd "ollama"
$checks.Add((New-Check "ollama" $false $(if ($ollama) { "PASS" } else { "DEGRADED" }) $(if ($ollama) { (& ollama --version 2>&1) } else { "ollama not found; local model stages degrade" })))

$wsl = Test-Cmd "wsl"
$checks.Add((New-Check "wsl" $false $(if ($wsl) { "PASS" } else { "DEGRADED" }) $(if ($wsl) { "wsl available" } else { "wsl not found; Linux recipe must run elsewhere" })))

$gpu = "DEGRADED"
$gpuDetail = "no NVIDIA GPU detected"
try {
  $nvidia = Test-Cmd "nvidia-smi"
  if ($nvidia) {
    $gpu = "PASS"
    $gpuDetail = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1)
  }
} catch {}
$checks.Add((New-Check "gpu" $false $gpu $gpuDetail))

$requiredFailures = @($checks | Where-Object { $_.required -and $_.status -ne "PASS" })
$status = if ($requiredFailures.Count -eq 0) { "PASS" } else { "FAIL" }
$result = @{}
$result["status"] = $status
$result["checks"] = [object[]]$checks.ToArray()
$result["required_failures"] = [object[]]@($requiredFailures | ForEach-Object { $_.name })

if ($Json) { $result | ConvertTo-Json -Depth 8 } else { $result }
if ($requiredFailures.Count -ne 0) { exit 2 }
exit 0
