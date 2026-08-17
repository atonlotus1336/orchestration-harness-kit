Set-StrictMode -Version 2.0

function Get-AutoExperimentTreeHash {
  param([Parameter(Mandatory = $true)][string]$Root)
  $resolved = [System.IO.Path]::GetFullPath($Root)
  $map = @{}
  foreach ($file in @(Get-ChildItem -LiteralPath $resolved -Recurse -File -Force | Where-Object { $_.Name -ne '.autoexperiment-sandbox.json' })) {
    if ($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw "Reparse-point file is not sandbox-safe: $($file.FullName)" }
    $relative = $file.FullName.Substring($resolved.TrimEnd('\').Length).TrimStart('\').Replace('\', '/')
    $map[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
  }
  return $map
}

function New-AutoExperimentSandbox {
  param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$SandboxRoot
  )
  $sourceFull = [System.IO.Path]::GetFullPath($Source).TrimEnd('\')
  $rootFull = [System.IO.Path]::GetFullPath($SandboxRoot).TrimEnd('\')
  if (-not (Test-Path -LiteralPath $sourceFull -PathType Container)) { throw "Sandbox source is not a directory: $sourceFull" }
  if ($rootFull -eq $sourceFull -or $rootFull.StartsWith($sourceFull + '\')) { throw 'Sandbox root cannot be the source or a descendant of it.' }
  if (@(Get-ChildItem -LiteralPath $sourceFull -Recurse -Force | Where-Object { $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint }).Count) {
    throw 'Sandbox sources containing reparse points are rejected.'
  }
  New-Item -ItemType Directory -Force -Path $rootFull | Out-Null
  $id = 'sandbox-' + [guid]::NewGuid().ToString('N')
  $path = Join-Path $rootFull $id
  New-Item -ItemType Directory -Path $path | Out-Null
  Copy-Item -Path (Join-Path $sourceFull '*') -Destination $path -Recurse -Force
  [pscustomobject]@{ id = $id; source = $sourceFull; root = $rootFull; path = $path; created_at = (Get-Date -Format o) } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $path '.autoexperiment-sandbox.json') -Encoding UTF8
  return $path
}

function Compare-AutoExperimentTrees {
  param([string]$BeforeRoot, [string]$AfterRoot)
  $before = Get-AutoExperimentTreeHash $BeforeRoot
  $after = Get-AutoExperimentTreeHash $AfterRoot
  $keys = @($before.Keys + $after.Keys | Sort-Object -Unique)
  return @($keys | Where-Object { -not $before.ContainsKey($_) -or -not $after.ContainsKey($_) -or $before[$_] -ne $after[$_] })
}

function Remove-AutoExperimentSandbox {
  param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$SandboxRoot)
  $pathFull = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
  $rootFull = [System.IO.Path]::GetFullPath($SandboxRoot).TrimEnd('\')
  if (-not $pathFull.StartsWith($rootFull + '\')) { throw "Refusing cleanup outside sandbox root: $pathFull" }
  if (-not (Test-Path -LiteralPath (Join-Path $pathFull '.autoexperiment-sandbox.json') -PathType Leaf)) { throw "Refusing cleanup without sandbox marker: $pathFull" }
  Remove-Item -LiteralPath $pathFull -Recurse -Force
}
