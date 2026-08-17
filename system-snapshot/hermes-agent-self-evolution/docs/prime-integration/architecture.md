# Prime Integration Architecture Contract

## Status and provenance

This Phase 1 contract targets the official
`https://github.com/PrimeIntellect-ai/prime-agent` source at release `v0.7.1`, commit
`95afd319a78ae017a41241d50b013d656a0685ce`. The immutable Git commit identifier is
the source-integrity value (`git:sha1:95afd319a78ae017a41241d50b013d656a0685ce`).
Runtime enablement additionally requires the installed artifact checksum reported by
the read-only installation checker. A different release, commit, origin, or artifact
must pass this phase's compatibility gate before use. Tracking `main`, accepting an
unofficial fork, and unattended updates are forbidden.

## Authority boundary

There is exactly one Hermes backend for each existing surface. Prime is an optional
execution worker behind Hermes; it is not a Hermes provider, a replacement gateway,
or a second control plane. G-managed Hermes profile sources remain authoritative for
providers, model policy, MCP servers, memory, toolsets, platform tools, approvals, and
audit. Prime may receive only a sanitized, transient projection of that policy.

Prime calls the resolved underlying Ollama/OpenAI-compatible inference endpoint.
It must never call the Hermes gateway on port `8642`, the loopback dashboard on port
`9119`, or any Hermes agent API as its model endpoint. This prevents agent-to-agent
inference recursion and preserves the existing tailnet/SSH topology.

Phase 1 grants no direct MCP access, shared-memory write, profile-secret read, profile
apply, service restart, or gateway ownership change. Its probe runs in a disposable
directory with a reduced environment and no production repository path.

## Execution modes

- **Native:** Hermes owns and executes the session. This remains available at all
  times and is the fail-closed path when Prime is disabled, unavailable, incompatible,
  rate-limited, or fails.
- **Delegated:** Hermes explicitly hands one bounded job to a Prime worker, observes
  its versioned RPC events, and collects the result. Phase 1 proves only this contract.
- **Prime-backed session:** A future mode in which Prime may drive a selected Hermes
  session while Hermes retains UI, policy, capability, and audit authority. It is not
  implemented or enabled by Phase 1.

No existing session changes mode automatically. Integration activation is a restart
boundary: configuration discovered or prepared while Hermes is running takes effect
only after the relevant Hermes surface is closed and reopened, or through an
explicitly documented supported reload in a later phase.

## Versioned job and RPC contract

`contracts/prime-bridge.schema.json` is contract version `1.0`. Each job carries an
immutable job ID, profile, project, disposable workspace, lifecycle state and owner,
RPC version, model route, negotiated capabilities, pinned upstream provenance, native
fallback, and disable state. The RPC wire contract is `prime-rpc-v1`, strict JSONL
framed by LF only. Every request carries a correlation ID; responses must echo it and
match the requested command. Unexpected frames, EOF, or timeouts fail the job closed.

Lifecycle transitions are monotonic:

```text
created -> starting -> running -> completed
                         |  |--> failed
                         |-----> cancelled
                         `-----> orphaned (detected, never adopted silently)
```

The bridge terminates only the exact child it created and verifies cleanup. A job may
not silently select another provider or model. Any future explicit fallback must be
present in the input policy and audited; Phase 1 falls back only to native Hermes.

## Model and capability contracts

`contracts/prime-model-route.schema.json` accepts sanitized Ollama routes using the
OpenAI-compatible `openai-completions` API. It carries compatibility evidence and only
an environment-variable reference for credentials. Literal credentials, `.env`
contents, missing model or limit metadata, and Hermes agent/dashboard endpoints are
invalid.

`contracts/prime-capabilities.schema.json` records the exact runtime version, commit,
origin, integrity value, RPC surface, and Phase 1 restrictions. Capabilities are
negotiated from observed RPC behavior, not assumed from a version string.

## Emergency disable and updates

The integration has two independent fail-closed controls:

- a global disable makes Prime unavailable to every profile;
- a per-profile disable makes Prime unavailable to that profile even when globally
  enabled.

Either disable routes new work to native Hermes. It does not kill or convert unrelated
native sessions. A later supervisor must define bounded cancellation for already
running Prime jobs.

Updates require review of official provenance, an immutable version/commit, artifact
integrity, contract tests, model compatibility, RPC completion and cleanup, secret
redaction, and unchanged before/after Hermes baselines. Only a human-reviewed passing
report can authorize the next integration phase.
