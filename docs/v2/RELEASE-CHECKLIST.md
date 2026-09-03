# v2 release checklist

The candidate is releasable only when every applicable item is evidenced. A HOLD
must remain visible; it cannot be converted to PASS by editing an old receipt.

## Candidate qualification

- [ ] Worktree is clean and the source commit is recorded.
- [ ] Full provider-free test and verifier matrix passes.
- [ ] Windows and Linux/WSL clean clones produce identical payload and ZIP bytes.
- [ ] Public-payload privacy scan has zero findings.
- [ ] Git-history privacy and commit metadata have been reviewed separately.
- [ ] Release notes, migration, compatibility, adapter-authoring, and rollback
      documentation match the shipped bytes.
- [ ] Runtime version, compatibility matrix, and intended tag agree.
- [ ] No provider, model, default, service, or activation changed during build.

## Publication gate

- [ ] Repository visibility is an intentional decision.
- [ ] If publishing source history, every historical privacy hold is resolved or
      the history is replaced by an approved clean public lineage.
- [ ] If publishing the client artifact, it was rebuilt from the qualified commit
      and its SHA-256 matches the release receipt.
- [ ] Tag, push, release creation, and visibility change have separate approval.

## Activation gate

- [ ] One isolated profile is named; no cross-profile mutation is implied.
- [ ] Approval names provider class, call/token caps, and restart boundary.
- [ ] Quality, call count, latency, token, and rollback gates all pass.
- [ ] Pay-per-token use and default-model changes are explicitly authorized or
      remain forbidden.
- [ ] Production follows shadow/review evidence and has its own authorization.
