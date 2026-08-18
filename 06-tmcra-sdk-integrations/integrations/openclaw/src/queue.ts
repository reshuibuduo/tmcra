import { randomUUID } from "node:crypto";
import { chmod, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import type { IngestInput, IngestSubmission, JobPollResult } from "./client.js";

const QUEUE_VERSION = 2;
const RECEIPT_LIMIT = 500;

export type QueueItemStatus = "queued" | "submitted" | "succeeded" | "failed" | "cancelled" | "dead_letter";
export type ReceiptStatus = "queued" | "submitted" | "succeeded" | "failed" | "cancelled" | "dead_letter";

export interface PendingItem extends IngestInput {
  attempts: number;
  nextAttemptAt: string;
  enqueuedAt: string;
  status: QueueItemStatus;
  receiptId: string;
  jobId?: string;
  submittedAt?: string;
  completedAt?: string;
  lastError?: string;
  lastRequestId?: string;
}

export interface PendingTurn {
  identity: {
    scopeName: string;
    sessionId: string;
    runId: string;
    runKey: string;
  };
  prompt: string;
  userMessageId: string;
  startedAt: number;
}

export interface Receipt {
  receiptId: string;
  kind: "recall" | "ingest";
  status: ReceiptStatus;
  createdAt: string;
  updatedAt: string;
  scopeName?: string;
  scopeNames?: string[];
  sessionId?: string;
  query?: string;
  evidencePreview?: string;
  evidenceCount?: number;
  injected?: boolean;
  requestIds?: string[];
  idempotencyKey?: string;
  jobId?: string;
  attempts?: number;
  error?: string;
  lastError?: string;
}

export interface QueueSnapshot {
  status: "ready" | "repair_required";
  repairRequired?: {
    markerPath: string;
    quarantinedPath?: string;
    reason: string;
  };
  items: PendingItem[];
  pendingTurns: PendingTurn[];
  receipts: Receipt[];
}

export interface DrainOptions {
  limit?: number;
  force?: boolean;
}

export interface DrainResult {
  attempted: number;
  submitted: number;
  sent: number;
  succeeded: number;
  retried: number;
  deadLettered: number;
  remaining: number;
  repairRequired?: boolean;
}

interface QueueLogger {
  warn?(message: string): void;
}

interface QueueFile {
  version: number;
  items: PendingItem[];
  pendingTurns: Record<string, PendingTurn>;
  receipts: Receipt[];
}

interface RepairMarker {
  status: "repair_required";
  queuePath: string;
  quarantinedPath?: string;
  reason: string;
  detectedAt: string;
}

export class QueueRepairRequiredError extends Error {
  readonly markerPath: string;

  constructor(markerPath: string) {
    super("TMCRA OpenClaw queue requires repair");
    this.name = "QueueRepairRequiredError";
    this.markerPath = markerPath;
  }
}

function nextDelay(attempts: number): number {
  return Math.min(6 * 60 * 60 * 1000, 1000 * 2 ** Math.min(attempts, 12));
}

function pollDelay(): number {
  return 1000;
}

function requestId(value: Record<string, unknown>): string | undefined {
  const candidate = value.request_id ?? value.requestId;
  return typeof candidate === "string" && candidate.trim() ? candidate.trim() : undefined;
}

function receiptStatusFromQueue(item: PendingItem): ReceiptStatus {
  return item.status === "dead_letter" ? "dead_letter" : item.status;
}

export class DurablePendingQueue {
  private readonly path: string;
  private readonly markerPath: string;
  private readonly logger: QueueLogger;
  private readonly clock: Pick<typeof Date, "now">;
  private items: PendingItem[] | null = null;
  private pendingTurns: Record<string, PendingTurn> | null = null;
  private receipts: Receipt[] | null = null;
  private repairRequired: RepairMarker | null = null;
  private mutation: Promise<unknown> = Promise.resolve();

  constructor({ path, logger = console, clock = Date }: {
    path: string;
    logger?: QueueLogger;
    clock?: Pick<typeof Date, "now">;
  }) {
    this.path = path;
    this.markerPath = `${path}.repair-required.json`;
    this.logger = logger;
    this.clock = clock;
  }

  private async markRepairRequired(reason: string, quarantinedPath?: string): Promise<void> {
    const marker: RepairMarker = {
      status: "repair_required",
      queuePath: this.path,
      ...(quarantinedPath ? { quarantinedPath } : {}),
      reason,
      detectedAt: new Date(this.clock.now()).toISOString(),
    };
    await mkdir(dirname(this.markerPath), { recursive: true, mode: 0o700 });
    await writeFile(this.markerPath, `${JSON.stringify(marker, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
    });
    this.repairRequired = marker;
    this.items = [];
    this.pendingTurns = {};
    this.receipts = [];
    this.logger.warn?.("tmcra-openclaw: pending queue requires explicit repair");
  }

  private async load(): Promise<PendingItem[]> {
    if (this.items) return this.items;
    try {
      const marker = JSON.parse(await readFile(this.markerPath, "utf8")) as RepairMarker;
      if (marker.status === "repair_required") {
        this.repairRequired = marker;
        this.items = [];
        this.pendingTurns = {};
        this.receipts = [];
        return this.items;
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
        await this.markRepairRequired("invalid repair marker");
        return this.items || [];
      }
    }

    try {
      const parsed = JSON.parse(await readFile(this.path, "utf8")) as Partial<QueueFile> & {
        version?: number;
      };
      if (parsed.version === 1 && Array.isArray(parsed.items)) {
        this.items = parsed.items.map((item) => ({
          ...item,
          status: "queued" as const,
          receiptId: randomUUID(),
        }));
        this.pendingTurns = {};
        this.receipts = [];
        return this.items;
      }
      if (
        parsed.version !== QUEUE_VERSION
        || !Array.isArray(parsed.items)
        || !parsed.pendingTurns
        || typeof parsed.pendingTurns !== "object"
        || !Array.isArray(parsed.receipts)
      ) {
        throw new Error("unsupported queue format");
      }
      this.items = parsed.items.filter((item) => item.status !== "succeeded").map((item) => ({
        ...item,
        status: item.status || "queued",
      })) as PendingItem[];
      this.pendingTurns = parsed.pendingTurns as Record<string, PendingTurn>;
      this.receipts = parsed.receipts as Receipt[];
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        this.items = [];
        this.pendingTurns = {};
        this.receipts = [];
      } else {
        const quarantine = `${this.path}.corrupt-${Date.now()}`;
        await rename(this.path, quarantine).catch(() => undefined);
        await this.markRepairRequired(
          error instanceof Error ? error.message : "invalid queue format",
          quarantine,
        );
      }
    }
    return this.items || [];
  }

  private assertOperational(): void {
    if (this.repairRequired) throw new QueueRepairRequiredError(this.markerPath);
  }

  private async persist(): Promise<void> {
    this.assertOperational();
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.${process.pid}.tmp`;
    await writeFile(
      temporary,
      JSON.stringify({
        version: QUEUE_VERSION,
        items: this.items || [],
        pendingTurns: this.pendingTurns || {},
        receipts: (this.receipts || []).slice(-RECEIPT_LIMIT),
      } satisfies QueueFile, null, 2),
      { encoding: "utf8", mode: 0o600 },
    );
    await rename(temporary, this.path);
    await chmod(this.path, 0o600);
  }

  private async mutate<T>(operation: (items: PendingItem[]) => Promise<T> | T): Promise<T> {
    const next = this.mutation.then(async () => {
      const items = await this.load();
      this.assertOperational();
      const result = await operation(items);
      await this.persist();
      return result;
    });
    this.mutation = next.catch(() => undefined);
    return next;
  }

  private updateReceipt(receiptId: string, patch: Partial<Receipt>): void {
    const receipts = this.receipts || [];
    const index = receipts.findIndex((receipt) => receipt.receiptId === receiptId);
    if (index < 0) return;
    const current = receipts[index];
    if (!current) return;
    receipts[index] = {
      ...current,
      ...patch,
      updatedAt: new Date(this.clock.now()).toISOString(),
    };
  }

  enqueue(item: IngestInput, completedTurnKey?: string): Promise<boolean> {
    return this.mutate((items) => {
      const receipts = this.receipts || [];
      const duplicate =
        items.some((candidate) => candidate.idempotencyKey === item.idempotencyKey)
        || receipts.some((receipt) => receipt.idempotencyKey === item.idempotencyKey);
      if (completedTurnKey) delete (this.pendingTurns || {})[completedTurnKey];
      if (duplicate) return false;
      const timestamp = new Date(this.clock.now()).toISOString();
      const receiptId = randomUUID();
      items.push({
        ...item,
        attempts: 0,
        nextAttemptAt: timestamp,
        enqueuedAt: timestamp,
        status: "queued",
        receiptId,
      });
      receipts.push({
        receiptId,
        kind: "ingest",
        status: "queued",
        createdAt: timestamp,
        updatedAt: timestamp,
        scopeName: item.scopeName,
        sessionId: typeof item.payload.session_id === "string" ? item.payload.session_id : undefined,
        idempotencyKey: item.idempotencyKey,
      });
      this.receipts = receipts.slice(-RECEIPT_LIMIT);
      return true;
    });
  }

  savePendingTurn(turnKey: string, turn: PendingTurn): Promise<void> {
    return this.mutate(() => {
      this.pendingTurns = this.pendingTurns || {};
      this.pendingTurns[turnKey] = turn;
    });
  }

  async getPendingTurn(turnKey: string): Promise<PendingTurn | undefined> {
    await this.load();
    this.assertOperational();
    return this.pendingTurns?.[turnKey];
  }

  deletePendingTurn(turnKey: string): Promise<void> {
    return this.mutate(() => {
      delete (this.pendingTurns || {})[turnKey];
    });
  }

  recordRecallReceipt(receipt: Omit<Receipt, "receiptId" | "kind" | "createdAt" | "updatedAt">): Promise<string> {
    return this.mutate(() => {
      const timestamp = new Date(this.clock.now()).toISOString();
      const receiptId = randomUUID();
      const entry: Receipt = {
        receiptId,
        kind: "recall",
        createdAt: timestamp,
        updatedAt: timestamp,
        ...receipt,
      };
      this.receipts = [...(this.receipts || []), entry].slice(-RECEIPT_LIMIT);
      return receiptId;
    });
  }

  async size(): Promise<number> {
    await this.load();
    this.assertOperational();
    return (this.items || []).filter((item) => item.status !== "dead_letter").length;
  }

  async snapshot(limit = 50): Promise<QueueSnapshot> {
    await this.load();
    const receipts = (this.receipts || []).slice(-Math.max(1, limit)).reverse();
    return {
      status: this.repairRequired ? "repair_required" : "ready",
      ...(this.repairRequired ? {
        repairRequired: {
          markerPath: this.markerPath,
          quarantinedPath: this.repairRequired.quarantinedPath,
          reason: this.repairRequired.reason,
        },
      } : {}),
      items: [...(this.items || [])],
      pendingTurns: Object.values(this.pendingTurns || {}),
      receipts,
    };
  }

  drain(
    submit: (item: PendingItem) => Promise<IngestSubmission>,
    poll: (item: PendingItem, jobId: string) => Promise<JobPollResult>,
    { limit = 20, force = false }: DrainOptions = {},
  ): Promise<DrainResult> {
    return this.mutate(async (items) => {
      const currentTime = this.clock.now();
      const due = items
        .filter((item) => force || Date.parse(item.nextAttemptAt) <= currentTime)
        .slice(0, limit);
      const result: DrainResult = {
        attempted: 0,
        submitted: 0,
        sent: 0,
        succeeded: 0,
        retried: 0,
        deadLettered: 0,
        remaining: items.length,
      };

      for (const item of due) {
        result.attempted += 1;
        try {
          if (["queued", "failed", "cancelled"].includes(item.status)) {
            const submission = await submit(item);
            if (submission.status === "succeeded") {
              item.status = "succeeded";
              item.completedAt = new Date(this.clock.now()).toISOString();
              item.lastRequestId = submission.requestId;
              this.updateReceipt(item.receiptId, {
                status: "succeeded",
                requestIds: submission.requestId ? [submission.requestId] : undefined,
                attempts: item.attempts,
              });
              result.succeeded += 1;
              result.sent += 1;
              await this.persist();
              items.splice(items.indexOf(item), 1);
              await this.persist();
              continue;
            }
            if (!submission.jobId || !["queued", "pending", "running"].includes(submission.status)) {
              throw new Error("ingest response did not contain a pollable job_id");
            }
            item.status = "submitted";
            item.jobId = submission.jobId;
            item.submittedAt = new Date(this.clock.now()).toISOString();
            item.nextAttemptAt = new Date(this.clock.now() + pollDelay()).toISOString();
            item.lastRequestId = submission.requestId;
            this.updateReceipt(item.receiptId, {
              status: "submitted",
              jobId: submission.jobId,
              requestIds: submission.requestId ? [submission.requestId] : undefined,
              attempts: item.attempts,
            });
            result.submitted += 1;
            await this.persist();
          }

          if (item.status !== "submitted" || !item.jobId) continue;
          const polled = await poll(item, item.jobId);
          item.lastRequestId = polled.requestId || item.lastRequestId;
          if (polled.status === "succeeded") {
            item.status = "succeeded";
            item.completedAt = new Date(this.clock.now()).toISOString();
            this.updateReceipt(item.receiptId, {
              status: "succeeded",
              jobId: item.jobId,
              requestIds: item.lastRequestId ? [item.lastRequestId] : undefined,
              attempts: item.attempts,
            });
            result.succeeded += 1;
            result.sent += 1;
            await this.persist();
            items.splice(items.indexOf(item), 1);
            await this.persist();
          } else if (polled.status === "failed" || polled.status === "cancelled") {
            await this.scheduleRetryOrDeadLetter(item, polled.status, polled.error, result);
          } else {
            item.nextAttemptAt = new Date(this.clock.now() + pollDelay()).toISOString();
            this.updateReceipt(item.receiptId, {
              status: "submitted",
              jobId: item.jobId,
              attempts: item.attempts,
            });
            await this.persist();
          }
        } catch (error) {
          await this.scheduleRetryOrDeadLetter(
            item,
            "failed",
            error instanceof Error ? error.message : "send_failed",
            result,
          );
        }
      }
      result.remaining = items.length;
      return result;
    }).catch((error) => {
      if (error instanceof QueueRepairRequiredError) {
        return {
          attempted: 0,
          submitted: 0,
          sent: 0,
          succeeded: 0,
          retried: 0,
          deadLettered: 0,
          remaining: 0,
          repairRequired: true,
        } satisfies DrainResult;
      }
      throw error;
    });
  }

  private async scheduleRetryOrDeadLetter(
    item: PendingItem,
    status: "failed" | "cancelled",
    error: string | undefined,
    result: DrainResult,
  ): Promise<void> {
    item.attempts += 1;
    item.lastError = error || status;
    const maxAttempts = 8;
    if (item.attempts >= maxAttempts) {
      item.status = "dead_letter";
      this.updateReceipt(item.receiptId, {
        status: "dead_letter",
        jobId: item.jobId,
        attempts: item.attempts,
        lastError: item.lastError,
      });
      result.deadLettered += 1;
    } else {
      const failedJobId = item.jobId;
      item.status = status;
      item.jobId = undefined;
      item.nextAttemptAt = new Date(this.clock.now() + nextDelay(item.attempts)).toISOString();
      this.updateReceipt(item.receiptId, {
        status,
        jobId: failedJobId,
        attempts: item.attempts,
        lastError: item.lastError,
      });
      result.retried += 1;
    }
    this.logger.warn?.(
      `tmcra-openclaw: ingest ${status}; ${item.status === "dead_letter" ? "dead-lettered" : "retry scheduled"}`,
    );
    await this.persist();
  }
}
