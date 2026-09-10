# v2 release checklist

Release requires evidence for every applicable item. Keep each HOLD visible.
Never edit an old receipt to convert HOLD to PASS.

## Candidate qualification

- [ ] The worktree is clean and the receipt identifies the source commit.
- [ ] Full provider-free test and verifier matrix passes.
- [ ] Windows and Linux/WSL clean clones produce identical payload and ZIP bytes.
- [ ] Public-payload privacy scan has zero findings.
- [ ] A separate review covers Git-history privacy and commit metadata.
- [ ] Release notes, migration, compatibility, adapter-authoring, and rollback
      documentation match the shipped bytes.
- [ ] Runtime version, compatibility matrix, and intended tag agree.
- [ ] No provider, model, default, service, or activation changed during build.
- [ ] `LICENSE`, `PRIVATE-DISTRIBUTION.md`, and `THIRD-PARTY-NOTICES.md` are
      present and included in the allowlisted recipient payload.
- [ ] Third-party notices and the transitive license inventory are reconciled
      for every built image, executable, or dependency bundle being delivered.

## Publication gate

- [ ] Repository visibility is private; any exception requires a new legal and
      source-history review plus explicit authorization.
- [ ] The recipient, authorization, artifact digest, and delivery date are
      recorded. Confidential sharing uses a separate recipient agreement.
- [ ] Before making source history public, resolve every historical privacy hold
      or use an approved clean public lineage.
- [ ] Build the client artifact from the qualified commit and confirm that its
      SHA-256 matches the release receipt.
- [ ] Tag, push, release creation, and visibility change have separate approval.

## Activation gate

- [ ] Approval names one isolated profile and permits no cross-profile mutation.
- [ ] Approval names provider class, call/token caps, and restart boundary.
- [ ] Quality, call count, latency, token, and rollback gates all pass.
- [ ] Explicit authorization permits any pay-per-token use or default-model
      change. Otherwise, both remain forbidden.
- [ ] Production follows shadow/review evidence and has its own authorization.

## Current release qualification commands

Use `tools/qualify-clean-room.py --working-tree` to test a source snapshot before
commit approval. Run it on Windows and Linux/WSL with the same full HEAD value,
`--repository`, `--platform-label`, and an external `--output` receipt path.
This covers all eight adapters, including Cursor, and makes no provider calls.

After the approved commit, rerun without `--working-tree` on both platforms.
Run `tools/verify-release-readiness.py --receipt <windows-receipt>
--receipt <linux-receipt> --require-committed --visibility private`.
The gate rejects stale sources, missing adapters, mismatched artifacts, dirty
checkouts, and working-tree receipts used for publication. Run
`python tools/test-release-readiness.py` to replay synthetic failing evidence.

The internal name `public-payload` is a legacy technical identifier for the
allowlisted recipient payload; it does not grant permission to publish.

The existing Phase 80 receipts remain historical evidence. They do not qualify
the current release. Keeping the repository private does not clear the separate
public-history hold. A public repository change requires history review.
