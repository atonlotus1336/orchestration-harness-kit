# LapArt Universal Orchestration Protocol 2

## Boundary

The shared core owns every decision that must remain consistent across harnesses.
Adapters translate harness-native inputs and outputs into protocol envelopes.
They do not choose Prime lanes, execute OMP policy, authorize actions, select
memory or retrieval policy, hide capabilities, optimize context, select a provider,
account for calls, promote activation, or declare evaluation success.

Every envelope carries its protocol generation, operation, harness identity,
session state, payload, and optional reference-only authentication. Core responses
attach accounting, attribution, health, or approval evidence when the operation
requires it.

## Negotiation

Peers advertise supported generations. Both select the highest mutual generation.
A peer that supports only generation 1 retains v1 compatibility. No shared
generation produces `INCOMPATIBLE_PROTOCOL`. Neither side guesses a fallback.

Unknown optional fields live under `extensions`. Generation 2 readers ignore
unknown extension keys and reject unknown top-level keys. Removing or changing a
required field needs a new major protocol generation.

## Sessions and streaming

Session operations create, continue, detach, and reattach stable session IDs.
Message streams use ordered sequence numbers and a terminal marker. Detach never
implies cancellation, and reattach resumes from the last acknowledged sequence.

## Capability discovery

Tools and skills remain discoverable even when the core defers their detailed
schemas or instructions. Discovery returns summaries and stable IDs. A separate
load operation progressively reveals only the selected capability.

## Memory and retrieval

Memory requests declare read or write boundaries and purpose. Retrieval responses
identify sources with stable references and attribution. Adapters never read a
private store directly on behalf of the shared core.

## Prime and OMP

Prime selection returns both the recommended and actual lane. OMP requests carry
a governed job reference and capability limits. OMP returns a patch reference,
source hashes, and verification evidence. It never applies a patch to the
authoritative repository.

## Context and provider accounting

Context attribution reports system-prompt, authority-context, skills-index,
tool-schema, selected-content, rendered-total, and saved bytes separately.
Provider accounting records actual calls, input tokens, output tokens, route class,
and pay-per-token status. Zero is an explicit value, not a missing field.

An `optimization.plan` request may add `request_text` and `available_tools`
together inside its constraints. The core then returns `tool_projection` with the
classification, selected and disabled tool identifiers, discovery-preservation
status, and deterministic reason. Omitting both fields retains the generation-2
context-only behavior; supplying only one fails closed. Adapters never implement
their own request classifier or tool-selection policy.

## Activation and rollback

Activation states are `inactive`, `shadow`, `review`, and `production`. A transition
requires an approval reference, previous state, requested state, gate evidence,
and rollback reference. Qualification never implies activation. Rollback and
recovery report the restored state and verification result.

## Authentication and errors

Authentication objects contain only an environment-variable, secret-store, or
managed-identity reference. Credential values are forbidden. Errors use a stable
code, message, retryability flag, operation, and structured details.
