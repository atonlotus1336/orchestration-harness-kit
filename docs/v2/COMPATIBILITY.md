# Compatibility matrix

All entries below are source-qualified against the shared protocol and core.
"Not activated" means the SDK's default install/connect flow proves translation
and lifecycle behavior without changing a recipient's live harness. Every row can
consume the shared provider-free token-compute profile after explicit local
activation. That feature state does not imply provider or production activation.

| Harness | Tested contract/version | Qualified range | Protocol | Qualification | Live state |
|---|---|---|---|---|---|
| Hermes OS | source contract | source contract | 1, 2 | reference adapter | not activated by SDK |
| OpenCode | 1.17.14 | `>=1.17.14,<1.17.15` | 1, 2 | source-qualified | not activated |
| Pi coding agent | 0.84.4 | `>=0.84.4,<0.84.5` | 1, 2 | source-qualified | not activated |
| Antigravity | unreported CLI build | source contract | 1, 2 | source-qualified | not activated |
| Codex | 0.147.0 | `>=0.147.0,<0.147.1` | 1, 2 | source-qualified | not activated |
| Claude Code | 2.1.211 | `>=2.1.211,<2.1.212` | 1, 2 | source-qualified | not activated |
| MOA | 0.18.0 source contract | source contract | 1, 2 | source-qualified | not activated |
| Cursor | 2026-09-03 hook/plugin source contract | source contract | 1, 2 | source-qualified | not activated |

The required `token-compute-optimization` module covers all eight adapter IDs.
Its local registry activates deterministic budget admission, context cache and
rendering, usage accounting, and evidence-gated depth with full fallback.
Marginal-value stopping remains shadow-only until the measured promotion gates
pass independently for a recipient deployment.

The machine-readable authority is
`modules/adapters/compatibility-matrix.json`. A version outside the recorded
range is unqualified until conformance is rerun and the existing qualification
owner updates the range. Source-hash and provider-terms changes also require
requalification. Windows, Linux/WSL, and Docker Linux have
provider-free clean-room qualification. macOS and live messenger/provider paths
remain separately qualified surfaces.
