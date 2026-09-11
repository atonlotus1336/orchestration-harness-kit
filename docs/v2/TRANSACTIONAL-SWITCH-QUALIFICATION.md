# Transactional switch qualification

Qualification date: 2026-09-11

Decision: PASS for portable, recipient-configured activation and deactivation.
Fresh installation remains inactive. No live Codex, Hermes, provider, model,
service, credential, or production profile was changed by this qualification.

## Proven invariants

- Activation and deactivation use the same prepare, commit, verify,
  paper-state, and finalize transaction.
- A partial activation or deactivation compensates every selected adapter.
- The exact prior paper-state bytes are restored after a failed operation.
- `FAILED_ROLLED_BACK` is emitted only after adapter state and paper bytes are
  verified as restored.
- Adapter or paper restoration uncertainty emits `ROLLBACK_INCOMPLETE` and
  exposes the mismatch as `INVALID`.
- Disabled adapters are verified, so an externally active but paper-disabled
  harness cannot be reported as inactive.
- The CLI operates recipient-supplied adapters and exits nonzero when adapters
  are absent or a transaction is not successful.
- Stage, upgrade, rollback, uninstall, and uninstall rollback include both
  switch files while preserving recipient-local files.

## Evidence

- Targeted adaptive and activation tests: 25 passed.
- Broad provider-free installer suite: 489 passed, 7 skipped, 1 deselected.
- Additive feature conformance: PASS.
- Adaptive canonical-source parity: PASS.
- Lifecycle verifier: PASS for install, connect, doctor, verify, upgrade,
  rollback, and uninstall.
- Public-payload privacy gate: PASS with zero findings.
- Provider calls: 0. Provider tokens: 0. Activation changes: 0.

The one deselected test was
`test_git_clone_prime_omp_client_path`. Its known failure is caused by
pre-existing, unrelated modified files under the private system snapshot; those
files were not changed or included in this correction.
