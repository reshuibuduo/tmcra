# OpenAI Agents SDK 的 TMCRA 记忆适配

该包在 Agents SDK 的输入回调前执行召回，在 `RunHooks.on_agent_end` 成功后写入回合。证据是临时且不可信的上下文，不会被当作指令；outbox 会保存写入请求，响应丢失时用相同幂等键恢复。

请使用限定 scope 的 token，并为每个并发会话创建独立的 `TMCRAAgentsMemory`。只有 job `succeeded` 才确认并移除待处理记录。详见仓库 `docs/integrations/openai-agents.zh-CN.md`。
