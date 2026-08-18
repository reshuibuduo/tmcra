# Microsoft Agent Framework 的 TMCRA 提供程序

`TmcraAIContextProvider` 在 Agent 调用前召回临时证据，并在成功调用后写入 user/assistant 回合。宿主需要实现持久 `ITmcraPendingIngestStore`，用稳定幂等键进行响应丢失恢复；只有 job `succeeded` 才移除记录。

项目目标为 .NET 8，发布前必须完成 C# 的 `dotnet build`/`dotnet pack` 和一次性 scope 的真实 E2E。详见仓库 `docs/integrations/microsoft-agent-framework.zh-CN.md`。
