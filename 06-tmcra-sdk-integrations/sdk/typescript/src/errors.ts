export interface TMCRAErrorOptions {
  cause?: unknown;
  requestId?: string;
  details?: unknown;
}

export class TMCRAError extends Error {
  readonly requestId?: string;
  readonly details?: unknown;

  constructor(message: string, options: TMCRAErrorOptions = {}) {
    super(message, { cause: options.cause });
    this.name = "TMCRAError";
    this.requestId = options.requestId;
    this.details = options.details;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class TMCRAHttpError extends TMCRAError {
  readonly status: number;
  readonly method: string;
  readonly path: string;
  readonly retryAfterSeconds?: number;

  constructor(
    message: string,
    options: TMCRAErrorOptions & {
      status: number;
      method: string;
      path: string;
      retryAfterSeconds?: number;
    },
  ) {
    super(message, options);
    this.name = "TMCRAHttpError";
    this.status = options.status;
    this.method = options.method;
    this.path = options.path;
    this.retryAfterSeconds = options.retryAfterSeconds;
  }
}

export class TMCRANetworkError extends TMCRAError {
  constructor(message: string, options: TMCRAErrorOptions = {}) {
    super(message, options);
    this.name = "TMCRANetworkError";
  }
}

export class TMCRATimeoutError extends TMCRAError {
  readonly timeoutMs: number;

  constructor(timeoutMs: number, options: TMCRAErrorOptions = {}) {
    super(`TMCRA request timed out after ${timeoutMs} ms`, options);
    this.name = "TMCRATimeoutError";
    this.timeoutMs = timeoutMs;
  }
}

export class TMCRAAbortError extends TMCRAError {
  constructor(options: TMCRAErrorOptions = {}) {
    super("TMCRA request was aborted", options);
    this.name = "TMCRAAbortError";
  }
}

export class TMCRAResponseParseError extends TMCRAError {
  readonly status: number;

  constructor(status: number, options: TMCRAErrorOptions = {}) {
    super(`TMCRA returned an invalid JSON response (HTTP ${status})`, options);
    this.name = "TMCRAResponseParseError";
    this.status = status;
  }
}

export class TMCRAJobPollingTimeoutError extends TMCRAError {
  readonly jobId: string;
  readonly lastJob?: unknown;

  constructor(jobId: string, timeoutMs: number, lastJob?: unknown) {
    super(`Timed out polling TMCRA job ${jobId} after ${timeoutMs} ms`, {
      details: lastJob,
    });
    this.name = "TMCRAJobPollingTimeoutError";
    this.jobId = jobId;
    this.lastJob = lastJob;
  }
}

export class TMCRAJobFailedError extends TMCRAError {
  readonly jobId: string;
  readonly job: unknown;

  constructor(jobId: string, job: unknown) {
    super(`TMCRA job ${jobId} finished with a non-success terminal state`, {
      details: job,
    });
    this.name = "TMCRAJobFailedError";
    this.jobId = jobId;
    this.job = job;
  }
}
