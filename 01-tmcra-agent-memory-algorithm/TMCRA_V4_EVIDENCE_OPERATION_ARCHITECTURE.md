# TMCRA V4 Evidence Operation Architecture

Status: semantic compiler implementation contract

## 1. Objective

TMCRA must answer production memory questions from immutable source evidence without making retrieval membership depend on one LLM classification or selection decision.

The runtime is split into four responsibilities:

1. Recall preserves a broad source evidence reservoir.
2. Graph layers annotate and relate source evidence but do not replace it.
3. Evidence operations compile source evidence into verifiable structures and deterministic calculations.
4. The answer model expresses an answer whose claims are bound to source IDs and computed results.

## 2. Non-negotiable invariants

1. Source evidence text is immutable and remains the only answer-bearing evidence.
2. Fast-graph and slow-graph outputs must map back to immutable source coordinates.
3. A planner may annotate, group, or request operations. It may not delete the source reservoir.
4. Dates, differences, ordering, counts, sums, set operations, and exact entity comparisons are executed by code when their operands are available.
5. Every final factual claim must cite source evidence IDs or a deterministic computation that cites source evidence IDs.
6. Missing evidence is an explicit result, not a process failure and not permission to guess.
7. Model output errors and evidence insufficiency are different states and are audited separately.
8. Benchmark labels, gold answers, answer-session IDs, and supervision fields are forbidden runtime inputs.
9. No hidden fallback may change retrieval or answer behavior. Any second pass is an explicit, journaled stage over the same frozen evidence reservoir.

## 3. End-to-end pipeline

```text
question + recent dialogue
    -> source dense/cross retrieval
    -> immutable source reservoir
    -> fast/slow graph annotations mapped to source
    -> query-time evidence graph
    -> evidence atom catalog
    -> multi-operation plan
    -> deterministic operation executor
    -> compiled evidence packet
    -> evidence-bound answer
    -> claim and computation validator
```

## 4. Recall contract

### 4.1 Source reservoir

The source path remains:

```text
BGE-M3 dense shortlist
    -> parent-window deduplication
    -> BGE reranker cross encoding
    -> lossless parent-window reconstruction
    -> source reservoir
```

Top24 is the current candidate reservoir for evaluation, not a permanent universal constant. Production metrics must distinguish:

- session coverage;
- exact source-message coverage;
- exact source-span coverage;
- complete evidence-set coverage.

Session coverage alone must never be reported as complete evidence coverage.

### 4.2 Fast graph

Fast graph contributes current-state, event-neighbor, conflict, recency, and learned ranking signals. Every contribution must contain source coordinates. Fast graph cannot introduce answer text that is not present in source.

### 4.3 Slow graph

Slow graph contributes durable concepts, aliases, long-term preferences, and cross-session relationships. A slow claim is context metadata until its cited source parents are resolved. Slow text cannot replace source evidence.

### 4.4 Membership protection

Graph signals may reorder or annotate members of the source reservoir. They may propose additional source windows for a separately budgeted expansion set, but they may not evict reservoir members before evidence compilation.

## 5. Query-time evidence graph

The evidence graph is ephemeral and scoped to one query. It is not written into the persistent memory graph.

Node classes:

- `evidence`: immutable source window;
- `session`: one immutable conversation occurrence;
- `entity`: entity or resolved alias mentioned by evidence;
- `event`: event supported by one or more evidence nodes;
- `state`: historical or current state supported by evidence;
- `atom`: normalized date, number, quantity, currency, duration, or exact string operand.

Edge classes:

- `belongs_to_session`;
- `mentions_entity`;
- `supports_event`;
- `supports_state`;
- `precedes`;
- `updates`;
- `conflicts_with`;
- `same_entity_as`;
- `derived_from`.

Every non-source node and every computed edge must retain source evidence IDs.

## 6. Evidence atom catalog

Before an LLM plans operations, code extracts verifiable operands from the question and evidence reservoir.

Initial atom types:

- normalized calendar dates;
- session dates;
- numbers and ordinals;
- currencies and quantities;
- durations and frequencies;
- exact entity strings;
- source/session coordinates.

Each atom has a stable ID and provenance:

```json
{
  "atom_id": "D03",
  "atom_type": "date",
  "raw_text": "April 24, 2023",
  "normalized_value": "2023-04-24",
  "evidence_id": "E12",
  "char_start": 81,
  "char_end": 95
}
```

The operation planner selects atom IDs. It does not invent operands.

## 7. Multi-operation plan

The planner does not classify a question into one route. It emits zero or more requirements and operations.

```json
{
  "schema_version": "tmcra.evidence-operation-plan.v1",
  "requirements": [
    {
      "requirement_id": "R01",
      "description": "date of the baking class",
      "evidence_ids": ["E03"]
    }
  ],
  "operations": [
    {
      "operation_id": "O01",
      "operation_type": "date_difference",
      "input_atom_ids": ["D01", "D03"],
      "input_evidence_ids": ["E03", "E12"]
    }
  ],
  "bundles": [
    {
      "bundle_id": "B01",
      "role": "temporal_sequence",
      "evidence_ids": ["E03", "E12"]
    }
  ]
}
```

The plan is multi-label and compositional. One question may require entity binding, temporal ordering, aggregation, state resolution, preference synthesis, and counterevidence at the same time.

## 8. Operation executor

Initial deterministic operations:

- `date_difference`;
- `date_order`;
- `numeric_sum`;
- `numeric_difference`;
- `numeric_average`;
- `count_distinct`;
- `ordered_unique_list`;
- `latest_state`;
- `entity_exact_match`;
- `entity_mismatch`;
- `set_difference`.

An operation executes only from catalog atom IDs and source IDs. Invalid operands produce a structured operation error. They never silently invoke model arithmetic.

Semantic-only operations such as causal composition and preference synthesis remain model tasks, but their evidence bundles are explicit and source-bound.

## 9. Evidence compiler

The compiler does not summarize the memory into a new narrative and does not remove the raw reservoir. It creates an answer packet with five sections:

1. question contract;
2. requirement coverage matrix;
3. deterministic operation results;
4. role-grouped source evidence;
5. complete raw source reservoir appendix.

Requirement states:

- `satisfied`;
- `conflicting`;
- `missing`;
- `invalid_operation`.

The compiler must expose missing and conflicting requirements before answering.

## 10. Answer contract

The answer model receives the compiled packet and returns:

```json
{
  "schema_version": "tmcra.evidence-bound-answer.v2",
  "answerability": "sufficient",
  "claims": [
    {
      "claim_id": "C01",
      "text": "The elapsed time was 21 days.",
      "support_ids": ["E03", "E12"],
      "computation_ids": ["O01"]
    }
  ],
  "missing_requirements": [],
  "answer": "21 days."
}
```

The answer model must not recompute deterministic operation results. It may verbalize them.

## 11. Answer validator

The validator checks:

- every support ID exists in the frozen packet;
- every computation ID exists and completed successfully;
- sufficient answers have no missing requirements;
- every planned required operand is represented;
- claims do not bind values across mismatched entities;
- no unsupported factual claim is emitted;
- insufficient answers name the missing requirement.

A validation failure may trigger one explicit answer revision over the same packet. It cannot trigger hidden retrieval changes.

## 12. Cost and latency policy

1. Local recall and deterministic operations run for every query.
2. One operation-planning call is the initial API budget.
3. A second compiler or answer-revision call is allowed only when the structured plan reports multiple operations, conflicts, missing coverage, or failed answer validation.
4. Every physical call records model, token usage, latency, request hash, response hash, and stage.
5. The runtime accumulates planner and validation supervision for later local-model distillation. No local planner is trained until the corpus covers these operations with sufficient quality.

## 13. Failure semantics

- `runtime_error`: storage, index, model, API, or schema process failure;
- `invalid_plan`: planner output cannot be bound to supplied IDs;
- `operation_error`: valid plan with invalid or unsupported operands;
- `evidence_insufficient`: runtime succeeded but requirements are missing;
- `answer_invalid`: answer claims fail evidence or computation validation;
- `completed`: answer passed all contracts.

These states must not be collapsed into one generic error.

## 14. Implementation sequence

1. Implement evidence IDs, atom extraction, and query-time evidence graph.
2. Implement plan schema and deterministic executor with unit tests.
3. Implement compiled packet generation without an API planner using fixture plans.
4. Add the API operation planner with durable per-query call journals.
5. Render compiled packets in the GPT-5.4 answer layer.
6. Add claim/computation validation and one explicit revision boundary.
7. Smoke-test existing failure cases for temporal, aggregation, state update, preference, and false premise behavior.
8. Run the full 75-question regression only after the failure-case gates pass.

## 15. Acceptance gates

- Source reservoir membership is unchanged by planner output.
- Exact evidence-span coverage is reported separately from session coverage.
- Deterministic operation fixtures are 100% correct.
- No answer claim can cite an unknown source or computation ID.
- Temporal and aggregation failure cases use executor results instead of model arithmetic.
- Preference answers cite specific historical evidence.
- False-premise cases expose entity mismatch or missing evidence.
- No default production mode changes before full regression approval.

## 16. Semantic compiler amendment

This section supersedes the planner, compiler, answerability, and answer-layer
semantics in sections 7 and 9-11. The immutable recall and deterministic
operation contracts remain unchanged.

### 16.1 Unified runtime

```text
question + dialogue state
    -> evidence-independent Task Contract
Source Top24
    -> grounded Source Claim Ledger
Task Contract + Claim Ledger
    -> Premise Bindings + Resolution Program
    -> multidimensional Answerability Certificate
    -> bound-premise answer execution
    -> claim-origin and premise-provenance validation
```

The runtime never branches on benchmark question types. A contract is a
composition of target, relation, output shape, scope, premises, and permitted
derivations.

### 16.2 Evidence-independent Task Contract

The contract planner receives the question, question date, and an optional
compact dialogue state. It does not receive candidate evidence. This prevents
available evidence from changing the interpretation of the user's request.

The contract distinguishes four output origins:

- `memory_direct`: the requested payload itself must be remembered;
- `memory_derived`: the payload is computed from remembered premises;
- `memory_conditioned`: memory supplies constraints or background for a new response;
- `external_required`: a current-world payload requires a tool or external source.

Every premise also declares its source independently: `memory`,
`query_context`, `model_knowledge`, or `external_tool`. Only memory premises
enter Source resolution. Deterministic normalization removes a memory premise
that merely repeats current request text, while preserving at least one real
required memory premise for a memory-conditioned task.

### 16.3 Source Claim Ledger

Evidence IDs are not semantic support by themselves. Each usable memory claim
contains subject, predicate, object, valid time, polarity, modality, one Source
evidence ID, and an exact contiguous Source quote. A complete premise must bind
to grounded claims or a completed deterministic operation.

### 16.4 Resolution Program

Every contract premise is bound exactly once with independent coverage and
relation fields. Operations must be permitted by the contract, use supplied
atoms, name their output premises, and be referenced back by those premise
bindings. Operation-to-premise references are validated in both directions.
An operation that computes the requested payload names `TARGET` in
`output_refs`; `TARGET` is not a synthetic premise and cannot be bound as one.

Memory-direct or memory-derived `count`, `list`, and `set` outputs receive a
conditional completeness review after initial resolution. The reviewer audits
the whole Source reservoir in scope, including grounded multi-sentence state
transitions, deduplicates repeated mentions, and verifies that every distinct
in-scope claim reaches the TARGET operation. Other output shapes do not pay for
this additional call.

### 16.5 Answerability Certificate

Memory coverage is `complete`, `partial`, `conflicting`, or `absent`. Task
executability is independently represented as directly answerable, derivable,
partially answerable, memory-conditioned generation, external-tool required,
clarification required, or not answerable. Partial direct or
memory-conditioned evidence may produce a bounded partial answer that names
the unresolved premise. Partial derived aggregates still abstain because an
incomplete operand set cannot produce a trustworthy total. The absence of a
prewritten final response is not evidence
insufficiency when all required remembered constraints are present.

### 16.6 Answer execution

The answer model receives only the contract, certificate, bound premises,
grounded Source claims, their exact Source evidence, and completed operation
results. It does not rescan or replan the unbound Top24 reservoir.

Final claims declare one origin:

- `memory_fact` must cite Source claim IDs through the corresponding premises;
- `memory_derived` must cite completed operation IDs through the premises;
- `model_knowledge` is allowed only for memory-conditioned generation and may not cite memory;
- `tool_result` is reserved for an explicit tool-completed stage.

A partial memory-conditioned answer must still cite at least one bound memory
claim. A generic model-knowledge response that ignores available personal
constraints is rejected even when it correctly declares unresolved premises.

The validator derives Source evidence IDs from Source claims. The answer model
cannot attach an arbitrary evidence ID to an unrelated statement.

### 16.7 Durable execution

Task Contract and Resolution Program calls commit separate row journals. A
process interruption after either stage reuses the committed stage instead of
repeating API work. Schema repair is one explicit, journaled same-model call;
HTTP, authentication, billing, or runtime failures do not trigger hidden
fallback behavior.

## 17. Production integration amendment (2026-07-13)

This section supersedes conflicting production-path statements in sections 7,
9-12, and 16. The production route remains:

```text
Source Top24
    -> source-group neighbor attachment
    -> operation planner envelope
       - task contract
       - Source-bound requirements and bundles
       - optional typed observations and programs
    -> deterministic validation and execution
    -> immutable compiled packet plus complete Source reservoir
    -> one evidence-bound GPT-5.4 answer
```

The planner uses one benchmark-independent contract. It does not select one of
several question-type routes. `output_origin` distinguishes remembered payloads,
derived payloads, memory-conditioned generation, and external requirements.
Recommendation and advice requests use `memory_conditioned_generation`: memory
premises bind user constraints and preferences, while the final recommendation
is newly generated. The absence of a historical ready-made recommendation is
not missing evidence.

Source-group context attaches nearby messages from the same immutable session
to a selected Source window. It has a strict distance and character budget,
retains exact text and coordinates, and is part of production Source identity.
The compiler and answer layer process Source windows chronologically instead of
reordering them by lexical overlap.

Typed programs handle only deterministic, provenance-bound operations such as
latest-state selection, date and duration differences, numeric arithmetic,
rate multiplication, averages, relative offsets, and actual-event counts.
Every event observation declares `actual`, `planned`, `hypothetical`, or
`mentioned`. Accepted results are advisory for their exact operands; rejected
programs produce diagnostics and never establish absence.

Planner `missing` states are hypotheses. A second planner review is allowed only
for explicit structural risks or unbound required memory premises and is
journaled as another physical call. The GPT-5.4 answer layer sees the complete
Source reservoir and may correct planner coverage, but every final factual claim
must still bind to Source IDs or completed computation IDs.

Evaluation writes a post-judge reference-backed audit sidecar. Gold answers and
answer-session IDs never enter retrieval, compilation, or answering. Promotion
requires a targeted wrong-case plus guard-set gate before a full 100-question
paid run; the answer model stays fixed to GPT-5.4 throughout comparisons.
