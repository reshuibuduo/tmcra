import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import test from "node:test";

import { Miniflare } from "miniflare";
import { plugins } from "@tmcra/miniflare-loopback-api-plugin";

test("GPUHome loopback binding reaches only the configured local API service", async () => {
  const server = http.createServer((request, response) => {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ path: request.url, host: request.headers.host }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert(address && typeof address === "object");
  const target = `127.0.0.1:${address.port}`;

  const miniflare = new Miniflare({
    modules: true,
    script: `export default { async fetch(_request, env) {
      return env.TMCRA_MEMORY_API_CONTROL_FETCHER.fetch("http://${target}/v1/access-tokens");
    } }`,
    compatibilityDate: "2026-07-16",
    unsafeBindings: [loopbackBinding(target)],
  });

  try {
    const response = await miniflare.dispatchFetch("http://tmcra.test/");
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), {
      path: "/v1/access-tokens",
      host: target,
    });
  } finally {
    await miniflare.dispose();
    await new Promise((resolve) => server.close(resolve));
  }
});

test("GPUHome loopback binding rejects non-loopback targets", async () => {
  assert.throws(
    () => plugins.TMCRA_LOOPBACK_API.getServices({
      options: [{
        name: "TMCRA_MEMORY_API_CONTROL_FETCHER",
        type: "tmcra_loopback_api",
        options: { address: "203.0.113.10:2009" },
      }],
      workerIndex: 0,
    }),
    /must target a literal 127\.0\.0\.1 TCP port/,
  );
});

test("loopback API calls have a same-service HTTPS fallback without weakening loopback validation", () => {
  const source = fs.readFileSync(
    new URL("../app/lib/memory-api-fetch.ts", import.meta.url),
    "utf8",
  );
  assert.match(source, /TMCRA_MEMORY_API_BASE_URL/);
  assert.match(source, /base\.protocol !== "https:"/);
  assert.match(source, /TMCRA loopback API fetch failed; using the HTTPS API fallback/);
  assert.match(source, /requestSignal\(input, init\)\?\.aborted/);
  assert.match(source, /redirect: "manual" as const/);
  assert.match(source, /response\.status >= 300 && response\.status < 400/);
});

test("GPUHome release verifies the relocatable plugin after moving into place", () => {
  const deploymentScript = fs.readFileSync(
    new URL("../deploy/gpuhome/deploy_release.sh", import.meta.url),
    "utf8",
  );
  assert.match(
    deploymentScript,
    /mv "\$temp_release" "\$release"[\s\S]+await import\("@tmcra\/miniflare-loopback-api-plugin"\)/,
  );
});

test("GPUHome release rebinds desktop verification assets after moving into place", () => {
  const deploymentScript = fs.readFileSync(
    new URL("../deploy/gpuhome/deploy_release.sh", import.meta.url),
    "utf8",
  );
  assert.match(
    deploymentScript,
    /mv "\$temp_release" "\$release"[\s\S]+installer_manifest="\$release\/public\/downloads\/tmcra-memory-desktop-release\.json"[\s\S]+installer_verifier="\$release\/deploy\/gpuhome\/verify_desktop_release\.py"[\s\S]+verify_desktop_update "\$desktop_update_final"/,
  );
});

test("GPUHome release stops every verified supervisor when the PID file drifts", () => {
  const deploymentScript = fs.readFileSync(
    new URL("../deploy/gpuhome/deploy_release.sh", import.meta.url),
    "utf8",
  );
  assert.match(deploymentScript, /for command_file in \/proc\/\[0-9\]\*\/cmdline/);
  assert.match(
    deploymentScript,
    /\*"\$root\/current\/deploy\/gpuhome\/supervisor\.py"\*\)/,
  );
  assert.match(deploymentScript, /supervisor_pids\+=\("\$candidate_pid"\)/);
  assert.match(deploymentScript, /ps -o pid= --ppid "\$supervisor_pid"/);
});

test("GPUHome release loads host process management and publishes readable updater assets", () => {
  const deploymentScript = fs.readFileSync(
    new URL("../deploy/gpuhome/deploy_release.sh", import.meta.url),
    "utf8",
  );
  assert.match(
    deploymentScript,
    /done <"\$shared\/deployment\.env"[\s\S]+systemd_service="\$\{TMCRA_SYSTEMD_SERVICE:-\}"[\s\S]+invalid systemd service name/,
  );
  assert.match(deploymentScript, /chmod 0755 "\$desktop_update_stage"/);
});

test("VM nginx serves desktop release metadata from shared downloads", () => {
  const nginxConfiguration = fs.readFileSync(
    new URL("../deploy/vm/nginx-tmcra.conf", import.meta.url),
    "utf8",
  );
  assert.match(
    nginxConfiguration,
    /alias \/srv\/tmcra-official\/shared\/downloads\/tmcra-memory-desktop-release\.json;/,
  );
  assert.match(
    nginxConfiguration,
    /alias \/srv\/tmcra-official\/shared\/downloads\/TMCRA-Memory-Setup-latest\.exe\.sha256;/,
  );
  assert.match(
    nginxConfiguration,
    /alias \/srv\/tmcra-official\/shared\/downloads\/macos-current\/\$1;/,
  );
  assert.match(
    nginxConfiguration,
    /alias \/srv\/tmcra-official\/shared\/downloads\/macos-current\/desktop\/macos\/\$1\/\$2;/,
  );
});

test("VM API tunnel reads the current SSH endpoint from host configuration", () => {
  const tunnelUnit = fs.readFileSync(
    new URL("../deploy/vm/tmcra-api-tunnel.service", import.meta.url),
    "utf8",
  );
  assert.match(tunnelUnit, /Environment=TMCRA_API_SSH_PORT=30131/);
  assert.match(
    tunnelUnit,
    /EnvironmentFile=-\/srv\/tmcra-official\/shared\/api-tunnel\.env/,
  );
  assert.match(tunnelUnit, /-p \$\{TMCRA_API_SSH_PORT\}/);
  assert.doesNotMatch(tunnelUnit, /-p 30334/);

  const tunnelEnvironment = fs.readFileSync(
    new URL("../deploy/vm/api-tunnel.env.example", import.meta.url),
    "utf8",
  );
  assert.match(tunnelEnvironment, /^TMCRA_API_SSH_PORT=30131$/mu);
});

test("GPUHome release refuses runtime state bundled inside an archive", () => {
  const deploymentScript = fs.readFileSync(
    new URL("../deploy/gpuhome/deploy_release.sh", import.meta.url),
    "utf8",
  );
  assert.match(deploymentScript, /release archive contains reserved runtime path/);
  assert.match(deploymentScript, /ln -sT "\$shared\/wrangler" "\$temp_release\/\.wrangler"/);
  assert.match(
    deploymentScript,
    /ln -sT "\$shared\/deployment\.env" "\$temp_release\/deploy\/gpuhome\/deployment\.env"/,
  );
});

test("GPUHome release accepts only provenance-bound Linux dependencies", () => {
  const deploymentScript = fs.readFileSync(
    new URL("../deploy/gpuhome/deploy_release.sh", import.meta.url),
    "utf8",
  );
  assert.match(deploymentScript, /TMCRA_PREINSTALLED must be 0 or 1/);
  assert.match(deploymentScript, /\.tmcra-preinstalled\.json/);
  assert.match(deploymentScript, /"platform": "linux"/);
  assert.match(deploymentScript, /"architecture": expected_machine/);
  assert.match(deploymentScript, /"nodeMajor": node_major/);
  assert.match(deploymentScript, /"packageLockSha256": sha256\(lock_path\)/);
  assert.match(
    deploymentScript,
    /if \[ "\$preinstalled" -eq 0 \]; then\s+"\$npm" ci --no-audit --no-fund/,
  );
});

function loopbackBinding(address) {
  return {
    name: "TMCRA_MEMORY_API_CONTROL_FETCHER",
    type: "tmcra_loopback_api",
    plugin: {
      package: "@tmcra/miniflare-loopback-api-plugin",
      name: "TMCRA_LOOPBACK_API",
    },
    options: { address },
  };
}
