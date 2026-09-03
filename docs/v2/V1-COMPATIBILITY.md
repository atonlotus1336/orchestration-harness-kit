# v1 Preservation and v2 Compatibility

The published v1 baseline remains addressable by its full Git commit identifier in
the version policy. The proposed `v1.0.0` tag is intentionally blocked: the source
history contains a machine-identity fallback that the v2 merge removed. Tagging
that commit as release-ready would misstate its source privacy status.

This does not erase or rewrite v1. The immutable commit remains the rollback
reference, and the clean allowlisted v1 distribution remains reproducible. Reconsider
the tag only after the release process can distinguish historical
source provenance from a sanitized client bundle without weakening either scan.

## Compatibility rules

- v2 retains every v1 installer command and config schema 1.
- Protocol generation 1 remains supported beside generation 2.
- Negotiation selects the highest mutually supported generation.
- No shared generation produces a structured incompatibility error. It never
  silently selects an unadvertised version.
- Upgrade and rollback may change only files recorded in the managed-file ledger.
- Recipient data, credentials, and harness-local state remain outside that ledger.
- Breaking contract changes require a new major protocol generation.

## Privacy classification

The historical repository scan reported 20 findings:

| Count | Classification | Reason |
|---:|---|---|
| 1 | EXCLUDE | Synthetic credential-detection fixture outside the payload allowlist |
| 17 | EXCLUDE | Private snapshot tree forbidden by the payload contract |
| 2 | EXCLUDE | Non-distributed RAG implementation outside the current payload |

The public-payload scan evaluates the allowlisted distribution separately and must
pass. The repository scan remains available and continues to fail closed. The v2
merge also removes the actual machine-specific fallback found by the identity
tripwire.
