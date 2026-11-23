# Semantic v1 implementation contracts

The three Schema files are implementation copies of the frozen 2026-11-18
`semantic-contract/1.0.0` design. Their original files and evaluation golds are
unchanged. Runtime DTOs are separate; never add API metadata to a closed asset or
RetrievalBundle object.

`local-seed.json` contains metadata only: the frozen 429-node migration projection,
exact source JSON texts, and the explicit logical-to-PostgreSQL table map. It has no
credentials and no query result rows. All imported authority revisions start as
DRAFT, with unknown business ownership/policy preserved. Sources/QA remain
technical observations. The source `state` field means geographic region; it is
not buyer business status.

The local technical deployment uses a **TechnicalSnapshotManifest**, deliberately
separate from a production Release because the latter requires genuine Approval
references. The Bundle still follows the strict retrieval Schema; scope and
limitations stay in its transport envelope. Exact/BM25/GRAPH are implemented;
Vector is disabled and is not reported as executed. Source timestamps have unknown
timezone. Typed time policy and coverage remain null where unknown; the executor
must observe actual coverage and receive explicit SOURCE-time confirmation.

Production governance is a separate explicit workflow:

1. Admin assigns real existing workspace users as STEWARD, BUSINESS_OWNER,
   DATA_OWNER, PUBLISHER through `/semantic/assignments`.
2. Admin creates an immutable read/governance object policy and gives its exact
   reference to a new asset revision. This policy does not grant database row or
   column execution rights.
3. Steward registers a versioned BUSINESS_SPEC source and archives the exact JSON
   text through `/semantic/sources/{id}/archive`; hash must match. This release
   supports JSON_POINTER evidence, not arbitrary URL/file fetching.
4. Required structural, source, ownership, policy and type-specific verification
   must PASS. Submit freezes target/dependency hashes.
5. A separately assigned Owner approves the exact revision. A revision author
   cannot approve it; Binding/Relation need separate business and data identities.
6. Publisher seals a dependency-closed Release, builds a checked index and activates
   the matching pair with CAS. Current revocation remains authoritative.

Default local data has no automatic role assignments, policies or approvals.
`production_ready_paths` for those data remains zero. The root Agent compiler
continues to reject production execution until real data-source policy and query
acceptance are enabled. Source labels, units, currency, JOIN semantics and other
known governance gaps are not automatically repaired by migration.

Tests: `scripts/test_semantic_v1.py` checks typed seed/recall/closure/negative cases;
`scripts/test_semantic_workflow.py` exercises real FastAPI routes over an isolated
database with explicit identities (auth dependency override). The latter validates
workflow authorization/CAS, not cookie/CSRF authentication, which is covered by live
deployment acceptance. PostgreSQL migration installs immutable-content triggers;
SQLite test fixtures do not represent production trigger enforcement.
