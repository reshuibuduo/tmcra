import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { resolveProductScriptPath } from "../src/lib/resource-paths.mjs";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const packageJson = JSON.parse(await readFile(join(projectRoot, "package.json"), "utf8"));
const archiveName = packageJson.tmcra?.pluginArchive;
const manifestName = packageJson.tmcra?.pluginReleaseManifest;

if (process.platform !== "win32") {
  throw new Error("The TMCRA Windows resource verifier must run on Windows.");
}
if (!/^[A-Za-z0-9_.-]+\.zip$/u.test(String(archiveName || ""))) {
  throw new Error("package.json has an invalid TMCRA plugin archive name.");
}
if (!/^[A-Za-z0-9_.-]+\.json$/u.test(String(manifestName || ""))) {
  throw new Error("package.json has an invalid TMCRA release manifest name.");
}

const archivePath = join(projectRoot, "resources", archiveName);
const releasePath = join(projectRoot, "resources", manifestName);
if (!existsSync(archivePath) || !existsSync(releasePath)) {
  throw new Error(
    `Missing desktop resources. Copy ${archiveName} and ${manifestName} into resources/.`,
  );
}

const requiredScriptMappings = [
  "expand-plugin.ps1",
  "find-codex.ps1",
  "verify-codex-plugin.ps1",
];
for (const filename of requiredScriptMappings) {
  const source = `scripts/${filename}`;
  const destination = `desktop-scripts/${filename}`;
  const mapping = packageJson.build?.extraResources?.find(
    (entry) => entry?.from === source && entry?.to === destination,
  );
  if (!mapping || !existsSync(join(projectRoot, "scripts", filename))) {
    throw new Error(`Missing packaged PowerShell resource mapping for ${filename}.`);
  }
  const mappedPath = resolveProductScriptPath(
    {
      isPackaged: true,
      resourcesPath: join(projectRoot, ".packaged-resources-contract"),
      sourceRoot: projectRoot,
    },
    filename,
  );
  if (mappedPath !== join(projectRoot, ".packaged-resources-contract", destination)) {
    throw new Error(`Packaged PowerShell path contract is inconsistent for ${filename}.`);
  }
}

const release = JSON.parse(await readFile(releasePath, "utf8"));
const version = String(release?.plugin?.version || "");
const expectedHash = String(release?.archive?.sha256 || "").toLowerCase();
if (!/^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/u.test(version)) {
  throw new Error("The release manifest has an invalid plugin version.");
}
if (!/^[0-9a-f]{64}$/u.test(expectedHash)) {
  throw new Error("The release manifest does not contain a valid SHA-256.");
}
if (release?.archive?.latest && basename(archivePath) !== release.archive.latest) {
  throw new Error("The release manifest points at a different latest archive.");
}

const digest = createHash("sha256").update(await readFile(archivePath)).digest("hex");
if (digest !== expectedHash) {
  throw new Error("The bundled TMCRA plugin archive does not match its release manifest.");
}

const temporaryRoot = await mkdtemp(join(tmpdir(), "tmcra-desktop-verify-"));
try {
  const extractionPath = join(temporaryRoot, "release");
  const extractionScript = join(projectRoot, "scripts", "expand-plugin.ps1");
  const extracted = spawnSync(
    "powershell.exe",
    [
      "-NoLogo",
      "-NoProfile",
      "-NonInteractive",
      "-ExecutionPolicy",
      "Bypass",
      "-File",
      extractionScript,
      "-ArchivePath",
      archivePath,
      "-DestinationPath",
      extractionPath,
    ],
    { encoding: "utf8", windowsHide: true },
  );
  if (extracted.status !== 0) {
    throw new Error("PowerShell could not validate the TMCRA plugin archive.");
  }

  const pluginManifestPath = join(
    extractionPath,
    "plugins",
    "tmcra-memory",
    ".codex-plugin",
    "plugin.json",
  );
  const installerPath = join(extractionPath, "Install-TMCRA.ps1");
  if (!existsSync(pluginManifestPath) || !existsSync(installerPath)) {
    throw new Error("The plugin archive is missing its Codex manifest or Windows installer.");
  }

  const plugin = JSON.parse(await readFile(pluginManifestPath, "utf8"));
  if (plugin.name !== "tmcra-memory" || plugin.version !== version) {
    throw new Error("The plugin archive version does not match the release manifest.");
  }

  const installer = await readFile(installerPath, "utf8");
  if (!/\[string\]\$NodePath/u.test(installer) || !/\[switch\]\$ProgressJson/u.test(installer)) {
    throw new Error("The plugin installer does not support the desktop runtime contract.");
  }
} finally {
  await rm(temporaryRoot, { recursive: true, force: true });
}

process.stdout.write(`TMCRA desktop resources verified: plugin ${version}, ${digest}\n`);
