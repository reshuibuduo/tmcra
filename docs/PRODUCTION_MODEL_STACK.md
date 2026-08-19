# Production Model Stack

This document maps every model-bearing stage in the supported TMCRA production
path to its job, configuration boundary, deployment location, and replacement
contract. TMCRA is a memory runtime that combines local retrieval models,
deterministic evidence operations, self-hosted generation models, optional
provider routes, and an operator-controlled outer agent.

The default production generation model is **`Qwen3.6-35B-A3B`**, with 35B total
parameters and about 3B active parameters per token. The validated deployment
serves it locally as `tmcra-qwen3.6-35b-a3b-iq3s` for Writer, Reviewer, recall
planning, and slow-graph generation.

For Chinese, see [PRODUCTION_MODEL_STACK.zh-CN.md](PRODUCTION_MODEL_STACK.zh-CN.md).

## End-to-end request path

```mermaid
flowchart LR
  EVENT["Attributed message or event"]
  WRITER["Writer model<br/>Qwen3.6-35B-A3B"]
  REVIEW["Reviewer / reconciliation<br/>Qwen3.6-35B-A3B"]
  STORE["Source + fast + slow memory"]
  EMBED["BAAI/bge-m3<br/>dense retrieval"]
  GRAPH["Graph and deterministic candidates"]
  VR["TMCRA runtime reranker<br/>bundled checkpoint"]
  CROSS["BAAI/bge-reranker-v2-m3<br/>cross encoding"]
  PLAN["Recall role planner<br/>Qwen3.6-35B-A3B"]
  COMPILE["Deterministic evidence compiler"]
  PACK["Bounded evidence pack"]
  AGENT["Operator's agent model<br/>or published reference answer route"]

  EVENT --> WRITER --> REVIEW --> STORE
  STORE --> EMBED
  STORE --> GRAPH
  EMBED --> CROSS
  GRAPH --> CROSS
  CROSS --> VR --> PLAN --> COMPILE --> PACK --> AGENT
```

The operator selects the outer agent. An application may use any model that can
consume the returned evidence object while preserving the trust boundary. The
published reference/evaluation run used GPT-5.4; the answer and judge runners
now accept operator-selected model identities as well.

## Models and deterministic stages

| Stage | Reference production identity | Purpose | Runs where | Configuration / contract |
|---|---|---|---|---|
| Primary Writer | `Qwen3.6-35B-A3B` | Extract attributed source records and fast-memory assertions from accepted writes | Local OpenAI-compatible endpoint | `TMCRA_WRITER_PROVIDER=local-qwen`, `TMCRA_LOCAL_WRITER_*`, `TMCRA_WRITER_PROMPT_ADAPTER=qwen36-v5` |
| Writer reviewer | `Qwen3.6-35B-A3B` | Reconcile ambiguous/conflicting writer output and perform the stronger review path | Local OpenAI-compatible endpoint | `TMCRA_WRITER_REVIEWER_PROVIDER=local-qwen`, `TMCRA_WRITER_REVIEWER_PROMPT_ADAPTER=qwen36-reconciliation-v1` |
| Slow graph | `Qwen3.6-35B-A3B` | Build durable semantic capsules and repair cross-slot conflicts in batches | Local OpenAI-compatible endpoint | `TMCRA_SLOW_GRAPH_PROVIDER=local-qwen`, `TMCRA_SLOW_GRAPH_PROMPT_ADAPTER=qwen36-slow-graph-v1` |
| Dense embedding | `BAAI/bge-m3` | Embed queries/source windows for dense shortlist generation | Local GPU in the Memory API process | `TMCRA_EMBEDDING_MODEL`; source contract uses 1,024 dimensions and max length 8,192 |
| Graph candidates | No foundation model in the supported public profile | Generate fast/slow graph candidates and source-coordinate annotations | Deterministic/local runtime | `TMCRA_LEARNED_GRAPH_ENABLED=0` is the supported public default |
| Cross encoder | `BAAI/bge-reranker-v2-m3` | Rerank the dense/graph union using query-evidence pairs | Local GPU | `TMCRA_CROSS_MODEL`; production lane uses max length 1,280 and batch size 24 |
| TMCRA runtime reranker | `tmcra_v3_reranker.pt` | Fuse cross-encoder representations/logits with dense, graph, selection, and recency channels | Local GPU; checkpoint is included | `TMCRA_CHECKPOINT`; Apache-2.0 declaration and checksum are in component 02 |
| Recall role planner | `Qwen3.6-35B-A3B` | Resolve the current query and assign evidence/context roles while preserving the source reservoir | Local OpenAI-compatible endpoint | `TMCRA_RECALL_PLANNER_PROVIDER=local-qwen`, `qwen36-planner-v1`; defaults are 512 output tokens and 60-second timeout |
| Evidence compiler | No model | Bind source IDs, execute dates/counts/ordering/set operations, and produce a verifiable packet | Deterministic Python code | Not replaceable by an untrusted free-form model response |
| Answer / application agent | Operator-selected | Consume prompt-ready evidence and complete the product task | Application or agent host | Any model/API may be used; the published reference/evaluation run used `gpt-5.4` |

## Model selection without name allowlists

The code accepts any non-empty model identity at every generation role. The
following variables are the primary model selectors:

| Role | Model selector | Related route setting |
|---|---|---|
| Writer | `TMCRA_WRITER_MODEL` | `TMCRA_WRITER_PROVIDER`, `TMCRA_WRITER_BASE_URL` |
| Reviewer | `TMCRA_WRITER_REVIEWER_MODEL` | `TMCRA_WRITER_REVIEWER_PROVIDER` |
| Recall planner | `TMCRA_RECALL_PLANNER_MODEL` | `TMCRA_RECALL_PLANNER_PROVIDER` |
| Slow graph | `TMCRA_SLOW_GRAPH_MODEL` | `TMCRA_SLOW_GRAPH_PROVIDER` |
| Session graph | `TMCRA_SESSION_GRAPH_MODEL` or `TMCRA_SESSION_GRAPH_LOCAL_MODEL` | `TMCRA_SESSION_GRAPH_PROVIDER` |
| Evidence planner | `TMCRA_EVIDENCE_PLANNER_MODEL` or `--planner-model` | `--planner-provider`, `--planner-base-url` |
| Subject attribution | `TMCRA_SUBJECT_ATTRIBUTION_MODEL` | attribution base URL and key pool |
| Benchmark/application answer | `TMCRA_ANSWER_MODEL` | `TMCRA_ANSWER_BASE_URL`, `TMCRA_ANSWER_API_KEY` |
| Benchmark judge | `TMCRA_JUDGE_MODEL` | defaults to the configured answer model |

Legacy `TMCRA_DEEPSEEK_FLASH_MODEL` and `TMCRA_DEEPSEEK_PRO_MODEL` variables
remain compatible fallbacks. Their default values reproduce the historical
route and do not form an allowlist. Embedding and cross-encoder paths also
accept operator-owned local model paths or configured API identities.

Safety checks remain at the contract boundary: the configured model identity
must be non-empty; a live endpoint must expose the requested alias; structured
responses must pass schema and attribution validation; and resumed caches,
journals, or benchmark files must match the model that created them. These
checks prevent mixed artifacts without restricting a model family.

## Retrieval profile shipped in source

`V4OnlineEngine` constructs a serialized model lane with the following explicit
profile. These are implementation defaults, not universal recommendations:

| Parameter | Source value | Meaning |
|---|---:|---|
| embedding dimension | 1,024 | BGE-M3 vector width used by the runtime |
| embedding max length | 8,192 | Longest input supplied to the embedding model |
| dense shortlist | 32 | First-stage dense candidates |
| slow dense shortlist | 24 | Slow-layer dense candidates |
| graph candidates | 24 | Candidate graph events before fusion |
| graph top-k | 12 | Graph contribution to the ranked union |
| cross-encoder max length | 1,280 | Query/window cross-encoder input bound |
| cross-encoder batch size | 24 | Local rerank batch size |
| default returned top-k | 8 | Base evidence pack size |
| adaptive top-k | 8 / 12 / 16 | Simple / standard / complex recall profiles |

The service settings default to two recall lanes, a global recall queue of eight,
a per-tenant recall queue of two, 6 GiB reserved GPU headroom, and a 5 GiB
per-replica estimate. Tune these only after measuring the selected model files,
context sizes, concurrency, and latency SLO on the target GPU.

## Default self-hosted production route

The public production default uses:

- local `Qwen3.6-35B-A3B` for writing, review, recall-role planning, and slow-graph work;
- local BGE-M3 and BGE reranker V2 M3 for retrieval;
- the bundled TMCRA runtime reranker checkpoint for learned local fusion;
- deterministic evidence compilation; and
- GPT-5.4 in the published reference answer/evaluation run.

The Qwen endpoint binds to loopback in the single-node profile. Its API key file
lives under the operator-owned local-model state directory. Provider calls,
when enabled, are journaled and attributed to tenant/scope usage. Credentials
stay in `/etc/tmcra/writer.env` or a mode-restricted key file.

Copy the sanitized templates:

```bash
sudo install -d -m 700 /etc/tmcra
sudo cp 02-tmcra-memory-api/deploy/tmcra-service.env.example /etc/tmcra/service.env
sudo cp 02-tmcra-memory-api/deploy/writer.env.example /etc/tmcra/writer.env
sudo chmod 600 /etc/tmcra/service.env /etc/tmcra/writer.env
```

The live files must contain operator-owned paths and credentials. Do not commit
them after filling the placeholders.

## Self-hosted and bring-your-own routes

The self-hosted route is the default production route for Writer, Reviewer,
recall planner, and slow graph. The tested model is `Qwen3.6-35B-A3B`; its
reference deployment alias is `tmcra-qwen3.6-35b-a3b-iq3s` on a loopback
OpenAI-compatible endpoint. The alias, GGUF path, file-size pin, and
`llama-server` binary are configuration values. Runtime validation checks the
configured identity and live interface without restricting the model family.

Each role has a separate prompt adapter and the GPU scheduler serializes Writer,
planner, and slow-graph lanes. Operators supply and license the underlying
weights. When another model replaces Qwen, tune all four role prompts and rerun
preflight, integration tests, and benchmark regression before rollout.

DeepSeek V4 Flash/Pro remains available as an optional provider route. Selecting
it requires explicit provider settings and an operator-owned key pool.

Writer/reviewer also expose an explicitly validated OpenAI-compatible provider
boundary. Configuration freedom does not establish behavioral compatibility: a
new route must preserve structured schemas, source attribution, deterministic
validation, context length, timeout behavior, and failure semantics.

For a different memory algorithm, integrate at the event adapter, recall
adapter, or selected-flow boundary described in
[INTEGRATION_AND_EXTENSION.md](INTEGRATION_AND_EXTENSION.md). For a different
outer agent model, keep the Memory API unchanged and consume its bounded evidence
pack from the SDK, lifecycle adapter, or MCP host.

## Audio model path

Audio capture is separate from the text-memory recall stack:

- Android uses sherpa-onnx for local VAD/streaming ASR and local speaker models;
- the optional remote review route uses Qwen3-ASR 0.6B;
- normal-path raw audio and speaker embeddings stay on the device; and
- only accepted text plus attribution crosses the Memory API boundary.

Exact upstream references, redistribution status, and license signals are in
[component 10 model provenance](../10-tmcra-model-data-assets/MODEL_PROVENANCE.md).

## What is and is not redistributed

TMCRA redistributes its source code and the small TMCRA runtime reranker
checkpoint. Third-party BGE, Qwen, ASR, segmentation, and speaker model weights
are not bundled. Operators download them from upstream, pin their own checksums,
review the applicable licenses, and record the deployed artifact identity.
