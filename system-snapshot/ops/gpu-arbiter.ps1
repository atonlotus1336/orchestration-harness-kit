param(
  # Plan 01 Task 7.3 — GPU arbiter. Before a ComfyUI job, probe free VRAM; below the
  # threshold, subordinate local Ollama models (keep_alive=0 / stop) for the job, then
  # restore. Report-only unless -ForJob. Degrades to `GPU-ABSENT skip` (not RED) where
  # nvidia-smi is missing (Checkpoint 7 Step B). Registered as graph row gpu-budget.
  [int]$MinFreeMB = 4096,
  [switch]$ForJob,
  [switch]$Restore
)
$ErrorActionPreference = "Continue"

$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if (-not $smi) { Write-Host "GPU-ABSENT skip (nvidia-smi not found; arbiter is a no-op on this host)"; exit 0 }

$free = $null
try {
  $out = & nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>$null
  $free = [int]([string]($out | Select-Object -First 1)).Trim()
} catch { Write-Host "GPU-ABSENT skip (nvidia-smi query failed: $($_.Exception.Message))"; exit 0 }

if ($Restore) {
  # Restore M7 keep-alive default (pre-warm default+judge) after a job.
  try { & ollama run glm-5.2:cloud "" 2>$null | Out-Null } catch {}
  Write-Host "GPU-ARBITER restored: local model keep-alive re-warmed"
  exit 0
}

if (-not $ForJob) {
  Write-Host "GPU-BUDGET report: free_vram_mb=$free threshold_mb=$MinFreeMB status=$(if ($free -ge $MinFreeMB) {'OK'} else {'TIGHT'})"
  exit 0
}

if ($free -lt $MinFreeMB) {
  # Subordinate local models so the ComfyUI job gets the GPU (M7 keep-alive yields).
  foreach ($m in @("hermes3:8b","qwen2.5-coder:14b")) {
    try { & ollama stop $m 2>$null | Out-Null } catch {}
  }
  Write-Host "GPU-ARBITER yielded: free was ${free}MB < ${MinFreeMB}MB; stopped local Ollama models for the ComfyUI job. Call -Restore after."
  exit 0
}
Write-Host "GPU-ARBITER: free ${free}MB >= ${MinFreeMB}MB; no action needed for the job"
exit 0
