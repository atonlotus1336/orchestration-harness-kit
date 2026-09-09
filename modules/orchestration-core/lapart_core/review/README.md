# Shared review checks

This package chooses how much review a change needs. Execution lanes T0-T4
choose who does the work; review levels S0-S4 choose how to check it. There is
no numeric mapping between them.

Install this part through the orchestration-core module. All eight adapters
receive the review classification in the shared routing response. No reviewer
runs during classification. S0 uses deterministic evidence and zero model calls.
The optional Runner accepts host-configured reviewers, checks the complete
catalog and call budget first, and refuses S0 and unconfirmed S4 requests.

Run the shared-core self-test from the kit root:

    python modules/orchestration-core/verify-core.py --selftest

The host owns reviewer credentials, authorization, and evidence collection.
A caller-reported fact can select a route; it cannot certify completed review.


## Review admission and order of operations

The shared order is: collect evidence, classify review depth, preflight the
complete reviewer set, admit its budget, invoke bounded reviewers, reconcile
findings, and verify completion. T0–T4 execution depth does not set S0–S4 review
depth. A classification result never counts as a completed review.

Classification accepts `routing_facts`, a closed mapping of boolean observations.
S0 requires `mechanical_or_exact`, `independent_deterministic_validation`,
`no_unknown_load_bearing_fact`, and `no_risk_trigger`, plus measured changed-line
and subsystem counts. Missing values retain an S1 floor. Explicit risk facts
such as `security`, `public_interface`, or `concurrency` raise the review floor.
Arbitrary legacy facts cannot remove these requirements. Caller observations
are classification inputs, not proof that review has passed.

Before the first request, the runner validates every required reviewer, skill
hash, provider, report-only capability, scope hash, and complete context packet.
Required pipeline membership overrides optional catalog metadata. A mismatch
refuses the whole batch. Source bytes are checked before and after each review.

Use `ReportOnlyProvider(transport=JsonReviewTransport(endpoint, model))` with an
explicitly configured OpenAI-compatible review endpoint. Credentials may be
referenced by environment variable name through `credential_env`; do not put
credentials in source or URLs. No provider is installed or selected by default.
The old unrestricted callback is rejected. Attestation alone is insufficient.

One host-owned budget covers the entire reviewer set. It counts actual HTTP
requests and observed input/output/total usage. Full request bytes include the
reviewer lens and source envelope. UTF-8 bytes form a conservative input-token
estimate. Each request also has an output cap of at most 2,048 tokens, bounded by
remaining aggregate output and total allowances. Missing/inconsistent usage is
INCONCLUSIVE. Requests never retry automatically or follow redirects. The client
exposes no model tools, shell, write, or patch channel; a returned tool call is
rejected. A deadline kills and reaps the local HTTP worker. Remote cancellation
and billing remain provider-dependent; a local timeout does not prove remote
compute stopped.

The runtime CLI `run` accepts an S0 PASS only from a finished host-owned checkpoint
receipt via `--workspace`, `--evidence-receipt`, and `--evidence-run-id`, with
`--scope checkpoint` and matching declared paths. The host rechecks current
content, collector/policy identity, lifecycle and scope. Supplying a task packet
also requires its exact hash to match. New risk facts cannot reuse S0 proof.
`--deterministic-pass` remains accepted as a legacy argument but grants no PASS.
Unverified execution is UNKNOWN, and finding totals reflect observed findings.

S1–S4 execution is the `Runner` library interface; the CLI has no reviewer-provider
configuration interface and returns INCONCLUSIVE for those levels. The portable
core exposes classification, guarded read-only change-material collection, and
the same bounded runner. It does not ship a test executor or runtime receipt
authenticator. Recipient hosts must supply those trusted evidence paths through
their existing owners.

Keep rollout in shadow mode until a real configured review provider is separately
qualified for review quality. Passing local fake-provider tests certifies the
transport controls, not the model's ability to find defects. Fixed phase review
retains authority; S4 requires explicit confirmation or qualified host policy admission.

Semantic review packets include the accepted task contract and exact before,
after, change-state, and unified-diff evidence for every declared path. Deletions
are first-class reviewed changes. Git content observations fail closed when a
clean, smudge, or process filter is configured, so a filter command cannot run as
a side effect of review. The contract is appended to the already-budgeted reviewer
prompt and does not add a model call.


## Correctness and independent proof

Every semantic reviewer now uses a compact owned prompt with a correctness baseline,
its specialist lens, the exact finding schema, and its assigned reviewer identity.
The catalog still checks the original skill pin. Interactive skill formatting does
not enter the model prompt. Unsupported lens names fail preflight.

The host can supply a HostTestVerifier with a workspace, reviewed source hashes,
and a registry of HostCheck entries. Each entry pins one trusted acceptance test
file by SHA-256 and describes its acceptance contract. Register independent
tests that actually exercise that contract. A failing unrelated test proves nothing.

A model can cite check:<id> to request verification. It cannot select a command,
test path, expected value, or hash. The host runs the registered pytest file,
checks source and test hashes before and after, and accepts only a nonempty
assertion failure with no collection or runtime errors. The check consumes the
same review tool and elapsed-time budget. Repeated references reuse evidence only
within that review. A new review runs verification again.

The host verifies only reproducible_failed_test_tied_to_acceptance in this adapter.
Other proof labels remain advisory until a separate host verifier supports them.
Unknown labels and forged reviewer identities fail validation. No proof verifier,
missing reference, or passing test can grant blocking status. Do not treat a
reviewer's severity or prose as independent evidence.

These host tests are trusted project tests, not model-generated commands. This
adapter does not sandbox arbitrary untrusted Python. Source identity includes all
dependencies needed by the registered checks. The caller owns that scope contract.

No model, context, output, tool, or time budget increased. Review rollout remains
shadow. Production activation requires broader qualification, including usability
and accessibility where a task changes the human interface.


The transport accepts an explicit optional reasoning_effort setting. Leave it
unset for providers that do not support this field. Qualify the exact setting
with the chosen model before use. Low reasoning passed the local synthetic
qualification. Disabling reasoning failed defect detection and must not count
as an approved cheaper substitute. Provider finish status is recorded without
retaining raw reasoning. Truncated responses cannot pass even if they contain
parseable JSON. These settings do not change the configured budget ceilings.


## Shared completion integration

See [COMPLETION.md](COMPLETION.md) for the host adapter, configuration, signed
receipt, correction handoff and aggregate budget contracts. The shared controller
is portable; recipient native-hook activation requires target-specific evidence.

See [S4-SCENARIOS.md](S4-SCENARIOS.md) for deterministic milestone, architecture
and goalpost candidates, plus the shared host admission library. Native activation
remains target-specific.
