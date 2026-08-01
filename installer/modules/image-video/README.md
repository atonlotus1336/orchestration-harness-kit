# image-video module (M14, opt-in tier)

Adds ComfyUI-based image/video generation to the orchestration stack. This module is
**optional and GPU-gated**: the installer runs only when the M11 preflight (or its own
check) finds an NVIDIA GPU; on a no-GPU machine it **skips with a note and exit 0** —
it never hangs and never fails your install.

What ships here:
- `install.ps1` / `install.sh` — GPU-gated installer (clone ComfyUI, set up venv,
  place reference workflows). Supports `-DryRun` to print the plan without changes.
- `workflows/` — a REFERENCE workflow set (generic, public-knowledge graphs). Your
  own models, LoRAs, and private workflows are NEVER part of this kit; you supply
  checkpoints yourself.

What stays yours (local-only, via `bindings.local.yaml`):
```yaml
slots:
  comfyui_endpoint: "http://127.0.0.1:8188"   # where YOUR ComfyUI listens
  comfyui_models_root: "/path/to/ComfyUI/models"
  comfyui_models_manifest: "/path/to/models-manifest.json"
```

Health pattern (optional): register ComfyUI as a `severity: degrade` surface in your
surface graph — generation being down should inform you, not fail your core-parity
gate. A reference staged probe ships with the source system as
`scripts/probe-comfyui.ps1` (server check + models-manifest compare; it hashes the
manifest, never multi-GB model blobs).

GPU note: integrated GPUs (e.g. AMD APUs) are treated as no-GPU for this module —
practical ComfyUI throughput needs a discrete NVIDIA card. Force the install logic
without a GPU (testing only) with `M14_FORCE_GPU=1`.
