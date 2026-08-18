# Install TMCRA for Microsoft Agent Framework

Add the `TMCRA.AgentFramework` project or NuGet package to a .NET 8 host. Register
`TmcraAIContextProvider` with an `HttpClient`, `TmcraMemoryOptions`, a scope-bound
token, and an application-owned `ITmcraPendingIngestStore`.

Run `dotnet build` and `dotnet pack` in an environment with the pinned
`Microsoft.Agents.AI.Abstractions` 1.13.0 package. The repository Python
contract test does not replace the C# build or host E2E.
