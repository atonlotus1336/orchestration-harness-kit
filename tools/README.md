# tools — the kit's own build & safety machinery

**What it is:** the scripts that build, scrub, and verify this kit itself.
**Downloaders never need this directory** — it exists for whoever maintains a kit
like this one (including you, if you fork it and start publishing your own).

## The pipeline (run in this order)

1. `m10-generate-from-surface-graph.ps1` — reads the maintainer's machine manifest
   (a YAML "surface graph" listing every component and its privacy class) and
   generates the allowlist of what may be exported. Fails closed on unsafe paths.
2. `m10-validate-manifest.ps1` — sanity-checks the generated manifest.
3. `m10-scrub-v2.ps1` — the tripwire: scans an assembled preview for ANY private
   token (identities, hostnames, unmapped absolute paths) and aborts on a hit.

Plus `../sync-shareable-v2.ps1` at repo root, which chains generate → copy →
sanitize → scrub into one command.

`verify-v2-provenance.py` validates the v2 source map, immutable-import policy,
v1 HOLD truthfulness, mandatory public exclusions, and no-activation boundaries.
Run it with `--selftest` to exercise its fail-closed negative cases.

`verify-v1-compatibility.py` pins the immutable v1 rollback commit and proves that
v2 retains the v1 CLI, config schema, migration boundaries, and protocol fallback.

`verify-release-readiness.py` closes the v2 candidate without publishing it. It
checks the public documentation set, compatibility claims, Phase 80 evidence,
public-payload privacy, historical-source boundary, commit metadata posture, and
inactive production policy. Historical holds are reported as holds; they are not
silently converted into public-release proof.

`check-doc-counts.py` runs `modules/guards/test-dry-run-gate.py` and fails when
a doc states a different test count. Run it with `--selftest` to replay its
failing inputs. `verify-release-readiness.py` and `.githooks/pre-commit` call it.

## The philosophy (why this isn't just "being careful")

- **Allowlist-in, never scrub-out**: only explicitly named files can enter the
  export. A list mistake produces a *missing file* (visible, harmless), never a
  *leaked file* (invisible, permanent).
- **Every channel scans its own payload**: the preview scrub, the pre-commit
  hook (`.githooks/pre-commit`), and the sync each independently verify — because
  each sees a different slice of the content.
- **Scanners match bytes, not meanings**: approved placeholder tokens are listed
  per *representation* (including escaped forms) in `approved-public-tokens.txt`.

Full maintainer procedure, hard rules, and the update/push checklist:
[`../KIT-MAINTENANCE.md`](../KIT-MAINTENANCE.md).
