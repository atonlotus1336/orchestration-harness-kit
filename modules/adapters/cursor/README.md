# Cursor adapter

This adapter maps Cursor hook events to the Universal Orchestration SDK v2.
It keeps authority, routing, memory, retrieval, capability discovery, token
optimization, provider accounting, and activation policy in the shared core.

The included Cursor plugin is an opt-in deployment template. It adds a compact
session-start context hook, a fail-closed shell safety hook for destructive
command patterns, and an MCP (Model Context Protocol) entry for the kit's
MemoryWeaver service. Installing the SDK or running `connect` does not copy the
plugin into Cursor, edit `.cursor`, restart Cursor, authenticate, or call a
model.

## Install this adapter

First install the SDK, then create its reference-only connection descriptor:

```text
python installer/kit.py connect --target <install-root> --harness cursor --apply --yes
```

Review `<install-root>/modules/adapters/cursor/cursor-plugin` and set its four
non-secret variables to the installed Python, SDK root, recipient data root, and
the token-compute activation registry created by the required optimization
module.
For a local rehearsal, link that directory into Cursor's local plugin directory,
reload the Cursor window, and keep the plugin disabled until the provider-free
checks below pass. Do not copy a credential into the plugin or repository.

## Test it

```text
python modules/conformance-kit/run-conformance.py --harness-dir modules/adapters/cursor
python -m pytest installer/tests/test_cursor_adapter.py -q
```

These checks exercise every protocol operation without a provider call. Live
activation remains a separate, reversible deployment step.
