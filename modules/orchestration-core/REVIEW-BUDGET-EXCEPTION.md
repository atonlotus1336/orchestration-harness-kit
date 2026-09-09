# Inactive, one-use review budget exception

This extension cooperates with the existing review controller and capability
approval owner. It does not replace execution routing, review selection, approval
UI, capability policy, leases, job ownership, or cumulative spending records.
No model request is needed to prepare or validate a proposal.

## Setup contract (not activation instructions)

The trusted host validates finished execution evidence and the current Git
identity, then lets the existing automatic selector choose S0-S4. Only an admitted
S1-S3 plan/phase review can prepare a budget exception. It cannot downgrade S4.
T0-T4 execution routing remains separate.

1. Supply the existing capability ledger to `Controller(approval_ledger=...)`.
   A ledger object must expose `get(request_id)`. `ExistingApprovalReader(path)`
   provides a read-only SQLite implementation for the existing
   `capability_requests` table; it never creates or migrates a database.
2. Prepare with `Controller.prepare_budget_exception` only after host validation.
   The source host's `complete(..., budget_proposal={limits, expires_at})` performs
   that validation and returns a draft with `INCONCLUSIVE`, not review PASS.
3. The existing broker may bind its internal `review-budget` handler to
   `ApprovalBridge(controller).call`. The proposed alias is
   `review.budget.exception`, classification `action`, approval `required`, tool
   `approve_exception`. Its closed arguments contain only `proposal_id`.
   **No alias or runtime activation flag is installed by this change.**
4. An operator must use the existing approval UI/nonce flow for that exact request.
   The broker authorizes it, invokes the projection handler, and completes its
   existing ledger record. The bridge never resolves or approves requests itself.
5. A separately authorized runtime activation must set `budget_exceptions_enabled`
   to true. The validated host can then pass the exact `budget_exception_id` and
   trusted `capability_ledger_path` to completion. No automatic exception choice
   or live model review is enabled by adding these files.

The request job ID must match the original execution owner from the validated
receipt (or its existing governed job). Do not invent a replacement job to fit a
ledger format. A host whose owner ID is incompatible with its approval ledger must
refuse until an explicit ownership-preserving adapter is qualified.

## Integrity and limits

A v2 signed approval projection is accepted only while its backing record is
`executed`, operator-approved, unexpired, and bound to the same request, alias,
target, original job and canonical proposal argument hash. Pending, executing,
failed, policy-auto, altered and unbacked approvals refuse before dispatch.
The old signed-only v1 approval is no longer accepted. The existing trusted host
process is the boundary; this is not a sandbox against arbitrary Python in it.

The proposal fingerprints sources, evidence, run, workspace, harness, policy,
configuration, skills, catalog, implementation, serialized requests and cumulative
lineage. Drift requires a fresh proposal and human approval. Every mandatory
reviewer remains present; requests are not truncated or compressed to fit.

Only context bytes, aggregate input units and total units may increase. Calls,
output, tools, files, elapsed time and delivery limits cannot increase. Existing
spending is subtracted; unknown usage refuses. Hard ceilings are 1 MiB context,
4000000 conservative input units and 5000000 total units, not defaults or
tokenizer-based counts. Expiry is at most one day.

The exclusive claim precedes dispatch and is not refunded after failure. The
existing lineage lock, delivery cap and unknown-usage protections still apply.
Cached results do not dispatch again. Never delete a claim or reset lineage to retry.

## Portability and qualification

The portable modules use relative imports, standard-library authority reading,
and host-provided paths; they select no provider, shell or host. The kit does not
ship the source application's private `host.py` or MCP server. Its host must
implement the validation contract above before exposing the controller.

[bash] From a kit checkout with its existing test environment:

```bash
python -m pytest -q installer/tests/test_review_budget_exception.py installer/tests/test_review_controller.py installer/tests/test_review_packets.py
```

The source additionally tests the real capability broker/SQLite operator flow
and validated completion with Codex, Claude Code, Hermes and MOA identifiers.
Tests use temporary state and synthetic local HTTP responses, not external models.
Identifier coverage does not certify live cross-harness operation.

## Troubleshooting

- Disabled: expected until separately promoted; do not enable to silence a refusal.
- Missing backing owner: connect the host's existing ledger, never caller-selected
  model output or a new empty database.
- Approval mismatch: inspect the existing request's status, exact scope and expiry;
  do not hand-sign a replacement file.
- Source/configuration/lineage drift: rebuild the proposal; keep prior evidence.
- Unknown usage or an existing claim: reconcile through the current owner.
- `INCONCLUSIVE`, even with process exit 0: review did not pass.

Rebuild candidate manifests and packet measurements after edits. Qualification,
activation, semantic review, and Git history approval are separate gates.
