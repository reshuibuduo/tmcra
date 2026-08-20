# Changelog / 变更记录

All notable release changes are recorded here. Package ecosystems use their own
valid prerelease spelling: `0.3.0-rc.1` for npm/NuGet and `0.3.0rc1` for Python.
Both map to the repository release `0.3.0-rc1`.

这里记录公开版本的重要变化。各包生态使用自身合法的预发布格式：npm/NuGet
使用 `0.3.0-rc.1`，Python 使用 `0.3.0rc1`，均对应仓库版本
`0.3.0-rc1`。

## 0.3.0-rc1 — 2026-08-20

### English

- Published the complete ten-component source tree: memory algorithms, service
  API, Web console, desktop and mobile clients, SDKs and integrations, Codex and
  Claude plugins, MCP server, benchmark harness, and model/data tooling.
- Opened the production memory chain, commercial application modules, deployment
  automation, configuration templates, prompts, evidence provenance, knowledge
  graph, personal knowledge base, one-click integration, and cross-Agent flows.
- Documented the default self-hosted Qwen3.6-35B-A3B route and operator-selected
  model substitution. Exact model-name allowlists were removed; alternative
  models can require prompt and runtime tuning.
- Added source-traceable memory contracts and the adapters required to connect an
  existing memory system through the HTTP API, SDK lifecycle, MCP, or Agent hooks.
- Published the frozen LongMemEval and LoCoMo score records with model, judge,
  denominator, and single-run boundaries. The benchmark memory chain is tied to
  the 2026-04-24 DeepSeek-V4 Preview model snapshot.
- Added repository-wide version drift and credential checks plus GitHub release
  gates for API, benchmark, SDK, Web, desktop, mobile, and Android builds.
- Development reference: one NVIDIA RTX 5090. At least 32 GB VRAM is recommended
  for the default local profile. Multi-GPU topology and tuning remain deployment-
  specific and require operator validation.
- Third-party datasets and model weights remain under their upstream licenses and
  are downloaded separately; generated benchmark responses are not vendored.

### 中文

- 完整发布十个组件的源码：记忆算法、服务 API、Web 控制台、桌面端、移动端、
  SDK 与集成、Codex/Claude 插件、MCP Server、Benchmark 链路、模型与数据工具。
- 开源生产记忆链路、商业应用模块、部署自动化、配置模板、Prompt、证据溯源、
  知识图谱、个人知识库、一键接入和跨 Agent 流程。
- 明确默认本地生产模型为 Qwen3.6-35B-A3B，并允许部署者替换为自己的模型。
  精确型号白名单已移除；替换模型后可能需要调整 Prompt 和运行参数。
- 提供可追溯来源的记忆合同，以及通过 HTTP API、SDK 生命周期、MCP、Agent Hook
  接入现有记忆系统所需的适配层。
- 发布 LongMemEval 与 LoCoMo 的冻结成绩记录，写明模型、Judge、分母和单次运行
  边界；Benchmark 记忆链路对应 2026-04-24 DeepSeek-V4 Preview 模型快照。
- 增加全仓版本漂移检查、密钥扫描和 GitHub 发布门禁，覆盖 API、Benchmark、
  SDK、Web、桌面端、移动端与 Android 构建。
- 开发参考硬件为单张 NVIDIA RTX 5090；默认本地配置建议至少 32 GB 显存。
  多卡拓扑与调优取决于部署环境，需要部署者自行验证。
- 第三方数据集与模型权重遵循上游许可并独立下载，仓库不附带生成式评测响应。
