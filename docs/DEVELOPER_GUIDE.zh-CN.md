# TMCRA 中文工程指南

本文面向准备把 TMCRA 接入产品、部署服务或二次开发的团队。它说明公开版本的实际应用
能力、API 运行方式、单卡部署边界，以及把已有记忆系统接入 TMCRA 的可执行路径。

## 1. 应用端有什么，如何实现

| 端 | 能力 | 实现与安全边界 |
|---|---|---|
| Web 控制台 | 个人记忆空间、企业组织/Agent、图谱、时间线和表格视图 | vinext/Workers BFF 与 D1 保存账户到 Tenant/Scope 的服务端绑定；浏览器没有生产 API Key，不能自行选择 Scope |
| Windows 桌面端 | 检测 Codex、校验插件包、安装、设备授权、连接验证和更新 | Electron 主进程校验 SHA-256；授权窗口禁用 Node 并启用隔离；Token、设备码和 PKCE 校验值不交给渲染层 |
| Android | 录音、VAD、端侧流式 ASR、端侧说话人归因、离线 outbox、文本记忆写入/召回 | 原始音频和声纹留在手机；服务器只接收文本、时间、匿名本地说话人 ID/标签、置信度和 ASR 来源 |
| Python/TypeScript SDK | 后端或产品直接调用 | 调用方负责将认证后的用户/项目映射为稳定 Scope，并保存写入回执 |
| Agent 适配器 | OpenClaw、Hermes、LangGraph、OpenAI Agents、Vercel AI SDK、Microsoft Agent Framework | 回合开始召回、结束可靠写回；失败不能被静默吞掉 |
| Codex 插件 | 项目/全局记忆、自动召回、压缩前检查点、MCP 查看召回结果 | 九个需用户审阅的 Hook；自动 Hook 超时后 fail-open，不阻塞 Codex |
| MCP Server | 显式 recall、ingest、查询/等待 job | 本地 `stdio`，密钥留在 MCP 宿主进程 |

Web 图谱采用渐进加载：初始只取慢速层摘要；用户展开节点才读取 Fast/Source 邻居；只有
证据端点返回原始 Source 文本。这样既避免一次读取整个记忆空间，也减少浏览器持有敏感
内容的范围。

## 2. API 如何做到可靠和可控

写入不是一次不可靠的 HTTP 调用，而是持久化任务：

```text
认证并确认 Tenant/Scope
  -> 校验请求长度、速率和队列
  -> 以 Idempotency-Key 创建/复用 SQLite Job
  -> 常驻 Writer 通过租约写入 Source 证据
  -> Fast 索引与 Slow 语义演化独立执行
  -> 返回回执；需要强可见性时等待 job succeeded 后再 recall
```

同一个 `Idempotency-Key` 配合相同 payload 会返回原有 Job；使用相同键却改写内容会返回
HTTP 409。外部副作用不确定的写入不会被盲目自动重放，避免重复记忆。需要读己之写时，
写入使用 `read_your_writes`，等待 Job 成功，再将 `wait_for_job_id` 传给 recall。

性能和成本控制不是只靠加机器：

- 服务启动时预加载一个 GPU 检索引擎，避免首个客户请求承担模型加载；
- 常驻 Writer 池只加载一次 Writer/图后端，顺序处理工作，和 API 请求隔离；
- Fast 索引默认 16 条消息或 2 秒触发，让新事实快速可查；
- Slow 图默认 32,000 token 或 64 轮才演化，低活跃 Scope 需满足 24 小时加阈值，且有
  30 分钟冷却，不会每条消息都产生第二次付费模型调用；
- `evidence_mode=auto` 对低风险证据保留 raw，高风险形态才走 Pro 编译；调用方可明确选
  `raw` 或 `compiled`；
- SQLite 事务同时限制每 Tenant 请求/Job、全局 Job、供应商并发和冷却；
- 检索读版本化只读快照，`/readyz` 读取缓存的预检结果而不会每次探活重载模型。

Recall 返回结构化 `evidence` 和可注入的 `prompt_evidence`。后者会去掉内部路径、分数和
调试字段。生产 Recall 固定 Top8 窗口，不能偷偷改大上下文和成本。历史记忆是证据，不能
覆盖当前系统指令或用户指令。

## 3. 单卡部署基线

全量开源版本以**单张 NVIDIA RTX 5090**作为开发基线。公开支持配置为一个服务进程拥有
一个预加载 GPU 检索引擎，配合常驻 Writer 池处理写入。部署默认模型组合时建议**至少
32 GB 显存**；实际并发和时延取决于模型版本、上下文、批处理、主机内存、磁盘和流量，
不应把此建议理解为 SLA。

最小安装步骤：

```bash
git clone https://github.com/reshuibuduo/tmcra.git
cd tmcra/02-tmcra-memory-api
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-tmcra-service.txt

sudo install -d -m 700 /etc/tmcra
sudo cp deploy/tmcra-service.env.example /etc/tmcra/service.env
# 在 service.env 填公网 HTTPS 地址、状态目录、模型路径、CUDA 与非密钥配置
# 在 /etc/tmcra/writer.env 填部署方自己的供应商凭证，绝不提交 Git
python ops/run_tmcra_service_preflight.py --env-file /etc/tmcra/service.env
```

生产环境必须保持 `TMCRA_SERVICE_STARTUP_PREFLIGHT_MODE=full`。预检会检查共享算法哈希、
目录原子写、SQLite、磁盘、供应商配置、索引校验和、CUDA、模型推理、图适配器和 Writer
握手，且不会产生付费供应商调用。`/healthz` 只说明进程存活；切流应以 `/readyz` 为准。

多卡不是当前公开版本的已支持拓扑。若要多卡并行、模型切分、跨设备检索或故障切换，
部署方必须自行验证模型和进程到 GPU 的映射、显存压力、路由、索引代际可见性、Writer
重启、部分 GPU 不可用时的就绪语义、基准一致性及回滚到单卡的路径。

## 4. 让已有记忆系统接入 TMCRA

推荐先把 TMCRA 当作“可追溯证据 + 有界召回”的旁路，而不是一次性替换 CRM、聊天历史或
向量库。可按以下四个阶段推进：

1. **定义身份映射。** 客户账户对应 `tenant_id`；终端用户、项目、Agent 或记忆角色对应
   稳定且不透明的 `scope_name`；会话/任务对应 `session_id`；原系统事件 ID 同时作为
   `message_id` 与幂等键来源。
2. **双写一个业务流。** 原数据库继续作为事实源，后端把带 role、来源、原始 timestamp 的
   消息/事件写入 TMCRA，并将返回 Job 回执和源事件一起保存。
3. **先观察再注入。** 对照 Recall 结果、队列时间、失败率、费用和 Scope 是否正确；未通过
   审核时只记录，不把记忆注入客户回答。
4. **按功能逐步开启。** 先对低风险功能或小租户群在 Agent/工作流动作前注入
   `prompt_evidence`，结果完成后再按同一边界写回。保留可关闭 Recall 的功能开关。

浏览器、手机和普通客户端不应持有 Tenant Key。它们先由产品后端认证，后端选择 Scope 后
调用 TMCRA；直接客户端场景使用 Scoped Token 或受控 Gateway。

## 5. 需要改造 TMCRA 时改哪里

| 改造目标 | 推荐位置 | 不能破坏的约束 |
|---|---|---|
| 新业务后端 | 组件 06 的 Python/TypeScript SDK 或 REST | 后端负责 Tenant/Scope 和回执 |
| 新 Agent 框架 | `06-tmcra-sdk-integrations/integrations/` 新适配器 | 开始召回、结束写回、失败显式可见 |
| 新交互宿主 | 组件 08 MCP Server | 凭证留在本地宿主，记忆保持为不可信证据 |
| 新浏览器产品 | 组件 03 BFF 模式 | 浏览器没有 Tenant Key 和任意 Scope 选择权 |
| 新领域数据结构 | 事件映射/metadata | 稳定 ID、原始时间、Actor 来源、幂等性 |
| 核心算法 | 组件 01 与组件 02 的共享核心 | 更新 manifest、跑 `TMCRA_VERIFY_SHARED_CORE=1` 验证、再打包预检 |

组件 02 对共享核心文件的 SHA-256 进行 fail-closed 验证。不要在运行中的服务器直接改
Algorithm 文件；应在源码中改造、重建 manifest、运行服务验证、再生成新发布物并做预检。

## 6. 商业模块实际提供什么

TMCRA 是自托管软件，不是代收款 SaaS。公开版本提供的是让部署方做商业化产品的技术
控制面：

- **账户与组织。** Web Console 有个人、企业、内部运营三类控制面；D1 保存账户、组织、
  Agent、成员和服务端 Tenant/Scope 绑定，浏览器没有生产 Key。
- **凭证与设备。** Tenant API Key 只展示一次且只存 PBKDF2 哈希；支持撤销、权限/Scope
  受限的短期 Token 与桌面设备授权。
- **套餐/成员/配额。** 内部 Billing 支持套餐版本、周期、价格/币种元数据、成员角色、状态、
  原始写入 token 和 recall request 权益；`tokens:manage` 可设置每 Subject 的额度。
- **成本与运营。** Usage 按 Scope、阶段、模型、平台、集成、Agent 等归因；独立 staff key
  的运行时视图提供 Ready、队列、费用和 p50/p95/p99，而不返回客户正文或凭证。
- **产品自动化和数据控制。** 支持签名 Webhook、保留策略、内容删除、导出状态和反馈。Webhook
  只允许 HTTPS，并拒绝本地/私网目标以降低 SSRF 风险。

支付渠道、发票、税务、退款、SSO、法务条款、数据处理协议、地区合规、客服与 SLA 仍由
部署方的产品和运营系统负责。应通过受信任后端、套餐权益 API 和 Webhook 与现有商业系统
连接，不能把 Tenant Key 暴露给用户客户端。详细边界见[商业模块](COMMERCIAL_MODULES.md)。

更完整的英文分册见：[应用实现](APPLICATIONS.md)、[API 与运行时](API_AND_RUNTIME.md)、
[接入与扩展](INTEGRATION_AND_EXTENSION.md)、[模块功能矩阵](MODULES.md)、
[商业模块](COMMERCIAL_MODULES.md)和[部署指南](DEPLOYMENT.md)。
