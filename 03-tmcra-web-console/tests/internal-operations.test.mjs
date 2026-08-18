import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { collectInternalOperations } from "../app/api/internal/operations.ts";

const readSource = (path) => readFile(new URL(path, import.meta.url), "utf8");

function jsonResponse(payload, { status = 200, serviceLatency = null } = {}) {
  const headers = new Headers({ "content-type": "application/json" });
  if (serviceLatency !== null) {
    headers.set("x-tmcra-latency-ms", String(serviceLatency));
  }
  return new Response(JSON.stringify(payload), { status, headers });
}

test("internal operations collector returns only verified live probe fields", async () => {
  let clock = 0;
  const requested = [];
  const snapshot = await collectInternalOperations({
    memoryApiBaseUrl: "https://api.tmcra.com",
    requestUrl: "https://tmcra.com/api/internal",
    now: () => new Date("2026-07-18T12:00:00.000Z"),
    monotonicNow: () => (clock += 5),
    fetchImpl: async (input) => {
      const url = new URL(input);
      requested.push(url.href);
      if (url.pathname === "/healthz") {
        return jsonResponse(
          {
            status: "ok",
            service: "tmcra-memory",
            version: "0.2.0",
            api_key: "must-not-leak",
          },
          { serviceLatency: 0.87 },
        );
      }
      if (url.pathname === "/readyz") {
        return jsonResponse(
          {
            status: "ready",
            service: "tmcra-memory",
            version: "0.2.0",
            checks: {
              control_db: true,
              gpu: true,
              active_indexes: true,
            },
            snapshot_stale: false,
            snapshot_age_seconds: 4.277,
            monitor_generation: 640,
            provider_token: "must-not-leak-either",
          },
          { serviceLatency: 0.86 },
        );
      }
      if (url.pathname === "/__deployment/health") {
        return jsonResponse({
          ok: true,
          service: "tmcra-commercial-site",
          release: "20260718T090154Z-console-hub",
          upstreamStatus: 200,
          secret: "not-an-operational-field",
        });
      }
      throw new Error(`unexpected probe ${url.href}`);
    },
  });

  assert.deepEqual(requested.sort(), [
    "https://api.tmcra.com/healthz",
    "https://api.tmcra.com/readyz",
    "https://tmcra.com/__deployment/health",
  ]);
  assert.equal(snapshot.collectedAt, "2026-07-18T12:00:00.000Z");
  assert.equal(snapshot.health.status, "healthy");
  assert.equal(snapshot.health.version, "0.2.0");
  assert.equal(snapshot.readiness.status, "ready");
  assert.deepEqual(snapshot.readiness.checks, {
    control_db: true,
    gpu: true,
    active_indexes: true,
  });
  assert.equal(snapshot.deployment.status, "healthy");
  assert.equal(snapshot.deployment.release, "20260718T090154Z-console-hub");
  assert.equal(snapshot.latency.availability, "partial");
  assert.equal(snapshot.latency.healthServiceMs, 0.87);
  assert.equal(snapshot.latency.readinessServiceMs, 0.86);
  assert.equal(snapshot.release.availability, "partial");

  for (const unavailable of [
    snapshot.startupPreflight,
    snapshot.queue,
    snapshot.costs,
  ]) {
    assert.equal(unavailable.availability, "unavailable");
    assert.ok(unavailable.reason.length > 20);
  }
  assert.equal(snapshot.queue.failed, null);
  assert.equal(snapshot.costs.knownCostMicroCny, null);
  assert.equal(snapshot.latency.p95Ms, null);
  assert.equal(snapshot.release.canaryPercent, null);

  const serialized = JSON.stringify(snapshot);
  assert.doesNotMatch(serialized, /must-not-leak/);
  assert.doesNotMatch(serialized, /provider_token|api_key|secret/);
});

test("invalid probe origins stay unavailable and are never represented as zero", async () => {
  let calls = 0;
  const snapshot = await collectInternalOperations({
    memoryApiBaseUrl: "http://api.tmcra.com?token=secret",
    requestUrl: "https://attacker.example/api/internal",
    fetchImpl: async () => {
      calls += 1;
      throw new Error("must not be called");
    },
  });

  assert.equal(calls, 0);
  assert.equal(snapshot.health.status, "unavailable");
  assert.match(snapshot.health.reason, /not an approved HTTPS or loopback origin/);
  assert.equal(snapshot.readiness.status, "unavailable");
  assert.equal(snapshot.deployment.status, "unavailable");
  assert.match(snapshot.deployment.reason, /not approved/);
  assert.equal(snapshot.latency.availability, "unavailable");
  assert.equal(snapshot.latency.healthProbeMs, null);
  assert.equal(snapshot.latency.readinessProbeMs, null);
  assert.equal(snapshot.latency.deploymentProbeMs, null);
});

test("malformed responses retain a concrete unavailable reason", async () => {
  let clock = 0;
  const snapshot = await collectInternalOperations({
    memoryApiBaseUrl: "https://api.tmcra.com",
    requestUrl: "https://www.tmcra.com/api/internal",
    monotonicNow: () => (clock += 2),
    fetchImpl: async () => new Response("not-json", { status: 502 }),
  });

  assert.equal(snapshot.health.status, "unavailable");
  assert.match(snapshot.health.reason, /without valid JSON/);
  assert.equal(snapshot.readiness.status, "unavailable");
  assert.equal(snapshot.deployment.status, "unavailable");
  assert.equal(snapshot.latency.availability, "partial");
  assert.match(snapshot.latency.reason, /failed or timed-out probe durations/);
});

test("internal RBAC precedes probes and both gateway layers keep IP restrictions in force", async () => {
  const [route, proxy, layout, nginx, deploymentEnvironment] = await Promise.all([
    readSource("../app/api/internal/route.ts"),
    readSource("../deploy/gpuhome/proxy.py"),
    readSource("../app/internal/layout.tsx"),
    readSource("../deploy/vm/nginx-tmcra.conf"),
    readSource("../deploy/gpuhome/deployment.env.example"),
  ]);

  const authorization = route.indexOf("await getInternalSnapshot(");
  const probe = route.indexOf("await collectInternalOperations(");
  assert.ok(authorization >= 0 && probe > authorization);
  assert.match(route, /await requireIdentity\(\)/);
  assert.match(route, /Cache-Control[\s\S]*private, no-store/);
  assert.match(route, /X-Robots-Tag[\s\S]*noindex, nofollow, noarchive/);
  assert.match(proxy, /INTERNAL_PREFIXES\s*=\s*\("\/internal", "\/api\/internal"\)/);
  assert.match(proxy, /TMCRA_INTERNAL_ALLOWED_IPS must contain exact host routes only/);
  assert.match(proxy, /if not self\._internal_ip_allowed\(client_ip\):/);
  assert.match(layout, /robots:\s*\{\s*index:\s*false,\s*follow:\s*false/);
  assert.match(nginx, /location = \/internal \{ return 404; \}/);
  assert.match(nginx, /location \^~ \/api\/internal\/ \{ return 404; \}/);
  assert.match(nginx, /listen 9443 ssl http2;/);
  assert.match(nginx, /include \/etc\/nginx\/tmcra-internal-allowlist\.conf;/);
  assert.match(nginx, /proxy_set_header Host \$http_host;/);
  assert.match(deploymentEnvironment, /tmcra\.com:9443/);
  assert.match(deploymentEnvironment, /TMCRA_MEMORY_API_STAFF_MONITORING_KEY=/);
});

test("staff release policy is an audited desired-state form, not a deployment trigger", async () => {
  const client = await readSource("../app/internal/InternalClient.tsx");

  assert.match(client, /function ReleasePolicyForm\(/);
  assert.match(client, /"release_policy\.update"/);
  assert.match(client, /targetReleaseId:\s*targetReleaseId\.trim\(\)/);
  assert.match(client, /rollbackReleaseId:\s*rollbackReleaseId\.trim\(\) \|\| null/);
  assert.match(client, /channel,/);
  assert.match(client, /canaryPercent:\s*Number\(canaryPercent\)/);
  assert.match(client, /reason:\s*reason\.trim\(\)/);
  assert.match(client, /expectedVersion:\s*policy\.version/);
  assert.match(client, /does not deploy, restart, or shift production traffic/);
  assert.match(client, /channel === "stable"[\s\S]*Number\(canaryPercent\) === 100/);
  assert.match(client, /reason\.trim\(\)\.length < 10/);
});
