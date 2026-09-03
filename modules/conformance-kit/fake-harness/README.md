# Fake harness

This provider-free harness proves that a previously unknown client can connect
with one manifest and one translation-only adapter. Its local event keys are
`op`, `body`, `conversation`, and `state`; its local responses preserve payload,
provider accounting, context attribution, and structured errors.

It has no network client, provider configuration, credential value, runtime
service, or private machine dependency.
