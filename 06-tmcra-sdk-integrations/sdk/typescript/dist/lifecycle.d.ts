import type { IdempotentRequestOptions, RequestOptions, WaitForJobOptions } from "./client.ts";
import type { EvidenceMode, IngestRequest, JobView, JsonValue, RecallRequest, RecallResponse, RecallReceipt, IngestReceipt, LifecycleTurnReceipt, ReceiptStatus } from "./models.ts";
import type { PendingTurnQueue } from "./queue.ts";
/** The subset of TMCRAClient used by the optional automatic lifecycle wrapper. */
export interface MemoryLifecycleClient {
    recall(scopeName: string, body: RecallRequest, options?: RequestOptions): Promise<RecallResponse>;
    ingest(scopeName: string, body: IngestRequest, options?: IdempotentRequestOptions): Promise<JobView>;
    waitForJob(jobId: string, options?: WaitForJobOptions): Promise<JobView>;
    getJob?(jobId: string, options?: RequestOptions): Promise<JobView>;
}
export interface TurnIdentityOptions {
    /** Stable logical turn identifier. Prefer this over relying on generated session IDs. */
    turnId?: string;
    /** Full idempotency key. When present it takes precedence over deterministic derivation. */
    turnIdempotencyKey?: string;
}
export interface LifecycleTurnOptions extends TurnIdentityOptions {
    sessionId?: string;
    strictRecall?: boolean;
    strictIngest?: boolean;
}
export interface AutomaticLifecycleConfig {
    /** Required shared team/project boundary. All automatic turn writes go only to this scope. */
    projectScope: string;
    /** Optional user-level scope recalled before the project scope. It is never written automatically. */
    globalScope?: string;
    /** Optional current-agent private scope, recalled after shared project memory and never written automatically. */
    agentPrivateScope?: string;
    /** Current Agent attribution copied into the supported ingest metadata object. */
    agentMetadata?: Readonly<Record<string, JsonValue>>;
    evidenceMode?: EvidenceMode;
    /** Continue the Agent turn when one or more recalls fail. Defaults to true for compatibility. */
    recallFailOpen?: boolean;
    /** Strict mode always stops before the answer when any recall fails. */
    strictRecall?: boolean;
    /** Poll the post-answer ingest job to completion. Defaults to true. */
    waitForIngest?: boolean;
    /** Strict mode requires a succeeded terminal ingest receipt. */
    strictIngest?: boolean;
    /** Polling and timeout controls used only when waitForIngest is true. */
    waitForJob?: Omit<WaitForJobOptions, "throwOnFailure">;
    /** Optional durable queue. The Node file queue is exported separately. */
    pendingQueue?: PendingTurnQueue;
    /** Metadata value identifying the host integration. */
    source?: string;
}
export interface RecallFailure {
    readonly scopeName: string;
    readonly name: string;
    readonly message: string;
}
export interface LifecycleModelMessage {
    readonly role: "system" | "user";
    readonly content: string;
}
export declare class PreparedTurn {
    readonly userContent: string;
    readonly sessionId: string;
    readonly turnId?: string;
    readonly turnIdempotencyKey: string;
    readonly systemContext: string;
    readonly recalledScopes: readonly string[];
    readonly recallErrors: readonly RecallFailure[];
    readonly recallReceipts: readonly RecallReceipt[];
    readonly createdAt: string;
    constructor(options: {
        userContent: string;
        sessionId: string;
        turnId?: string;
        turnIdempotencyKey?: string;
        systemContext: string;
        recalledScopes: readonly string[];
        recallErrors?: readonly RecallFailure[];
        recallReceipts?: readonly RecallReceipt[];
        createdAt?: string;
    });
    /** Ready-to-send system and user messages for chat-style Agent APIs. */
    modelMessages(): LifecycleModelMessage[];
}
export interface LifecycleTurnResult {
    readonly prepared: PreparedTurn;
    readonly assistantContent: string;
    /** Backward-compatible job fields. `jobStatus` is the observed submission/final status. */
    readonly jobId: string;
    readonly jobStatus: string;
    readonly rolesWritten: readonly ["user", "assistant"];
    readonly turnIdempotencyKey: string;
    readonly recallReceipts: readonly RecallReceipt[];
    readonly ingestReceipt: IngestReceipt;
    readonly receipt: LifecycleTurnReceipt;
    readonly submittedStatus: ReceiptStatus;
    readonly finalStatus: ReceiptStatus | null;
    readonly final: boolean;
}
export type LifecycleAnswer = (prepared: PreparedTurn) => string | Promise<string>;
export interface PendingTurnReconciliationResult {
    readonly key: string;
    readonly jobId?: string;
    readonly status: string;
    readonly final: boolean;
    readonly error?: string;
}
export interface ReconcilePendingTurnsOptions {
    waitForIngest?: boolean;
    waitForJob?: Omit<WaitForJobOptions, "throwOnFailure">;
}
/** Deterministically derive the API idempotency key for one logical turn. */
export declare function deriveTurnIdempotencyKey(options: {
    projectScope: string;
    sessionId: string;
    userContent: string;
    turnId?: string;
}): Promise<string>;
/**
 * Opt-in Agent turn wrapper: recall global/project memory, call the answer
 * function with fenced context, then persist separate user/assistant messages.
 */
export declare class TMCRAMemoryLifecycle {
    readonly client: MemoryLifecycleClient;
    private readonly config;
    constructor(client: MemoryLifecycleClient, config: AutomaticLifecycleConfig);
    prepareTurn(userContent: string, options?: LifecycleTurnOptions): Promise<PreparedTurn>;
    commitTurn(prepared: PreparedTurn, assistantContent: string, options?: TurnIdentityOptions & {
        strictIngest?: boolean;
    }): Promise<{
        turnIdempotencyKey: string;
        jobId: string;
        jobStatus: string;
        ingestReceipt: IngestReceipt;
    }>;
    runTurn(userContent: string, answer: LifecycleAnswer, options?: LifecycleTurnOptions): Promise<LifecycleTurnResult>;
    /** Reconcile records left in the durable queue after a crash or lost response. */
    reconcilePendingTurns(options?: ReconcilePendingTurnsOptions): Promise<readonly PendingTurnReconciliationResult[]>;
}
//# sourceMappingURL=lifecycle.d.ts.map