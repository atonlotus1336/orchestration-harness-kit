# Compatibility matrix

All entries below are source-qualified against the shared protocol and core.
“Not activated” means the repository proves translation and lifecycle behavior
without claiming a change to a recipient's live harness.

| Harness | Tested contract/version | Protocol | Qualification | Live state |
|---|---|---|---|---|
| Hermes OS | source contract | 1, 2 | reference adapter | not activated by SDK |
| OpenCode | 1.17.14 | 1, 2 | source-qualified | not activated |
| Pi coding agent | 0.84.4 | 1, 2 | source-qualified | not activated |
| Antigravity | unreported CLI build | 1, 2 | source-qualified | not activated |
| Codex | 0.147.0 | 1, 2 | source-qualified | not activated |
| Claude Code | 2.1.211 | 1, 2 | source-qualified | not activated |
| MOA | 0.18.0 source contract | 1, 2 | source-qualified | not activated |
| Cursor | 2026-09-03 hook/plugin source contract | 1, 2 | source-qualified | not activated |

The machine-readable authority is
`modules/adapters/compatibility-matrix.json`. A newer harness version is not
automatically incompatible, but it must rerun conformance before maintainers
update the tested version. Windows, Linux/WSL, and Docker Linux have
provider-free clean-room qualification. macOS and live messenger/provider paths
remain separately qualified surfaces.
