# Activation boundary

## Adaptive parallel switches

The optional module ships the `on`, `off`, `status`, and `refresh`
grammar with `--global` or repeatable `--harness`. Installation stays
inactive. Recipients must supply a closed registry, signer, identity adapter,
and consumer verifier. The reference CLI reports `ADAPTER_REQUIRED` and
performs zero activation changes.

SDK installation and adapter connection are not activation. The portable kit
ends in `installed-not-activated`. This is the safe default for every recipient.

## Promotion sequence

1. Install and run provider-free verification.
2. Connect exactly one isolated harness profile by reference.
3. Capture the pre-change state and a deterministic rollback reference.
4. Obtain explicit authorization for that profile, restart boundary, provider
   class, call cap, and token cap.
5. Run shadow canaries and require matched quality, call count, latency, token,
   and rollback gates.
6. Promote to review, observe the bounded window, and rehearse rollback.
7. Request separate production authorization. Do not infer it from qualification.

## Current SDK state

All eight adapters are source-qualified and remain unactivated by the SDK's
default install/connect flow. The shared core deliberately denies production
activation by default. Recipient deployments need a local policy overlay and
profile-specific live evidence. Keep recipient secrets and runtime receipts
outside this repo.

This is the end of Phase 82's portable scope, without a Phase 83. Live
deployment is an operational choice after the SDK release candidate is complete.

## Token-compute feature activation

The token-compute module has a narrower activation boundary than provider or
production activation. A recipient may activate its deterministic budgets,
evidence-gated depth, cache, rendering, and accounting controls without calling a
provider or restarting a harness. The portable activation command writes a local,
rollback-backed registry. Marginal-value stopping remains shadow-only, and weak
evidence selects full depth. This feature activation does not change the SDK's
default `installed-not-activated` connection contract or authorize a provider.

Activate the safe feature profile for every installed adapter with:

```text
python modules/token-compute-optimization/activate-token-compute.py activate --registry <recipient-data>/token-compute-activation.json --harness all --approval-reference <local-reference> --apply --yes
python modules/token-compute-optimization/activate-token-compute.py verify --registry <recipient-data>/token-compute-activation.json --require-all-active
```

This marks feature controls active, not a provider, model, service, or production
profile. Harness-native integration must consume the registry or dispatch
`optimization.plan`. The Cursor template demonstrates fail-closed request
admission through `beforeSubmitPrompt`.

## Additive parallel observation feature

The optional `modules/additive-parallel` feature ships as an **inactive**
profile. It permits a recipient-local, separately qualified mapping to the
existing core `shadow` state for at most two independent read-only child
assignments. It does not add a core activation state or modify a harness during
installation.

Before a recipient enables the local feature overlay, require a provider-free
replay of the module and feature-conformance verifiers, an isolated profile,
explicit local approval, and a rollback reference. The feature's provider and
token cap is zero. Orca may only observe a host-bound existing job. It cannot
launch, command, approve, review, complete, or poll. Rollback returns the local
overlay to inactive, retains evidence, and blocks new child batches.

V1 keeps this exact observation-only meaning. Adaptive v2 installs separately
with all five layer grants disabled and no activation command. See
[adaptive parallelism v2](ADAPTIVE-PARALLELISM.md) for staged installation,
source-parity verification, qualification boundaries, and byte-exact rollback.
