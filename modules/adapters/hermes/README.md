# Hermes OS reference adapter

This module is the canonical example of connecting Hermes OS to the Universal
Orchestration SDK v2. It translates Hermes-shaped events and responses; the
shared core continues to own routing, authority, capability discovery, context
selection, provider policy, accounting, activation, rollback, and verification.

The adapter is source-qualified for four protected surfaces: Home, Workspace,
NucBox Telegram, and VPS Telegram. Installing or testing this module does not
modify, restart, migrate, or activate any live profile. Runtime migration needs
a separate, profile-specific qualification and authorization.

Run its provider-free qualification from the repository root:

```text
python modules/conformance-kit/run-conformance.py --harness-dir modules/adapters/hermes
python modules/adapters/hermes/verify-hermes-adapter.py
```

Local integration sends a closed event with `operation`, `payload`,
`conversation`, `state`, and `surface`. The response keeps provider accounting
and context attribution visible so a harness cannot hide cost or envelope
growth. Configuration and authentication remain recipient-owned references;
this module contains no endpoint, model, provider, credential, or machine path.
