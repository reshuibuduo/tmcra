import { type AuthenticatedSessionView, type BillingProfileView, type BulkIngestRequest, type BulkIngestResponse, type EntitlementUpdateRequest, type FeedbackRequest, type FeedbackResponse, type HealthResponse, type IngestRequest, type IssuedScopeToken, type IssuedWebhook, type JobView, type JsonValue, type MemoryGraphEvidenceResponse, type MemoryGraphLayer, type MemoryGraphResponse, type MemoryGraphTraceRequest, type MemoryGraphTraceResponse, type RecallRequest, type RecallResponse, type QuotaView, type RetentionPolicy, type RetentionPolicyRequest, type ReadinessResponse, type ScopeLifecycle, type ScopeCatalogView, type ScopeSummaryView, type ScopeTokenCreateRequest, type ScopeTokenView, type UsageCosts, type WebhookCreateRequest, type WebhookView } from "./models.ts";
export type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
export interface RetryPolicy {
    /** Total attempts, including the initial request. Set to 1 to disable retries. */
    maxAttempts?: number;
    initialDelayMs?: number;
    maxDelayMs?: number;
    /** Proportion of full jitter around the calculated delay. */
    jitter?: number;
    retryStatusCodes?: readonly number[];
}
export interface RequestOptions {
    signal?: AbortSignal;
    /** Per HTTP attempt timeout. Omit to use the client default. */
    timeoutMs?: number;
    /** Set false to disable the otherwise safe retry policy for this operation. */
    retry?: boolean;
    headers?: HeadersInit;
}
export interface IdempotentRequestOptions extends RequestOptions {
    /** Must be 8-200 characters for the current service. Generated when omitted. */
    idempotencyKey?: string;
}
export interface WaitForJobOptions extends RequestOptions {
    /** Overall polling deadline. Defaults to five minutes. */
    timeoutMs?: number;
    pollIntervalMs?: number;
    maxPollIntervalMs?: number;
    pollBackoffFactor?: number;
    throwOnFailure?: boolean;
}
export interface MemoryGraphOverviewOptions extends RequestOptions {
    layers?: readonly MemoryGraphLayer[];
    limit?: number;
    cursor?: string;
    query?: string;
}
export interface MemoryGraphNeighborsOptions extends RequestOptions {
    depth?: 1 | 2;
    layers?: readonly MemoryGraphLayer[];
    limit?: number;
    cursor?: string;
}
export interface MemoryGraphEvidenceOptions extends RequestOptions {
    limit?: number;
    cursor?: string;
}
export interface TMCRAClientOptions {
    /** Defaults to the production TMCRA API. */
    baseUrl?: string;
    /** The raw API key; it is sent only as a Bearer token. */
    apiKey?: string;
    fetch?: FetchLike;
    defaultTimeoutMs?: number;
    retry?: RetryPolicy;
    headers?: HeadersInit;
    /** Ledger surface. Defaults to `typescript`. */
    clientPlatform?: string;
    /** Optional installation/connection registry ID. */
    integrationId?: string;
    /** Optional invoking Agent ID for multi-agent attribution. */
    agentId?: string;
}
export declare class TMCRAClient {
    readonly baseUrl: string;
    private readonly apiKey?;
    private readonly fetchImpl;
    private readonly defaultTimeoutMs?;
    private readonly retryPolicy;
    private readonly defaultHeaders?;
    constructor(options: TMCRAClientOptions);
    healthz(options?: RequestOptions): Promise<HealthResponse>;
    readyz(options?: RequestOptions): Promise<ReadinessResponse>;
    authenticatedSession(options?: RequestOptions): Promise<AuthenticatedSessionView>;
    listScopes(options?: RequestOptions & {
        prefix?: string;
        limit?: number;
    }): Promise<ScopeCatalogView[]>;
    scopeSummary(scopeName: string, options?: RequestOptions): Promise<ScopeSummaryView>;
    quota(subject?: string, options?: RequestOptions): Promise<QuotaView>;
    billingProfile(options?: RequestOptions): Promise<BillingProfileView>;
    setEntitlement(subject: string, body: EntitlementUpdateRequest, options?: RequestOptions): Promise<QuotaView>;
    setQuotaEntitlement(subject: string, body: EntitlementUpdateRequest, options?: RequestOptions): Promise<QuotaView>;
    ingest(scopeName: string, body: IngestRequest, options?: IdempotentRequestOptions): Promise<JobView>;
    bulkIngest(scopeName: string, body: BulkIngestRequest, options?: RequestOptions): Promise<BulkIngestResponse>;
    consolidate(scopeName: string, options?: IdempotentRequestOptions): Promise<JobView>;
    recall(scopeName: string, body: RecallRequest, options?: RequestOptions): Promise<RecallResponse>;
    memoryGraph(scopeName: string, options?: MemoryGraphOverviewOptions): Promise<MemoryGraphResponse>;
    memoryGraphNeighbors(scopeName: string, memoryId: string, options?: MemoryGraphNeighborsOptions): Promise<MemoryGraphResponse>;
    memoryGraphEvidence(scopeName: string, memoryId: string, options?: MemoryGraphEvidenceOptions): Promise<MemoryGraphEvidenceResponse>;
    traceMemoryRecall(scopeName: string, body: MemoryGraphTraceRequest, options?: RequestOptions): Promise<MemoryGraphTraceResponse>;
    getJob(jobId: string, options?: RequestOptions): Promise<JobView>;
    cancelJob(jobId: string, options?: RequestOptions): Promise<JobView>;
    retryJob(jobId: string, options?: IdempotentRequestOptions): Promise<JobView>;
    usageCosts(scopeName?: string, options?: RequestOptions & {
        scopePrefix?: string;
        fromTimestamp?: number;
        toTimestamp?: number;
        groupBy?: string;
    }): Promise<UsageCosts>;
    issueAccessToken(body: ScopeTokenCreateRequest, options?: IdempotentRequestOptions): Promise<IssuedScopeToken>;
    confirmAccessToken(tokenId: string, options?: RequestOptions): Promise<ScopeTokenView>;
    listAccessTokens(options?: RequestOptions): Promise<ScopeTokenView[]>;
    revokeAccessToken(tokenId: string, options?: RequestOptions): Promise<{
        token_id: string;
        revoked: boolean;
    }>;
    createWebhook(body: WebhookCreateRequest, options?: RequestOptions): Promise<IssuedWebhook>;
    listWebhooks(options?: RequestOptions): Promise<WebhookView[]>;
    disableWebhook(endpointId: string, options?: RequestOptions): Promise<{
        endpoint_id: string;
        disabled: boolean;
    }>;
    exportScope(scopeName: string, options?: IdempotentRequestOptions): Promise<JobView>;
    downloadScopeExport(scopeName: string, exportId: string, options?: RequestOptions): Promise<Uint8Array>;
    deleteScope(scopeName: string, options?: IdempotentRequestOptions): Promise<JobView>;
    reopenScope(scopeName: string, options?: RequestOptions): Promise<ScopeLifecycle>;
    setRetentionPolicy(scopeName: string, body: RetentionPolicyRequest, options?: RequestOptions): Promise<RetentionPolicy>;
    getRetentionPolicy(scopeName: string, options?: RequestOptions): Promise<RetentionPolicy>;
    submitFeedback(scopeName: string, body: FeedbackRequest, options?: RequestOptions): Promise<FeedbackResponse>;
    waitForJob(jobId: string, options?: WaitForJobOptions): Promise<JobView>;
    private requireIdempotencyKey;
    private requestJson;
    private request;
}
export type { JsonValue };
//# sourceMappingURL=client.d.ts.map