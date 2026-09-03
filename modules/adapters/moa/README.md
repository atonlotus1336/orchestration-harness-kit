# MOA adapter

This adapter translates MOA proposal, critique, aggregation, and lifecycle
events into the universal orchestration protocol. It does not copy the Hermes
MOA loop, aggregator, trace store, model-slot selection, or cost policy.

Prime routing, authority, capability discovery, context selection,
token-compute optimization, provider/model policy, and call accounting remain
shared-core responsibilities. Source qualification does not enable MOA mode or
make a provider request.

```text
python modules/conformance-kit/run-conformance.py --harness-dir modules/adapters/moa
```
