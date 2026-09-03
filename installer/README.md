# installer — the client-safe staged installer

**What it is:** the deterministic client path. It installs a small core first and
keeps external services optional. Commands are dry-run by default and require both
`--apply` and `--yes` before writing.

**Why you'd want it:** if you're replicating the complete system (memory + RAG +
doctrine + modules) onto a new machine, staged-with-verification beats "run twenty
commands and hope." Every stage leaves evidence; a failed stage names itself.

## What's inside

- `preflight/` — environment checks (per-OS) that run before anything installs.
- `modules/` — the installable stack pieces, each with its own README:
  - `memory/` — the memory engine (see also `../system/`)
  - `rag/` — RAG backend wiring
  - `harness/` — per-harness (Claude/Codex/Hermes) configuration
  - `hermes-snapshot/` — a *pinned snapshot* of the Hermes agent setup (versioned
    deliberately — it tracks a known-good state, not a moving target)
  - `../../modules/topology/` — required solo-PC role contract; VPS and
    second-PC profiles are optional and disabled when rendered
  - `../../system-snapshot/` — required private implementation snapshot for
    Prime/OMP, Hermes Telegram, auto-learning, maintenance, autoexperiment,
    backup, and restore operations; verified by SHA-256 before restoration
  - `image-video/` — scaffolding for wiring your own ComfyUI (structure only;
    no models ship in this kit)
  - `prime-omp/` — opt-in Prime Agent and oh-my-pi worker contract; Hermes
    retains final patch and verification authority
  - `verified-maintenance/` — opt-in backup-gated daily, weekly, and monthly
    maintenance policy; it is report-only until a human acts
  - `../../modules/messaging/TELEGRAM.md` — optional direct Telegram setup
  - `module-manifest.json` — what's installable, with per-module exclusions
- `gate.ps1` — the stage-by-stage verifier.
- `clone-eval/` — fixture tasks that prove an installed clone actually *works*
  (can it fix code? operate on files?), not just that files landed.
- `coverage/DECLARED-COVERAGE.md` — honest statement of what the installer does
  and doesn't verify.

## Use

Follow `../docs/QUICK-START.md`. The portable implementation is `kit.py`; the
PowerShell and shell files are thin entrypoints. Its lifecycle is:

`install` → `connect` → `doctor` → `verify` → `upgrade` / `rollback` → `uninstall`

Every mutating command is dry-run unless both `--apply` and `--yes` are present.
`connect` writes reference-only local descriptors and never activates a harness.
`verify` is provider-free. Upgrade snapshots the exact managed state and rolls it
back automatically on failure. Uninstall preserves the separate data root and
unmanaged recipient files.
