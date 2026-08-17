# OMP-Prime Coding Runbook

**Phase 10 — Prime-Governed OMP Coding Capabilities**
**Owner:** NUCBOX/WSL (rootless Podman). **__OWNER_HANDLE__ desktop:** thin client only.

## 1. Topology

- Hermes is the sole orchestration, provider, memory, approval, audit, and learning authority.
- OMP is a **sibling rootless Podman container** launched by the Hermes host supervisor through the capability broker.
- Prime's container never receives a Podman socket and never creates a nested container.
- The authoritative repository is **read-only** to OMP. Worker writes occur only in an ephemeral worktree/overlay.
- OMP exports a patch and evidence bundle; **Hermes validates and applies** the patch, then runs approved verification commands.

## 2. Locked call graph

```
Hermes request
  -> closed code_task classifier
  -> Prime controller
  -> Hermes capability broker
  -> one OMP mode
       A. direct adapter: read-only LSP or hash-edit preview
       B. transient OMP worker in a disposable worktree
  -> typed evidence returned to Prime
  -> Hermes independently applies/verifies any patch
  -> existing Prime learning and Hermes promotion lanes
```

Forbidden edges (all closed): OMP cannot call Prime, Hermes delegation, another OMP worker, OMP subagents, MCP discovery, shared memory, or an alternate model provider.

## 3. Runtime lock

- `config/omp-runtime-lock.json` pins `@oh-my-pi/pi-coding-agent@17.3.0`, Bun `>=1.3.14`, source commit `326d24bd40d9858e24e1036ae739c27c72eeb543`, license MIT, image digest, and SBOM hash.
- The image `localhost/hermes-omp-sidecar:phase10-a` is built from `containers/omp/Dockerfile`.
- SBOM: `containers/omp/sbom.spdx.json`.

## 4. Status and cancel

- `PrimeSupervisor.omp_status(child_job_id)` — read child job status.
- `PrimeSupervisor.omp_cancel(child_job_id)` — cancel a child OMP job.
- `PrimeSupervisor.omp_result(child_job_id)` — read a terminal result.

## 5. Evidence locations

- Feasibility verdict: `.planning/phases/10-prime-governed-omp-coding/evidence/omp-feasibility.json`
- Runtime lock: `config/omp-runtime-lock.json`
- LSP registry: `config/omp-lsp-servers.json`
- Test command policy: `config/omp-test-command-policy.json`

## 6. Update pin procedure

1. Verify the new OMP version's npm integrity and source commit.
2. Update `config/omp-runtime-lock.json` (package version, integrity, source commit, license).
3. Rebuild the image: `podman build -t localhost/hermes-omp-sidecar:phase10-a -f containers/omp/Dockerfile .`
4. Regenerate the SBOM: `python scripts/omp/generate-omp-sbom.py`
5. Update the image digest and SBOM hash in the lock.
6. Re-run the feasibility probe and all Plan 01–06 tests.

## 7. Emergency disable

- Set `"enabled": false` in `config/omp-coding-policy.json` (global) or the per-profile policy.
- The capability broker will reject all OMP aliases (`omp.lsp.read`, `omp.hash_edit.preview`).

## 8. Cleanup and recovery

- Worker cleanup is verified via `OmpWorktree.cleanup()` and the ledger's child-job lifecycle.
- On any mismatch or failed required test, Hermes reverts only the broker-applied patch and returns `VERIFIED_FAILED`.
- Recovery uses the durable child-job ledger (`child_jobs` table) bound to the parent Prime job.

## 9. NUCBOX ownership and __OWNER_HANDLE__ thin-client behavior

- NUCBOX/WSL owns the runtime and launches OMP containers.
- __OWNER_HANDLE__ desktop displays gateway-issued status only; it never launches OMP locally.
- The desktop renders server-issued executor states (`omp_adapter:lsp`, `omp_adapter:hash_edit`, `omp_worker`) and performs no local classification or launch.
