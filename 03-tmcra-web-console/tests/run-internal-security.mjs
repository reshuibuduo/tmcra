import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";

const ROOT = path.resolve(import.meta.dirname, "..");
const OWNER_EMAIL = "seedy@sites.test";
const tempRoot = await mkdtemp(path.join(tmpdir(), "tmcra-security-"));
const port = await reservePort();
const baseUrl = `http://127.0.0.1:${port}`;
const vinextCli = path.join(ROOT, "node_modules", "vinext", "dist", "cli.js");
const server = spawn(
  process.execPath,
  [vinextCli, "dev", "--hostname", "127.0.0.1", "--port", String(port)],
  {
    cwd: ROOT,
    env: {
      ...process.env,
      TMCRA_INTERNAL_BOOTSTRAP_OWNER_EMAIL: OWNER_EMAIL,
      TMCRA_VITE_PERSIST_STATE_PATH: path.join(tempRoot, "state"),
      WRANGLER_LOG_PATH: path.join(tempRoot, "logs"),
      MINIFLARE_REGISTRY_PATH: path.join(tempRoot, "registry"),
      WRANGLER_WRITE_LOGS: "false",
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  },
);

let serverLog = "";
for (const stream of [server.stdout, server.stderr]) {
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => {
    serverLog = `${serverLog}${chunk}`.slice(-16_384);
    process.stderr.write(chunk);
  });
}

let exitCode = 1;
try {
  await waitUntilReady(baseUrl, server);
  exitCode = await runNodeSecurityTest(baseUrl);
} finally {
  await stopProcessTree(server);
  await removeOwnedTempDirectory(tempRoot);
}

process.exitCode = exitCode;

function reservePort() {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.unref();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      if (!address || typeof address === "string") {
        probe.close(() => reject(new Error("Unable to reserve a local TCP port.")));
        return;
      }
      probe.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

async function waitUntilReady(url, child) {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(
        `Local security-test Worker exited with ${child.exitCode}.\n${serverLog}`,
      );
    }
    try {
      const response = await fetch(`${url}/api/internal`, {
        redirect: "manual",
        signal: AbortSignal.timeout(1_000),
      });
      if (response.status === 401) return;
    } catch {
      // The local listener is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Local security-test Worker did not become ready.\n${serverLog}`);
}

async function runNodeSecurityTest(url) {
  const test = spawn(
    process.execPath,
    ["--test", "tests/internal-security-sites.test.mjs"],
    {
      cwd: ROOT,
      env: {
        ...process.env,
        TEST_BASE_URL: url,
        TMCRA_TEST_OWNER_EMAIL: OWNER_EMAIL,
      },
      stdio: "inherit",
      windowsHide: true,
    },
  );
  const [code, signal] = await once(test, "exit");
  if (signal) {
    throw new Error(`Internal security test terminated by ${signal}.`);
  }
  return code ?? 1;
}

async function stopProcessTree(child) {
  if (child.exitCode !== null || !child.pid) return;
  if (process.platform === "win32") {
    const killer = spawn(
      "taskkill.exe",
      ["/PID", String(child.pid), "/T", "/F"],
      { stdio: "ignore", windowsHide: true },
    );
    await once(killer, "exit");
  } else {
    child.kill("SIGTERM");
  }
  if (child.exitCode === null) {
    await Promise.race([
      once(child, "exit"),
      new Promise((resolve) => setTimeout(resolve, 5_000)),
    ]);
  }
  if (child.exitCode === null) child.kill("SIGKILL");
}

async function removeOwnedTempDirectory(directory) {
  const resolved = path.resolve(directory);
  const expectedParent = `${path.resolve(tmpdir())}${path.sep}`;
  if (!resolved.startsWith(expectedParent) || !path.basename(resolved).startsWith("tmcra-security-")) {
    throw new Error(`Refusing to remove unexpected test directory: ${resolved}`);
  }
  await rm(resolved, { recursive: true, force: true, maxRetries: 3 });
}
