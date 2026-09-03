# Universal orchestration protocol

**What it is:** the versioned language spoken between the LapArt shared core and
any Hermes, OpenCode, Pi, Antigravity, Codex, Claude Code, MOA, or future adapter.

**Why you would want it:** a new harness implements one translation layer instead
of rebuilding routing, safety, tools, skills, memory, retrieval, and token policy.

## Install only this part

Copy this directory beside the kit's `installer` package. Keep the operation
catalog and envelope schema together. They share the same protocol version.
No provider, service, model, or credential is configured by this module.

## Test it worked

Run:

```bash
python verify-protocol.py --selftest
```

The command validates the schema, operation coverage, ownership boundary,
credential-reference rule, fixtures, and fail-closed negative cases without a
provider call.

Read [SPEC.md](SPEC.md) before authoring an adapter.
