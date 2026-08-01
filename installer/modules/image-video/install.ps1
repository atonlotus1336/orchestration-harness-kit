param(
  # M14 image-video module installer (kit-shareable). GPU-gated opt-in:
  #   no NVIDIA GPU  -> SKIPPED_NO_GPU, exit 0 (skip-with-note; never hangs, never fails)
  #   GPU present    -> clone ComfyUI + venv + reference workflows into -TargetDir
  # Test hook: M14_FORCE_GPU=1 treats the GPU as present (logic testing only).
  [string]$TargetDir = ".\comfyui",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$gpuName = ""
if ($env:M14_FORCE_GPU -eq "1") {
  $gpuName = "FORCED (M14_FORCE_GPU=1 - test mode)"
} elseif (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
  $gpuName = [string](& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1)
}

if (-not $gpuName) {
  Write-Host "IMAGE-VIDEO: SKIPPED_NO_GPU - no NVIDIA GPU detected; this optional module needs a discrete GPU. Core install is unaffected."
  exit 0
}

$plan = @(
  "1. git clone https://github.com/comfyanonymous/ComfyUI '$TargetDir'",
  "2. python -m venv '$TargetDir\venv' && install requirements.txt (torch per YOUR cuda version - see ComfyUI README)",
  "3. copy reference workflows -> '$TargetDir\user\default\workflows\'",
  "4. add comfyui_endpoint/comfyui_models_root slots to bindings.local.yaml",
  "5. supply your own model checkpoints under '$TargetDir\models\checkpoints' (kit ships none)"
)
if ($DryRun) {
  Write-Host "IMAGE-VIDEO: DRYRUN_OK gpu=$gpuName"
  $plan | ForEach-Object { Write-Host "  $_" }
  exit 0
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Host "IMAGE-VIDEO: FAILED - git is required for the GPU install path"
  exit 1
}
if (-not (Test-Path -LiteralPath $TargetDir)) {
  & git clone --depth 1 https://github.com/comfyanonymous/ComfyUI $TargetDir
  if ($LASTEXITCODE -ne 0) { Write-Host "IMAGE-VIDEO: FAILED - git clone exited $LASTEXITCODE"; exit 1 }
}
& python -m venv (Join-Path $TargetDir "venv")
Write-Host "IMAGE-VIDEO: BASE_INSTALLED gpu=$gpuName - now install torch for YOUR CUDA version, then: pip install -r requirements.txt"
$wfDir = Join-Path $TargetDir "user\default\workflows"
New-Item -ItemType Directory -Force -Path $wfDir | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot "workflows\*") -Destination $wfDir -Force
Write-Host "IMAGE-VIDEO: REFERENCE_WORKFLOWS_PLACED -> $wfDir"
exit 0
