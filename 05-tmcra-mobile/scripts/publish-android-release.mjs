import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { copyFile, mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const siteRoot = resolve(projectRoot, "..", "..");
const downloadsRoot = join(siteRoot, "public", "downloads");
const releaseRoot = resolve(
  process.env.TMCRA_MOBILE_RELEASE_DIR || join(siteRoot, ".release-assets"),
);
const packageJson = JSON.parse(await readFile(join(projectRoot, "package.json"), "utf8"));
const version = String(packageJson.version || "");
if (!/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/u.test(version)) {
  throw new Error("Mobile package version is invalid.");
}

const apkSource = join(projectRoot, "android", "app", "build", "outputs", "apk", "release", "app-release.apk");
const bundleSource = join(projectRoot, "android", "app", "build", "outputs", "bundle", "release", "app-release.aab");
const versionedApk = `TMCRA-Memory-Mobile-${version}.apk`;
const versionedBundle = `TMCRA-Memory-Mobile-${version}.aab`;
const latestName = "TMCRA-Memory-Mobile-latest.apk";
const androidRoot = join(releaseRoot, "mobile", "android");

async function sha256(path) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest("hex");
}

async function describe(source, name, contentType) {
  const [metadata, digest] = await Promise.all([stat(source), sha256(source)]);
  await copyFile(source, join(androidRoot, name));
  return {
    name,
    path: `/downloads/mobile/android/${name}`,
    bytes: metadata.size,
    sha256: digest,
    contentType,
  };
}

await mkdir(androidRoot, { recursive: true });
const apk = await describe(
  apkSource,
  versionedApk,
  "application/vnd.android.package-archive",
);
const bundle = await describe(
  bundleSource,
  versionedBundle,
  "application/octet-stream",
);
await copyFile(apkSource, join(releaseRoot, latestName));
await writeFile(
  join(downloadsRoot, `${latestName}.sha256`),
  `${apk.sha256}  ${latestName}\n`,
  "utf8",
);
await writeFile(
  join(downloadsRoot, "tmcra-memory-mobile-android-release.json"),
  `${JSON.stringify({
    schemaVersion: 1,
    product: "TMCRA Memory",
    platform: "android",
    architecture: "universal",
    version,
    channel: "preview",
    minimumSdk: 24,
    targetSdk: 36,
    installer: {
      latestPath: `/downloads/${latestName}`,
      bytes: apk.bytes,
      sha256: apk.sha256,
      releaseSigned: true,
    },
    artifacts: [apk, bundle],
    generatedAtUtc: new Date().toISOString(),
  }, null, 2)}\n`,
  "utf8",
);

process.stdout.write(
  `Published Android ${versionedApk} and ${versionedBundle}\nSHA-256 ${apk.sha256}\n`,
);
