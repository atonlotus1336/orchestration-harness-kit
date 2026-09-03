# Authoring a thin harness adapter

An adapter translates harness-local events and lifecycle signals. It does not
copy or reinterpret shared policy. Authority, routing, memory, retrieval,
capability discovery, token optimization, provider choice, accounting,
activation, and rollback decisions remain in the shared core.

## Build the adapter

1. Copy `modules/adapter-sdk/templates/harness-manifest.json` and `adapter.py`.
2. Give the adapter a stable ID and declare only capabilities the harness can
   actually expose.
3. Implement the six translation/lifecycle methods named by the manifest.
4. Put credential references in `env://`, `file://`, `secret://`, or
   `recipient://` form. Never put credential values in a manifest or receipt.
5. Negotiate the highest mutual protocol version; fail with a structured
   incompatibility error when there is no mutual version.
6. Preserve unknown generation-2 extension keys and reject unknown top-level
   keys.
7. Run the SDK self-test and the provider-free conformance driver.

```sh
python3 modules/adapter-sdk/verify-adapter-sdk.py --selftest
python3 modules/conformance-kit/run-conformance.py --adapter-dir path/to/adapter
```

## Acceptance contract

- translation remains deterministic and provider-free
- discovery summaries remain visible while detail loads progressively
- context attribution and provider accounting are returned unchanged
- dry-run, readiness, detach/reattach, and rollback signals are implemented
- source qualification records zero provider calls and no activation change
- live activation begins only with an isolated shadow profile and a tested
  rollback reference

Use the Hermes OS adapter as the reference for the complete manifest and the
other six adapters as examples of harness-specific event translation.
