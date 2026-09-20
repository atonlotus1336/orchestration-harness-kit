# Orchestration Harness Kit v3 — MARM retrieval design

Status: proposed for user review  
Product release: `3.0.0`  
Protocol compatibility: generations 1 and 2 remain supported  
Migration posture: additive, preview-first, rollback-backed

## Decision

Version 3 is a product-generation boundary, not a protocol break. It keeps the
existing authority core, harness adapters, installer lifecycle, MemoryWeaver
data, and protocol negotiation. It replaces the kit's default knowledge
architecture with the verified MARM retrieval and ontology layer.

The release name is **Orchestration Harness Kit v3.0.0 — MARM Retrieval and
Ontology Generation**.

## Goals

1. Give every supported harness the same deterministic exact, semantic,
   document, relationship, and multi-hop routing behavior.
2. Keep MemoryWeaver the only canonical memory authority.
3. Add a disposable, evidence-bearing ontology sidecar without copying a
   recipient's corpus into the distributable kit.
4. Make relevance primary and use freshness only to break near-ties.
5. Replace normal GraphRAG and LightRAG duties with bounded portable routes.
6. Ship a real, falsifiable retrieval benchmark and reversible migration.
7. Preserve v2 data, protocol, command, and rollback compatibility.

## Non-goals

- Do not ship the owner's memories, indexes, ontology database, GraphRAG
  outputs, benchmark receipts, local provider configuration, or machine paths.
- Do not require GraphRAG, LightRAG, a named embedding vendor, or a paid API.
- Do not remove protocol generation 1 or 2.
- Do not make an ontology failure block canonical writes, startup, or ordinary
  MemoryWeaver recall.
- Do not automatically activate MARM during an upgrade from v2.

## Product and protocol versioning

The SDK/product version becomes `3.0.0`. The orchestration wire protocol remains
generation 2 because MARM adds optional retrieval fields and operations without
removing or changing required generation-2 fields. Legacy peers continue to
negotiate generation 1 or 2.

The existing v2 release remains the rollback generation. A v2 installation may
receive v3-managed files and verification tools while retaining its current
active retrieval generation until the recipient explicitly activates MARM.

## Portable architecture

### Runtime ownership

- `system/memoryweaver/` owns canonical records, strict exact retrieval,
  semantic retrieval, provenance, lifecycle events, and temporal scoring.
- `system/memoryweaver-mcp/` exposes backward-compatible `query_mode` support.
- `system/rag-orchestrator/` remains the front door and selects the active
  retrieval lane.
- `modules/marm-retrieval/` is the portable feature boundary. It contains the
  public contracts, default policy, sanitized benchmark, verifier, activation
  rules, and a user-facing README. It does not duplicate runtime source.
- `installer/modules/marm/` stages, verifies, activates, and rolls back the
  managed MARM files while preserving recipient-owned data.

### Retrieval lanes

| Intent | Default v3 lane | Invariant |
|---|---|---|
| exact | MemoryWeaver strict FTS/BM25 | No embeddings, temporal adjustment, or semantic fallback |
| semantic | MemoryWeaver hybrid | The orchestrator appends ontology context after primary ranking and cannot reorder it |
| document | Generic read-only document-retrieval port | The port permits only status and query operations |
| relationship | LapArt ontology sidecar | Bounded cited traversal with scope and confidence filters |
| multi-hop | LapArt ontology sidecar | Depth and node/edge caps apply. Every hop resolves to source evidence |

The document lane ships a provider-neutral port plus a reference adapter. A
recipient may bind local-RAG or another compatible local service, but core
policy never names or activates a vendor.

### Exact and temporal behavior

The classifier recognizes syntax-heavy identifiers, paths, extensions, flags,
function names, dotted names, URLs, quoted literals, and error strings. Callers
may force `auto`, `exact`, `semantic`, `document`, `relationship`, or
`multi-hop`. Existing backend mode parameters remain accepted separately.

Semantic scoring computes relevance first. A bounded half-life freshness bonus
applies only within a configured relevance band. Exact results never enter this
scorer. Responses expose relevance, temporal contribution, final score, policy
version, selected lane, and route reason.

### Ontology sidecar

Treat the ontology as derived and disposable. It stores stable source handles,
bounded excerpts or hashes, nodes, typed edges, lifecycle state, confidence,
and evidence links—not a second canonical corpus. SQLite uses WAL on a local
runtime filesystem that the installer chooses. Windows/WSL installations use the
host-appropriate bridge and never open the same database concurrently across
filesystems.

Canonical mutations emit idempotent create, update, revoke, supersede, and
delete events after canonical success. A bounded spool/outbox, leased worker,
reconciler, retry budget, and quarantine keep the sidecar current without
blocking MemoryWeaver. The worker indexes only recipient-registered roots and
manifests. There is no default broad crawl.

### GraphRAG and LightRAG

GraphRAG may remain registered only as `manual-diagnostics/unverified` when a
recipient already has it. It is never eligible for automatic routing, fanout,
fallback, or recovery claims. The kit does not install or build a GraphRAG
index. LightRAG is absent from production routing and evaluation registration.

## Installation and migration

### New v3 full-stack installation

The installer installs and verifies MARM files by default. Activation occurs only after
the empty-store smoke test and sanitized benchmark pass. Optional document or
ontology dependencies may be unavailable. In that case, the system reports a
truthful degraded state and keeps canonical exact and semantic MemoryWeaver usable.

### Upgrade from v2

1. Preview the managed-file and data-path plan.
2. Snapshot managed files. Never snapshot or copy private record contents into
   the kit repository.
3. Install MARM runtime files in an inactive state.
4. Migrate schema only on temporary copies and verify idempotence.
5. Build the derived ontology from recipient-authorized sources.
6. Run the sanitized benchmark, route checks, and rollback rehearsal.
7. Require a separate recipient confirmation to activate MARM.

Failure restores the exact v2 managed-file snapshot and leaves recipient data
untouched. Operators may discard and rebuild the ontology database without
affecting canonical recall.

## Verification and evidence

The portable module ships a sanitized frozen suite covering exact identifiers,
paths, semantic recall, temporal ties, authoritative older evidence, document
read-only enforcement, relationship and multi-hop evidence, lifecycle changes,
scope isolation, degradation truth, and hostile retrieved content.

Every load-bearing gate includes a named failing input. At minimum, the verifier
must inject and detect:

- an exact request misrouted to semantic
- a missing citation
- a temporal inversion outside the relevance band
- an orphan relationship edge
- a project/platform scope leak
- an unauthorized document mutation operation
- automatic GraphRAG eligibility
- a manifest or source-hash change.

Release qualification runs on clean Windows and Linux/WSL targets, validates
protocol generations 1 and 2, exercises upgrade and rollback from the last v2
release, compares bundle bytes, and runs both preview and raw-Git privacy gates.

## Documentation and release surface

The release adds `docs/v3/` with a v2-to-v3 migration guide, compatibility
matrix, activation boundary, rollback procedure, release notes, and retrieval
operations guide. The top-level README describes v3 as the same orchestration
foundation with a new knowledge architecture.

The version policy changes the SDK release to `3.0.0` while leaving protocol
support `[1, 2]`. Installer manifests add the MARM module after canonical memory
and before the RAG front door. The public payload allowlist includes only the
portable runtime, contracts, tests, fixtures, and documentation.

## Acceptance criteria

- Existing v2 memory data loads without destructive backfill.
- Existing generation-1 and generation-2 protocol conformance remains green.
- All five retrieval intents select the expected lane in the sanitized suite.
- Exact mode makes zero embedding calls and never applies temporal scoring.
- Temporal scoring cannot invert results outside its relevance band.
- Every relationship sentence and hop resolves to recipient-owned source
  evidence.
- Document mutation/admin operations fail closed.
- Missing or corrupt ontology state leaves canonical retrieval available with
  truthful degradation metadata.
- GraphRAG cannot become automatic through default configuration.
- Upgrade rollback restores the v2 managed-file snapshot and preserves data.
- Privacy scans find no owner identity, corpus, memory, index, credential, host,
  or local evidence content.

## Implementation sequence

1. Freeze sanitized v2 and v3 retrieval baselines.
2. Port and sanitize MemoryWeaver exact/scoring/provenance changes.
3. Port the ontology contract, store, event pipeline, and bounded worker.
4. Port and sanitize orchestrator routing and provider-neutral document port.
5. Add installer staging, migration, activation, and rollback.
6. Add the falsifiable benchmark and cross-platform qualification.
7. Update v3 documentation, manifests, version policy, changelog, and bundle
   gates.
8. Run independent evidence and privacy audits before release authorization.
