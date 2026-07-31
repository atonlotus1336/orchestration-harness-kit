#!/usr/bin/env bash
# M14 image-video module installer (POSIX twin). GPU-gated opt-in:
#   no NVIDIA GPU -> SKIPPED_NO_GPU exit 0 (skip-with-note) | GPU -> ComfyUI base install.
# Test hook: M14_FORCE_GPU=1 treats the GPU as present (logic testing only).
set -u
TARGET="${1:-./comfyui}"
DRYRUN="${2:-}"

GPU=""
if [ "${M14_FORCE_GPU:-}" = "1" ]; then
  GPU="FORCED (M14_FORCE_GPU=1 - test mode)"
elif command -v nvidia-smi >/dev/null 2>&1; then
  GPU="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
fi

if [ -z "$GPU" ]; then
  echo "IMAGE-VIDEO: SKIPPED_NO_GPU - no NVIDIA GPU detected; this optional module needs a discrete GPU. Core install is unaffected."
  exit 0
fi

if [ "$DRYRUN" = "--dry-run" ]; then
  echo "IMAGE-VIDEO: DRYRUN_OK gpu=$GPU"
  echo "  1. git clone https://github.com/comfyanonymous/ComfyUI '$TARGET'"
  echo "  2. python3 -m venv '$TARGET/venv' && pip install torch (per YOUR cuda) + -r requirements.txt"
  echo "  3. copy reference workflows -> '$TARGET/user/default/workflows/'"
  echo "  4. add comfyui_endpoint/comfyui_models_root slots to bindings.local.yaml"
  echo "  5. supply your own checkpoints under '$TARGET/models/checkpoints' (kit ships none)"
  exit 0
fi

command -v git >/dev/null 2>&1 || { echo "IMAGE-VIDEO: FAILED - git required"; exit 1; }
[ -d "$TARGET" ] || git clone --depth 1 https://github.com/comfyanonymous/ComfyUI "$TARGET" || { echo "IMAGE-VIDEO: FAILED - clone"; exit 1; }
python3 -m venv "$TARGET/venv"
mkdir -p "$TARGET/user/default/workflows"
cp "$(dirname "$0")/workflows/"* "$TARGET/user/default/workflows/" 2>/dev/null || true
echo "IMAGE-VIDEO: BASE_INSTALLED gpu=$GPU - install torch for YOUR CUDA, then: pip install -r requirements.txt"
echo "IMAGE-VIDEO: REFERENCE_WORKFLOWS_PLACED -> $TARGET/user/default/workflows"
exit 0
