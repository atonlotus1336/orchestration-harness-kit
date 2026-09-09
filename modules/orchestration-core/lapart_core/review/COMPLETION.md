# Shared completion controller

Every harness can use the same Controller in this module. The classifier still
selects the existing S0-S4 workloads. Classification alone never completes review.
The portable message core stays provider-free. A trusted host completion adapter
owns model invocation and proof verification.

## Host adapter contract

1. Read and authenticate the host execution receipt, verify its lifecycle owner,
   session, run, declared paths, policy and collector fingerprints, and current
   repository snapshot. Never pass model-supplied test results to Controller.run.
2. Select the review pipeline from observed changes and verified host facts.
   Scope ceilings still apply. Ordinary automatic completion uses phase scope. S4 requires explicit confirmation
   or admission through a qualified automatic host path described in S4-SCENARIOS.md.
3. Construct Controller with host-owned state_root, vault_root, catalog_path and
   config. Config schema_version is 1.0. max_deliveries is 1 or 2. harness_modes
   maps harness IDs to shadow, advisory or enforced, with missing IDs shadow.
   Optional required_environment maps harness IDs to exact environment-name/value
   bindings. A mismatch keeps that target shadow. Use it to admit only qualified
   native profiles. approved_workspaces admits source roots. provider contains endpoint, model,
   optional credential_env and optional reasoning_effort. The portable kit selects no vendor. checks maps workspace paths to pinned check registries.
4. Call run with workspace, the validated finished evidence, pipeline, scope,
   current_identity callback, and an explicit boolean s4_confirmed. A host may
   also supply deadline_seconds to shorten, never enlarge, policy time limits.
   Completion adapters must reserve time below their native hook timeout.
5. Consume the returned review-completion.v1 receipt. A verified blocking finding
   in enforced mode returns workflow_action=remediate. Incomplete review returns
   hold. The host maps the remediation route to its canonical coding router.
   Advisory findings remain advisory. A fresh correction must carry new execution
   evidence and still fits the remaining task review budget.

The required evidence fields are stage=finished, execution_evidence_status=VERIFIED,
harness, run_id, before.contents, before.index, after.identity, after.contents,
binding.packet_hash, binding.session_binding_sha256, and packet.objective plus
packet.allowed_paths. S1-S4 also require packet.change_contract with the same
objective and at least one acceptance criterion. S0 additionally requires the
host's review_pipeline=S0 and review_verdict=PASS. These are a trusted in-process
interface, not an authentication protocol for user JSON.

## Evidence and bounded work

Receipts are atomically stored and HMAC-authenticated using a host-local key.
Keep state and its key outside model-writable task directories. HMAC protects
cache integrity. It is not a security boundary against an agent with full access
to the host account. Source, policy, catalog, controller code and configuration
fingerprints bind each delivery.

CLI and hook adapters must use the same state root. Duplicate deliveries reuse
the verified result with zero new requests. Interrupted deliveries cannot replay.
Changing run IDs within the same session/task does not reset request, token, tool,
or elapsed-time budgets. The first selected pipeline's limits bound the lineage.
A later tier cannot silently enlarge them. Unknown usage closes automatic reuse.
The controller admits at most two deliveries. Exhaustion returns
Loop Limit Reached - Escape Hatch.

For S1-S4, the portable controller reconstructs the declared baseline from the
receipt and guarded read-only Git blob observations. It refuses configured Git
clean, smudge, or process filters before every content observation. Each existing
review request receives the objective, invariants, acceptance contract, current
contents, before evidence, change state, and unified diff. Deleted files remain
in scope with explicit `deleted` state and before bytes. A deleted file is not
treated as a present empty file. This adds no model request.
Recipient evidence adapters preserve bounded exact bytes at admission only for
declared dirty or untracked baselines that Git cannot reconstruct. The signed
hash must match before portable review accepts those bytes.

Automatic S4 remains disabled in the general selector. A qualified host may use
`s4_dispatch`. It records the source-bound atomic claim first, then receives a
sealed grant from the existing policy owner. The Controller and Runner reject a
plain confirmation flag as automatic authorization. Interrupted or indeterminate
usage leaves the claim and lineage on hold and cannot replay automatically.

HostCheck.test_path is relative to the workspace. Its SHA-256 and description
come from the trusted host registry. Checks must independently exercise the
declared acceptance behavior. An unrelated failing assertion proves nothing.
Proof checks disable bytecode output so they do not alter the source snapshot.

This module installs no active provider or recipient harness. Qualify the
exact provider profile and native completion event before setting that target's
mode. A direct handler test is not evidence that an already-running app has loaded
the hook. Preserve a per-target activation record and rollback configuration.

## Verification

Run python -m pytest installer/tests/test_review_controller.py
and installer/tests/test_review_enforcement.py from the kit root.
These tests use a local synthetic HTTP server, real transport processes, usage
accounting, signed receipt tampering, duplicate/interrupted deliveries, source
mutation and independently executed acceptance assertions.


## Native lifecycle boundaries

The portable native_review module normalizes Cursor conversation identity and
completion status, Pi settled events, OpenCode idle events and DSH prompt completion.
The normalizer rejects conflicting identities. Aborted events cannot start review. Native
PreToolUse denials use the harness permission schema. Stop denials use its completion
schema. Cursor follow-ups stop at the configured loop cap.

A native event name is not proof of a blocking completion boundary. OpenCode CLI
can exit before an idle listener finishes. An outer completion owner must await
the review and withhold final output on failure. DSH compatibility Stop bridges
can force unlimited continuations. A native owner must bound or cancel that path.
DSH ACP end_turn is transport quiescence and can include hook cancellation.
Always consume the signed review receipt as the completion authority.

The local adapters and launchers are host installation assets, not bundled native
runtime activations. Recipients implement and qualify these lifecycle contracts
against their installed harness version before enabling semantic review.


## Native stop versus verified completion

Codex Stop decision=block requests another model turn. An integrity hold must use
continue=false with a visible incomplete-review warning. A proven remediation may
request one correction turn. stop_hook_active prevents another automatic turn.
The signed receipt remains authoritative even if the native turn status says completed.

Antigravity normalization requires one consistent conversationId, fullyIdle=true,
terminationReason=model_stop and no error. Its native Stop response cannot provide
a failed process status. A supervised host must withhold final output until review
passes. Cursor CLI also needs a supervised owner when its installed hook transport
cannot demonstrate completion dispatch. Qualify each native path independently.

## Portable supervised release gates

Use native_review.inspect_cursor_completion(result, session_id) or
native_review.inspect_opencode_completion(events) before starting semantic review.
They require exact native session identity, successful completion and nonnegative
integer usage. OpenCode must supply accounted step_finish events and a final stop
reason. Truncated output cannot pass.

After the trusted host authenticates the signed completion receipt, call
native_review.review_allows_release(result). It requires action=allow,
review_completion.mode=enforced, review_completed=true and
consumption.review_passed=true. An allow alone or a shadow result cannot release
final output. These functions do not authenticate caller-supplied JSON. The host
must verify evidence, signatures and current source through Controller first.

These portable functions select no provider, install no native CLI and grant no
recipient trust. Qualify each recipient's supervised launcher before activation.

## Block resolution

The existing Controller seals blocking results with a block ID. A trusted host
may use `recover_refusal` only for known zero-usage workspace-admission or
provider-configuration refusals. `resolve_block` accepts freshly host-validated
execution evidence, reads signed reviews itself, and binds resolution to the
original task, packet, session, workspace, finding IDs and review depth. It also
requires current policy, configuration, catalog and implementation fingerprints.
Neither API accepts a caller-supplied PASS or clears unrelated blocks.

Both receipts and cumulative usage remain intact. Unknown usage, budget holds
and unsigned legacy blocks require owner intervention. Deleting their state is
not recovery. These APIs do not activate a harness or replace its completion
owner. Review transport accepts only the successful terminal `stop` reason.
