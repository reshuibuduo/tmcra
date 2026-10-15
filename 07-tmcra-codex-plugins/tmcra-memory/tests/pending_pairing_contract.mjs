import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// A separate temporary state directory, no server, model or real user memory.
const root = process.env.TMCRA_PAIRING_TEST_ROOT || resolve(dirname(fileURLToPath(import.meta.url)), '..');
const temp = await mkdtemp(join(tmpdir(), 'tmcra-pending-pairing-'));
process.env.PLUGIN_DATA = temp;
process.env.CLAUDE_PLUGIN_DATA = temp;
process.env.TMCRA_CLIENT_PLATFORM = 'claude-code';
const { pairingTurnId, recordFailedTurn } = await import(pathToFileURL(join(root, 'hooks/hook_common.mjs')));
const { savePendingTurn, loadPendingTurn, withTaskStateLock } = await import(pathToFileURL(join(root, 'scripts/tmcra_client.mjs')));
const hash = value => createHash('sha256').update(value).digest('hex');
const pause = ms => new Promise(resolvePromise => setTimeout(resolvePromise, ms));
const results = [];
const selected = process.env.TMCRA_PAIRING_TEST_CASE;
async function test(name, fn) {
  if (selected && !name.includes(selected)) return;
  const start = Date.now();
  try { await fn(); results.push({ name, ok: true, elapsedMs: Date.now() - start }); }
  catch (error) { results.push({ name, ok: false, message: error.message, elapsedMs: Date.now() - start }); }
}
let serial = 0;
async function fixture(ids = [{ prompt_id: 'present' }]) {
  const sessionId = `pairing-fixture-${++serial}`;
  const base = { session_id: sessionId, transcript_path: join(temp, `transcript-${serial}.jsonl`) };
  const entries = [];
  for (const id of ids) {
    const turnId = pairingTurnId({ ...base, ...id });
    await savePendingTurn({ sessionId, turnId, prompt: 'synthetic fixture prompt', transcriptPath: base.transcript_path });
    entries.push({ turnId, status: 'pending', transcriptPath: base.transcript_path });
  }
  const indexPath = join(temp, 'pending-index', `${hash(sessionId)}.json`);
  await mkdir(dirname(indexPath), { recursive: true });
  await writeFile(indexPath, JSON.stringify({ schemaVersion: 1, entries }));
  return { base, entries, indexPath };
}
try {
  for (const field of ['prompt_id', 'message_id', 'tool_use_id', 'event_id']) {
    await test(`unmatched ${field} cannot consume another pending turn`, async () => {
      const f = await fixture([{ [field]: 'present' }]);
      const before = await readFile(f.indexPath, 'utf8');
      assert.equal(await recordFailedTurn({ ...f.base, [field]: 'missing', error: 'fixture error' }), null);
      assert.equal(await readFile(f.indexPath, 'utf8'), before);
      assert.equal((await loadPendingTurn(f.base.session_id, f.entries[0].turnId)).status, undefined);
    });
  }
  await test('stable event cannot consume a different transcript pending turn', async () => {
    const f = await fixture();
    assert.equal(await recordFailedTurn({ ...f.base, prompt_id: 'present', transcript_path: `${f.base.transcript_path}.other` }), null);
    assert.equal((await loadPendingTurn(f.base.session_id, f.entries[0].turnId)).status, undefined);
  });
  await test('exact stable event matches its pending turn', async () => {
    const f = await fixture();
    const result = await recordFailedTurn({ ...f.base, prompt_id: 'present', error: 'fixture error' });
    assert.equal(result.turnId, f.entries[0].turnId);
    assert.equal((await loadPendingTurn(f.base.session_id, result.turnId)).status, 'failed');
  });
  await test('legacy no-ID single pending turn remains supported', async () => {
    const f = await fixture();
    assert.equal((await recordFailedTurn(f.base)).turnId, f.entries[0].turnId);
  });
  await test('legacy no-ID ambiguous pending turns remain rejected', async () => {
    const f = await fixture([{ prompt_id: 'one' }, { prompt_id: 'two' }]);
    assert.equal(await recordFailedTurn(f.base), null);
  });
  await test('750ms task-lock contention preserves serialized callbacks', async () => {
    const sessionId = `lock-fixture-${++serial}`;
    let releaseEntered;
    const entered = new Promise(resolvePromise => { releaseEntered = resolvePromise; });
    let firstCompleted = false;
    const first = withTaskStateLock(sessionId, async () => { releaseEntered(); await pause(750); firstCompleted = true; });
    await entered;
    let error;
    try { await withTaskStateLock(sessionId, async () => assert.equal(firstCompleted, true)); } catch (caught) { error = caught; }
    await first;
    if (error) throw error;
  });
  await test('fresh held lock times out without takeover and later recovers', async () => {
    const sessionId = `lock-fixture-${++serial}`;
    const lockPath = join(temp, 'task-locks', `${hash(sessionId)}.lock`);
    await mkdir(dirname(lockPath), { recursive: true });
    await writeFile(lockPath, 'existing owner');
    let called = false;
    const start = Date.now();
    await assert.rejects(withTaskStateLock(sessionId, () => { called = true; }), /task continuity state is busy/u);
    assert(Date.now() - start < 4000, 'lock wait must be bounded');
    assert.equal(called, false);
    assert.equal(await readFile(lockPath, 'utf8'), 'existing owner');
    await rm(lockPath);
    await withTaskStateLock(sessionId, () => { called = true; });
    assert.equal(called, true);
    await assert.rejects(stat(lockPath), { code: 'ENOENT' });
  });
  await test('callback failure releases only its acquired task lock', async () => {
    const sessionId = `lock-fixture-${++serial}`;
    await assert.rejects(withTaskStateLock(sessionId, () => { throw new Error('fixture callback'); }), /fixture callback/u);
    assert.equal(await withTaskStateLock(sessionId, () => 'recovered'), 'recovered');
  });
} finally { await rm(temp, { recursive: true, force: true }); }
const report = { ok: results.every(row => row.ok), tests: results };
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
if (!report.ok) process.exitCode = 1;
