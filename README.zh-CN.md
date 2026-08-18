# TMCRA

[English](README.md) | [简体中文](README.zh-CN.md)

TMCRA（Temporal Memory-Centric Retrieval Architecture）是面向产品和 AI Agent
的私有化长时记忆平台。它将对话和业务事件保存为带身份、来源和时间信息的原始证据；
将近期信息快速变为可检索记忆；再以批处理方式形成可演化的长期语义图谱；最终返回
有来源、可追溯、长度受控的召回证据。

部署方自行掌握服务器、数据、模型、供应商凭证、Tenant 与 Scope 规则。TMCRA
提供可部署的记忆服务、客户端、SDK、MCP、插件和评测工具；它不是依赖 TMCRA
托管账号的服务。

## 为什么需要 TMCRA

普通聊天记录只是按时间排列的文本，难以精确召回，也容易混淆不同用户或项目。
TMCRA 将这些问题拆分为清晰边界：

- 原始证据层保存可检查的内容、Actor 和来源；
- 快速记忆层让新事实在数秒内可检索；
- 慢速语义层批量演化概念、关系、修订与冲突，不会每条消息都重建图谱；
- 证据优先召回将历史内容作为数据，不会把历史 Agent 输出伪装为当前指令；
- Tenant 与 Scope 隔离防止客户、用户或项目之间的记忆串扰。

## 基准评测结果

TMCRA 同时公开分数、评测边界和失败分析，避免将不可复现的结果包装为营销结论。

| 评测 | 结果 | 范围与解释 |
|---|---:|---|
| LongMemEval-S 冻结官方评分卡 | **411/500（82.2%）** | 单次冻结、端到端 500 题评测。 |
| 冻结 Source24 100 题生产候选路径 | **77/100** | 使用 GPT-5.4 作答模型和官方 GPT-5.4 Judge；是该冻结切片当前的生产候选答案路径。 |
| Semantic V4 影子实验路径 | **61/100** | 使用同一 100 题切片；仅保留为诊断实验，明确不替代 77/100 基线。 |
| LoCoMo Mem0 风格 LLM Judge | **80.92%** | Category 1–4 的辅助五次运行均值（`N = 1,540`）；不包含 Category 5，因此不作为全量官方准确率展示。 |
| LoCoMo 官方 Token F1 | **55.20** | 确定性评分器，覆盖全部 1,986 道题。 |
| LoCoMo 证据召回率 | **82.00%** | 证据检索覆盖，覆盖全部 1,986 道题。 |

411/500 来自一次冻结的端到端 500 题评测。另一个 100 题对比中所有题目均完成作答，
但语义路径相比基线回退 16 分。该负结果被完整保留，而非隐藏：它表明剩余问题位于语义
解析和答案绑定，不在第一阶段检索。冻结集契约、分类拆分、限制与晋升决定见
[评测复现说明](09-tmcra-benchmarks/README.md)及完整
[Semantic100 报告](09-tmcra-benchmarks/TMCRA_V4_SEMANTIC100_BENCHMARK_REPORT.md)。LoCoMo
使用不同的协议和分母，结果单独呈现，详见
[LoCoMo 成绩记录](09-tmcra-benchmarks/LOCOMO_BENCHMARK_REPORT.md)。

## 系统架构

~~~mermaid
flowchart LR
  subgraph clients["应用与 Agent 宿主"]
    WEB["Web 控制台"]
    DESKTOP["桌面端"]
    MOBILE["Android"]
    SDK["Python / TypeScript SDK"]
    PLUGIN["Codex / Claude 插件"]
    MCP["本地 stdio MCP 宿主"]
  end
  BFF["Web BFF 与账号绑定"]
  AUTH["租户密钥或 Scope Token"]
  API["TMCRA Memory API"]
  CONTROL["控制 SQLite<br/>密钥、任务、租约、回执、成本"]
  WRITERS["常驻 Writer 进程池"]
  RETRIEVAL["预加载检索引擎"]
  SOURCE["原始证据层"]
  FAST["快速记忆层"]
  SLOW["慢速语义图谱层"]
  INDEX["只读索引代际"]

  WEB --> BFF --> API
  DESKTOP --> PLUGIN --> AUTH --> API
  SDK --> AUTH
  MCP --> AUTH
  MOBILE --> AUTH
  API --> CONTROL
  API --> WRITERS
  API --> RETRIEVAL
  WRITERS --> SOURCE --> FAST --> SLOW
  FAST --> INDEX
  SLOW --> INDEX
  RETRIEVAL --> SOURCE
  RETRIEVAL --> INDEX
~~~

浏览器不是可信的记忆客户端。Web 控制台使用服务端 BFF 将登录账号解析为服务端维护的
Tenant/Scope 绑定；浏览器不会获得生产 API 密钥，也不能任意选择 Scope。MCP 使用
本地 stdio，凭证始终由宿主进程控制。

## 三层记忆模型

每次写入都带 tenant、scope、actor、source application、时间戳和幂等边界。Tenant
是客户级安全边界；Scope 是该 Tenant 下的记忆命名空间。典型商业产品可为每个客户
账户建立一个 Tenant，再为每个终端用户、Agent 或记忆角色建立稳定且不透明的 Scope。

| 层级 | 写入时机 | 作用 | 关键保证 |
|---|---|---|---|
| 原始证据层 | 每个被接收的写入任务 | 保存原始内容、Actor 和来源 | 所有派生记忆可回溯到来源记录 |
| 快速记忆层 | 正常写入与索引流程 | 让近期事实快速可检索 | 无需等待慢速图谱演化 |
| 慢速语义层 | Scope 满足批处理条件时 | 构建和修订长期语义关系 | 冲突不会静默覆盖原始证据 |

召回返回结构化证据、确定性的 prompt-ready 视图和有限追踪信息。历史证据始终是
数据，不能覆盖当前系统指令或用户指令。

## 写入、索引和召回

1. 客户端带租户密钥或 Scope Token 和幂等键提交写入。
2. API 检查租户策略、Scope、请求大小、速率和队列容量，然后创建持久化任务。
3. 常驻 Writer 通过租约领取任务，写入带主体信息的原始证据；外部副作用不确定时
   不会盲目重放。
4. 快速层和新索引代际独立调度。公开服务默认目标为 16 条消息或 2 秒后索引。
5. 慢速图谱按照新 token、新轮次、空闲时间和冷却窗口批量演化，而非每条消息额外
   模型调用。
6. 召回时，预加载检索引擎读取该 Scope 的原始证据和活动索引代际，排序去重后返回
   有来源的有限证据。

这让跨 Scope 请求、不确定写入重放和慢速层覆盖原始证据等故障无法静默发生。

## 接入已有记忆系统与一键商业化

TMCRA 不要求替换已有 CRM、聊天历史、向量库或业务数据库。它可以作为原始证据和
召回层，也可以先并行运行，待质量和迁移规则确认后逐步扩大流量。

商业化接入路径：

1. 一次部署：在自身基础设施部署 Memory API，并建立与客户模型对应的 Tenant/Scope 规则。
2. 一次接入配置：支持的 Agent 栈使用
   [组件 06](06-tmcra-sdk-integrations/) 生命周期适配器；其他系统使用 REST API、
   Python/TypeScript SDK 或本地 MCP Server。
3. 映射存量记录：将旧系统的消息或事件作为带来源的写入记录发送，使用稳定外部 ID
   与幂等键；迁移审核前，旧系统仍可作为事实源。
4. 按业务流开启召回：在 Agent 或应用动作前读取有来源的有限证据，并在完成后通过
   同一边界保存结果。

对于已支持的 SDK 和 Agent 框架，这是一条部署服务加配置接入的一键商业化路径，无需
fork 记忆引擎。私有数据模式仍需轻量映射适配器及审核后的导入策略；TMCRA 不会虚假
承诺任意客户数据都能无风险自动迁移。

## 模块

| # | 模块 | 主要职责 |
|---|---|---|
| 01 | [agent-memory-algorithm](01-tmcra-agent-memory-algorithm/) | V4 记忆算法、契约和共享文件清单 |
| 02 | [memory-api](02-tmcra-memory-api/) | Memory API、部署包、调度器、Writer 池和运维工具 |
| 03 | [web-console](03-tmcra-web-console/) | Web 控制台和服务端账号到 Scope 绑定 |
| 04 | [desktop](04-tmcra-desktop/) | Electron 安装器和桌面集成账号控制台 |
| 05 | [mobile](05-tmcra-mobile/) | Android 采集、VAD、端侧 ASR 与本地说话人归因 |
| 06 | [sdk-integrations](06-tmcra-sdk-integrations/) | Python/TypeScript SDK 和 Agent 框架适配器 |
| 07 | [codex-plugins](07-tmcra-codex-plugins/) | Codex 与 Claude Code 生命周期插件 |
| 08 | [mcp-server](08-tmcra-mcp-server/) | 显式可靠写入和召回的 MCP Server |
| 09 | [benchmarks](09-tmcra-benchmarks/) | LongMemEval 复现、测试与评分卡 |
| 10 | [model-data-assets](10-tmcra-model-data-assets/) | 模型来源与烟雾测试素材清单 |

## 部署与安全边界

生产 API 部署在可信 HTTPS 反向代理之后。启动预检会验证共享算法哈希、状态目录写入、
SQLite 完整性和锁、可用磁盘、供应商配置、索引校验和、CUDA、模型推理、图谱适配器和
Writer 握手，且不发起付费供应商调用。端口监听不等于服务 Ready，应以预检和就绪状态
作为部署判定。

可部署服务位于 [02-tmcra-memory-api](02-tmcra-memory-api/)。完整服务契约、健康状态、
租户模型和运维文档见
[tmcra_service/README.md](02-tmcra-memory-api/tmcra_service/README.md)。

代码使用 Apache-2.0 协议，可用于商业自部署，但部署方仍须遵守所选模型、云服务和
供应商的许可及条款。发布包已清除已配置的凭证、私钥、客户数据库、用户记录和生产日志；
示例配置仅含占位符。环境文件、供应商密钥和状态目录必须放在版本控制之外。

仓库包含 Apache-2.0 的小型 TMCRA checkpoint，但不分发第三方模型权重。组件 10 的
公开音频素材仅用于烟雾测试，不是 TMCRA 用户录音；商业再分发前请复核上游条款。

### 开发硬件与部署边界

全量开源版本以**单张 NVIDIA RTX 5090**作为开发基线：一个服务进程预加载一个检索
引擎，常驻 Writer 进程池独立完成可靠写入。部署公开默认配置时，建议准备**至少 32 GB
GPU 显存**。实际容量仍取决于嵌入、重排和生成模型、并发、批大小、上下文长度与延迟目标，
该建议不是吞吐或可用性承诺。

多卡服务不属于当前公开版本的已支持部署拓扑。需要张量/模型并行、跨设备检索、
多进程调度或多卡故障切换的部署方，应自行完成模型放置、显存压力、请求路由、就绪检查与
回滚验证。执行前请阅读[部署指南](docs/DEPLOYMENT.md)。

## 开发者文档

首页用于说明整体架构；以下文档回答“应用有什么功能、如何实现、API 如何控制性能和成本、
怎样部署与运维”。

| 文档 | 用途 |
|---|---|
| [应用端实现](docs/APPLICATIONS.md) | Web、桌面、Android、SDK、生命周期插件与 MCP 的功能和实现边界 |
| [API 与运行时](docs/API_AND_RUNTIME.md) | 端点、可靠写入/召回、隔离、性能控制、成本与恢复 |
| [接入与扩展](docs/INTEGRATION_AND_EXTENSION.md) | 已有记忆系统接入、双写迁移、召回注入及扩展边界 |
| [模块功能矩阵](docs/MODULES.md) | 已核对的 01–10 模块功能、实现入口、运行方式和限制 |
| [商业模块](docs/COMMERCIAL_MODULES.md) | Tenant、账号、套餐、配额、成本、Webhook、保留策略和运营边界 |
| [部署指南](docs/DEPLOYMENT.md) | 5090 单卡基线、32 GB 建议、预检、生产拓扑及多卡边界 |
| [中文工程指南](docs/DEVELOPER_GUIDE.zh-CN.md) | 中文版可执行的应用、API、部署与运维说明 |

## 验证与贡献

- 安全问题报告见 [SECURITY.md](SECURITY.md)，不要公开发布漏洞细节。
- 贡献规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。
- 引用信息见 [CITATION.cff](CITATION.cff)。
- 评测来源见 [09-tmcra-benchmarks](09-tmcra-benchmarks/)。
- GitHub 发布流程见 [GITHUB_PUBLISH.md](GITHUB_PUBLISH.md)。

## 许可证

TMCRA 使用 Apache License 2.0，详见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
