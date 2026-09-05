# Changelog / 变更记录

## 1.0.0-rc.1 — 2026-09-06

- Unify product and adapter versions for this major release candidate. Add account-free Windows one-click local installation, automatic private Python, bundled backend inventory, automatic local identity discovery and protection against stale cloud requests.

- Add Windows full-local runtime preview with pinned E5/MiniLM, BGE and Qwen retrieval profiles, private local identity, loopback generation routing and per-model index identity checks.
- Add memory workspace, provider configuration, knowledge/graph views, task continuity, session privacy controls and interactive correction confirmation.
- Add effective source feedback, portable Windows locks and full-local cloud-handoff isolation. Production deployment is a separate operation.
- Synchronize Codex 1.0.0-rc.1 and the nine-tool MCP implementation; preserve binary assets in reproducible plugin archives.
- Validation boundary: synthetic CPU ingest/raw recall passed; complex compilation timed out. Organizer, full-service restart, and balanced/high-tier hardware acceptance remain pending.

Current package spellings: npm/NuGet `1.0.0-rc.1`, Python `1.0.0rc1`. The previous release's package spelling is retained below for historical reference.

All notable release changes are recorded here. Package ecosystems use their own
valid prerelease spelling: `0.3.0-rc.2` for npm/NuGet and `0.3.0rc2` for Python.
Both map to the repository release `0.3.0-rc2`.

这里记录公开版本的重要变化。各包生态使用自身合法的预发布格式：npm/NuGet
使用 `0.3.0-rc.2`，Python 使用 `0.3.0rc2`，均对应仓库版本
`0.3.0-rc2`。

## 0.3.0-rc2 — 2026-08-20

### English

- Validated the public deployment chain in an isolated single-RTX-5090 clean
  room with the production defaults: Qwen3.6-35B-A3B at three 65,536-token
  slots, four Writer workers, and two recall replicas.
- Verified ingest, read-your-writes recall, evidence provenance, memory graph,
  cross-Agent SDK sharing, and the local-Qwen personal knowledge base. The
  tested knowledge base retained a source fingerprint and seven evidence links.
- Pinned a Transformers-compatible Hugging Face Hub range and made model
  downloads portable by fetching runtime-required BGE files, disabling Xet by
  default, and checking the main BGE artifacts by SHA-256.
- Added the required BGE reranker model manifest to the standalone service
  archive and corrected the installed integrated-repository path.
- Fixed custom-prefix and custom-virtualenv resolution in service and
  maintenance controls, plus direct execution of the preflight script.
- Fixed projection-progress serialization for the valid
  `dedicated-local-slot` resource mode and added regression coverage.
- Kept the immutable `v0.3.0-rc1` tag unchanged; these deployment fixes begin
  with `v0.3.0-rc2`.

### 中文

- 在单张 RTX 5090 隔离区按公开生产默认配置完成部署验证：
  Qwen3.6-35B-A3B 使用三个 65,536 Token 槽位，配置四个 Writer worker 与
  两个召回副本。
- 验证写入、读己之写召回、证据溯源、记忆图谱、跨 Agent SDK 共享，以及由
  本地 Qwen 生成的个人知识库；测试知识库保留来源指纹和七条证据链接。
- 固定与 Transformers 兼容的 Hugging Face Hub 版本范围；BGE 仅下载运行所需
  文件，默认禁用 Xet，并对主要 BGE 模型文件执行 SHA-256 校验。
- 将运行时要求的 BGE reranker 模型清单加入独立服务发布包，并修正安装后的
  集成源码路径。
- 修复自定义安装前缀、虚拟环境在服务控制和维护脚本中的解析顺序，同时支持
  从任意工作目录直接执行预检脚本。
- 修复投影进度接口对合法 `dedicated-local-slot` 资源模式的序列化，并补充
  回归测试。
- 保持不可变的 `v0.3.0-rc1` 标签不变；以上部署修复从 `v0.3.0-rc2` 起发布。

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
