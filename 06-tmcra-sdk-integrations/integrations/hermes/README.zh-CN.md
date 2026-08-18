# TMCRA Hermes 记忆接入

TMCRA 0.4.1 通过 Hermes 官方 `MemoryProvider` 生命周期接入，不把凭证或写入能力暴露为模型可调用工具。

## 自动生命周期

1. Hermes 在主 Agent 回答前调用 `prefetch()`；插件会召回用户全局记忆和项目共享记忆，并以“不可信数据”边界注入上下文。`queue_prefetch()` 只负责预热同一查询的缓存。
2. 主 Agent 成功回答后，`sync_turn()` 把本轮用户消息和 Agent 回答分成两条记录，持久化到项目共享 scope。
3. 写入先进入本地持久队列，再由后台线程提交；`202 Accepted` 只表示已提交，不表示最终成功。队列会保存 `job_id` 并轮询 `/v1/jobs/{id}`，只有状态为 `succeeded` 才删除；`failed/cancelled` 沿用原幂等键重试，超过上限后进入死信。召回失败和写入失败都会保留诊断收据。
4. 只有 Hermes 明确写入 `USER.md` 的新增或替换内容，才由 `on_memory_write()` 提升到用户全局 scope。普通项目对话不会跨项目写入。

## 多 Agent 规则

同一个项目里的规划、编码、审查等 Agent 必须使用同一个 `TMCRA_PROJECT_ID`，或使用相同的项目/工作区根目录。不要把 Agent 名称拼进项目 ID。项目 scope 不包含 Agent 身份，因此各 Agent 能召回彼此的项目进度；session 的派生材料包含 `agent_identity`，所以不同 Agent 的会话位置仍然独立。

如果使用 TMCRA 桌面应用的项目注册表，请配置应用复制出的精确 `TMCRA_PROJECT_SCOPE`。它会覆盖本地派生，使 Hermes 与 Codex、OpenClaw 读写同一服务器项目，同时不合并各 Agent 的 session。

普通轮次会保留消息主体：用户消息带有 `actor_role=user` 和 `target_agent_id`，Agent 回答带有 `actor_role=assistant` 和 `agent_id`。Hermes 子进程本身不会单独挂载 MemoryProvider；插件使用父进程的 `on_delegation()`，把委派请求和结果都按 assistant 消息写入同一个项目 scope，并保留父子 Agent 归属，绝不会把委派任务伪装成用户陈述。

0.4.1 的 Hermes 适配器没有开放 Agent 私有 scope 配置，因此私有召回默认且固定关闭，自动写入也不会进入私有 scope。确实需要“只给当前 Agent 召回”的场景，应使用 Python 或 TypeScript SDK 的可选生命周期封装。

## 凭证与配置

普通用户登录 TMCRA 应用后，插件会读取受保护的 `~/.config/tmcra/config.json` 和同目录 `installation.json`。服务端部署也可以使用环境变量：

```bash
export TMCRA_BASE_URL='https://api.tmcra.com'
export TMCRA_TENANT_ID='tenant-a'
export TMCRA_API_KEY=YOUR_ISSUED_API_KEY
export TMCRA_IDENTITY_SECRET='stable-secret-at-least-16-characters'
export TMCRA_PROJECT_ID='shared-project-id'
export TMCRA_PROJECT_SCOPE='personal-user-project-checkout-0123456789abcdef'
```

本地待写队列默认位于 `$HERMES_HOME/tmcra-hermes/pending-ingest.json`。它可能含有对话正文，必须限制为 Hermes 服务账号可读写，并排除在源码仓库之外。

队列旁的 `.receipts.jsonl` 保存不含凭证的召回和写入诊断，包括查询、scope、命中/注入状态、请求 ID、job ID 和终态；`TmcraMemoryProvider.get_diagnostics()` 提供可读摘要。如果队列 JSON 损坏，原文件会保留为 `.corrupt.*`，并创建 `.repair_required` 标记。插件不会静默启动空队列，必须恢复有效 JSON 后显式调用 `resume_after_repair()`。

安装和核验见 [INSTALL.zh-CN.md](./INSTALL.zh-CN.md)。本地确定性测试：

```bash
python -m unittest discover -s tests -v
```

`tmcra-hermes-smoke` 会连接真实服务，只能使用受限凭证和一次性项目 scope。测试报告不输出凭证或召回正文。
