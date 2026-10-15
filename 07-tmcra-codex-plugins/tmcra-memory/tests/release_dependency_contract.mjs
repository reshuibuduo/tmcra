/** Check the actual release allowlist's transitive relative ESM imports. No service/model startup. */
import assert from 'node:assert/strict';
import { readFile, mkdtemp, mkdir, copyFile, rm, access } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { dirname, join, resolve, relative, isAbsolute } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const script = await readFile(join(root, 'scripts/build_release.ps1'), 'utf8');
const entries = [...script.matchAll(/Source\s*=\s*["']([^"'$]+)["'];\s*Archive\s*=\s*["']([^"'$]+)["']/gu)]
  .map(m => ({ source: m[1], target: m[2] }));
assert(entries.length > 40, 'release allowlist was not parsed; fail closed');
const bySource = new Map(entries.map(e => [e.source, e]));
assert.equal(bySource.size, entries.length, 'duplicate release source');
const edges = [];
for (const entry of entries.filter(e => /\.[cm]?js$/u.test(e.source))) {
  const source = await readFile(join(root, entry.source), 'utf8');
  // Covers static imports, re-exports and literal dynamic imports. Variable imports
  // remain outside this static check and require named entrypoint/runtime tests.
  const imports = [
    ...source.matchAll(/(?:import|export)\s+(?:[^;]*?\sfrom\s*)?["'](\.{1,2}\/[^"']+)["']/gu),
    ...source.matchAll(/import\s*\(\s*["'](\.{1,2}\/[^"']+)["']\s*\)/gu),
  ].map(m => m[1]);
  for (const specifier of new Set(imports)) {
    const target = relative(root, resolve(root, dirname(entry.source), specifier)).split('\\').join('/');
    assert(!target.startsWith('../') && !isAbsolute(target), `relative dependency escapes plugin: ${entry.source}`);
    assert(bySource.has(target), `release allowlist missing ${target} imported by ${entry.source}`);
    const expected = relative(dirname(entry.target), bySource.get(target).target).split('\\').join('/');
    assert.equal(resolve('/', dirname(entry.target), specifier), resolve('/', bySource.get(target).target),
      `archive layout breaks dependency ${entry.source} -> ${expected}`);
    edges.push([entry.source, target]);
  }
}
assert(edges.length > 20, 'dependency scanner produced suspiciously few edges');
const temporary = await mkdtemp(join(tmpdir(), 'tmcra-release-imports-'));
try {
  let extracted = process.env.TMCRA_TEST_EXTRACTED_RELEASE_ROOT;
  if (!extracted) {
    // Materialize the exact allowlist, never the source checkout's whole directory.
    extracted = temporary;
    for (const entry of entries) {
      const target = join(extracted, entry.target);
      await mkdir(dirname(target), { recursive: true });
      await copyFile(join(root, entry.source), target);
    }
  }
  const plugin = join(extracted, 'plugins/tmcra-memory');
  const modules = ['scripts/prompt_origin.mjs', 'scripts/tmcra_client.mjs', 'scripts/workflow_client.mjs', 'hooks/hook_common.mjs'];
  for (const name of modules) await access(join(plugin, name));
  const code = `globalThis.fetch=()=>{throw new Error('unexpected network in packaged import')};\n` +
    modules.map(name => `await import(${JSON.stringify(pathToFileURL(join(plugin, name)).href)});`).join('\n') +
    `\nconsole.log('PACKAGED_HOOK_AND_CLIENT_IMPORT_OK');`;
  const child = spawnSync(process.execPath, ['--input-type=module', '-e', code], {
    cwd: temporary, encoding: 'utf8', timeout: 15_000,
    env: { ...process.env, HOME: temporary, USERPROFILE: temporary, TMCRA_DATA_DIR: join(temporary, 'data'),
      TMCRA_CONFIG: join(temporary, 'missing-config.json'), TMCRA_API_KEY: '', TMCRA_BASE_URL: '' },
  });
  assert.equal(child.status, 0, child.stderr || child.error?.message || child.stdout);
  assert.equal(child.stdout.trim(), 'PACKAGED_HOOK_AND_CLIENT_IMPORT_OK');
  const hook = spawnSync(process.execPath, [join(plugin, 'hooks/user_prompt_submit.mjs')], {
    cwd: temporary, encoding: 'utf8', timeout: 15_000,
    input: JSON.stringify({ origin_kind: 'automation', prompt: 'packaged synthetic provenance test' }),
    env: { ...process.env, HOME: temporary, USERPROFILE: temporary, PLUGIN_DATA: join(temporary, 'hook-data'),
      TMCRA_API_KEY: '', TMCRA_BASE_URL: '' },
  });
  assert.equal(hook.status, 0, hook.stderr || hook.error?.message);
  assert.deepEqual(JSON.parse(hook.stdout.trim()), { continue: true });
  assert.match(hook.stderr, /automatic_origin_excluded/u);
  assert.doesNotMatch(hook.stderr, /failed.open|ERR_MODULE_NOT_FOUND/iu);
  console.log(JSON.stringify({ ok: true, releaseEntries: entries.length, relativeImportEdges: edges.length,
    packagedModulesImported: modules.length, actualExtractedArchive: Boolean(process.env.TMCRA_TEST_EXTRACTED_RELEASE_ROOT),
    dynamicNonliteralImportsCovered: false, installedHostTested: false }));
} finally { await rm(temporary, { recursive: true, force: true }); }
