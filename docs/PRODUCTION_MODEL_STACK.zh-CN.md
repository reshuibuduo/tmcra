# 生产模型栈与完整请求链路

本文把 TMCRA 生产链路中每个使用模型的阶段，逐一对应到实际职责、配置变量、部署位置、
替换边界和开源资产。TMCRA 是由本地检索模型、确定性证据计算、本地生成模型、可选
Provider 路由和部署方自己的 Agent 共同组成的长期记忆运行时。

默认生产生成模型为 **`Qwen3.6-35B-A3B`**，总参数 35B、每个 token 约激活 3B。
已验证部署通过 `tmcra-qwen3.6-35b-a3b-iq3s` 本地别名运行，承担 Writer、Reviewer、
召回规划和慢速图谱生成。

## 端到端生产链路

```mermaid
flowchart LR
  EVENT["带 Actor 和来源的消息/事件"]
  WRITER["Writer<br/>Qwen3.6-35B-A3B"]
  REVIEW["Reviewer / 对账<br/>Qwen3.6-35B-A3B"]
  STORE["原始证据 + 快速记忆 + 慢速图谱"]
  EMBED["BAAI/bge-m3<br/>向量召回"]
  GRAPH["图谱候选与确定性候选"]
  VR["TMCRA 运行时 reranker<br/>仓库内置 checkpoint"]
  CROSS["BAAI/bge-reranker-v2-m3<br/>交叉编码重排"]
  PLAN["召回角色规划器<br/>Qwen3.6-35B-A3B"]
  COMPILE["确定性证据编译器"]
  PACK["有来源约束的证据包"]
  AGENT["部署方 Agent 模型<br/>或固定参考答案链路"]

  EVENT --> WRITER --> REVIEW --> STORE
  STORE --> EMBED
  STORE --> GRAPH
  EMBED --> CROSS
  GRAPH --> CROSS
  CROSS --> VR --> PLAN --> COMPILE --> PACK --> AGENT
```

外层 Agent 由部署方选择。任何能够正确消费结构化证据、并遵守指令与证据边界的模型
都可以接入。固定的 GPT-5.4 只用于公开参考/评测答案链路；Memory API 集成可以使用
自己的业务模型。

## 每个模型负责什么

| 阶段 | 参考生产配置 | 实际职责 | 运行位置 | 配置与替换边界 |
|---|---|---|---|---|
| 主 Writer | `Qwen3.6-35B-A3B` | 从已接受写入中提取带来源的原始记录和快速记忆断言 | 本地 OpenAI-compatible 地址 | `TMCRA_WRITER_PROVIDER=local-qwen`、`TMCRA_LOCAL_WRITER_*`、`qwen36-v5` |
| Writer Reviewer | `Qwen3.6-35B-A3B` | 审核歧义/冲突输出，执行更强的对账与修复路径 | 本地 OpenAI-compatible 地址 | `TMCRA_WRITER_REVIEWER_PROVIDER=local-qwen`、`qwen36-reconciliation-v1` |
| 慢速图谱 | `Qwen3.6-35B-A3B` | 批量构建长期语义胶囊，修复跨槽位冲突 | 本地 OpenAI-compatible 地址 | `TMCRA_SLOW_GRAPH_PROVIDER=local-qwen`、`qwen36-slow-graph-v1` |
| 向量模型 | `BAAI/bge-m3` | 对 Query 和原始证据窗口编码，产生向量候选 | Memory API 本地 GPU | `TMCRA_EMBEDDING_MODEL`；源码配置为 1024 维、最大长度 8192 |
| 图谱候选 | 公开默认配置不调用基础模型 | 生成 Fast/Slow 图谱候选及原始证据坐标 | 本地确定性运行时 | 公开支持配置为 `TMCRA_LEARNED_GRAPH_ENABLED=0` |
| Cross Encoder | `BAAI/bge-reranker-v2-m3` | 对 Query/证据对进行交叉编码重排 | Memory API 本地 GPU | `TMCRA_CROSS_MODEL`；最大长度 1280、batch 24 |
| TMCRA 本地 reranker | `tmcra_v3_reranker.pt` | 融合 Cross Encoder 表征/分数以及 dense、graph、selection、recency 通道 | 本地 GPU，checkpoint 随仓库提供 | `TMCRA_CHECKPOINT`；组件 02 提供 Apache-2.0 声明和校验值 |
| 召回规划器 | `Qwen3.6-35B-A3B` | 解析当前问题，为各证据层分配 evidence/context 角色并保留原始证据池 | 本地 OpenAI-compatible 地址 | `TMCRA_RECALL_PLANNER_PROVIDER=local-qwen`、`qwen36-planner-v1`；默认输出 512 tokens、超时 60 秒 |
| 证据编译器 | 不使用模型 | 绑定 Source ID，执行时间、计数、排序、集合运算并生成可验证证据包 | 确定性 Python 代码 | 不允许用自由生成模型替代确定性计算 |
| 答案/业务 Agent | 部署方选择 | 消费 prompt-ready 证据，完成客服、助手、业务 Agent 等产品任务 | 应用或 Agent 宿主 | 可使用自己的模型；公开参考/评测答案链路固定为 `gpt-5.4` |

## 源码内置召回参数

`V4OnlineEngine` 创建串行化模型 Lane，源码中的生产参数如下。这些是当前实现默认值；
部署方需要根据目标硬件实测：

| 参数 | 当前值 | 含义 |
|---|---:|---|
| Embedding 维度 | 1024 | BGE-M3 运行时向量宽度 |
| Embedding 最大长度 | 8192 | 输入向量模型的文本上限 |
| Dense shortlist | 32 | 第一阶段向量候选数 |
| Slow dense shortlist | 24 | 慢速层向量候选数 |
| Graph candidates | 24 | 融合前图谱事件候选数 |
| Graph top-k | 12 | 图谱进入排序联合的候选数 |
| Cross Encoder 最大长度 | 1280 | Query/证据对输入上限 |
| Cross Encoder batch | 24 | 本地重排批大小 |
| 默认返回 top-k | 8 | 基础证据包大小 |
| 自适应 top-k | 8 / 12 / 16 | simple / standard / complex 配置 |

服务源码默认保持 2 个召回 Lane、全局召回队列 8、单 Tenant 队列 2，并预留 6 GiB GPU
余量、按每个模型副本 5 GiB 估算。修改前需要在目标 GPU 上测量真实权重占用、上下文、
并发和延迟目标。

## 默认本地生产配置

公开生产默认链路使用：

- 本地 `Qwen3.6-35B-A3B`：Writer、Reviewer、召回角色规划和慢速图谱；
- 本地 BGE-M3 与 BGE reranker V2 M3：向量召回和 Cross Encoder 重排；
- 仓库内置 TMCRA reranker checkpoint：本地学习排序融合；
- 确定性证据编译器：计算和绑定 Source ID；
- GPT-5.4：仅用于固定参考/评测答案链路。

本地 Qwen 在单机配置中绑定 loopback，API Key 文件位于部署方控制的本地模型状态目录。
启用 Provider 后，调用会进入日志和 Tenant/Scope 用量归因；凭证保存在
`/etc/tmcra/writer.env` 或权限受限的 Key 文件中。

部署时复制脱敏模板：

```bash
sudo install -d -m 700 /etc/tmcra
sudo cp 02-tmcra-memory-api/deploy/tmcra-service.env.example /etc/tmcra/service.env
sudo cp 02-tmcra-memory-api/deploy/writer.env.example /etc/tmcra/writer.env
sudo chmod 600 /etc/tmcra/service.env /etc/tmcra/writer.env
```

必须替换模板中的路径和密钥占位符；填入真实值后禁止提交到 Git。

## 本地开源模型和自有模型接入

本地生成路由是默认生产路由，承担 Writer、Reviewer、Recall Planner 和 Slow Graph。
已测试模型为 `Qwen3.6-35B-A3B`，参考部署别名为
`tmcra-qwen3.6-35b-a3b-iq3s`，通过本机 OpenAI-compatible 地址和分角色 Prompt Adapter
运行。模型别名、GGUF 路径、文件大小校验值和 `llama-server` 路径均可配置；运行时校验
配置身份、回环地址和真实接口可用性，不再限制模型家族。

GPU Scheduler 会分别调度 Writer、Planner 和慢速图谱 Lane。仓库不重新分发对应权重；
部署方需要自行准备权重、固定 checksum 并核对许可证。换用其他模型后，需要调整 Writer、
Reviewer、Planner、Slow Graph 四组 Prompt，并重新执行预检、接入测试与 Benchmark 回归。
DeepSeek V4 Flash/Pro 保留为可选 Provider 路由，启用时必须显式配置 Provider 与部署方
自己的 Key Pool。

Writer/Reviewer 还提供经过 URL、Schema 和 Prompt Adapter 校验的
OpenAI-compatible Provider 边界。模型可配置只代表接口入口开放；替换模型仍须验证
结构化输出、Source 归因、上下文长度、超时、重试和失败语义。

如果要接入自己的记忆算法，可在事件适配、召回适配或选定业务流边界接入，详见
[自有系统接入与扩展](INTEGRATION_AND_EXTENSION.md)。如果只是更换业务 Agent 模型，
无需修改 Memory API，只要通过 SDK、生命周期 Adapter 或 MCP 消费有边界的证据包。

## 语音模型链路

语音采集与文本记忆召回链路相互独立：

- Android 使用 sherpa-onnx 执行本地 VAD、流式 ASR 和本地说话人模型；
- 可选的远程复核链路使用 Qwen3-ASR 0.6B；
- 正常路径下原始音频和说话人 embedding 留在设备端；
- 只有审核后的文本及主体归因进入 Memory API。

模型上游地址、分发状态和许可证信号见
[组件 10 模型溯源](../10-tmcra-model-data-assets/MODEL_PROVENANCE.md)。

## 仓库实际分发什么

仓库分发完整 TMCRA 源码以及小型 TMCRA 运行时 reranker checkpoint。第三方 BGE、
Qwen、ASR、分段和说话人模型权重不打包进仓库。部署方需从上游下载，记录自身 checksum，
核对相应许可证，并在发布清单中固定实际部署的模型身份。
