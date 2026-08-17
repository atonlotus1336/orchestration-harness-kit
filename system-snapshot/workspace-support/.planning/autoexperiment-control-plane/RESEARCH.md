---
initiative: autoexperiment-control-plane
status: planning-research
created: 2026-08-09
upstream_commit_checked: 228791fb499afffb54b46200aca536f79142f117
---

# AutoExperiment Control Plane - Research Basis

## Upstream Karpathy Kernel

The official `karpathy/autoresearch` repository intentionally reduces research to three important files: a fixed evaluator/data environment (`prepare.py`), one agent-editable implementation (`train.py`), and a human-edited research instruction (`program.md`). Each experiment receives a fixed five-minute training budget, optimizes one primary metric (`val_bpb`), logs keep/discard/crash in `results.tsv`, and prefers simpler changes when gains are comparable.

Source: https://github.com/karpathy/autoresearch at commit `228791fb499afffb54b46200aca536f79142f117`, checked 2026-08-09.

## What WorkspaceKit Should Preserve

- A frozen, hashed evaluator and baseline.
- One primary metric for each campaign; all other measures are guardrails.
- One bounded editable surface and one causal idea per iteration.
- A fixed, comparable resource budget.
- Explicit keep, discard, and crash records.
- Simplicity and deletion as positive outcomes.
- Fast iteration with state stored outside model context.

## What WorkspaceKit Must Change

| Upstream choice | WorkspaceKit adaptation | Reason |
|---|---|---|
| One training target | Nine balanced portfolios | WorkspaceKit is a heterogeneous orchestration ecosystem. |
| One `train.py` edit surface | Adapter-declared editable surfaces | Each subsystem needs its own safe boundary and evaluator. |
| One metric | One campaign metric plus hard invariants | A local gain cannot justify breaking recovery, authority, privacy, or cost. |
| Infinite loop | Bounded mini-loops with all three WorkspaceKit hard stops | Subscription rate limits and system safety require finite campaigns. |
| Agent commits then resets Git | Copy-on-write sandbox, patch lineage, and recovery anchor | Shared-history rules and concurrent work make automatic history mutation unsafe. |
| Direct keep/discard on one score | Reproduction, holdout, regression, simplicity, and cost-benefit gates | Broader systems have noise and hidden downstream effects. |
| One agent edits and judges | Builder separated from verifier for non-deterministic work | Prevent self-grading drift. |
| Overnight autonomous run | Backup-triggered daily/weekly/monthly campaigns | WorkspaceKit needs recovery-aware cadence and inspectable reporting. |

## Improvements Over Original Autoresearch

- System-wide applicability through adapters.
- Balanced coverage plus evidence-based priority ranking.
- Nested clarity and verification loops that resist step-to-step drift.
- Compatibility envelopes and whole-system regression testing.
- Economic and operational cost-benefit scoring.
- Backup-bound rollback and patch lineage.
- Source provenance, privacy tiers, and prompt-injection quarantine.
- GSD promotion path for winners instead of direct production mutation.

## What Is Lost Compared With the Original

- Less conceptual simplicity and lower experiment throughput.
- More setup cost before the first useful experiment.
- Weaker causal attribution when a candidate crosses multiple layers.
- Less radical freedom because invariants constrain the search space.
- Less portability because adapters know about WorkspaceKit surfaces.
- No indefinite autonomous momentum.

The plan compensates by keeping the inner kernel deliberately small, requiring ablations for multi-layer candidates, and reserving a bounded exploration budget for non-obvious ideas.

## Preliminary Markdown Opportunity Baseline

A bounded scan of registered high-value roots found approximately 2,000 Markdown files and 4.28 million rough tokens (bytes divided by four). This is an opportunity map, not an instruction to rewrite files.

| Root | Files | Approx. tokens | Notable concentration |
|---|---:|---:|---|
| `.claude` | 431 | 1,053,694 | `agents/` is about 72% of this root. |
| `.planning` | 244 | 768,982 | `graphs/GRAPH_REPORT.md` is roughly 492,822 tokens. |
| `wiki` | 134 | 122,960 | No exact duplicate files found in the preliminary scan. |
| `Projects` | 1,191 | 2,334,520 | Vendor/generated material is about 831,279 tokens; FABLE plans about 673,529. |

Representative hotspots include the monolithic graph report and a large generated PortalFlow API reference. The whole-drive scan was unreliable; the implementation should scan registered roots independently, classify authored/generated/vendor/archive material, and replay real retrieval tasks before recommending structural edits.

## Existing WorkspaceKit Contracts Reused

- `__WINDOWS_WORKSPACE__/.claude/LOOP-CONTRACT.md` supplies the loop decision gate, loop contract, builder/verifier rule, external state rule, and mandatory hard stops.
- `__WINDOWS_WORKSPACE__/SURFACE-GRAPH.yaml` remains the surface registry and health spine.
- `__WINDOWS_WORKSPACE__/.claude/AUTHORITY-CONSTITUTION.md`, `RETRIEVAL-PLAYBOOK.md`, and `MASTER-INDEX.md` remain authoritative for source selection.
- Existing backup scripts and scheduled tasks provide recovery checkpoints.
- GSD remains the lifecycle for planning, executing, verifying, and shipping accepted work.

