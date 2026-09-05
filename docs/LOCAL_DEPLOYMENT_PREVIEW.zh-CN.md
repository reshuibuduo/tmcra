# TMCRA 本地部署预览版

核验日期：2026-09-06。代码已接入真实 Memory API；**完整系统验收尚未完成**。生产服务器没有改动。

## 已实现

- Codex / DSH 工作台「模型配置」增加三档模型、容量推荐、安装确认、进度、状态与停止入口。
- 安装器固定模型提交和权重 SHA-256，下载可复用；依赖放进独立 Python 环境。
- 使用本机 SQLite 创建独立身份；连接密钥留在当前用户的私有目录。原云端身份、真实记忆和配置保留。
- Writer、Reviewer、召回规划、Slow Graph、身份归属、证据编译、Session Graph 全部配置到本机生成模型。
- 完全本地模式关闭「把模型任务交回客户端 API」的通道，避免沿用客户端以前保存的云端 Writer / Organizer。
- Python 主进程及子进程拦截外网连接、DNS、外网 UDP 和对外监听；模型运行只用本机文件。此保护属于进程级约束，覆盖范围不包括恶意本机程序、原生扩展或整个操作系统。
- 保留真实 Source → Fast / Slow → 索引 → 召回 / 证据的生产流程。Windows 增加跨进程写锁。
- 384 / 1024 / 2560 维索引分别绑定模型身份；变更模型、前缀、池化或分窗策略后，旧索引拒绝直接复用。不同档位使用独立数据目录，现有记忆不会自动迁移。

## 三档选择

| 档位 | Embedding + Reranker | 完整系统建议内存 | 当前验证 |
|---|---|---|---|
| 轻量 | multilingual-e5-small + mmarco-mMiniLMv2-L12-H384-v1 | 16GB；启动前另检查空闲内存 | CPU 写入、原始证据召回通过；复杂编译超时 |
| 均衡 | BGE-M3 + BGE-reranker-v2-m3 + TMCRA 融合模型 | 32GB；检索建议 6GB 以上显存 | 模型组合与生产一致；消费电脑安装待测 |
| 高配候选 | Qwen3-Embedding-4B + Qwen3-Reranker-0.6B | 64GB；检索建议 16GB 以上显存 | yes/no 适配与接口测试已加；真实硬件与质量待测 |

上述容量为规划估算。安装器分别检测总内存、空闲内存、磁盘和 CUDA。高配档的实际质量提升尚待 TMCRA 记忆任务比较。

轻量 E5 使用 `query:` / `passage:` 前缀与 mean pooling；BGE 使用 CLS；Qwen embedding 使用 last-token pooling。轻量与 Qwen 重排使用各自的语义分数，保留 BGE 专属融合权重的兼容边界。

长 Source 按 token 容量分窗，保留完整原文字符范围；长 Slow 文本明确采用分窗向量均值后归一化。重排长文档覆盖全部窗口并取最高窗口分数。后续质量评测需包含这种长 Slow 聚合策略。

生成统一使用 Qwen3-4B-Q4_K_M、llama.cpp b10276、32K 上下文、单生成槽。当前便携生成程序使用 CPU；CUDA 用于可用设备上的检索模型。生产 35B 模型的 64K 单槽要求保持原样。

## 本机实测

测试机约 16GB 内存、无独显；只写入独立合成测试范围。

| 检查 | 结果 |
|---|---|
| 完整启动预检 | 13 项通过，包含真实模型加载与计算 |
| 单条合成记忆写入 | 112.218 秒，真实 Qwen 生成和 SQLite / 索引持久化 |
| 原始证据召回 | 0.516 秒，正确召回「蓝鲸项目、周五下午三点」 |
| 复杂证据编译 | 本机模型调用在 600 秒超时；未通过 |
| 后台整理及完整重启恢复 | 本轮未完成，不能标注通过 |

回归验证：Python 服务测试 31 项通过；DSH 测试 20 项通过、1 项远程测试跳过；Codex 生命周期、聊天确认、客户端执行器和本地身份隔离测试通过；前端在 390 / 768 / 1280 像素宽度检查通过，测试页面外网请求为 0。模型编译失败已增加可识别的 `local_evidence_compilation_failed` 响应，原始记忆保留。

扩展测试期间可用内存降至约 0.15–0.33GB；生成约 2.6–3 token/s。停止实例后空闲内存约 1.5GB。这里存在明显资源压力，尚未完成性能瓶颈归因。启动前现已要求轻量档约 6.3GB 空闲内存，并缩小生成批次、关闭额外 prompt cache。**这些新限制后的整套运行仍待复测。**

建议当前体验采用前台快速召回、后台异步写入。复杂编译保持单独的未验收状态。

## 安装与连接

当前预览支持 Windows x64，无需预装 Python、TMCRA 账号或 TMCRA 服务器。首次下载 Python、依赖和模型时需要联网；安装完整后，记忆服务运行使用本机模型与数据库。

独立运行包解压后双击 `Install-Local.cmd`，默认轻量档；`Install.ps1 -Profile balanced-bge` / `quality-qwen` 可选其他档位。Codex ZIP 同样提供 `Install-Local.cmd`，额外注册插件。DSH 装入插件后运行 `dsh-tmcra-memory local-install`，免登录打开安装页。三种方式包含真实服务源码、校验清单和 TMCRA 融合模型；其他模型权重自动下载。用户仍需审核宿主的 Hook / 插件权限。

日后运行 `Start.ps1` 或工作台启动按钮直接启动，运行阶段不下载模型。默认数据目录是 `%LOCALAPPDATA%/TMCRA/local`。安装自动选择空闲回环端口，重装优先复用已有端口；启动再次检测冲突，保留原有进程。后端按内容哈希复制到独立运行目录，插件缓存更新不会移动正在使用的代码。

安装完成生成私有文件 `state/<档位>/secrets/client-plugin.json`，并在当前用户 `.config/tmcra/local-memory.json` 登记不含密钥的本地选择。重启 Codex / DSH / 通用 TMCRA MCP 后自动使用独立本地身份。凭据文件请勿粘贴到聊天、提交 Git 或发送给他人。显式高级配置 `TMCRA_CONFIG_FILE` 仍优先，自动安装会提示先清除该覆盖。

Release、Codex 市场源码包与 DSH tarball 均携带后端运行文件和 SHA-256 清单。安装前登记本地选择；下载或启动失败会保持本地模式，旧云端身份继续保存在原位置。选择本地之后，旧连接的新请求与后台云模型调用会被阻止。宿主自身若使用云端主模型，仍可能把召回证据交给其模型提供方；要实现整套 Agent 离线，还需宿主主模型同样本地化。

全新隔离目录已实测：无需系统 Python，自动下载并创建 Python 3.12.14 环境；182 个后端文件的校验与持久复制通过。该结果证明安装基础链路，完整模型运行的验收结果仍以上表为准。

诊断文件：安装输出在 `installation.log`，启动错误在 `launch-error.json` / `launcher-error.log`，服务日志在各档位 `state/<档位>/logs`。停止服务保留模型、身份和记忆；卸载与跨档位记忆迁移尚未实现。

## 下一轮验收

先腾出至少约 6.3GB 可用内存，再验证限流参数后的 Writer / Reviewer、身份归属、证据编译、Slow Graph、知识库投影和服务重启恢复。均衡、高配档需要在对应硬件上另测。当前结果不足以标注「所有电脑都可完整离线运行」。

模型与依赖来源：[E5](https://huggingface.co/intfloat/multilingual-e5-small)、[MiniLM 重排](https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1)、[BGE-M3](https://huggingface.co/BAAI/bge-m3)、[BGE 重排](https://huggingface.co/BAAI/bge-reranker-v2-m3)、[Qwen Embedding](https://huggingface.co/Qwen/Qwen3-Embedding-4B)、[Qwen Reranker](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)、[Qwen 生成模型](https://huggingface.co/Qwen/Qwen3-4B-GGUF)、[PyTorch 安装版本](https://pytorch.org/get-started/previous-versions/)。

E5 / BGE-M3 为 MIT，其余所选上游模型为 Apache-2.0；分发时保留对应许可证和声明。TMCRA 自有融合模型随项目授权。上述结论覆盖本次文本记忆运行包，眼镜、生物识别、音视频模型继续使用各自授权边界。
