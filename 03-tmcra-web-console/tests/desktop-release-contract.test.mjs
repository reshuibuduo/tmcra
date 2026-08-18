import assert from "node:assert/strict";
import { createReadStream } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { createHash } from "node:crypto";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const downloads = resolve(root, "public", "downloads");
const releaseAssets = resolve(
  process.env.TMCRA_DESKTOP_RELEASE_DIR || resolve(root, ".release-assets"),
);
async function sha256(path) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest("hex");
}

test("Windows desktop download, checksum, and release manifest describe one artifact", async () => {
  const manifest = JSON.parse(
    await readFile(resolve(downloads, "tmcra-memory-desktop-release.json"), "utf8"),
  );
  const latestName = "TMCRA-Memory-Setup-latest.exe";
  const latest = resolve(releaseAssets, latestName);

  assert.equal(manifest.schemaVersion, 1);
  assert.equal(manifest.platform, "windows");
  assert.equal(manifest.architecture, "x64");
  assert.match(manifest.version, /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/u);
  assert.equal(manifest.channel, "preview");
  assert.equal(manifest.installer.authenticodeSigned, false);
  assert.equal(
    (await readFile(resolve(downloads, `${latestName}.sha256`), "utf8")).trim(),
    `${manifest.installer.sha256}  ${latestName}`,
  );
  await assert.rejects(stat(resolve(downloads, latestName)), { code: "ENOENT" });

  try {
    const [latestStat, latestHash] = await Promise.all([stat(latest), sha256(latest)]);
    assert.equal(latestStat.size, manifest.installer.bytes);
    assert.equal(latestHash, manifest.installer.sha256);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }

  if (manifest.updater) {
    assert.equal(manifest.updater.feedPath, "/downloads/desktop/windows/x64");
    assert.deepEqual(
      manifest.updater.artifacts.map((artifact) => artifact.name),
      [
        "latest.yml",
        `TMCRA-Memory-Setup-${manifest.version}-x64.exe`,
        `TMCRA-Memory-Setup-${manifest.version}-x64.exe.blockmap`,
      ],
    );
  }
});

for (const architecture of ["x64", "arm64"]) {
  test(`macOS ${architecture} preview metadata describes one architecture-bound release`, async () => {
    const manifest = JSON.parse(
      await readFile(
        resolve(downloads, `tmcra-memory-desktop-macos-${architecture}-release.json`),
        "utf8",
      ),
    );
    const latestName = `TMCRA-Memory-latest-${architecture}.dmg`;
    const latest = resolve(releaseAssets, latestName);

    assert.equal(manifest.schemaVersion, 1);
    assert.equal(manifest.platform, "macos");
    assert.equal(manifest.architecture, architecture);
    assert.match(manifest.version, /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/u);
    assert.equal(manifest.channel, "preview");
    assert.equal(manifest.installer.latestPath, `/downloads/${latestName}`);
    assert.equal(manifest.installer.codeSigned, false);
    assert.equal(manifest.installer.notarized, false);
    assert.equal(manifest.updater.enabled, false);
    assert.equal(manifest.updater.feedPath, `/downloads/desktop/macos/${architecture}`);
    assert.deepEqual(
      manifest.updater.artifacts.map((artifact) => artifact.name),
      [
        "latest-mac.yml",
        `TMCRA-Memory-${manifest.version}-${architecture}.dmg`,
        `TMCRA-Memory-${manifest.version}-${architecture}.zip`,
      ],
    );
    assert.equal(
      (await readFile(resolve(downloads, `${latestName}.sha256`), "utf8")).trim(),
      `${manifest.installer.sha256}  ${latestName}`,
    );
    await assert.rejects(stat(resolve(downloads, latestName)), { code: "ENOENT" });

    try {
      const [latestStat, latestHash] = await Promise.all([stat(latest), sha256(latest)]);
      assert.equal(latestStat.size, manifest.installer.bytes);
      assert.equal(latestHash, manifest.installer.sha256);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
  });
}

test("download page exposes verified desktop and Android preview installers", async () => {
  const page = await readFile(resolve(root, "app", "download", "page.tsx"), "utf8");

  assert.match(page, /TMCRA-Memory-Setup-latest\.exe/u);
  assert.match(page, /TMCRA-Memory-latest-arm64\.dmg/u);
  assert.match(page, /TMCRA-Memory-latest-x64\.dmg/u);
  assert.match(page, /TMCRA-Memory-Mobile-latest\.apk/u);
  assert.match(page, /not yet backed by commercial code-signing certificates/u);
  assert.match(page, /requires Apple Developer signing/u);
  assert.match(page, /Privacy & Security/u);
  assert.match(page, /Open Anyway/u);
  assert.match(page, /shasum -a 256/u);
  assert.match(page, /about an hour/u);
  assert.match(page, /support\.apple\.com\/guide\/mac-help\/mh40617\/mac/u);
  assert.doesNotMatch(page, /spctl --master-disable/u);
});

test("Codex integration page keeps the actor model and links to desktop downloads", async () => {
  const page = await readFile(resolve(root, "app", "developers", "codex", "page.tsx"), "utf8");

  assert.match(page, /Download the Windows or macOS preview/u);
  assert.match(page, /href="\/download"/u);
  assert.match(page, /tmcra-codex-latest\.zip/u);
  assert.match(page, /Both remain recallable/);
  assert.match(page, /用户记录承载要求与事实，Codex 记录承载已经完成的进度与结果/);
});
