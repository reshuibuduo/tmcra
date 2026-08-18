import { sha256Hex } from "./hash.js";
const EMPTY_WATERMARKS = Object.freeze({
    sourceEventSeq: null,
    promotedEventSeq: null,
    indexedEventSeq: null,
    sourceRawTokenEstimate: null,
    available: false,
});
function asRecord(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value)
        ? value
        : undefined;
}
function finiteNumber(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function findWatermarkObject(value, depth = 0) {
    if (depth > 4)
        return undefined;
    const record = asRecord(value);
    if (!record)
        return undefined;
    if ("source_event_seq" in record || "promoted_event_seq" in record ||
        "indexed_event_seq" in record || "source_raw_token_estimate" in record)
        return record;
    for (const child of Object.values(record)) {
        const found = findWatermarkObject(child, depth + 1);
        if (found)
            return found;
    }
    return undefined;
}
export function extractWatermarks(value) {
    const record = findWatermarkObject(value);
    if (!record)
        return EMPTY_WATERMARKS;
    const result = {
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
function promptEvidence(response) {
    return asRecord(response.prompt_evidence);
}
export async function makeRecallReceipt(response) {
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
function terminalReceiptStatus(status) {
    if (status === "succeeded" || status === "failed" || status === "cancelled")
        return status;
    throw new TypeError(`job status ${status || "unknown"} is not terminal`);
}
export function makeSubmittedIngestReceipt(scopeName, messageIds, job) {
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
export function makeFinalIngestReceipt(initial, job) {
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
export function receiptJson(value) {
    return JSON.parse(JSON.stringify(value));
}
//# sourceMappingURL=receipts.js.map