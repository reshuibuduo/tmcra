export const JOB_ID_PATTERN = /^[a-f0-9]{32}$/;
export const EXPORT_ID_PATTERN = /^exp_[a-f0-9]{32}$/;

const PENDING_STATES = new Set(["pending", "running"]);
const FAILED_STATES = new Set(["failed", "cancelled"]);

export class ExportContractError extends Error {
  constructor(code) {
    super(code);
    this.name = "ExportContractError";
    this.code = code;
  }
}

export function requireJobId(value) {
  const clean = typeof value === "string" ? value.trim() : "";
  if (!JOB_ID_PATTERN.test(clean)) throw new ExportContractError("invalid_job_id");
  return clean;
}

export function requireExportId(value) {
  const clean = typeof value === "string" ? value.trim() : "";
  if (!EXPORT_ID_PATTERN.test(clean)) throw new ExportContractError("invalid_export_id");
  return clean;
}

export function inspectOwnedExportJob(value, globalScope, expectedExportId, nowSeconds = Date.now() / 1000) {
  if (
    !isRecord(value) ||
    value.scope_name !== globalScope ||
    value.job_type !== "export_scope"
  ) {
    throw new ExportContractError("export_not_found");
  }

  const jobId = requireJobId(value.job_id);
  const upstreamState = typeof value.status === "string" ? value.status.toLowerCase() : "";
  if (FAILED_STATES.has(upstreamState)) {
    return { jobId, status: "failed", updatedAt: optionalNumber(value.updated_at) };
  }
  if (PENDING_STATES.has(upstreamState)) {
    return { jobId, status: "pending", updatedAt: optionalNumber(value.updated_at) };
  }
  if (upstreamState !== "succeeded" || !isRecord(value.result)) {
    throw new ExportContractError("invalid_export_job");
  }

  const exportId = requireExportId(value.result.export_id);
  if (expectedExportId !== undefined && exportId !== requireExportId(expectedExportId)) {
    throw new ExportContractError("export_not_found");
  }
  const expiresAt = optionalNumber(value.result.expires_at);
  if (expiresAt === null || expiresAt <= nowSeconds) {
    return { jobId, exportId, status: "expired", expiresAt };
  }
  return {
    jobId,
    exportId,
    status: "ready",
    expiresAt,
    sizeBytes: optionalNonNegativeInteger(value.result.size_bytes),
    updatedAt: optionalNumber(value.updated_at),
  };
}

function optionalNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function optionalNonNegativeInteger(value) {
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function isRecord(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
