# Modules — agentic-patterns additions (2026-07-31)

Three self-contained modules distilled from the vault's agentic-patterns v1
milestone. Each is harness-agnostic and ships with its own verification.

## guards/ — dry-run destructive-command gate

A deterministic PreToolUse safety gate: policy-as-data (`destructive-patterns.json`),
a hook (`dry-run-gate.py`) that predicts effects and returns ask/deny verdicts,
and a self-test (`test-dry-run-gate.py`) that replays every rule's named
failing input plus negative controls. **Run the test first** — it passes out
of the box against the shipped template. Then edit the `example-vault`
patterns to your real paths and add your own incident-derived deny rules
(the `example-incident-deny` entry shows the shape). Design rules inherited
from the source system: deny is checked before any allow exemption; every
rule must name a failing input; a false positive costs one keypress, so
`ask` is the default and `deny` requires an incident citation.

## reasoning-shapes/ — portable reliability for small models

Four method skills (single context, file+shell, no subagents) that carry
reliability in the *procedure* instead of the model: `self-consistency-method`
(N blind attempts + majority vote; refuses on no-majority),
`ensemble-method` (persona-diverse voters + aggregation),
`tree-of-thoughts-method` (K alternatives, boolean-flag pruning — never
numeric rubrics), `lats-method` (tree search over checkable actions).
Drop into any skills directory; each documents driving a local model via
`ollama run` subprocesses. Escalation ladder: plain loop → self-consistency
→ ensemble → ToT → LATS; use the smallest shape that works.

## recipe-miner/ — agent workflow memory

Scans a completed session transcript (Claude Code JSONL) for repeatable
3–6 step shell procedures and stages draft candidates. It **never installs**
anything — staging + human review is the only path in. Configure via
`SKILL_STAGING` and `SKILLS_STORE` env vars; wire as a SessionEnd hook.
Candidates are hash-deduped against a ledger so rejected proposals are
never re-proposed.
