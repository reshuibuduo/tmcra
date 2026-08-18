import { copyFile, mkdir, readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const siteRoot = resolve(projectRoot, "..", "..");
const packageJson = JSON.parse(await readFile(join(projectRoot, "package.json"), "utf8"));
const archiveName = String(packageJson.tmcra?.pluginArchive || "");
const manifestName = String(packageJson.tmcra?.pluginReleaseManifest || "");

for (const filename of [archiveName, manifestName]) {
  if (!/^[A-Za-z0-9_.-]+\.(?:zip|json)$/u.test(filename)) {
    throw new Error(`Invalid desktop resource filename: ${filename || "(empty)"}`);
  }
}

const resourcesRoot = join(projectRoot, "resources");
await mkdir(resourcesRoot, { recursive: true });
for (const filename of [archiveName, manifestName]) {
  const source = join(siteRoot, "public", "downloads", filename);
  const destination = join(resourcesRoot, filename);
  await copyFile(source, destination);
  process.stdout.write(`Synced ${filename}\n`);
}
