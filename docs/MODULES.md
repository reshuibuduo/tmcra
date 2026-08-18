# TMCRA module capability matrix / 模块功能矩阵

本页按公开发布物的 01–10 逐项核对功能、实现入口、运行方式和边界。它描述的是仓库中
实际包含的代码与文档，不把计划、私有服务或未验证硬件能力写成已交付功能。

| # | 模块 | 交付物角色 | 主要使用者 |
|---|---|---|---|
| 01 | Agent Memory Algorithm | 共享 V4 记忆算法核心 | 算法/服务开发者 |
| 02 | Memory API | 生产 HTTP 控制面与部署包 | 自托管平台团队 |
| 03 | Web Console | 个人、企业和运营控制台 | 产品用户与运营人员 |
| 04 | Desktop | Windows Codex 集成安装器 | Windows Codex 用户 |
| 05 | Mobile | Android 音频记忆 MVP | 移动端产品团队 |
| 06 | SDK & Integrations | SDK 与 Agent 生命周期适配器 | 应用/Agent 开发者 |
| 07 | Codex Plugins | Codex 长时记忆插件 | Codex 用户 |
| 08 | MCP Server | 显式 MCP 记忆桥接 | MCP 宿主开发者 |
| 09 | Benchmarks | LongMemEval/LoCoMo 评测与复现 | 评测和算法团队 |
| 10 | Model & Data Assets | 模型/数据来源和公开烟雾素材 | 构建与合规负责人 |

## 01 · Agent Memory Algorithm

**作用。** 这是生产服务和评测共用的 V4 算法文件集，并由
`shared_core_manifest.json` 逐文件固定 SHA-256。组件 02 启动时验证该清单；文件
缺失或变更会 fail-closed，而不是带着未知算法继续服务。

**实现功能。**

- Source 层保留带 actor、来源和时间的原始证据；Fast 层在写入后快速形成可检索断言；
  Slow 层以批次方式合并、修订、挑战语义关系。
- `tmcra_v4_batch_writer.py` 实现带幂等 claim、租约和显式 commit marker 的耐久批写入。
- `tmcra_v4_online_runtime.py`、`tmcra_v4_recall_planner.py` 承担在线检索和证据选择；
  `tmcra_v4_evidence_operations.py` / `tmcra_v4_evidence_planner.py` 区分 raw 与
  compiled evidence。
- Task Contract、typed semantics、route policy、cost report 和 slow graph 分别定义
  任务语义、类型约束、路由/费用与慢速图维护。

**改造边界。** 修改核心必须重新生成 manifest，并让组件 02 以
`TMCRA_VERIFY_SHARED_CORE=1` 运行验证；不要在已部署目录手改 pinned 文件。

## 02 · Memory API

**作用。** 生产 HTTP 控制面，不保存 benchmark 标签或冻结答案。它把算法核心包装为
多租户、可观测、可部署的服务：认证、作业、写入、检索、图、成本、配额和运维都在这里。

**实现功能。**

- `POST ingest`、`POST recall`、`POST consolidate`、Job 查询/取消/重试，以及图摘要、
  邻居、证据和 trace 端点；OpenAPI 由服务合同导出。
- 通过 `Idempotency-Key`、SQLite job/stage journal、租约、心跳和 commit marker 保证
  写入可追踪；不确定的外部 Writer 副作用不自动重放。
- 每个 `(tenant_id, scope_name)` 有独立 Source SQLite 与只读索引代际；控制 SQLite
  保存密钥、作业、回执、费用、水位和运行审计。
- 预加载 GPU 检索引擎、常驻 Writer 池、Fast 索引（默认 16 条消息或 2 秒）、批量 Slow
  图（默认 32k token 或 64 轮加冷却）共同降低首包时延和每条消息的成本。
- 具备 Tenant API Key、权限受限 Scoped Token、配额/权益、成本归因、Webhook、保留策略、
  删除/导出、反馈、`/healthz`、`/readyz` 和独立 staff runtime 监控。

**运行。** 用 `requirements-tmcra-service.txt` 安装；在 `deploy/` 配置环境文件、模型路径、
反向代理和 systemd/supervisor；生产必须通过 full preflight 才可进入 Ready。详细 API 行为见
[API 与运行时](API_AND_RUNTIME.md)。

## 03 · Web Console

**作用。** vinext + Cloudflare Workers 控制台。D1 保存账户资料、个人空间绑定、企业组织、
Agent、成员关系、API Key 元数据和控制面事件；生产记忆快照留在组件 02。

**实现功能。**

- 个人端路由覆盖个人图谱、聊天/语音、音频记忆事件与说话人、会话图、知识库、记忆控制、
  数据导出及删除相关流程。
- 企业端提供组织资源和 Agent 对应的记忆图入口；内部端作为 TMCRA staff 控制面；未分类
  登录只进入账户设置，不会自动创建组织或个人空间。
- BFF 读取可信身份头并解析 D1 的个人或组织/Agent 绑定；浏览器不持有 API Key、不能任意
  选 Scope。初始图只返回 Slow 摘要，展开后才读取 Fast/Source 邻居，原文需经 evidence
  端点；所有记忆响应 `no-store`。
- 提供产品、架构、开发者、API、连接 Codex/DeepSeek Harness、下载、价格、隐私、条款、
  安全和 benchmark 等公开页面，并以 Sigma.js/Graphology 呈现语义图。

**运行。** Node.js `>=22.13.0`；在本模块执行 `npm install`、`npm run dev`、
`npm run build`、`npm test`。生产需服务端配置 Memory API URL 与 D1 绑定，绝不把
`TMCRA_MEMORY_API_*` 密钥变量打进浏览器。

## 04 · Desktop

**作用。** 独立 Windows Electron 应用，解决普通 Codex 用户的安装、设备授权、连接验证和
更新，而不要求用户接触 SSH 或生产 API Key。

**实现功能。** 检测 Codex；校验插件 ZIP 与 release manifest SHA-256；解包至稳定用户目录；
调用插件安装器；在隔离窗口完成设备授权；提示用户重启 Codex 并审阅 Hooks。连接状态只有
在本地插件注册和认证服务检查均成功后才恢复，不依赖“本地有 Token 文件”的猜测。

**安全/发布边界。** 渲染层只有六个无参数 IPC；登录/授权/`/personal` 窗口关闭 Node、开启
sandbox/context isolation；NDJSON 只白名单字段，Token/设备码/PKCE/回执不进渲染层。
`npm test`、`npm run dist:win`、`npm run publish:win` 分别覆盖测试、NSIS 构建和发布资产。
当前 Windows 发行包未签名，SmartScreen 警告在引入 Authenticode 前是已知边界。

## 05 · Mobile

**作用。** Android 音频记忆 MVP。目标是手机/耳机个人记忆闭环，而不是把原始录音交给
服务器。

**实现功能。** `AudioRecord 16 kHz` 经自适应 VAD 和受限本地 WAV 缓存，进入端侧 Zipformer
流式 ASR；一句话结束后使用端侧 ERes2NetV2 做保守说话人匹配，写入本地 SQLite outbox，再以
文本、时间、匿名本地说话人 ID/标签、置信度和 ASR 来源调用 TMCRA。支持 account-global 与
当前音频项目 Recall、可选本地提醒和 Android TTS。模糊声纹保持 `unknown`，非 owner 语音写为
观察到的 sensor/tool 记录。

**隐私/边界。** 声纹模板以 Android Keystore 加密在 app-private 存储；WAV 最多 24 小时或
256 MiB；默认只端侧 ASR，远端 ASR 默认为关闭且需用户 opt-in。当前 2025 中文 ASR 权重在
商用前仍需确认许可；Apache-2.0 的旧 14M 模型为候选回退。运行 `npm run android:debug` 或
Gradle 单测/构建；手机时延、续航、热、蓝牙、噪音 WER 与误识率必须在真实 arm64 设备验证。

## 06 · SDK & Integrations

**作用。** 将组件 02 的记忆合同接入产品与 Agent。提供 Python `tmcra_client`、TypeScript
`@tmcra/typescript`，以及 OpenClaw、Hermes、LangGraph、OpenAI Agents、Vercel AI SDK、
Microsoft Agent Framework 适配器。

**实现功能。** 统一遵循“回合开始 Recall、回合结束耐久写回”的 lifecycle；保留 Job/Recall
receipt；`StopFailure` 不静默吞写入失败。每个框架目录有独立 `INSTALL.md`/README 与中英文
安装说明，适合按宿主框架集成而不是复制整个引擎。

**改造方式。** 新框架在 `integrations/` 中实现相同的开始/结束边界、稳定 Scope 规则、
幂等回执和 degraded-recall 行为。详情见[接入与扩展](INTEGRATION_AND_EXTENSION.md)。

## 07 · Codex Plugins

**作用。** `tmcra-memory` 为 Codex 提供自动长时记忆，不要求用户在安装时拥有 TMCRA 服务端
权限。

**实现功能。** `SessionStart` 初始化全局/项目 Scope；`UserPromptSubmit` 以当前提示召回；
`Stop` 写入完成的 user/assistant 轮次；`PostToolUse` 保留有限且已脱敏的长任务进度；
`PreCompact`/`PostCompact` 提供可恢复检查点；还覆盖 Subagent 和 StopFailure。全局稳定事实、
项目 Scope 和任务 Session 分离，MCP 工具可以查看上一轮实际使用的 recall、手动 recall、
ingest 和查询 Job。

**安装/边界。** Windows 使用 `Install-TMCRA.ps1`，macOS/Linux 使用 `install.sh`；浏览器
设备授权写入受保护本地配置。Hook 要由用户在 Codex 中审阅/信任，插件不能静默授权。自动
Hook 有短超时并 fail-open，显式 MCP 操作可更长；不存 chain-of-thought、开发者指令、密码、
私钥或 Token，历史导入也先预览、再经显式确认。

## 08 · MCP Server

**作用。** 面向任意 MCP 宿主的显式兼容层，而非每回合自动注入器。

**实现功能。** 本地 `stdio` transport 运行 `tmcra-mcp`，提供 `tmcra_recall`、
`tmcra_ingest`、`tmcra_get_job`、`tmcra_wait_job`。宿主通过环境变量提供 Base URL、
服务端 API Key 和可选默认 Scope，密钥保留在宿主进程。`tmcra-mcp-smoke` 可对临时 Scope
执行真实 initialize/list/call 并验证 prompt-ready recall，但不打印凭证或 evidence 内容。

**边界。** 需要“每回合自动”时用组件 06 的生命周期适配器；MCP 返回的记忆始终是
untrusted data，宿主必须保留 trust boundary。

## 09 · Benchmarks

**作用。** LongMemEval-S 和 LoCoMo 的评测复现、评分来源、编排脚本、测试与共享算法副本。

**实现功能。** 包含数据准备、构建、证据编译/选择、检索、答案、官方 Judge、quality gate、
Writer 成本/soak 等 `run_tmcra_v4_*` 编排脚本和回归/恢复测试。公开结果为：一次冻结端到端
LongMemEval-S500 **411/500（82.2%）**；冻结 100 题生产候选基线 **77/100**；Semantic V4
影子路径 **61/100**，明确不替代基线。LoCoMo 单独报告 80.92% Mem0 风格 Judge（Cat 1–4，
N=1540，五次均值）、全量 Token F1 55.20 和全量证据召回 82.00%，不可与 LongMemEval 混算。

**运行/边界。** 用组件 02 的构建依赖后运行脚本 `--help`/评测入口。部分脚本保留原构建机
绝对路径作为默认值，公开运行时应通过 `TMCRA_DATA_DIR`、`TMCRA_REPO_DIR`、模型路径等参数化。
算法副本被 manifest 固定，不可就地修改。

## 10 · Model & Data Assets

**作用。** 不重新分发未确认可再分发的模型权重和 benchmark 数据，而是交付模型/数据来源、
SHA-256、许可证线索与可公开的烟雾测试素材。

**实现功能。** `MANIFEST.md`/`MODEL_PROVENANCE.md` 列出构建机已知 artifact 的来源和哈希；
`fixtures/` 包含来自官方 sherpa-onnx speaker-segmentation-models 发布的公开音频：57 秒四人
中文、16 秒双人英文、34 秒重叠双人英文。组件 05 在 build 时用固定 SHA-256 获取移动模型；
组件 02 从部署方环境路径加载 embedding/reranker/cross-encoder。

**边界。** 运营方必须自行取得第三方模型、数据集与云服务的许可，并在商用发布前审核模型
权重和公开 fixture 的上游条款。本组件不是权重镜像或用户数据包。

## 交付边界

模块 11 已明确不纳入本次公开发布。每个模块的安装入口、运行时配置和商业接入方式见
[应用实现](APPLICATIONS.md)、[API 与运行时](API_AND_RUNTIME.md)、
[商业模块](COMMERCIAL_MODULES.md)、[接入与扩展](INTEGRATION_AND_EXTENSION.md)及
[部署指南](DEPLOYMENT.md)。
