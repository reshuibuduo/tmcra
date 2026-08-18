const HTTPS = "https:";
const VERIFICATION_PATH = "/console/connect/codex";
const MAX_URL_LENGTH = 2048;

export function parseHttpsUrl(value, label = "URL") {
  if (typeof value !== "string" || value.length === 0 || value.length > MAX_URL_LENGTH) {
    throw new TypeError(`${label} is invalid.`);
  }
  if (/[\u0000-\u001f\u007f]/u.test(value)) {
    throw new TypeError(`${label} contains control characters.`);
  }

  let url;
  try {
    url = new URL(value);
  } catch {
    throw new TypeError(`${label} is invalid.`);
  }
  if (url.protocol !== HTTPS || url.username || url.password) {
    throw new TypeError(`${label} must use HTTPS without embedded credentials.`);
  }
  return url;
}

export function normalizeHttpsOrigin(value, label = "origin") {
  const url = parseHttpsUrl(value, label);
  if (url.pathname !== "/" || url.search || url.hash) {
    throw new TypeError(`${label} must be an HTTPS origin without a path, query, or fragment.`);
  }
  return url.origin;
}

export function normalizeAuthorizationBaseUrl(value) {
  const url = parseHttpsUrl(value, "TMCRA authorization URL");
  if (url.search || url.hash) {
    throw new TypeError("TMCRA authorization URL must not contain a query or fragment.");
  }
  return url.toString().replace(/\/$/u, "");
}

export function validateVerificationUrl(value, authorizationBaseUrl) {
  const verification = parseHttpsUrl(value, "TMCRA verification URL");
  const authorization = parseHttpsUrl(
    authorizationBaseUrl,
    "TMCRA authorization URL",
  );
  if (verification.origin !== authorization.origin) {
    throw new TypeError("TMCRA verification URL has an unexpected origin.");
  }
  if (verification.pathname !== VERIFICATION_PATH) {
    throw new TypeError("TMCRA verification URL has an unexpected path.");
  }
  return verification.toString();
}

export function buildConsoleUrl(authorizationBaseUrl, consolePath = "/personal") {
  const authorization = parseHttpsUrl(
    authorizationBaseUrl,
    "TMCRA authorization URL",
  );
  if (typeof consolePath !== "string" || !consolePath.startsWith("/") || consolePath.startsWith("//")) {
    throw new TypeError("TMCRA console path must be a root-relative path.");
  }
  const consoleUrl = new URL(consolePath, `${authorization.origin}/`);
  if (consoleUrl.origin !== authorization.origin) {
    throw new TypeError("TMCRA console URL has an unexpected origin.");
  }
  return consoleUrl.toString();
}

export function allowedRemoteOrigins(primaryOrigin, authenticationOrigins = []) {
  const primary = parseHttpsUrl(primaryOrigin, "TMCRA origin");
  const origins = new Set([normalizeHttpsOrigin(`${primary.origin}/`, "TMCRA origin")]);
  for (const origin of authenticationOrigins) {
    origins.add(normalizeHttpsOrigin(origin, "authentication origin"));
  }
  return origins;
}

export function validateRemoteNavigation(value, origins) {
  const url = parseHttpsUrl(value, "remote navigation URL");
  if (!(origins instanceof Set) || !origins.has(url.origin)) {
    throw new TypeError("Remote navigation was blocked because its origin is not allowed.");
  }
  return url.toString();
}

export function isHttpsResource(value) {
  try {
    return parseHttpsUrl(value, "remote resource URL").protocol === HTTPS;
  } catch {
    return false;
  }
}
