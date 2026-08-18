type MemoryApiEnvironment = Pick<
  Cloudflare.Env,
  | "TMCRA_MEMORY_API_BASE_URL"
  | "TMCRA_MEMORY_API_CONTROL_BASE_URL"
  | "TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK"
  | "TMCRA_MEMORY_API_CONTROL_FETCHER"
>;

export async function fetchMemoryApi(
  runtimeEnv: MemoryApiEnvironment,
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const target = requestUrl(input);
  const rejectRedirects = init?.redirect === "error";
  const compatibleInit = rejectRedirects
    ? { ...init, redirect: "manual" as const }
    : init;
  const loopbackOrigin = configuredLoopbackOrigin(runtimeEnv);
  if (loopbackOrigin && target.origin === loopbackOrigin) {
    const fetcher = runtimeEnv.TMCRA_MEMORY_API_CONTROL_FETCHER;
    const fallback = configuredHttpsFallback(runtimeEnv, target);
    if (!fetcher) {
      if (fallback) {
        return rejectUnexpectedRedirect(
          await fetch(rewriteRequest(input, compatibleInit, fallback)),
          rejectRedirects,
        );
      }
      throw new Error("TMCRA loopback API fetcher is not configured.");
    }
    try {
      return rejectUnexpectedRedirect(
        await fetcher.fetch(input, compatibleInit),
        rejectRedirects,
      );
    } catch (error) {
      if (!fallback || requestSignal(input, init)?.aborted) throw error;
      console.warn("TMCRA loopback API fetch failed; using the HTTPS API fallback.", {
        error: error instanceof Error ? error.name : "UnknownError",
      });
      return rejectUnexpectedRedirect(
        await fetch(rewriteRequest(input, compatibleInit, fallback)),
        rejectRedirects,
      );
    }
  }
  return rejectUnexpectedRedirect(
    await fetch(input, compatibleInit),
    rejectRedirects,
  );
}

function configuredLoopbackOrigin(runtimeEnv: MemoryApiEnvironment): string | null {
  if (runtimeEnv.TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK !== "1") return null;
  let base: URL;
  try {
    base = new URL(String(runtimeEnv.TMCRA_MEMORY_API_CONTROL_BASE_URL ?? "").trim());
  } catch {
    return null;
  }
  if (
    base.protocol !== "http:" ||
    base.hostname !== "127.0.0.1" ||
    base.username ||
    base.password
  ) {
    return null;
  }
  return base.origin;
}

function configuredHttpsFallback(
  runtimeEnv: MemoryApiEnvironment,
  target: URL,
): URL | null {
  let base: URL;
  try {
    base = new URL(String(runtimeEnv.TMCRA_MEMORY_API_BASE_URL ?? "").trim());
  } catch {
    return null;
  }
  if (
    base.protocol !== "https:" ||
    base.username ||
    base.password ||
    base.origin === target.origin
  ) {
    return null;
  }
  const fallback = new URL(target.toString());
  fallback.protocol = base.protocol;
  fallback.host = base.host;
  return fallback;
}

function rewriteRequest(
  input: RequestInfo | URL,
  init: RequestInit | undefined,
  target: URL,
): Request {
  if (input instanceof Request) {
    return new Request(target, new Request(input, init));
  }
  return new Request(target, init);
}

function requestSignal(input: RequestInfo | URL, init?: RequestInit) {
  return init?.signal ?? (input instanceof Request ? input.signal : null);
}

function rejectUnexpectedRedirect(response: Response, rejectRedirects: boolean) {
  if (rejectRedirects && response.status >= 300 && response.status < 400) {
    throw new TypeError("TMCRA Memory API redirects are not allowed.");
  }
  return response;
}

function requestUrl(input: RequestInfo | URL): URL {
  if (input instanceof Request) return new URL(input.url);
  return new URL(input.toString());
}
