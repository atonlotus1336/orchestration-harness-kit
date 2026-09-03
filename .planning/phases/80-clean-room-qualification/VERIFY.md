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
