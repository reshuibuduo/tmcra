import vinext from "vinext";
import { defineConfig } from "vite";
import { sites } from "@openai/sites-vite-plugin";
import hostingConfig from "./.openai/hosting.json";

const SITE_CREATOR_PLACEHOLDER_DATABASE_ID =
  "00000000-0000-4000-8000-000000000000";

const WORKER_RUNTIME_VAR_NAMES = [
  "TMCRA_MEMORY_API_BASE_URL",
  "TMCRA_MEMORY_API_CONTROL_BASE_URL",
  "TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK",
  "TMCRA_MEMORY_API_CONTROL_KEY",
  "TMCRA_MEMORY_API_STAFF_MONITORING_KEY",
  "TMCRA_MEMORY_API_TENANT_BINDINGS",
  "TMCRA_INTERNAL_BOOTSTRAP_OWNER_EMAIL",
  "TMCRA_DEVICE_TOKEN_ENCRYPTION_KEY",
  "TMCRA_DEVICE_FLOW_HASH_KEY",
  "TMCRA_DEVICE_MAINTENANCE_SECRET",
] as const;

const { d1, r2 } = hostingConfig;
const loopbackApiBindings = localLoopbackApiBindings();
const localPersistStatePath = process.env.TMCRA_VITE_PERSIST_STATE_PATH?.trim();

// macOS Seatbelt blocks FSEvents, so Codex previews need polling for HMR.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === "seatbelt";

const localBindingConfig = {
  main: "./worker/index.ts",
  compatibility_flags: ["nodejs_compat"],
  d1_databases: d1
    ? [
        {
          binding: d1,
          database_name: "site-creator-d1",
          database_id: SITE_CREATOR_PLACEHOLDER_DATABASE_ID,
        },
      ]
    : [],
  r2_buckets: r2
    ? [
        {
          binding: r2,
          bucket_name: "site-creator-r2",
        },
      ]
    : [],
  ...(loopbackApiBindings.length
    ? { unsafe: { bindings: loopbackApiBindings } }
    : {}),
};

function localLoopbackApiBindings() {
  if (process.env.TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK !== "1") return [];
  let controlUrl: URL;
  try {
    controlUrl = new URL(String(process.env.TMCRA_MEMORY_API_CONTROL_BASE_URL ?? "").trim());
  } catch {
    throw new Error(
      "GPUHome loopback control requires TMCRA_MEMORY_API_CONTROL_BASE_URL=http://127.0.0.1:<port>.",
    );
  }
  if (
    controlUrl.protocol !== "http:" ||
    controlUrl.hostname !== "127.0.0.1" ||
    controlUrl.username ||
    controlUrl.password ||
    !controlUrl.port
  ) {
    throw new Error(
      "GPUHome loopback control requires TMCRA_MEMORY_API_CONTROL_BASE_URL=http://127.0.0.1:<port>.",
    );
  }
  const address = `127.0.0.1:${controlUrl.port}`;
  return [
    {
      name: "TMCRA_MEMORY_API_CONTROL_FETCHER",
      type: "tmcra_loopback_api",
      address,
      dev: {
        plugin: {
          package: "@tmcra/miniflare-loopback-api-plugin",
          name: "TMCRA_LOOPBACK_API",
        },
        options: { address },
      },
    },
  ];
}

export default defineConfig(async () => {
  // Keep Wrangler and Miniflare state project-local. These are non-secret tool
  // settings; application environment belongs in ignored `.env*` files.
  process.env.WRANGLER_WRITE_LOGS ??= "false";
  process.env.WRANGLER_LOG_PATH ??= ".wrangler/logs";
  process.env.MINIFLARE_REGISTRY_PATH ??= ".wrangler/registry";

  // Wrangler snapshots its log path while the Cloudflare plugin is imported.
  const { cloudflare } = await import("@cloudflare/vite-plugin");

  return {
    server: {
      watch: {
        ignored: ["**/dist/**", "**/.vinext/**"],
        ...(isCodexSeatbeltSandbox ? { useFsEvents: false, usePolling: true } : {}),
      },
    },
    preview: {
      allowedHosts: [
        "euvbyqa1jpvdm7yq-2000.sc01-webservice.gpuhome.cc",
        "tmcra.com",
        "www.tmcra.com",
      ],
    },
    plugins: [
      vinext(),
      sites(),
      cloudflare({
        viteEnvironment: { name: "rsc", childEnvironments: ["ssr"] },
        // Active security tests use a fresh, disposable D1 database so a
        // previous developer session cannot influence owner bootstrap or RBAC.
        persistState: localPersistStatePath
          ? { path: localPersistStatePath }
          : true,
        // Keep values out of build artifacts. The generated Wrangler config
        // declares the allowlist, then `vite preview` resolves each binding
        // from the GPUHome supervisor's protected process environment.
        config: {
          ...localBindingConfig,
          secrets: { required: [...WORKER_RUNTIME_VAR_NAMES] },
        },
      }),
    ],
  };
});
