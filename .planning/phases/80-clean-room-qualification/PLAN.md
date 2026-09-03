# Phase 80 — Clean-Room Portability Qualification

## Goal

Prove from local clean Git clones that the same provider-free SDK installs,
connects, verifies, upgrades, rolls back, and uninstalls on native Windows and a
Linux runtime without changing any live harness.

## Dependencies

- Phase 79 commit `6b73fac`
- v1 rollback commit `04b7630937557c2dd4e6b429fc033873d5edf127`
- native Windows Python and Git
- Linux Python and Git; no external packages required

## Coding route

The deterministic governed evaluator selected actual lane T3 for the three
declared Phase 80 paths. `--execution-value-only` made no provider call.

## Delivery waves

1. Commit the provider-free cross-platform qualification runner and unit tests.
2. Run that exact commit from a clean Windows clone.
3. Run that exact commit from a clean Linux/WSL clone.
4. Compare artifact hashes and bind both receipts to the current commit.
5. Commit the target-local evidence separately, preserving any HOLD truthfully.

## Acceptance criteria

1. Source must be a clean local Git checkout at an explicit full commit hash.
2. The runner must use a detached, no-hardlink local clone and no network source.
3. All current source verifiers must pass from the clone.
4. Two payload builds and two offline bundles must be byte-reproducible.
5. Install, doctor, and aggregate provider-free verification must pass.
6. All seven harness connection descriptors must remain non-activated and each
   adapter verification must report zero provider calls.
7. v1 install → v2 upgrade → v2 verify → v1 rollback → v1 doctor must pass while
   preserving recipient-owned data.
8. Managed uninstall must preserve the data root and unmanaged files.
9. Windows and Linux receipts must name the same source commit and artifact hashes.

## Threat model

- a dirty source tree could make receipts unreproducible
- hardlinked clones could mutate or depend on the source object store
- a platform check could silently use a provider or active harness
- an upgrade check could validate v2 without proving v1 rollback
- logs or receipts could expose local paths or identity
- matching file counts could conceal artifact-content drift

## Rollback, cost, and activation impact

The first commit only adds a maintainer qualification tool and tests. All clean
clones, installs, bundles, and data stores are disposable temporary fixtures.

- Provider calls and tokens: 0
- Pay-per-token and DSH calls: 0
- Service restarts: 0
- Live configuration, model, provider, or activation changes: 0
- Network fetches, tags, pushes, and default-branch merges: 0
