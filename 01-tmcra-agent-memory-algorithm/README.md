# TMCRA agent memory algorithm

The shared V4 algorithm modules consumed by both the benchmark distribution
and the production memory service. The exact file set is pinned by
`shared_core_manifest.json` (SHA-256 per file; the service fails closed on
mismatch).

## Layered memory graph

- **Source layer** — durable raw evidence with actor provenance and
  timestamps.
- **Fast layer** — freshly ingested assertions, searchable within seconds of a
  write.
- **Slow layer** — batched semantic evolution (merge/revise/challenge) that
  consolidates the graph without a second per-message write.

## Key modules

| Module | Role |
|---|---|
| `tmcra_v4_batch_writer.py` | Durable batch ingestion with idempotent claims, leases, and explicit commit markers |
| `tmcra_v4_slow_graph.py` | Slow-layer semantic graph maintenance |
| `tmcra_v4_online_runtime.py` | Online retrieval runtime used by recall |
| `tmcra_v4_recall_planner.py` | Recall planning and evidence selection |
| `tmcra_v4_evidence_operations.py` / `_planner.py` | Evidence compilation (raw vs compiled) |
| `tmcra_v4_task_contract.py` / `typed_semantics.py` | Task and typed-semantics contracts |
| `tmcra_v4_route_policy.py` / `cost_report.py` | Routing policy and cost accounting |

Design documents: `TMCRA_V4_IMPLEMENTATION_CONTRACT.md`,
`TMCRA_V4_EVIDENCE_OPERATION_ARCHITECTURE.md`, `FAST_SLOW_GRAPH_V1.md`.
