# Activation boundary

SDK installation and adapter connection are not activation. The portable kit
ends in `installed-not-activated`; this is the safe default for every recipient.

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

All seven adapters are source-qualified and remain unactivated by the SDK. The
shared core deliberately denies production activation by default. Enabling a
recipient deployment requires a local policy overlay and profile-specific live
evidence; those recipient secrets and runtime receipts do not enter this repo.

This is the end of Phase 82's portable scope. No Phase 83 is required. Live
deployment is an operational choice after the SDK release candidate is complete.
