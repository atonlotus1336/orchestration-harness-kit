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
