# Benchmark model profile

This file separates the model configuration used by the recorded TMCRA
benchmarks from the model configuration recommended for current self-hosted
production deployments.

## Frozen version boundary

The Semantic100 benchmark run was frozen on 2026-07-13. It used the
DeepSeek-V4 Preview API snapshot released on 2026-04-24 and therefore predates
the 2026-08-13 DeepSeek update. The 2026-08-13 revision was not used and must
not be attached to these scores. The API IDs and parameter sizes follow the
[official V4 Preview release](https://api-docs.deepseek.com/news/news260424/)
and [official change log](https://api-docs.deepseek.com/updates/).

## Recorded TMCRA memory-chain models

The recorded benchmark implementation uses two DeepSeek generation/planning
tiers through the DeepSeek-compatible API boundary:

| Frozen model version | API model ID | Roles in the benchmark chain | Primary configuration |
|---|---|---|---|
| DeepSeek-V4-Flash Preview (284B total / 13B active) | `deepseek-v4-flash` | Primary Writer, high-volume extraction, recall-role planning, primary slow-graph work, and first-pass evidence selection | `TMCRA_WRITER_MODEL`, `TMCRA_RECALL_PLANNER_MODEL`, `TMCRA_SLOW_GRAPH_MODEL` |
| DeepSeek-V4-Pro Preview (1.6T total / 49B active) | `deepseek-v4-pro` | Writer Reviewer, reconciliation, subject attribution, evidence-operation planning, semantic planning, and higher-assurance review/repair | `TMCRA_WRITER_REVIEWER_MODEL`, `TMCRA_SUBJECT_ATTRIBUTION_MODEL`, `TMCRA_EVIDENCE_PLANNER_MODEL` |

Retrieval is a separate local path using `BAAI/bge-m3`,
`BAAI/bge-reranker-v2-m3`, and the published TMCRA node/path scoring
checkpoints.

## Answer and judge boundary

The DeepSeek models above are the generation/planning models inside the TMCRA
memory chain. The frozen Semantic100 comparison used GPT-5.4 as the answer model
and the official GPT-5.4 judge for both the 77/100 baseline and 61/100 semantic
path. This keeps the answer/judge layer constant while comparing the memory and
evidence protocols.

An answer or judge model is an evaluation dependency; it must not be presented
as the model that built and planned the memory chain. Likewise, the DeepSeek
memory-chain profile must not be described as the current local deployment
default.

## Current production default

The current repository's default production generation model is the locally
downloaded `Qwen3.6-35B-A3B-UD-IQ3_S.gguf`, served on the operator's own GPU by
`llama-server`. That Qwen3.6 profile is intended for new self-hosted deployments
and was not retroactively substituted into the recorded benchmark runs.

All generation roles are configurable and no exact model-name allowlist is
enforced. A reproduction that replaces either DeepSeek tier, the answer model,
or the judge model is a new run and must publish its own model bindings, hashes,
thresholds, and score report.

---

# Benchmark 模型说明

本文档区分已记录 TMCRA Benchmark 的模型配置与当前自托管生产部署的默认配置。

## 冻结版本边界

本次 Semantic100 Benchmark 于 2026-07-13 冻结，使用 2026-04-24 发布的
DeepSeek-V4 Preview API 快照，时间早于 2026-08-13 DeepSeek 更新。0813 新版本没有
用于这些成绩，不能将该版本归到本次评测。API 型号 ID 与参数规模以
[DeepSeek 官方 V4 Preview 发布说明](https://api-docs.deepseek.com/zh-cn/news/news260424)
和[官方更新日志](https://api-docs.deepseek.com/zh-cn/updates/)为准。

## 已记录的 TMCRA 记忆链模型

Benchmark 记忆链通过 DeepSeek-compatible API 使用两档生成/规划模型：

| 冻结模型版本 | API 型号 ID | Benchmark 链路中的职责 | 主要配置项 |
|---|---|---|---|
| DeepSeek-V4-Flash Preview（总参数 284B / 激活 13B） | `deepseek-v4-flash` | 主 Writer、高吞吐抽取、召回角色规划、主要慢速图谱任务和第一轮证据选择 | `TMCRA_WRITER_MODEL`、`TMCRA_RECALL_PLANNER_MODEL`、`TMCRA_SLOW_GRAPH_MODEL` |
| DeepSeek-V4-Pro Preview（总参数 1.6T / 激活 49B） | `deepseek-v4-pro` | Writer Reviewer、对账、主体归因、证据操作规划、语义规划及高可靠审核/修复 | `TMCRA_WRITER_REVIEWER_MODEL`、`TMCRA_SUBJECT_ATTRIBUTION_MODEL`、`TMCRA_EVIDENCE_PLANNER_MODEL` |

召回属于独立本地链路，使用 `BAAI/bge-m3`、`BAAI/bge-reranker-v2-m3` 及公开的
TMCRA node/path scoring checkpoint。

## 回答模型与 Judge 边界

上述 DeepSeek 型号负责 TMCRA 记忆链内部的生成与规划。冻结 Semantic100 对比在
77/100 基线和 61/100 语义路径中统一使用 GPT-5.4 作答，并统一使用官方 GPT-5.4 Judge，
以保持回答/评分层不变，只比较记忆与证据协议。

回答模型或 Judge 是评测依赖，不能写成构建、规划记忆链的模型；DeepSeek Benchmark
配置也不能写成当前本地部署默认模型。

## 当前生产默认配置

当前仓库默认将 `Qwen3.6-35B-A3B-UD-IQ3_S.gguf` 下载到部署方自己的服务器，通过
本机 GPU 上的 `llama-server` 提供生成能力。该 Qwen3.6 配置服务于新的自托管部署，
没有倒填或替换已记录 Benchmark 的模型来源。

所有生成角色都可配置，代码不使用精确型号白名单。替换任一 DeepSeek 档位、回答模型或
Judge 后，应作为新的复现运行，单独公开模型绑定、输入哈希、阈值和成绩报告。
