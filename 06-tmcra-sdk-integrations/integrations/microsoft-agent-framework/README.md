# TMCRA for Microsoft Agent Framework

`TmcraAIContextProvider` implements the Agent Framework 1.13 two-phase
`AIContextProvider` contract. `ProvideAIContextAsync` recalls transient evidence;
the framework's default provider pipeline applies source attribution and merges
it. `StoreAIContextAsync` runs only after a successful invocation and writes the
external user message plus final assistant response.

Before invoking an agent, put the TMCRA persona and conversation identifiers in
the session state bag:

```csharp
session.StateBag.SetValue("tmcra.scope_name", "person_123");
session.StateBag.SetValue("tmcra.session_id", "conversation_456");
```

Register the provider with an `HttpClient` and `TmcraMemoryOptions`. Use a
scope-bound token with only `memory:read` and `memory:write`. Recall and ingest
fail closed by default; the two fail-open settings must be enabled explicitly.

The adapter uses `AIContext.Instructions` rather than permanent context messages,
so recalled evidence is not copied into chat history. It also wraps retrieved
text in an indirect-prompt-injection trust boundary.
