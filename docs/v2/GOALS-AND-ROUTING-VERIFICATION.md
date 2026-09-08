# Local goalpost and routing verification

Scope: portable source integration into the unpublished v2 development kit.
This report does not certify live harness activation or publication.

## Results

- WSL installer suite: 218 passed, one deselected, in 284.70 seconds.
  The excluded test restores the unrelated full Prime/OMP runtime snapshot.
- Native Windows shared-core self-test: passed.
- Shared adapter verifier: 217 operation paths passed with zero provider calls.
- Safety gate self-test: 32 passed, zero failures.
- Protocol verifier: passed, including its negative fixtures.
- Current-source lifecycle verifier: passed.
- Public-payload privacy gate: passed.
- Raw tracked-file privacy scan: clean across 793 tracked files.
- Strict export-preview scrub: 20 findings in eight older files. Publication
  remains blocked. No finding remains in the new goalpost test placeholder.
- The first full-suite attempt timed out at 180 seconds. The completed run
  above used visible progress and excluded only the runtime snapshot test.
- The update corrected text encoding after the completed run. Final source-hash,
  core, lifecycle, and whitespace checks cover those corrections.

## Gate audit

| Gate | Concrete failing input exercised | Evidence scope |
|---|---|---|
| Finite goals | Empty endpoint or missing usability category | Synthetic contract fixtures |
| Usability | Builder self-grades a judgment check | Synthetic evidence fixtures |
| Evidence freshness | Source changes after proof capture | Real temporary file changes |
| Regression protection | A previously passing check fails | Synthetic result sequence |
| T4 intent | High risk, quoted autonomy, or negative instruction | Routing regression tests |
| S0-S4 selection | Low requested level with a structural path | Scope-floor tests |
| Reviewer admission | Empty catalog, wrong pipeline, excess call count | Stub providers, no live calls |
| Packaging integrity | Modified source in a staged payload | Installer negative test |
| Privacy | Credential and private-path canaries | Temporary fixture files |

The same implementation agent adapted the tests and reviewed the results.
These tests support the stated code behavior. They are not an independent
human usability evaluation or evidence of live production performance.
An independent review remains useful before release.

## Integration boundaries

The goalpost command is part of the installed shared core. Hosts must call it
at work boundaries and honor its decisions. It does not patch native goal
timers or activate an existing session. Routing classification does not execute
work or complete a review. Existing native goal status and approval owners
remain in control.

No commit, push, release tag, provider activation, or service restart occurred.
