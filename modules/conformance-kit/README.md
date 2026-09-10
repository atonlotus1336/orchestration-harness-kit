# Provider-free adapter conformance kit

**What it is:** a deterministic test runner that sends every orchestration
operation through a real thin adapter, the universal bridge, and the shared core.

**Why you would want it:** an adapter author gets one objective PASS/HOLD result
before installing or activating anything. A passing adapter has not copied core
policy, has not hidden accounting, and has exercised every operation it claims.

## Run the included fake harness

```bash
python run-conformance.py --selftest
```

The command performs no network request, provider call, installation, restart,
or activation. To test another adapter, pass its directory with `--harness-dir`.

## Required harness files

- `harness-manifest.json`
- the Python entry point named by the manifest
- a README explaining harness-local translation behavior

The adapter may translate local formats only. Routing, authority, memory,
retrieval, provider selection, optimization, and activation remain core-owned.

## Optional additive-parallel feature conformance

The primary conformance suite remains closed: it exercises every protocol
operation exactly once. The optional additive-parallel profile therefore has a
separate provider-free scenario suite:

```text
python verify-additive-parallel.py
```

It verifies only the portable feature boundary: two independent read-only
children, serial fallback, parent-only completion, and Orca observation-only.
It does not launch a child, connect Orca, call a provider, or activate a
harness.
