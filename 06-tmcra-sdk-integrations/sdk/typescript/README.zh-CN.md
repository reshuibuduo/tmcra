# @tmcra/typescript SDK

0.5.0 是零运行时依赖的 TypeScript/JavaScript TMCRA 客户端，同时提供显式 endpoint client 和**可选**的 Agent 自动记忆生命周期。发布包包含 ESM JavaScript 与类型声明，JavaScript 用户安装同一个包即可。

## 安装

```bash
npm install https://tmcra.com/downloads/integrations/tmcra-typescript-0.5.0.tgz
```

0.5.0 目前由 `tmcra.com` 直接分发，尚未发布到公共 npm registry。在官网明确宣布 registry 版本前，请使用上面的已验证 tgz。

本地发布包核验见 [INSTALL.zh-CN.md](./INSTALL.zh-CN.md)。

## 可选自动生命周期

```ts
import { TMCRAClient, TMCRAMemoryLifecycle } from "@tmcra/typescript";

const client = new TMCRAClient({
  baseUrl: process.env.TMCRA_BASE_URL ?? "https://api.tmcra.com",
  apiKey: process.env.TMCRA_API_KEY!,
});

const memory = new TMCRAMemoryLifecycle(client, {
  projectScope: "project-acme-checkout",           // 项目内所有 Agent 共用
  globalScope: "user-42-global",                   // 可选，只召回
  agentMetadata: {
    agent_id: "implementer",
    agent_name: "Implementer",
  },
  // agentPrivateScope: "agent-implementer-private", // 可选，只召回；默认关闭
  recallFailOpen: true,
  strictRecall: false,
  waitForIngest: true,
  strictIngest: false,
});

const result = await memory.runTurn(
  "接着解析器方案继续做",
  async (prepared) => callAgent(prepared.modelMessages()),
  { sessionId: "implementer-conversation-7", turnId: "turn-42" },
);
console.log(result.receipt.ingest.finalStatus, result.turnIdempotencyKey);
```

执行顺序固定为：根据本轮提问召回可选的用户全局 scope、项目共享 scope 以及可选的当前 Agent 私有 scope；把召回证据作为不可信系统上下文交给回答函数；得到非空回答后，把 user 和 assistant 分成两条记录，只写入项目共享 scope；默认等待写入任务完成。

默认 `recallFailOpen: true`。召回失败会进入 `PreparedTurn.recallErrors`，但 Agent 可以继续回答。回答函数异常或返回空文本时不会写入不完整轮次。回答后的写入错误会抛给宿主。

每个 turn 都有稳定的幂等键，默认由 `projectScope`、`sessionId`、`turnId` 和用户消息确定性派生；也可以传入 `turnIdempotencyKey`。同一个键还会稳定绑定两条消息 ID 和请求时间，因此响应丢失后可以安全恢复而不会重复写入。

`RecallReceipt` 包含 query ID、scope、证据哈希和可得水位；`IngestReceipt` 明确区分 `submittedStatus: "submitted"` 与服务端观测状态。`waitForIngest: false` 时 `final` 必为 `false`，不能当成已经写入完成。开启 `strictRecall` 会在任一召回失败时停止，开启 `strictIngest` 会要求写入最终成功。

Node 应用可将 `createFilePendingTurnQueue("/path/pending.json")` 传入 `pendingQueue`，并在进程启动时调用 `reconcilePendingTurns()`。文件队列使用临时文件替换，重启恢复时复用原幂等键；浏览器可使用 `MemoryPendingTurnQueue` 或实现自己的队列。

Node 22 及以上也可以使用 `SqlitePendingTurnQueue.open("/path/pending.db")`，将待处理队列保存到 SQLite。

## 多 Agent 边界

- 一个项目的所有专业 Agent 使用同一个 `projectScope`，不要把 Agent 名称拼进 scope。
- 不同 Agent 对话使用不同 `sessionId`。不传时每次调用都会新建 session，因此多轮宿主应主动传入稳定值。
- `agentPrivateScope` 默认关闭，只召回、不写入；启用时应在 `agentMetadata.agent_id` 提供当前 Agent 身份。
- 用户记录保留 `role=user`、`actor_role=user` 和 `target_agent_id`；Agent 回答保留 `role=assistant`、`actor_role=assistant`、`agent_id` 以及其他 Agent 元数据。
- 自动对话写入只进入项目 scope。稳定的用户基础信息应通过产品中的显式流程提升到全局 scope。

需要自行控制写入、召回、图谱、保留策略或导出时，直接使用 `TMCRAClient`。生产召回使用固定 Top8 证据合约，`max_windows` 省略或设为 `8`。根租户密钥只能放在可信后端，非可信客户端必须使用短期、最小权限、限定 scope 的 token。

```bash
npm ci  # 下面仅用于源码检出目录的开发构建
npm test
npm run typecheck
npm run build
```

本地确定性测试不连接真实服务。`npm run test:server` 只供操作者使用，必须使用一次性 scope 和受限凭证。
