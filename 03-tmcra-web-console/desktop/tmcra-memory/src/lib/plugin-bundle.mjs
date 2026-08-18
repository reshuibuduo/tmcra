import { createHash, randomUUID } from "node:crypto";
import { createReadStream, existsSync } from "node:fs";
import { mkdir, readFile, rename, rm, stat } from "node:fs/promises";
import { basename, isAbsolute, join, relative, resolve } from "node:path";

const VERSION_PATTERN = /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/u;
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;

export async function sha256File(path) {
  return new Promise((resolvePromise, reject) => {
    const digest = createHash("sha256");
    const stream = createReadStream(path);
    stream.on("error", reject);
    stream.on("data", (chunk) => digest.update(chunk));
    stream.on("end", () => resolvePromise(digest.digest("hex")));
  });
}

export function assertDirectChild(parent, child) {
  const relativePath = relative(resolve(parent), resolve(child));
  if (!relativePath || relativePath.startsWith("..") || isAbsolute(relativePath)) {
    throw new Error("The TMCRA integration destination is outside its managed directory.");
  }
  return resolve(child);
}

export async function preparePluginBundle({
  archivePath,
  releaseManifestPath,
  integrationRoot,
  fallbackVersion,
  runExtraction,
  assertActive = () => {},
}) {
  if (!existsSync(archivePath)) {
    throw setupError("plugin_archive_missing", "The bundled TMCRA plugin archive is missing.");
  }

  let release = null;
  if (existsSync(releaseManifestPath)) {
    try {
      release = JSON.parse(await readFile(releaseManifestPath, "utf8"));
    } catch {
      throw setupError("plugin_manifest_invalid", "The bundled TMCRA release manifest is invalid.");
    }
  }

  const version = String(release?.plugin?.version || fallbackVersion || "");
  if (!VERSION_PATTERN.test(version)) {
    throw setupError("plugin_manifest_invalid", "The bundled TMCRA plugin version is invalid.");
  }
  if (release?.archive?.latest && basename(archivePath) !== release.archive.latest) {
    throw setupError("plugin_manifest_mismatch", "The TMCRA release manifest points to a different archive.");
  }

  const expectedHash = String(release?.archive?.sha256 || "").toLowerCase();
  if (!SHA256_PATTERN.test(expectedHash)) {
    throw setupError("plugin_manifest_invalid", "The bundled TMCRA release manifest has no valid SHA-256.");
  }
  const archiveStat = await stat(archivePath);
  if (Number.isSafeInteger(release?.archive?.bytes) && release.archive.bytes !== archiveStat.size) {
    throw setupError("plugin_archive_mismatch", "The bundled TMCRA plugin size does not match its manifest.");
  }
  if ((await sha256File(archivePath)) !== expectedHash) {
    throw setupError("plugin_archive_mismatch", "The bundled TMCRA plugin failed its integrity check.");
  }

  await mkdir(integrationRoot, { recursive: true });
  const destination = assertDirectChild(integrationRoot, join(integrationRoot, version));
  const staging = assertDirectChild(
    integrationRoot,
    join(integrationRoot, `.staging-${version}-${randomUUID()}`),
  );

  try {
    assertActive();
    await runExtraction(archivePath, staging);
    assertActive();

    const pluginManifestPath = join(
      staging,
      "plugins",
      "tmcra-memory",
      ".codex-plugin",
      "plugin.json",
    );
    const installerPath = join(staging, "Install-TMCRA.ps1");
    if (!existsSync(pluginManifestPath) || !existsSync(installerPath)) {
      throw setupError("plugin_archive_invalid", "The TMCRA plugin package is incomplete.");
    }

    let plugin;
    try {
      plugin = JSON.parse(await readFile(pluginManifestPath, "utf8"));
    } catch {
      throw setupError("plugin_archive_invalid", "The TMCRA plugin manifest cannot be read.");
    }
    if (plugin.name !== "tmcra-memory" || plugin.version !== version) {
      throw setupError("plugin_manifest_mismatch", "The TMCRA plugin version does not match its release manifest.");
    }

    const installer = await readFile(installerPath, "utf8");
    if (!/\[string\]\$NodePath/u.test(installer) || !/\[switch\]\$ProgressJson/u.test(installer)) {
      throw setupError("plugin_installer_incompatible", "The TMCRA plugin installer is not desktop-compatible.");
    }

    assertActive();
    await rm(destination, { recursive: true, force: true });
    await rename(staging, destination);
    return {
      version,
      root: destination,
      installerPath: join(destination, "Install-TMCRA.ps1"),
    };
  } catch (error) {
    await rm(staging, { recursive: true, force: true });
    throw error;
  }
}

function setupError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}
