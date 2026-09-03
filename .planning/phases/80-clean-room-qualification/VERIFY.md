# Phase 80 Verification — Runner Implementation

## Current verdict

HOLD pending the atomic cross-platform artifact-determinism fix commit and fresh
target-local reruns from that exact commit. Windows and Linux both passed the
functional matrix at `85ac8a4`, but their payload and bundle hashes differed.

## Implemented gates

| Gate | State |
|---|---|
| Clean local source checkout required | PASS |
| Full 40-character commit binding | PASS |
| Detached no-hardlink clone | PASS |
| Six current-source verifiers | implemented |
| Reproducible payload tree | implemented; content hash plus file count |
| Reproducible offline ZIP | implemented; byte hash plus reported hash |
| Clean install/doctor/verify | implemented |
| Seven non-activated harness connections | implemented |
| Per-adapter provider accounting | required to equal zero |
| v1→v2 upgrade and v2→v1 rollback | implemented |
| Recipient data preservation | implemented |
| Managed uninstall boundary | implemented |
| Path and identity redaction | implemented |

## Runner-commit evidence

- Runner self-test: 2/2 PASS
- Focused runner unit tests: 4/4 PASS
- Dirty-source negative canary: HOLD with `QualificationError`
- Provider calls and tokens: 0
- Service restarts: 0
- Activation changes: 0

## First Windows run and root cause

- Decision: HOLD
- Typed failure: `QualificationError`
- Failing boundary: installed `doctor`
- Root cause: `_safe_root` treated `target == ROOT` as unsafe everywhere. For an
  installed controller, `ROOT` is the managed installation itself, not the
  development source tree.
- Fix: keep filesystem-root rejection in `_safe_root`; enforce source-directory
  rejection specifically in `install` when `ROOT` is a Git checkout or release
  distribution.
- Regression proof: installed self-doctor passes, while install-over-source is
  still blocked by the named `source-boundary` gate.
- Historical Phase 79 lifecycle receipt remains unchanged; the corrected current
  source is bound by `lifecycle-qualification-phase80.json`.

## Pending after implementation commit

- fresh native Windows clean-clone receipt after the fix commit
- Linux/WSL clean-clone receipt
- cross-platform artifact-hash comparison
- current-source aggregate receipt and independent verification

No Phase 80 clean-room PASS may be claimed until those target-local results are
recorded after the implementation commit.

## Cross-platform artifact-determinism diagnosis

The first paired Windows/Linux runs at `85ac8a4` each passed all nine functional
checks, with zero provider calls and no activation change, but failed the Phase
80 cross-platform hash requirement. Persistent clean payloads reproduced 85
differences:

- 74 platform/Python-version-specific `.pyc` files generated when payload
  verification ran without Python's no-bytecode flag
- 10 public text files whose Windows CRLF bytes normalized exactly to the Linux
  LF hashes
- `RELEASE-MANIFEST.json`, which differed downstream of those source bytes

The scoped candidate fix runs payload verification with `-B`, declares LF as
the default checkout EOL with explicit CRLF exceptions for Windows scripts, and
adds regression tests for both boundaries. It also adds an immutable revision-2
lifecycle receipt while preserving the earlier Phase 79 and Phase 80 receipts as
historical evidence.

Candidate verification before commit:

- 204/204 scoped tests PASS
- six current-source verifiers PASS
- fixed envelope PASS at 311 bytes
- public privacy scan PASS
- clean payload PASS at 293 allowlisted files
- generated payload bytecode artifacts: 0
- provider calls/tokens, service restarts, and activation changes: 0

Cross-platform payload and bundle equality remains pending a clean exact-commit
rerun; the candidate receipt states this explicitly and is not promotion proof.

## First exact-commit determinism rerun

Commit `b697b4d` held on both Windows and Linux at the same source-verification
boundary, before install or any live action. The revision-2 receipt's
`.gitattributes` hash had been measured from the pre-commit Windows working-tree
bytes; the committed clean checkout correctly normalized that file to LF. The
other 19 bound source paths matched on the clean checkout. The follow-up changes
only the receipt's expected `.gitattributes` hash to the committed LF byte hash;
it does not alter implementation or claim artifact equivalence.

## Generated-artifact diagnosis

After the receipt-only correction, both platforms passed all nine functional
gates at `23b5e2a`, but their artifact hashes still differed. Persistent payload
comparison reduced the remaining drift to generated metadata:

1. All 293 allowlisted source files matched, but Python translated the generated
   `RELEASE-MANIFEST.json` newlines to CRLF on Windows.
2. With explicit LF manifest bytes, all 294 payload-tree files matched. ZIP entry
   content and metadata also matched except for `ZipInfo.create_system`, which
   defaulted to Windows (`0`) or Unix (`3`) according to the host.

The candidate writes the release manifest as UTF-8 bytes with LF and pins ZIP
creator-system metadata to Unix (`3`) while retaining the existing permissions,
fixed timestamp, file order, and stored contents. Patched disposable clones—not
promotion receipts—then produced:

- differing payload files: 0
- release-manifest SHA-256:
  `9dae6a58195eb82c4e2855abf6db57f26e6b0d5698005dbd26651ab57aa3526d`
- Windows/Linux bundle SHA-256:
  `60b67a78ac9a50a3cb837846d7b9a91bcf88c444fd624dc78f18f1934b57c76e`
- scoped tests: 206/206 PASS
- six current-source verifiers: PASS
- fixed envelope: 311 bytes, PASS
- public privacy/build gate: PASS, 293 files
- generated bytecode artifacts: 0

Fresh exact-commit Windows and Linux receipts remain required before Phase 80
promotion.
