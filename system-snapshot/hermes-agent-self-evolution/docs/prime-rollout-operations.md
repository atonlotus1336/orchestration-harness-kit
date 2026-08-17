# Prime Rollout Operations

## Authority

NUCBOX remains the only Hermes backend. The __OWNER_HANDLE__ desktop is a thin Hermes OS client
and independent verifier. Prime remains an optional execution harness behind Hermes-owned
profiles, providers, MCP, memory/RAG, approvals, audit, and native fallback.
Every Prime-backed session still requires an explicit handoff.

## Candidate upgrade sequence

1. Pin the candidate Prime source commit, immutable image digest, command fingerprint,
   RPC protocol, and bridge version. Mutable tags and unreviewed `main` are forbidden.
2. Run all six compatibility checks: RPC contract, model route, containment, capability
   boundary, profile matrix, and native Hermes. Every receipt must bind the same candidate
   fingerprint and remain within the evidence freshness window.
3. Require native Hermes health and at least the configured eligible-profile coverage.
   A missing, failed, stale, or mismatched check blocks the candidate.
4. Rehearse canary profiles first. Move to broad only after load, failure injection,
   detach/recovery, approval, native fallback, and rollback tests pass.
5. Apply profile overlays only at the restart boundary. Do not convert active sessions.

## Dashboard and admission

Use `prime_rollout_status`. READY requires independent PASS results for bridge liveness,
runtime compatibility, capability readiness, model/auth readiness, remote-client
reachability, and profile rollout. DEGRADED is not READY.

Prime admission rejects on global or per-profile saturation, stale or unknown resource
sampling, CPU pressure, or memory pressure. Rejection leaves native Hermes available.
Never raise limits merely to make a load test pass.

## Emergency disable

1. Run `scripts/prime-emergency-disable.py` without `--apply` and record the exact before
   and candidate SHA-256 values.
2. Apply only with `--apply`, the exact before hash, a unique rollback ID, and
   `--confirm DISABLE-PRIME`. This changes only central `enabled: true` to `false`.
3. Re-render overlays and restart the relevant Hermes surface. Native Hermes, global MCP,
   memory, RAG, toolsets, gateway ownership, and dashboard topology remain intact.
4. Verify `prime_rollout_status` reports DISABLED and native Hermes remains AVAILABLE.
5. If verification fails, restore the exact backup through `EmergencyController.rollback`,
   re-render at the restart boundary, and re-run native health before any Prime retry.

Never place provider credentials, dashboard tokens, SSH keys, or raw Prime traces in a
rollout receipt, command line, planning artifact, or shared learning record.
