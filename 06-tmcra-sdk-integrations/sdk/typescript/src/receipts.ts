import type {
  IngestReceipt,
  JobView,
  JsonValue,
  RecallReceipt,
  RecallResponse,
  ReceiptStatus,
  WatermarkView,
} from "./models.ts";
import { sha256Hex } from "./hash.ts";

const EMPTY_WATERMARKS: WatermarkView = Object.freeze({
  sourceEventSeq: null,
  promotedEventSeq: null,
  indexedEventSeq: null,
  sourceRawTokenEstimate: null,
  available: false,
});

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function findWatermarkObject(value: unknown, depth = 0): Record<string, unknown> | undefined {
  if (depth > 4) return undefined;
  const record = asRecord(value);
  if (!record) return undefined;
  if (
    "source_event_seq" in record || "promoted_event_seq" in record ||
    "indexed_event_seq" in record || "source_raw_token_estimate" in record
  ) return record;
  for (const child of Object.values(record)) {
    const found = findWatermarkObject(child, depth + 1);
    if (found) return found;
  }
  return undefined;
}

export function extractWatermarks(value: unknown): WatermarkView {
  const record = findWatermarkObject(value);
  if (!record) return EMPTY_WATERMARKS;
  const result: WatermarkView = {
    sourceEventSeq: finiteNumber(record.source_event_seq),
    promotedEventSeq: finiteNumber(record.promoted_event_seq),
    indexedEventSeq: finiteNumber(record.indexed_event_seq),
    sourceRawTokenEstimate: finiteNumber(record.source_raw_token_estimate),
    available: [
      record.source_event_seq,
      record.promoted_event_seq,
      record.indexed_event_seq,
      record.source_raw_token_estimate,
    ].some((item) => finiteNumber(item) !== null),
  };
  return Object.freeze(result);
}

function promptEvidence(response: RecallResponse): Record<string, unknown> | undefined {
  return asRecord(response.prompt_evidence);
}

export async function makeRecallReceipt(response: RecallResponse): Promise<RecallReceipt> {
  const evidence = promptEvidence(response);
  const content = typeof evidence?.content === "string" ? evidence.content : null;
  const declaredHash = typeof evidence?.content_sha256 === "string" ? evidence.content_sha256 : null;
  const evidenceHash = declaredHash ?? (content === null ? null : await sha256Hex(content));
  return Object.freeze({
    queryId: response.query_id,
    scopeName: response.scope_name,
    indexJobId: response.index_job_id,
    evidenceHash,
    submittedStatus: "completed",
    finalStatus: "completed",
    submitted: true,
    final: true,
    statusUrl: null,
    watermarks: extractWatermarks(response),
  });
}

function terminalReceiptStatus(status: string): "succeeded" | "failed" | "cancelled" {
  if (status === "succeeded" || status === "failed" || status === "cancelled") return status;
  throw new TypeError(`job status ${status || "unknown"} is not terminal`);
}

export function makeSubmittedIngestReceipt(
  scopeName: string,
  messageIds: readonly string[],
  job: JobView,
): IngestReceipt {
  return Object.freeze({
    scopeName,
    messageIds: Object.freeze([...messageIds]),
    jobId: job.job_id,
    submittedStatus: "submitted",
    observedStatus: job.status,
    finalStatus: null,
    submitted: true,
    final: false,
    statusUrl: job.status_url || null,
    watermarks: extractWatermarks(job),
  });
}

export function makeFinalIngestReceipt(
  initial: IngestReceipt,
  job: JobView,
): IngestReceipt {
  const status = terminalReceiptStatus(job.status);
  return Object.freeze({
    ...initial,
    jobId: job.job_id,
    finalStatus: status,
    observedStatus: job.status,
    final: true,
    statusUrl: job.status_url || initial.statusUrl,
    watermarks: extractWatermarks(job),
  });
}

export function receiptJson(value: unknown): JsonValue {
  return JSON.parse(JSON.stringify(value)) as JsonValue;
}
