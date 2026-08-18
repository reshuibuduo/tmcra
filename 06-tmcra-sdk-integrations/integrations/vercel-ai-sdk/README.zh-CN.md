# Vercel AI SDK 的 TMCRA 中间件

该包用 `LanguageModelV3Middleware` 在生成前召回，并在完成的 `generateText`/`streamText` 回合后写入。tool-call 中间步骤不会被当作完成回合；持久队列会用原始请求体和幂等键处理响应丢失。

只有 job `succeeded` 才移除队列记录。详见仓库 `docs/integrations/vercel-ai-sdk.zh-CN.md`。
