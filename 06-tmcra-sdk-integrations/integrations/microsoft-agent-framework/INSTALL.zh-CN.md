# 安装 Microsoft Agent Framework 的 TMCRA 适配

将 `TMCRA.AgentFramework` 项目或 NuGet 包加入 .NET 8 宿主，使用 `HttpClient`、
`TmcraMemoryOptions`、限定 scope 的 token 和宿主实现的
`ITmcraPendingIngestStore` 注册 `TmcraAIContextProvider`。

必须在具备固定版本 `Microsoft.Agents.AI.Abstractions` 1.13.0 的环境中执行
`dotnet build` 和 `dotnet pack`。仓库中的 Python contract 不能替代 C# 构建或宿主 E2E。
