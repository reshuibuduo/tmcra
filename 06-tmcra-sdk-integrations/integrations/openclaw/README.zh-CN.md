# TMCRA OpenClaw 记忆插件

`@tmcra/openclaw-memory` 0.3.0-rc.2 是基于 OpenClaw hook API 的已编译 ESM 插件。它会自动完成每轮记忆生命周期，并保留可由 Agent 显式调用的工具接口。

## 自动生命周期

- `before_prompt_build` 根据本轮真实提问，在模型回答前召回用户全局 scope 和项目共享 scope，再把有限长度、带“不可信数据”边界的内容注入系统上下文。
- `agent_end` 只处理成功且未中止的轮次，在回答完成后把用户提问和最终 Agent 回答分成两条消息，写入项目共享 scope。
- `gateway_start`、`gateway_stop` 和周期任务负责续传本地待写队列。召回失败时继续回答；TMCRA 暂时不可写时，已完成轮次留在本地队列中。
- 插件不会把 `TMCRA_API_KEY` 或身份派生密钥暴露为命令或模型工具。

## 多 Agent 规则

同一项目中的规划、编码、审查等 Agent 必须配置相同的 `sharedProjectId`（或环境变量 `TMCRA_PROJECT_ID`）。未配置时，插件依次使用 OpenClaw 的 `workspaceDir`、频道/聊天身份作为项目材料。项目 scope 不包含 Agent ID，因此专业 Agent 可以召回彼此已完成的项目进度；session 派生包含 Agent ID，因此各自会话位置不会混在一起。

如果通过 TMCRA 桌面应用管理项目，应优先使用应用复制出的精确 `TMCRA_PROJECT_SCOPE`。它会覆盖本地 scope 派生，使 OpenClaw 与 Codex、Hermes 读写服务器上的同一项目，同时仍保留各 Agent 的独立 session。

用户记录保留 `role=user`、`actor_role=user` 和 `target_agent_id`；Agent 回答保留 `role=assistant`、`actor_role=assistant` 和 `agent_id`。自动写入只进入项目共享 scope。插件会同时召回用户全局 scope 和项目共享 scope，但不会把普通对话写到全局 scope。

0.3.0-rc.2 暂未开放 Agent 私有 scope 配置，因此私有召回默认且固定关闭。需要“仅当前 Agent 可召回”的宿主，应使用 Python 或 TypeScript SDK 的可选生命周期封装。

## 必须由操作者授予的权限

OpenClaw 要求操作者显式允许非内置插件读取完整轮次并注入提示上下文；插件代码不能自行授予：

```json5
{
  plugins: {
    entries: {
      "tmcra-openclaw": {
        enabled: true,
        hooks: {
          allowConversationAccess: true,
          allowPromptInjection: true,
          timeouts: {
            before_prompt_build: 20000,
            agent_end: 30000
          }
        },
        config: {
          baseUrl: "https://api.tmcra.com",
          tenantId: "customer-a",
          sharedProjectId: "checkout-service",
          projectScope: "personal-user-project-checkout-0123456789abcdef",
          queuePath: "/var/lib/openclaw/tmcra/pending-ingest.json"
        }
      }
    }
  }
}
```

普通用户登录 TMCRA 应用后可直接使用受保护的设备配置。托管 Gateway 也可以只在服务进程环境中提供：

```bash
export TMCRA_API_KEY=YOUR_ISSUED_API_KEY
export TMCRA_IDENTITY_SECRET='stable-secret-at-least-16-characters'
```

安装和全流程核验见 [INSTALL.zh-CN.md](./INSTALL.zh-CN.md)。本地测试：

```bash
npm ci
npm test
npm run typecheck
```
