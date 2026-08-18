# 安装 Vercel AI SDK 的 TMCRA 中间件

```bash
npm install @tmcra/vercel-ai-sdk
```

使用 `createTMCRAMiddleware` 包装应用模型，提供限定 scope 的 client、scope、
session；需要恢复能力时启用持久 `FilePendingTurnQueue`。对于 tool agent，
应从外层 `onFinish` 提交，不能把中间 tool 步骤当作完成回合。

本地执行 `npm run typecheck`、`npm test` 和 `npm run build`。这些命令不会配置
provider，也不等于真实 `generateText`/`streamText` E2E。
