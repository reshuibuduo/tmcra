# Hierarchical Fast/Slow Memory Graph V1

## Decision

TMCRA uses one physical evidence graph with two logical layers. The slow layer is
an immutable, versioned overlay grown from fast atomic evidence. Fast records are
never moved into, rewritten by, or deleted by the slow layer.

## Model Responsibilities

- DeepSeek Pro is the slow-graph control plane. It proposes typed `GraphPatch`
  operations for capsule lifecycle management.
- The local node/path graph models are execution-plane models. They retrieve fast
  evidence and candidate neighborhoods; they do not create, merge, split, or
  retire capsules.
- DeepSeek Flash produces one discrete `RecallPlan` before retrieval.
- A deterministic transaction controller validates and commits patches. An LLM
  never writes SQLite directly.

## Write Pipeline

1. Persist the immutable source message.
2. Atomically claim the source-journal row before any Writer API call, then stage
   Writer output under that owner/token lease.
3. Commit fast assertions, facets, interactions, and provenance.
4. Stage the graph commit, complete call/message logs, and audit the fast graph.
5. Enqueue slow-graph region jobs only after the Writer audit passes.
6. DeepSeek Pro proposes GraphPatch operations from supplied fast leaf evidence.
7. Validate evidence scope, revision, claim provenance, and operation schema.
8. Commit immutable capsule revisions, typed edges, and append-only patch logs.
9. Build retrieval indexes only after all required capsule jobs are completed and
   the slow-graph audit passes.

Source speaker role is controller-owned immutable metadata. A model role echo
that conflicts with the source is retained as a validation warning and replaced
before semantic layers are validated against the real source role.

## Capsule Invariants

- Capsule revisions are immutable.
- Capsule identity is controller-owned and derived from `scope_id + region_key`.
  The manager cannot choose or rename persistent capsule identity. A patch that
  names a capsule owned by another region is rejected before revision lookup.
- A region has at most one current capsule head. New evidence mutates that head;
  unchanged regions reuse their completed batch jobs and do not call Pro again.
- A slow job is claimed with `BEGIN IMMEDIATE` before Pro is called. Job and
  attempt completion/failure are compare-and-swap updates bound to the same
  owner/token lease. A competing controller cannot call Pro, and a late failure
  cannot overwrite a completed job. Healthy calls renew their lease. An expired
  started call has an unknowable external outcome, so it retains its attempt as
  audit history and moves the job to visible failure; only explicit `resume`
  authorizes a possible replay.
- Only fast `product_semantic_memory` leaf records can support or contradict a
  claim. Capsules, summaries, aggregates, clusters, and other derived nodes cannot
  increase support or resolve challenges.
- Every Pro evidence item carries controller-owned `record_state` and
  `slow_graph_evidence_role`. `historical_noncurrent` evidence cannot be promoted
  as a current claim while a `current_authoritative` replacement exists for the
  same slot; the final slow audit enforces this invariant.
- Every non-noop claim has explicit leaf evidence and structured source-parent
  locations including the exact evidence character span. Slow-to-fast descent
  selects only overlapping subchunks, never every subchunk in a long message.
- Claim semantics and evidence selection come from Pro. The controller assigns a
  deterministic technical `claim_id` and records an omitted empty evidence side
  as `[]`; the raw GraphPatch remains append-only for audit.
- `canonical_slot` is bound to cited fast leaves. If all cited evidence exposes
  one unique slot, that leaf slot is authoritative and a differing model echo is
  retained as a controller normalization. Multiple cited slots remain ambiguous
  and fail closed.
- Every revision transition uses optimistic `base_revision` validation.
- V1 rejects topology `merge` and `split`. Those operations require a separate
  global consolidation job plus explicit child-region routing; exposing the
  incomplete regional implementation would create capsules that cannot receive
  later evidence.
- A correction can challenge an active capsule immediately. Formal resolution is
  asynchronous and versioned.
- Manager failure leaves a visible failed or retryable job. There is no heuristic
  promotion fallback or semantic repair.
- Graph snapshot persistence refreshes the authoritative slow records, heads,
  history, and edges under the same write lock before replacing fast state. A
  stale in-memory adapter cannot erase a committed slow revision.
- Retrieval and answer-support audit events use append-only SQLite transactions;
  they never save an in-memory graph snapshot or rewrite fast graph state.
  Final layered retrieval audits carry a deterministic operation id, so retrying
  the same output operation confirms the existing event instead of appending a
  duplicate.
- Full graph saves use a structural `storage_revision` compare-and-swap. A stale
  fast snapshot fails before deletion, while audit events committed since the
  snapshot are merged under the write lock. One adapter instance serializes its
  read-modify-write operations with a reentrant lock.

## Recall Contract

The planner chooses exactly one execution mode:

- `FAST_ONLY`
- `SLOW_ONLY`
- `SLOW_WITH_FAST_OVERRIDE`
- `FAST_WITH_SLOW_CONTEXT`
- `CONFLICT_COMPARE`

Fast and slow scores are never added. Ranking occurs within the selected primary
layer or over composed canonical-slot units. Context-only evidence cannot compete
with primary evidence. Conflict mode retains both sides and their provenance.
Slow capsules must descend to valid immutable source-parent locations before they
can enter answer evidence.

Fast candidates use graph+dense retrieval and the trained VR reranker. Slow claims
use their own dense shortlist and cross-encoder rerank. Packing admits complete
units only; multiple claims that share one physical source window merge their
roles and capsule contexts instead of duplicating the window or dropping a claim.
Within one retrieval process, graph adapters are cached by logical scope and
database path so node/path models pay one cold start rather than reloading for
every query. Every adapter retrieval still reloads persisted graph state before
scoring. The graph scorer caches tensorized graph state and graph encodings, but
query-dependent full-graph recall and event/path relation scoring still execute
for every graph-routed query.

Checkpoint loading is fail-closed in the product scorer. Larger historical
categorical embedding tables may be narrowed to the runtime's stable vocabulary
prefix; all remaining missing, unexpected, or skipped parameters are fatal.
The runtime must never continue with randomly initialized checkpoint gaps.

The first graph pass is recall-only: it computes the same full-graph recall
logits but does not execute event/path rerank heads whose outputs are discarded
before the bounded second pass. Runtime tensor scores are transferred from GPU
to CPU in batches rather than one scalar synchronization at a time.
The GPT-5.4 answer layer receives both immutable source text and the selected slow
claim/context roles in one logical answer call. In
`SLOW_WITH_FAST_OVERRIDE`, the newer fast evidence is rendered with explicit
override precedence instead of being treated as untyped source text.

## Failure Semantics

- Missing model configuration, malformed API output, stale revisions, invalid
  evidence, unmapped source parents, and inconsistent indexes are fatal and
  auditable.
- Completed stages may resume only when their input fingerprint matches.
- Writer source rows use the same owner/token lease discipline. The controller
  records an API-start marker before the external call and renews the lease while
  it is in flight. If the process dies after the provider may have accepted the
  request but before output is staged, the row becomes failed/uncertain and is
  never replayed automatically. Exactly-once provider execution cannot be
  claimed without provider-side idempotency.
- Compatible online indexes are validated and reused without loading the
  embedding backend. A committed retrieval directory is reconciled and reused
  without rerunning the planner or reranker; incomplete directories fail visibly.
- A resumed full chain reuses existing slow jobs instead of re-enqueueing a new
  batch. Expired `started` attempts are closed as explicit uncertain audit
  history and require operator-authorized resume.
- Non-authoritative free-text planner reasoning is retained in debug output, not
  in the authoritative `RecallPlan`, so repeated evidence files must be byte-for-
  byte identical.
- Online indexes are bound to a logical scope fingerprint over records, heads,
  history, and graph edges; equal record counts are not considered sufficient.
- Slow-control failure does not roll back committed fast memory.
- Retrieval never substitutes a different mode or model after planner failure.

## Acceptance Gates

1. Unit tests for Writer transaction recovery, GraphPatch lifecycle, provenance,
   five recall modes, and fail-closed behavior.
2. Synthetic multi-turn test with durable state, correction, immutable revision,
   reinforcement, and a newly introduced routine.
3. Restart test proving no repeated Writer or manager API call after commit.
4. Real single-sample full chain: write, fast audit, capsule management, index,
   planned recall, rerank, GPT-5.4 answer, deterministic proof that the answer is
   present in the first window's current active slow claim/source, and official
   judge.
5. Only after the single-sample chain passes may the benchmark scope expand.
6. Cross-process graph retrieval must produce identical recall and selected
   event IDs for the same checkpoint, graph fingerprint, and query.
7. Product latency is a separate gate from correctness. On the 4090 acceptance
   host, the optimized five-mode matrix still measured 11.8-17.1 seconds in the
   graph stage per graph-routed query. Correctness is accepted; production
   latency remains open until full-graph recall and relation scoring are profiled
   and brought under the deployment SLO without reducing recall coverage.
