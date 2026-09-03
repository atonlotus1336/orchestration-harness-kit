# Phase 80 Verification — Runner Implementation

## Current verdict

HOLD pending the scoped installed-controller fix commit. Runner commit `1658e33`
correctly stopped its first Windows clean-room rehearsal when the installed
controller could not doctor its own root. No Linux promotion run was started.

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
