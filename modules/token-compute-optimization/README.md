# Token-compute optimization

**What it is:** the portable, provider-free control plane for request budgets,
adaptive execution depth, context reuse, bounded rendering, usage accounting,
and marginal-value stop decisions across every SDK adapter.

**Why you would want it:** each harness reads one activation registry instead of
copying optimization rules. The same conservative behavior follows a task from
Hermes OS to OpenCode, Pi, Antigravity, Codex, Claude Code, MOA, or Cursor.

The safe active profile keeps budget admission, evidence-gated depth, context
caching, bounded rendering, and accounting active. Depth falls back to `full`
when evidence is weak. Marginal-value stopping remains `shadow`; it cannot stop
work until a separately qualified promotion artifact exists.

## Activate this part

Create a recipient-local registry outside the SDK checkout:

```text
python activate-token-compute.py activate --registry <data-root>/token-compute-activation.json --harness all --approval-reference <local-approval-id> --apply --yes
```

The command stores only a hash of the approval reference. It does not call a
provider, restart a harness, change a model, or enable pay-per-token routing.
Harness launchers and hooks must point to the resulting registry.

## Test it

```text
python verify-token-compute.py
python activate-token-compute.py verify --registry <data-root>/token-compute-activation.json --require-all-active
```

To restore the previous registry, use the rollback identifier returned by the
activation command:

```text
python activate-token-compute.py rollback --registry <data-root>/token-compute-activation.json --snapshot <snapshot-id> --apply --yes
```
