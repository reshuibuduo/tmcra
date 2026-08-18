export class TMCRAError extends Error {
    requestId;
    details;
    constructor(message, options = {}) {
        super(message, { cause: options.cause });
        this.name = "TMCRAError";
        this.requestId = options.requestId;
        this.details = options.details;
        Object.setPrototypeOf(this, new.target.prototype);
    }
}
export class TMCRAHttpError extends TMCRAError {
    status;
    method;
    path;
    retryAfterSeconds;
    constructor(message, options) {
        super(message, options);
        this.name = "TMCRAHttpError";
        this.status = options.status;
        this.method = options.method;
        this.path = options.path;
        this.retryAfterSeconds = options.retryAfterSeconds;
    }
}
export class TMCRANetworkError extends TMCRAError {
    constructor(message, options = {}) {
        super(message, options);
        this.name = "TMCRANetworkError";
    }
}
export class TMCRATimeoutError extends TMCRAError {
    timeoutMs;
    constructor(timeoutMs, options = {}) {
        super(`TMCRA request timed out after ${timeoutMs} ms`, options);
        this.name = "TMCRATimeoutError";
        this.timeoutMs = timeoutMs;
    }
}
export class TMCRAAbortError extends TMCRAError {
    constructor(options = {}) {
        super("TMCRA request was aborted", options);
        this.name = "TMCRAAbortError";
    }
}
export class TMCRAResponseParseError extends TMCRAError {
    status;
    constructor(status, options = {}) {
        super(`TMCRA returned an invalid JSON response (HTTP ${status})`, options);
        this.name = "TMCRAResponseParseError";
        this.status = status;
    }
}
export class TMCRAJobPollingTimeoutError extends TMCRAError {
    jobId;
    lastJob;
    constructor(jobId, timeoutMs, lastJob) {
        super(`Timed out polling TMCRA job ${jobId} after ${timeoutMs} ms`, {
            details: lastJob,
        });
        this.name = "TMCRAJobPollingTimeoutError";
        this.jobId = jobId;
        this.lastJob = lastJob;
    }
}
export class TMCRAJobFailedError extends TMCRAError {
    jobId;
    job;
    constructor(jobId, job) {
        super(`TMCRA job ${jobId} finished with a non-success terminal state`, {
            details: job,
        });
        this.name = "TMCRAJobFailedError";
        this.jobId = jobId;
        this.job = job;
    }
}
//# sourceMappingURL=errors.js.map