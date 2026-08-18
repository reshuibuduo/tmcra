declare namespace Cloudflare {
  interface Env {
    DB: D1Database;
    TMCRA_MEMORY_API_BASE_URL?: string;
    /** Optional server-to-server endpoint; HTTP is accepted only for explicit loopback mode. */
    TMCRA_MEMORY_API_CONTROL_BASE_URL?: string;
    TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK?: string;
    /** GPUHome-only local service binding. Never configure this on Sites. */
    TMCRA_MEMORY_API_CONTROL_FETCHER?: Fetcher;
    TMCRA_MEMORY_API_TENANT_BINDINGS?: string;
    TMCRA_MEMORY_API_PERSONAL_BINDINGS?: string;
    /** Root control credential. Server-side secret; never expose to browser code. */
    TMCRA_MEMORY_API_CONTROL_KEY?: string;
    /** Dedicated read-only staff telemetry credential. Never reuse the root control credential. */
    TMCRA_MEMORY_API_STAFF_MONITORING_KEY?: string;
    /** Base64url-encoded 32-byte AES-GCM key for one-time device token storage. */
    TMCRA_DEVICE_TOKEN_ENCRYPTION_KEY?: string;
    /** Base64url-encoded 32-byte HMAC key for source/account rate-limit fingerprints. */
    TMCRA_DEVICE_FLOW_HASH_KEY?: string;
    /** Server-only bearer credential for the autonomous device cleanup worker. */
    TMCRA_DEVICE_MAINTENANCE_SECRET?: string;
    /** Exact internal email permitted to perform the one-time owner bootstrap. */
    TMCRA_INTERNAL_BOOTSTRAP_OWNER_EMAIL?: string;
  }
}
