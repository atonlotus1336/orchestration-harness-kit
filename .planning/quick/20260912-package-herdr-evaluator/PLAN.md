# Package Herdr evaluator in private kit

Goal: package the committed Herdr evaluator as a portable, credential-free private-kit snapshot with a checksum-pinned Herdr 0.9.0 bootstrap, inactive defaults, and a verified restore/activate/rollback lifecycle.

## Sequence

1. [x] Export the committed Herdr evaluator integration and reviewer-skill dependency closure.
2. [x] Add a Herdr 0.9.0 bootstrap pinned by release tag commit and per-platform SHA-256 digests.
3. [x] Preserve portable defaults as installed separately, unconfigured, inactive, and non-runnable.
4. [x] Provide post-restore Windows configuration and Codex-only supervised activation through one lifecycle control surface.
5. [x] Qualify a clean empty-workspace restore, real pinned install, temporary activation, provider-free canary, and rollback.
6. [ ] Commit and push the Herdr-only kit update separately after the repository-required merge-update confirmation.
7. [x] Run package, privacy, syntax, restore, and no-rebuild-on-toggle gates and leave the clean qualification inactive.

## Acceptance criteria

- Snapshot contents are hash-manifested and restore without credentials, binaries, runtime state, or machine identity.
- The official Herdr 0.9.0 release is installed only by an explicit `--apply --yes` operation after SHA-256 verification.
- Restoration leaves all nine harnesses off; activation targets Codex only; rollback returns every harness to off.
- Installation and reviewer configuration persist across toggles; binary digest, size, and mtime do not change.
- No provider/model call occurs during qualification.

## Evidence

- `CLEAN-INSTALL-QUALIFICATION.json`
- `system-snapshot/SNAPSHOT-MANIFEST.json`
- `tools/test-herdr-snapshot-selection.py`
- `system-snapshot/hermes-agent-self-evolution/tests/integration/test_herdr_bootstrap.py`
