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
  personalMemoryFetch,
  personalMemoryJson,
  requirePersonalAccess,
} from "../server";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
  try {
    const access = await requirePersonalAccess();
    const search = new URL(request.url).searchParams;
    const jobId = requireJobId(search.get("job_id"));
    const exportId = requireExportId(search.get("export_id"));
    const binding = personalMemoryBinding();
    const job = await personalMemoryJson(
      binding,
      `/v1/jobs/${encodeURIComponent(jobId)}`,
      requestId,
    );
    const globalScope = `${access.space.scopeName}-global`;
    const exportJob = inspectOwnedExportJob(job, globalScope, exportId);
    if (exportJob.status === "expired") {
      throw new ConsoleError(410, "export_expired", "This export has expired. Start a new export.");
    }
    if (exportJob.status !== "ready") {
      throw new ConsoleError(409, "export_not_ready", "This export is not ready for download.");
    }

    const upstream = await personalMemoryFetch(
      binding,
      `/v1/scopes/${encodeURIComponent(globalScope)}/exports/${encodeURIComponent(exportId)}`,
      requestId,
    );
    if (!upstream.ok) {
      if (upstream.status === 410) {
        throw new ConsoleError(410, "export_expired", "This export has expired. Start a new export.");
      }
      if (upstream.status === 409) {
        throw new ConsoleError(409, "export_not_ready", "This export is not ready for download.");
      }
      throw new ConsoleError(upstream.status === 404 ? 404 : 502, "export_download_failed", "Export download failed.");
    }
    if (!upstream.body || !upstream.headers.get("content-type")?.toLowerCase().startsWith("application/zip")) {
      throw new ConsoleError(502, "export_invalid_artifact", "The export artifact is invalid.");
    }

    const headers = new Headers(PERSONAL_NO_STORE_HEADERS);
    headers.set("Content-Type", "application/zip");
    headers.set("Content-Disposition", `attachment; filename="tmcra-memory-export-${exportId}.zip"`);
    headers.set("Referrer-Policy", "no-referrer");
    const size = upstream.headers.get("content-length");
    if (size && /^\d{1,16}$/.test(size)) headers.set("Content-Length", size);
    return new Response(upstream.body, { status: 200, headers });
  } catch (error) {
    return personalErrorResponse(mapContractError(error), requestId);
  }
}

function mapContractError(error: unknown) {
  if (!(error instanceof ExportContractError)) return error;
  if (error.code === "invalid_job_id" || error.code === "invalid_export_id") {
    return new ConsoleError(422, error.code, "Export identifier is invalid.");
  }
  if (error.code === "export_not_found") {
    return new ConsoleError(404, error.code, "Export not found.");
  }
  return new ConsoleError(502, error.code, "Export status is invalid.");
}
