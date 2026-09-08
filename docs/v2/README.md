# Universal Orchestration SDK v2

This directory records the public design and release guidance for the v2 SDK.
The SDK keeps policy and orchestration in a shared core while harness-specific
adapters translate local capabilities into one versioned protocol.

Version: 2.0.0. The shared core and eight thin adapters include the portable
lifecycle. Each release requires fresh Windows/Linux qualification bound to
its source. Check GitHub for publication status; source files are not proof
that a release exists.
No v2 runtime activation is implied by files in this directory.

- [Provenance audit](PROVENANCE-AUDIT.md)
- [v1 compatibility](V1-COMPATIBILITY.md)
- [Ten-minute quick start](../QUICK-START.md)
- [Migration guide](MIGRATION-GUIDE.md)
- [Adapter authoring guide](ADAPTER-AUTHORING.md)
- [Compatibility matrix](COMPATIBILITY.md)
- [Provider lease and backend admission](PROVIDER-BACKEND-ADMISSION.md)
- [Release notes](RELEASE-NOTES-2.0.0.md)
- [Release checklist](RELEASE-CHECKLIST.md)
- [Activation boundary](ACTIVATION.md)
- [Universal orchestration protocol](../../modules/orchestration-protocol/SPEC.md)
- [Shared orchestration core](../../modules/orchestration-core/README.md)
- [Token-compute feature activation](../../modules/token-compute-optimization/README.md)
- [Universal harness adapter SDK](../../modules/adapter-sdk/README.md)
- [Provider-free conformance kit](../../modules/conformance-kit/README.md)
- [Hermes OS reference adapter](../../modules/adapters/hermes/README.md)
- [Reference-adapter matrix](../../modules/adapters/README.md)
- [OpenCode adapter](../../modules/adapters/opencode/README.md)
- [Pi adapter](../../modules/adapters/pi/README.md)
- [Antigravity adapter](../../modules/adapters/antigravity/README.md)
- [Codex adapter](../../modules/adapters/codex/README.md)
- [Claude Code adapter](../../modules/adapters/claude-code/README.md)
- [MOA adapter](../../modules/adapters/moa/README.md)
- [Cursor adapter](../../modules/adapters/cursor/README.md)

- [Shared goalposts and execution/review boundaries](GOALS-AND-ROUTING.md)
