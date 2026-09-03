# Phase 70 — v1 Preservation and v2 Compatibility

## Goal

Preserve the published v1 behavior as an immutable rollback reference while
defining additive v2 negotiation and migration rules.

## Dependencies

- Phase 69 provenance map and ordinary history merge
- Published baseline commit available in local Git history
- Existing installer and managed-file ledger

## Source and output files

- Source: `installer/kit.py`, the v1 Git baseline, and public payload contract
- Output: version policy, compatibility guide, verifier, and focused tests

## Acceptance criteria

1. The exact v1 commit and proposed semantic-version tag appear in policy.
2. Existing v1 commands remain a subset of the current CLI.
3. Config schema 1 and protocol generation 1 remain supported.
4. v2 negotiation is additive and fails closed on unsupported versions.
5. Repository-wide and public-payload privacy scans stay distinct.
6. The public payload privacy scan passes without excluding selected files after
   a finding.

## Threat model

- A tag could falsely bless source history containing machine identity.
- An upgrade could remove a v1 command or silently rewrite recipient data.
- A broad scanner exclusion could conceal a selected unsafe file.
- A v2 client could silently negotiate an unsupported protocol.

## Rollback

Keep the published baseline addressable by its full commit identifier. Do not
create its proposed tag while the historical-source identity gate remains blocked.
Future install rollback must operate only through the managed-file ledger.

## Budget and activation

- Provider calls: 0
- Token budget: 0 provider tokens
- Service restarts: 0
- Activation impact: none
