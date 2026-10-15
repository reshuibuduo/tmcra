import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { syncBuiltinESMExports } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// Inject the Windows sharing-violation branch in an isolated temporary store.
// This does not claim to emulate the Windows kernel or a native filesystem.
const root = process.env.TMCRA_ATOMIC_TEST_ROOT || resolve(dirname(fileURLToPath(import.meta.url)), '..');
const temp = await fs.mkdtemp(join(tmpdir(), 'tmcra-atomic-control-'));
process.env.TMCRA_MEMORY_STATE_DIR = temp;
const controls = await import(pathToFileURL(join(root, 'scripts/memory_controls.mjs')));
const realRename = fs.rename;
const originalPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
const key = 'c'.repeat(64);
const target = join(temp, `${key}.json`);
const lockPath = join(temp, `${key}.lock`);
const original = JSON.stringify({ schemaVersion: 1, sessions: {}, tasks: {}, recent: [], budgetChars: 12000 });
const results = [];
async function reset() { await fs.writeFile(target, original); }
function inject(platform, code, failures) {
  Object.defineProperty(process, 'platform', { ...originalPlatform, value: platform });
  let attempts = 0;
  fs.rename = async (source, destination) => {
    if (destination !== target) return realRename(source, destination);
    attempts++;
    if (attempts <= failures) {
      // A retry must retain both the old readable target and the owner lock.
      assert.equal(await fs.readFile(target, 'utf8'), original);
      await fs.stat(lockPath);
      assert.equal(JSON.parse(await fs.readFile(source, 'utf8')).schemaVersion, 1);
      const error = new Error(`injected ${code} replacing the memory-control fixture`);
      error.code = code;
      throw error;
    }
    return realRename(source, destination);
  };
  syncBuiltinESMExports();
  return () => attempts;
}
function restore() {
  fs.rename = realRename;
  syncBuiltinESMExports();
  Object.defineProperty(process, 'platform', originalPlatform);
}
async function test(name, fn) {
  const start = Date.now();
  await reset();
  try { await fn(); results.push({ name, ok: true, elapsedMs: Date.now() - start }); }
  catch (error) { results.push({ name, ok: false, error: error.message, elapsedMs: Date.now() - start }); }
  finally { restore(); }
  assert.deepEqual((await fs.readdir(temp)).sort(), [`${key}.json`], 'temporary state and owner locks must be cleaned up');
}
try {
  for (const code of ['EPERM', 'EACCES', 'EBUSY']) {
    await test(`Windows ${code} transient replacement retries without deleting old state`, async () => {
      const attempts = inject('win32', code, 2);
      await controls.setMemoryMode(key, 'session-a', 'recall_only');
      assert.equal(attempts(), 3);
      assert.equal((await controls.memoryPolicy(key, 'session-a')).mode, 'recall_only');
    });
  }
  await test('Windows permanent EPERM remains bounded and preserves old state', async () => {
    const attempts = inject('win32', 'EPERM', Infinity);
    const start = Date.now();
    await assert.rejects(controls.setMemoryMode(key, 'session-a', 'off'), { code: 'EPERM' });
    assert(attempts() > 1, 'Windows replacement should retry before failing');
    assert(Date.now() - start < 3000, 'permanent error must not wait indefinitely');
    assert.equal(await fs.readFile(target, 'utf8'), original);
  });
  await test('non-sharing errors are not retried or hidden', async () => {
    const attempts = inject('win32', 'ENOSPC', Infinity);
    await assert.rejects(controls.setMemoryMode(key, 'session-a', 'off'), { code: 'ENOSPC' });
    assert.equal(attempts(), 1);
    assert.equal(await fs.readFile(target, 'utf8'), original);
  });
  await test('POSIX permission failures are not reinterpreted as Windows sharing violations', async () => {
    const attempts = inject('linux', 'EPERM', Infinity);
    await assert.rejects(controls.setMemoryMode(key, 'session-a', 'off'), { code: 'EPERM' });
    assert.equal(attempts(), 1);
    assert.equal(await fs.readFile(target, 'utf8'), original);
  });
  await test('concurrent control edits retain both sessions through transient replacement', async () => {
    const attempts = inject('win32', 'EPERM', 2);
    const settled = await Promise.allSettled([
      controls.setMemoryMode(key, 'session-a', 'off'),
      controls.setMemoryMode(key, 'session-b', 'recall_only'),
    ]);
    assert(settled.every(row => row.status === 'fulfilled'), JSON.stringify(settled.map(row => row.reason?.code || row.status)));
    assert.equal(attempts(), 4);
    assert.equal((await controls.memoryPolicy(key, 'session-a')).mode, 'off');
    assert.equal((await controls.memoryPolicy(key, 'session-b')).mode, 'recall_only');
  });
} finally { restore(); await fs.rm(temp, { recursive: true, force: true }); }
const report = { ok: results.every(row => row.ok), injectedFilesystemErrors: true, nativeWindowsExecution: originalPlatform.value === 'win32', tests: results };
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
if (!report.ok) process.exitCode = 1;
