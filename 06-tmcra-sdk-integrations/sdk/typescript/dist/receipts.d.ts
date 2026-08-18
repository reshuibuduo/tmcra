import type { IngestReceipt, JobView, JsonValue, RecallReceipt, RecallResponse, WatermarkView } from "./models.ts";
export declare function extractWatermarks(value: unknown): WatermarkView;
export declare function makeRecallReceipt(response: RecallResponse): Promise<RecallReceipt>;
export declare function makeSubmittedIngestReceipt(scopeName: string, messageIds: readonly string[], job: JobView): IngestReceipt;
export declare function makeFinalIngestReceipt(initial: IngestReceipt, job: JobView): IngestReceipt;
export declare function receiptJson(value: unknown): JsonValue;
//# sourceMappingURL=receipts.d.ts.map