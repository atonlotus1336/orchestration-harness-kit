# Claude Code adapter

This adapter maps Claude Code hook events to the universal orchestration
protocol. Hook events remain harness-local transport and lifecycle signals;
authority and approvals are decided by the shared core.

The adapter does not contain routing, safety, memory, retrieval, discovery,
token-compute, provider, or evaluation policy. Source qualification does not
install a hook, edit Claude settings, authenticate, or invoke a model.

```text
python modules/conformance-kit/run-conformance.py --harness-dir modules/adapters/claude-code
```
