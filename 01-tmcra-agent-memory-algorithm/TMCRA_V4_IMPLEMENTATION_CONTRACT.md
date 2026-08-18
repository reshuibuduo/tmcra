# TMCRA V4 implementation contract

This file is an executable-design contract for the V4 implementation. V3 remains
unchanged and is the regression baseline.

## Non-negotiable invariants

1. Immutable source messages are journaled before any external API request.
2. Every semantic item cites one exact source message and one controller-verified
   evidence span. An API response cannot invent source IDs or evidence text.
3. A normal writable batch makes exactly one Flash request. There is no generic
   retry, alternate model, semantic fallback, or silent item repair.
4. Pro is not a blanket second-pass reviewer. It is called only for a
   controller-detected exact-slot conflict, a compact candidate-slot binding
   event, or a structurally routed embedded-document subject-attribution audit.
5. Source, fast, and slow retrieval always execute when their inventories exist.
   A planner may set roles and weights but cannot disable a layer.
6. No benchmark question, answer, label, answer session ID, or judge output may
   enter a write or slow-graph request.
7. All external calls and graph commits are restart-auditable and idempotent.
   Every clean HTTP model response is durably written with its call key and
   SHA-256 before schema validation, including responses that later fail.
   A failed schema validation may be resumed only through the explicit raw-
   response revalidation entrypoint: HTTP 200/completed metadata and both
   hashes must match, `response_json` must still be empty, and revalidation
   records zero physical API calls. A failed batch with a previously validated
   response can resume only through the explicit validated-batch recovery path:
   its frozen response hash is retained, durable reconciliation jobs must be
   completed or explicitly `pro_pending`, and a `pro_started` job may become
   pending only after process-loss review proves that no response/call artifact
   exists and records the unknown prior call cost. The graph write is replayed
   with deterministic record/edge IDs and frozen reconciliation semantics. If a
   deterministic semantic record already exists, every immutable identity field
   must match before its controller-owned state and slot head are restored to the
   replayed decision; a slot head targeting a non-active record fails audit. A
   persisted reconciliation job is replayed before proposed-slot duplicate or
   append shortcuts, and duplicate detection then runs against the frozen bound
   slot; a partial graph write cannot silently bypass its completed Pro decision.
   A committed resume also converges verified immutable source records to
   `enrichment_status=enriched` when a crash occurred after batch commit but
   before graph metadata update, recording a zero-call repair artifact. A
   selected candidate absent from the active set may be rebound only when the
   batch is in this explicit recovery path, the historical fast leaf still
   exists, every frozen identity field matches, and the leaf records
   `superseded_reason=v4_reconciliation_replace_current`. `replace_current` may
   reuse that verified historical binding; another bound decision must resolve
   to exactly one active leaf whose full semantic identity, excluding only the
   deterministic memory ID, equals the frozen candidate. A later supersession is
   valid only as a complete, same-slot, monotonic chain ending at an active leaf,
   with every hop using a recognized graph/controller reason. Missing, drifted,
   ambiguous, or otherwise inactive identity remains a hard error and the
   zero-call recovery is audited.
   Resume always reuses the journaled historical batch request; it never
   rebuilds that request from today's unresolved-interaction state.
8. Journals and job tables are control-plane state only. Source, fast assertions,
   interactions, facets, edges, and slow capsules must live in the real TMCRA
   graph tables consumed by index/retrieval; a private shadow-memory table is not
   a valid write implementation.

## Batch writer

Schema: `tmcra.memory-write-batch.v4`.

Batching is limited to consecutive messages from one session. The target is
3,000 source tokens, the soft range is 2,000-4,000, and a single message is never
split. A message over the explicit hard limit is an error, not a hidden fallback.
System and tool messages remain immutable source but do not emit semantics.
Purely empty or whitespace-only message carriers have no source text to commit:
they are excluded without renumbering later messages and must appear with their
original coordinates in `source_exclusions.json`; the strict audit verifies the
artifact against the untouched writer input.
The outer `user` role is not authorship proof for embedded content. Forwarded
emails, quoted replies, articles, resumes, transcripts, logs, and signatures
retain their local author and subject. A post-Writer subject-attribution gate
routes only document-shaped messages; DeepSeek Pro makes each semantic
keep/quarantine decision against bounded exact Source excerpts. The journal and
Fast state update share one SQLite transaction. Quarantine never deletes Source.
An embedded sender or signatory is not the chat user unless text outside the
artifact explicitly bridges those identities. A new prompt revision preserves
older audits as `superseded` and names the unique active successor; it never
silently rewrites paid audit history.
The Pro request omits Writer-authored claim text and canonical slots. A
`keep_user` result must cite an exact Source bridge distinct from the candidate
evidence itself; otherwise the response cannot commit.
Repeated benchmark `session_id` values are preserved as repeated source
occurrences. Immutable source identity remains `session_index + message_index`,
while batch sequence numbers advance within the repeated external session ID;
the preparation manifest reports every duplicate occurrence.
Explicit `belief` and `opinion` assertions retain those memory types and share
the fact-like candidate family; they are not rewritten as generic state. The
prompt taxonomy is preferred rather than a fatal closed enum: another grounded,
valid snake_case type is preserved, assigned the fact-like candidate family,
and recorded as `memory_type_extension_accepted`.
Resolution is optional control metadata: an invalid target, state, or evidence
reference is conservatively omitted with `optional_resolution_dropped`, so the
system never falsely closes an interaction or discards grounded assertions.
In an online deployment, source journaling/indexing is synchronous and visible to
the next retrieval; semantic extraction is an explicit queued enrichment state
that may flush at an idle/session boundary. Retrieval must expose that state and
must not pretend an unprocessed message already has fast/slow semantics.

The Flash request contains:

- batch ID and exact ordered message IDs;
- role, timestamp, and one ordered non-overlapping source-span sequence for each
  message. Concatenating the span text must reproduce the source content exactly;
  the request must not also repeat the full content or a token-string array;
- unresolved cross-batch interactions from the same session;
- no existing memory-slot inventory and no benchmark fields.

Assertions are cross-session user memory, not a transcript summary. Generic
topic reactions, external-world commentary, current-turn navigation, accepted
advice, and hypothetical plans are excluded. Beliefs/opinions require a
substantive personal stance; events must involve the user; goals/plans require a
real commitment beyond the current turn. Interaction extraction remains
independent, so excluding a generic assertion never deletes a real request.

The response contains one entry for every user or assistant message:

```json
{
  "schema_version": "tmcra.memory-write-batch.v4",
  "batch_id": "exact controller value",
  "messages": [
    {
      "message_id": "exact controller value",
      "message_role": "user|assistant",
      "assertions": [
        {
          "memory_type": "fact|event|state|preference|goal|constraint|plan|identity|relationship|possession|routine",
          "entity_key": "stable.domain",
          "attribute_key": "stable_attribute",
          "operation": "append|replace",
          "evidence_span_id": "eN",
          "relation": "snake_case",
          "temporal_status": "past|current|planned|future|timeless|uncertain",
          "polarity": "positive|negative|neutral",
          "durability": "durable|episodic|uncertain",
          "facets": [
            {
              "type": "entity|time|quantity|state|location|role",
              "role": "snake_case",
              "quote": "exact shortest substring of the assertion evidence span"
            }
          ]
        }
      ],
      "interactions": [],
      "resolutions": [
        {
          "target": {
            "kind": "existing|batch",
            "interaction_id": "required for existing",
            "message_id": "required for batch",
            "interaction_index": 0
          },
          "resolution": "resolved|partial|unresolved",
          "evidence_span_id": "eN"
        }
      ]
    }
  ]
}
```

For each message, the controller strips `durability` and converts the response
to the proven V3 per-message validator contract. Assertion and interaction
evidence spans remain strict. Facets/about entries are optional: a malformed or
non-exact optional entry is dropped individually with an auditable warning while
the grounded parent assertion/interaction remains; the controller never rewrites
or invents the quote. Exact facet token/character coordinates are derived locally.
Batch-local resolution targets must reference an earlier message and valid
interaction index. After sequential commit, the controller stores
durability on the corresponding fast assertion leaf and resolves batch-local
references to the real deterministic interaction record ID.

## Reconciliation

The controller first checks the exact canonical slot. Identical normalized
evidence adds provenance without a Pro call. When no exact slot exists, a local
identity-token search may expose at most three current mutable candidate leaves.
Broad structural overlap such as only `home`, `setup`, or `service` is not a
candidate signal. Candidate selector v3 requires the same memory family, at
least one discriminative shared attribute token, and at least two
discriminative shared canonical tokens;
it never changes the slot itself and never sends a full slot inventory to Flash.
Pro receives only the new cited assertion and those cited candidates. It must
choose `bind_existing`, `keep_proposed`, or `quarantine`, and pair that with an
allowed value action (`insert`, `replace_current`, `keep_parallel`, `challenge`,
or `quarantine`). A bound assertion inherits the selected slot's stable identity.
Invalid core output outside the explicit structural normalizations fails the
job and never falls back to another model or semantic guess.
For an exact-slot request, slot identity is controller-owned. A Pro
`keep_proposed/insert` response is stored raw, then normalized to
`bind_existing/keep_parallel` against the deterministic active candidate; this
changes only graph action vocabulary and preserves the model's independent-item
intent.

`keep_parallel` is also controller-owned. The graph store may classify the new
record internally, but it cannot supersede any cited current record as a side
effect of that insert. The writer restores those current states, removes false
supersession edges, and persists the new record as `parallel_active`. Historical
runs affected by the old graph-policy overwrite require the explicit
`keep-parallel-authority-2026-07-12.1` SQLite migration. That migration performs
zero API calls, journals before/after hashes and job identity, and fails closed
when it cannot prove the same-job overwrite and downstream lifecycle.

Writer validation tolerates only auditable authority-preserving transport
defects. A grounded ampersand copied into a snake-case role label is normalized
to `_and_`. Assertions emitted for an assistant-authored message are discarded
because they cannot acquire user authority; the immutable assistant source,
interactions, and resolutions remain stored. A Pro conflict action copied into
`slot_decision` is normalized to `bind_existing` only when the same allowed
action appears in `decision` and `selected_memory_id` names one supplied
candidate. Every other malformed core field still fails closed.

Process-loss recovery is explicit rather than automatic. A completed, hashed
Flash or Pro response may be revalidated with zero API calls. A journal left at
`api_started` or `pro_started` with no raw response and no call artifact may be
replaced exactly once only under the process-loss recovery flag and with the
same model. The abandoned call is recorded as a possible physical call with
unknown usage, so cost reports become incomplete (`exact_cost_cny=null`) rather
than silently undercounting it. Any durable response, conflicting artifact,
missing operator confirmation of process loss, or model change makes replacement
invalid.

## Slow graph

Slow-graph jobs are change events, not full region snapshots. `episodic` leaves
are ignored; `uncertain` leaves are auditable but not promoted automatically.
Actual record state participates in the evidence fingerprint, so superseded
leaves cannot remain current after a state-only change. A challenged fast leaf
may challenge an existing capsule through Pro but cannot create a new capsule.
An unavailable Flash/Pro client is a hard preflight failure. Such a job may be
reopened only by the explicit zero-call configuration recovery when its sole
attempt proves `physical_api_calls=0`, contains no call ID or response, and the
recovery is committed in SQLite. Any physical or outcome-unknown attempt remains
failed and is never retried through this path.
An existing Slow claim whose support becomes noncurrent is not allowed to
survive behind `noop`. The controller removes only the invalid support and its
unsupported claim. If no claim retains current support, it writes a zero-call
`retire` revision; if a durable delta also exists, the sanitized prior revision
is the base merged with the new model delta.
Slow-graph policy is evaluated over a strict public leaf projection, not the
generic graph adapter's private metadata. An empty adapter-injected
`origin_answer_ids` field is excluded from that projection and may reopen only
through the separately journaled zero-call projection recovery; any non-empty
benchmark metadata remains a hard failure. A complete paid response containing
multiple operations may be replayed without another call only when every
operation is a pure capsule-free `noop` keyed by a supplied evidence ID; those
operations have exactly one no-op effect and are collapsed to one identity-free
`noop`. Mixed actions or unknown IDs remain failed.

- No capsule + one current durable leaf: deterministic cited create, zero API.
- No capsule + multiple compatible durable leaves: one Flash consolidation.
- Existing capsule + additive compatible leaves: one Flash delta proposal.
  The model may cite only the uncited delta IDs and must not restate prior
  claims. The controller merges validated delta claims with the stored prior
  revision and validates the complete result before commit.
- Same-slot correction, counterevidence, or unresolved challenge: one Pro call.
- Potential cross-slot conflict: Flash returns the exact escalation marker,
  then the controller makes one explicit Pro call over all current durable
  evidence in the region. This is a declared route, not invalid-response
  fallback; malformed Flash output never invokes Pro.
- Pro must first distinguish actual mutual exclusion from topic overlap.
  Compatible details remain separate support-only claims. `create` with
  counterevidence and reciprocal counterevidence cycles are invalid because
  they would encode an unresolved conflict as an active new capsule.
- No new eligible evidence: deterministic noop, zero API.
- A non-noop operation must include a self-contained semantic summary of the
  complete resulting user memory. The summary is not optional metadata and may
  not be JSON, a topic heading, or a description of graph/evidence operations.
- A stored legacy summary that violates this contract and has no new evidence
  is revised through `deterministic_summary_migration`; its claims and citations
  are projected losslessly and the route is audited with zero API calls.

The resulting capsule is synthesis over cited fast leaves, not a second copy of
the fast graph. Every claim retains exact source parents through those leaves.
Every support ID must have the same canonical slot as its claim. Negative
polarity remains statement content rather than counterevidence, and active Slow
state is incomplete when stored claim/evidence semantic integrity fails even if
all durable Fast IDs were cited.
The model receives only `region` and `capsules` as user data; the GraphPatch
output contract lives in the system instruction so the model cannot satisfy the
request by echoing an input-side schema envelope.

## Recall control plane

Schema: `tmcra.recall-role-plan.v1`. Flash returns a resolved query, query kind,
temporal focus, conflict policy, and per-layer role/weight. All three local
candidate generators run first. Weights are bounded controller inputs to ranking
and packing, never layer gates. Slow capsules bridge long-range concepts; fast
assertions provide atomic semantics; immutable source windows remain the final
answer evidence.

Slow retrieval has two typed inventory entries rather than one overloaded
candidate. `capsule_summary` is the high-level long-range retrieval key;
`capsule_claim` is the auditable atomic descent unit. Dense retrieval ranks both
types independently. Top summary hits expand their declared child claim IDs,
direct claim hits are unioned with those children, and the cross encoder ranks
the union using both summary and claim text. Only claim candidates enter role
composition and packing, and every selected claim must end at its exact Source
parents. Runtime traces persist the full `summary -> claim -> Source` path.
Indexes use `tmcra.v4.online-index.2` and
`tmcra.v4.slow-inventory.1`; a V3 claim-only index is a hard incompatibility.

Fast semantic inventory contains only current graph states
(`active`, `parallel_active`, `promoted`, or `challenged`); superseded leaves
remain recoverable only through source-window coverage. When multiple layers
descend to the same immutable window, packing merges every layer contribution
without charging the window twice. Under `prefer_recent`, a window supported by
a current fast assertion is promoted by its fast within-layer rank, so an early
source-only stale mention cannot erase a later graph update signal.

## Acceptance gates

- Unit tests cover schema rejection, forbidden benchmark fields, exact evidence,
  batch-local interaction resolution, no-fallback failures, sparse slow routing,
  and all-layer recall execution.
- Manual8 uses a fresh database and the complete write-to-answer chain.
- Report physical API calls, prompt/completion/cache tokens, per-stage latency,
  source/fast/slow counts, evidence coverage, and official answer score.
- Full benchmark execution is forbidden until Manual8 passes structural audits.
- Quality execution uses one frozen, deterministic, question-type and history-
  length-stratified order. The physical build shards are disjoint `20 + 30 +
  450`; a logical shard may be split into ordered execution subshards only when
  a hashed partition artifact proves their concatenation is exact. Cumulative
  50/500 reports must never rewrite an earlier shard.
- Online retrieval schema `tmcra.v4.online-retrieval.4` records the first 24
  source-path candidate identities and source coordinates without candidate
  text or evaluation labels. Gold-session coverage is computed only after the
  retrieval process has completed.
- Final evidence membership is fixed before answer-facing order is computed.
  Selected windows are grouped by immutable session occurrence using reciprocal
  rank support, then emitted in original message/subchunk order inside each
  session. Every window records its pre-session rank and session aggregate so
  this discourse-preserving reorder remains auditable and label-free.
- Final evidence keeps answer-facing `attachments`, `memory_contexts`, and
  `provenance` as object lists. Layer scores and packing contributions live only
  in `retrieval_metadata`; retrieval audit rejects either contract if malformed.
- Every cumulative gate requires 100% structural audit pass, 100% complete
  gold-session coverage in source Top24, at least 90% gold-session hit rate in
  final Top8, and at least 90% GPT5.4 official-answer accuracy. A failed gate
  blocks the next paid shard.
- Official correctness is read from the judge's boolean
  `autoeval_label.label`; missing labels and judge errors fail closed.
