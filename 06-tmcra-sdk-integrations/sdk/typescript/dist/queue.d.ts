import type { IngestRequest, JobStatus } from "./models.ts";
export interface PendingTurnRecord {
    readonly version: 1;
    readonly idempotencyKey: string;
    readonly scopeName: string;
    readonly sessionId: string;
    readonly messageIds: readonly string[];
    readonly body: IngestRequest;
    readonly createdAt: number;
    readonly updatedAt: number;
    readonly jobId?: string;
    readonly statusUrl?: string;
    readonly observedStatus?: JobStatus | string;
    readonly lastError?: string;
}
export interface PendingTurnQueue {
    enqueue(record: PendingTurnRecord): Promise<void>;
    update(idempotencyKey: string, patch: Partial<Omit<PendingTurnRecord, "version" | "idempotencyKey">>): Promise<void>;
    remove(idempotencyKey: string): Promise<void>;
    list(): Promise<readonly PendingTurnRecord[]>;
}
export declare class MemoryPendingTurnQueue implements PendingTurnQueue {
    private readonly records;
    enqueue(record: PendingTurnRecord): Promise<void>;
    update(idempotencyKey: string, patch: Partial<Omit<PendingTurnRecord, "version" | "idempotencyKey">>): Promise<void>;
    remove(idempotencyKey: string): Promise<void>;
    list(): Promise<readonly PendingTurnRecord[]>;
}
/**
 * Small JSON-file queue. It is opt-in so browser consumers remain zero-runtime
 * dependency; Node consumers can point it at an application data directory.
 * Writes use a temporary file followed by rename for crash-safe replacement.
 */
export declare class FilePendingTurnQueue implements PendingTurnQueue {
    private writeChain;
    readonly filePath: string;
    constructor(filePath: string);
    private readState;
    private writeState;
    private mutate;
    enqueue(record: PendingTurnRecord): Promise<void>;
    update(idempotencyKey: string, patch: Partial<Omit<PendingTurnRecord, "version" | "idempotencyKey">>): Promise<void>;
    remove(idempotencyKey: string): Promise<void>;
    list(): Promise<readonly PendingTurnRecord[]>;
}
export declare function createFilePendingTurnQueue(filePath: string): FilePendingTurnQueue;
/**
 * Optional SQLite queue for Node 22+ runtimes exposing `node:sqlite`.
 * The import is lazy so the SDK remains usable in browsers and Node 18.
 */
export declare class SqlitePendingTurnQueue implements PendingTurnQueue {
    readonly databasePath: string;
    private readonly database;
    private constructor();
    static open(databasePath: string): Promise<SqlitePendingTurnQueue>;
    enqueue(record: PendingTurnRecord): Promise<void>;
    update(idempotencyKey: string, patch: Partial<Omit<PendingTurnRecord, "version" | "idempotencyKey">>): Promise<void>;
    remove(idempotencyKey: string): Promise<void>;
    list(): Promise<readonly PendingTurnRecord[]>;
    close(): void;
    private find;
}
//# sourceMappingURL=queue.d.ts.map