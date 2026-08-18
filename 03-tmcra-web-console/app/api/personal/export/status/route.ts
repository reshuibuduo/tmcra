import { ConsoleError } from "@/db/console";

import {
  ExportContractError,
  inspectOwnedExportJob,
  requireExportId,
  requireJobId,
} from "../export-contract.mjs";
import {
  PERSONAL_NO_STORE_HEADERS,
  personalErrorResponse,
  personalMemoryBinding,
  personalMemoryJson,
  requirePersonalAccess,
} from "../server";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    const access = await requirePersonalAccess();
    const jobId = requireJobId(new URL(request.url).searchParams.get("job_id"));
    const job = await personalMemoryJson(
      personalMemoryBinding(),
      `/v1/jobs/${encodeURIComponent(jobId)}`,
      requestId,
    );
    const exportJob = inspectOwnedExportJob(job, `${access.space.scopeName}-global`);
    const readyExportId = exportJob.status === "ready" ? requireExportId(exportJob.exportId) : null;
    const downloadUrl = readyExportId
      ? `/api/personal/export/download?job_id=${encodeURIComponent(exportJob.jobId)}&export_id=${encodeURIComponent(readyExportId)}`
      : null;
    return Response.json(
      { ok: true, export: { ...exportJob, downloadUrl } },
      { headers: PERSONAL_NO_STORE_HEADERS },
    );
  } catch (error) {
    return personalErrorResponse(mapContractError(error), requestId);
  }
}

function mapContractError(error: unknown) {
  if (!(error instanceof ExportContractError)) return error;
  if (error.code === "invalid_job_id") {
    return new ConsoleError(422, error.code, "Export job ID is invalid.");
  }
  if (error.code === "export_not_found") {
    return new ConsoleError(404, error.code, "Export not found.");
  }
  return new ConsoleError(502, error.code, "Export status is invalid.");
}
