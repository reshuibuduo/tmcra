export interface TMCRAErrorOptions {
    cause?: unknown;
    requestId?: string;
    details?: unknown;
}
export declare class TMCRAError extends Error {
    readonly requestId?: string;
    readonly details?: unknown;
    constructor(message: string, options?: TMCRAErrorOptions);
}
export declare class TMCRAHttpError extends TMCRAError {
    readonly status: number;
    readonly method: string;
    readonly path: string;
    readonly retryAfterSeconds?: number;
    constructor(message: string, options: TMCRAErrorOptions & {
        status: number;
        method: string;
        path: string;
        retryAfterSeconds?: number;
    });
}
export declare class TMCRANetworkError extends TMCRAError {
    constructor(message: string, options?: TMCRAErrorOptions);
}
export declare class TMCRATimeoutError extends TMCRAError {
    readonly timeoutMs: number;
    constructor(timeoutMs: number, options?: TMCRAErrorOptions);
}
export declare class TMCRAAbortError extends TMCRAError {
    constructor(options?: TMCRAErrorOptions);
}
export declare class TMCRAResponseParseError extends TMCRAError {
    readonly status: number;
    constructor(status: number, options?: TMCRAErrorOptions);
}
export declare class TMCRAJobPollingTimeoutError extends TMCRAError {
    readonly jobId: string;
    readonly lastJob?: unknown;
    constructor(jobId: string, timeoutMs: number, lastJob?: unknown);
}
export declare class TMCRAJobFailedError extends TMCRAError {
    readonly jobId: string;
    readonly job: unknown;
    constructor(jobId: string, job: unknown);
}
//# sourceMappingURL=errors.d.ts.map