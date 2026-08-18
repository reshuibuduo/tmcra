const MAX_EMAIL_LENGTH = 254;

/**
 * Parses the single, exact email allowed to initialize the internal control
 * plane. Invalid or missing configuration deliberately resolves to no owner.
 */
export function configuredBootstrapOwnerEmail(value: unknown): string | null {
  if (typeof value !== "string" || value.length === 0 || value !== value.trim()) {
    return null;
  }

  const normalized = value.toLowerCase();
  if (normalized.length > MAX_EMAIL_LENGTH) {
    return null;
  }
  const parts = normalized.split("@");
  if (parts.length !== 2) return null;
  const [local, domain] = parts;
  if (
    local.length === 0 ||
    local.length > 64 ||
    !/^[a-z0-9!#$%&'+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'+/=?^_`{|}~-]+)*$/.test(local)
  ) return null;
  const labels = domain.split(".");
  if (
    labels.length < 2 ||
    labels.some(
      (label) =>
        label.length === 0 ||
        label.length > 63 ||
        !/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(label),
    )
  ) return null;
  return normalized;
}

export function isConfiguredBootstrapOwner(
  configuredValue: unknown,
  normalizedIdentityEmail: string,
): boolean {
  const configuredEmail = configuredBootstrapOwnerEmail(configuredValue);
  return configuredEmail !== null && configuredEmail === normalizedIdentityEmail;
}
