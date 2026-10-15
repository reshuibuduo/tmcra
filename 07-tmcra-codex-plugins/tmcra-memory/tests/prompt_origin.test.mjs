import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyPromptOrigin, permitsPromptCapture, normalizeQueuedOrigin } from '../scripts/prompt_origin.mjs';

test('role, text templates and actor_type never establish human authorship', () => {
  for (const prompt of ['ordinary synthetic fixture', '<heartbeat>quoted by user</heartbeat>', 'suggested-next-user-message: quoted documentation']) {
    assert.equal(classifyPromptOrigin({ role:'user',actor_type:'human',prompt }).origin_kind,'unknown');
    assert.equal(permitsPromptCapture(classifyPromptOrigin({prompt})),true);
  }
});
test('explicit metadata routes generated/automatic inputs away from capture without inspecting body', () => {
  for(const input of [{origin_kind:'app_generated'},{is_synthetic:true},{is_generated:true},{automation_id:'test-fixture'},{origin_kind:'automation'}]) {
    assert.equal(permitsPromptCapture(classifyPromptOrigin({...input,prompt:'quoted fixture'})),false);
  }
  for(const origin_kind of ['human_authored','human_adopted']) {
    const declared=classifyPromptOrigin({origin_kind,prompt:'suggested-next-user-message: quoted documentation'});
    assert.equal(declared.provenance_strength,'host_declared');
    assert.equal(permitsPromptCapture(declared),true);
  }
  assert.equal(permitsPromptCapture(classifyPromptOrigin({origin_kind:'human_authored',is_automated:true})),false);
});
test('legacy queued identities remain byte-compatible and do not become humans', () => {
  const old={actor_role:'user',actor_type:'human'};
  assert.deepEqual(normalizeQueuedOrigin(old),old);
  assert.equal(classifyPromptOrigin(old).origin_kind,'unknown');
  assert.equal(normalizeQueuedOrigin({origin_kind:'human_authored'},'assistant').origin_kind,'unknown');
  assert.equal(permitsPromptCapture(normalizeQueuedOrigin({origin_kind:'automation'})),false);
});

import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { rememberPrompt } from '../hooks/hook_common.mjs';
import { saveOutboxTurn, submitOutboxTurn } from '../scripts/tmcra_client.mjs';

test('full hook automatic metadata exits before config or network', async () => {
  assert.equal(await rememberPrompt({prompt:'synthetic',origin_kind:'app_generated'}),null);
});
test('full automatic outbox is discarded with metadata receipt and zero dispatch', async () => {
  const root=await mkdtemp(join(tmpdir(),'tmcra-full-origin-'));
  const prior=process.env.PLUGIN_DATA;const fetchBefore=globalThis.fetch;
  process.env.PLUGIN_DATA=root;
  try {
    let calls=0;globalThis.fetch=async()=>{calls++;throw new Error('unexpected dispatch');};
    const entry=await saveOutboxTurn({idempotencyKey:'synthetic-origin-id',projectId:'test',scope:'test',sessionId:'s',
      messages:[{role:'user',content:'synthetic only',metadata:{origin_kind:'automation'}}]});
    const result=await submitOutboxTurn(entry,{});
    assert.equal(result.reason,'automatic_origin_excluded');assert.equal(calls,0);
    const rows=await readdir(join(root,'outbox'));
    assert.equal(rows.filter(x=>x.endsWith('.json')).length,0);
  } finally {globalThis.fetch=fetchBefore;if(prior===undefined)delete process.env.PLUGIN_DATA;else process.env.PLUGIN_DATA=prior;await rm(root,{recursive:true,force:true});}
});
