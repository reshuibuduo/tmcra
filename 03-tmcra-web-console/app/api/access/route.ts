import { getD1 } from "@/db";

export const dynamic = "force-dynamic";

const RESPONSE_HEADERS = {
  "Cache-Control": "no-store",
  "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
};

const INDUSTRIES = new Set(["ai-software", "enterprise-services", "consumer", "finance", "healthcare", "education", "robotics", "research", "other"]);
const COMPANY_SIZES = new Set(["1-10", "11-50", "51-200", "201-1000", "1000+"]);
const TIMELINES = new Set(["now", "30-days", "quarter", "exploring"]);
const PLATFORMS = new Set(["REST / OpenAPI", "Python SDK", "TypeScript SDK", "MCP Server", "Codex", "OpenClaw", "Hermes Agent"]);
const MAX_BODY_BYTES = 16 * 1024;

export async function POST(request: Request) {
  try {
    requireSameOrigin(request);
    const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
    if (!contentType.startsWith("application/json")) {
      return error(415, "unsupported_media_type", "Content-Type must be application/json.");
    }

    const announcedLength = Number(request.headers.get("content-length") ?? "0");
    if (Number.isFinite(announcedLength) && announcedLength > MAX_BODY_BYTES) {
      return error(413, "payload_too_large", "Request body is too large.");
    }

    const raw = await request.text();
    if (new TextEncoder().encode(raw).byteLength > MAX_BODY_BYTES) {
      return error(413, "payload_too_large", "Request body is too large.");
    }

    let payload: unknown;
    try {
      payload = JSON.parse(raw);
    } catch {
      return error(400, "invalid_json", "Request body must be valid JSON.");
    }

    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return error(400, "invalid_request", "Request body must be a JSON object.");
    }

    const data = payload as Record<string, unknown>;
    if (typeof data.website === "string" && data.website.trim()) {
      return Response.json({ ok: true }, { status: 200, headers: RESPONSE_HEADERS });
    }

    const contactName = requiredText(data, "contactName", 120);
    const emailDisplay = requiredText(data, "email", 254);
    const emailNormalized = emailDisplay.toLowerCase();
    if (
      emailDisplay.length > 254 ||
      !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailDisplay)
    ) {
      return error(400, "invalid_email", "Enter a valid work email address.");
    }

    const companyName = requiredText(data, "companyName", 160);
    const industry = allowedValue(data, "industry", INDUSTRIES);
    const companySize = allowedValue(data, "companySize", COMPANY_SIZES);
    const timeline = allowedValue(data, "timeline", TIMELINES);
    const primaryUseCase = requiredText(data, "useCase", 3000);
    if (primaryUseCase.length < 30) {
      return error(400, "use_case_too_short", "Describe the pilot use case in at least 30 characters.");
    }
    if (data.consent !== true) {
      return error(400, "consent_required", "Consent is required before submitting the application.");
    }
    if (!Array.isArray(data.platforms)) {
      return error(400, "invalid_platforms", "Select at least one supported integration.");
    }
    const platforms = [...new Set(data.platforms.map((value) => typeof value === "string" ? value.trim() : ""))];
    if (!platforms.length || platforms.some((platform) => !PLATFORMS.has(platform))) {
      return error(400, "invalid_platforms", "Select one or more supported integrations.");
    }

    const now = Date.now();
    const requestId = crypto.randomUUID();
    const saved = await getD1()
      .prepare(
        `INSERT INTO early_access_requests
          (id, email_normalized, email_display, contact_name, company_name,
           industry, company_size, primary_use_case, platforms_json, timeline,
           source, status, version, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'website', 'new', 1, ?, ?)
         ON CONFLICT(email_normalized) DO UPDATE SET
          email_display = excluded.email_display,
          contact_name = excluded.contact_name,
          company_name = excluded.company_name,
          industry = excluded.industry,
          company_size = excluded.company_size,
          primary_use_case = excluded.primary_use_case,
          platforms_json = excluded.platforms_json,
          timeline = excluded.timeline,
          status = CASE WHEN early_access_requests.status = 'closed' THEN 'new' ELSE early_access_requests.status END,
          version = early_access_requests.version + 1,
          updated_at = excluded.updated_at
         RETURNING id`,
      )
      .bind(requestId, emailNormalized, emailDisplay, contactName, companyName, industry, companySize, primaryUseCase, JSON.stringify(platforms), timeline, now, now)
      .first<{ id: string }>();

    return Response.json({ ok: true, requestId: saved?.id ?? requestId }, { status: 200, headers: RESPONSE_HEADERS });
  } catch (cause) {
    if (cause instanceof AccessRequestError) {
      return error(cause.status, cause.code, cause.message);
    }
    console.error("TMCRA access request failed", {
      error: cause instanceof Error ? cause.name : "UnknownError",
    });
    return error(500, "internal_error", "The access request could not be saved.");
  }
}

function requiredText(data: Record<string, unknown>, field: string, maxLength: number) {
  const value = typeof data[field] === "string" ? data[field].trim() : "";
  if (!value || value.length > maxLength) {
    throw new AccessRequestError(400, `invalid_${field}`, `${field} is required and must be ${maxLength} characters or fewer.`);
  }
  return value;
}

function allowedValue(data: Record<string, unknown>, field: string, allowed: Set<string>) {
  const value = typeof data[field] === "string" ? data[field].trim() : "";
  if (!allowed.has(value)) {
    throw new AccessRequestError(400, `invalid_${field}`, `${field} is not supported.`);
  }
  return value;
}

function requireSameOrigin(request: Request) {
  if (request.headers.get("sec-fetch-site") === "cross-site") {
    throw new AccessRequestError(403, "cross_site_request", "Cross-site requests are not allowed.");
  }
  const origin = request.headers.get("origin");
  if (origin && !allowedRequestOrigins(request).has(normalizeOrigin(origin))) {
    throw new AccessRequestError(403, "origin_mismatch", "Request origin is not allowed.");
  }
}

function allowedRequestOrigins(request: Request) {
  const origins = new Set([new URL(request.url).origin]);
  for (const value of (process.env.TMCRA_PUBLIC_ORIGINS ?? "").split(",")) {
    const origin = normalizeOrigin(value);
    if (origin) origins.add(origin);
  }
  return origins;
}

function normalizeOrigin(value: string) {
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "https:" && url.protocol !== "http:") return "";
    if (url.username || url.password || url.pathname !== "/" || url.search || url.hash) return "";
    return url.origin;
  } catch {
    return "";
  }
}

function error(status: number, code: string, message: string) {
  return Response.json(
    { ok: false, error: { code, message } },
    { status, headers: RESPONSE_HEADERS },
  );
}

class AccessRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}
