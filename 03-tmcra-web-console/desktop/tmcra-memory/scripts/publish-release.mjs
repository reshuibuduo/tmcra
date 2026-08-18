import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const siteRoot = resolve(projectRoot, "..", "..");
const downloadsRoot = join(siteRoot, "public", "downloads");
const releaseAssetsRoot = resolve(
  process.env.TMCRA_DESKTOP_RELEASE_DIR || join(siteRoot, ".release-assets"),
);
const packageJson = JSON.parse(await readFile(join(projectRoot, "package.json"), "utf8"));
const version = String(packageJson.version || "");
const architecture = "x64";
const artifactName = `TMCRA-Memory-Setup-${version}-${architecture}.exe`;
const artifactPath = join(projectRoot, "dist", artifactName);
const latestName = "TMCRA-Memory-Setup-latest.exe";
const latestPath = join(releaseAssetsRoot, latestName);
const bytes = (await stat(artifactPath)).size;
const digest = createHash("sha256").update(await readFile(artifactPath)).digest("hex");

const pluginRelease = JSON.parse(
  await readFile(join(downloadsRoot, packageJson.tmcra.pluginReleaseManifest), "utf8"),
);

await mkdir(releaseAssetsRoot, { recursive: true });
await copyFile(artifactPath, latestPath);
await writeFile(
  join(downloadsRoot, `${latestName}.sha256`),
  `${digest}  ${latestName}\n`,
  "utf8",
);
await writeFile(
  join(downloadsRoot, "tmcra-memory-desktop-release.json"),
  `${JSON.stringify({
    schemaVersion: 1,
    product: packageJson.build.productName,
    platform: "windows",
    architecture,
    version,
    channel: "preview",
    installer: {
      latestPath: `/downloads/${latestName}`,
      bytes,
      sha256: digest,
      authenticodeSigned: false,
    },
    bundledCodexPlugin: {
      version: pluginRelease.plugin.version,
      sha256: pluginRelease.archive.sha256,
    },
    generatedAtUtc: new Date().toISOString(),
  }, null, 2)}\n`,
  "utf8",
);

process.stdout.write(`Published ${latestPath}\nSHA-256 ${digest}\n`);
