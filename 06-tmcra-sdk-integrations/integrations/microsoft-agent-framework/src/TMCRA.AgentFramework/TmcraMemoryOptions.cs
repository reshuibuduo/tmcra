namespace TMCRA.AgentFramework;

public interface ITmcraPendingIngestStore
{
    ValueTask SaveAsync(TmcraPendingIngest pending, CancellationToken cancellationToken = default);

    ValueTask MarkSubmittedAsync(string idempotencyKey, string jobId, string? statusUrl, CancellationToken cancellationToken = default);

    ValueTask<IReadOnlyList<TmcraPendingIngest>> ListAsync(CancellationToken cancellationToken = default);

    ValueTask RemoveAsync(string idempotencyKey, CancellationToken cancellationToken = default);
}

public sealed record TmcraPendingIngest(
    string ScopeName,
    string IdempotencyKey,
    string TurnId,
    string SessionId,
    string UserText,
    string AssistantText,
    string OccurredAt,
    string? JobId = null,
    string? StatusUrl = null);

public sealed record TmcraRecallReceipt(
    string Status,
    string? QueryId,
    bool Injected);

public sealed record TmcraIngestReceipt(
    string Status,
    string IdempotencyKey,
    string TurnId,
    string? JobId,
    string? StatusUrl);

public sealed class TmcraMemoryOptions
{
    public required Uri BaseUri { get; init; }

    public required string AccessToken { get; init; }

    public string ScopeNameStateKey { get; init; } = "tmcra.scope_name";

    public string SessionIdStateKey { get; init; } = "tmcra.session_id";

    public bool FailOpenOnRecall { get; init; }

    public bool FailOpenOnIngest { get; init; }

    /// <summary>Optional application-owned durable store for response-loss recovery.</summary>
    public ITmcraPendingIngestStore? PendingIngestStore { get; init; }
}
