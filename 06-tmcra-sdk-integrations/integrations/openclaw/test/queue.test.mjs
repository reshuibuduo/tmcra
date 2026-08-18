import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  DurablePendingQueue,
  QueueRepairRequiredError,
} from "../dist/queue.js";

function input(id = "item-1") {
  return {
    scopeName: "scope-a",
    payload: { session_id: "session-a", messages: [] },
    idempotencyKey: `idempotency-${id}`,
    agentId: "agent-a",
  };
}

async function fileState(path) {
  return JSON.parse(await readFile(path, "utf8"));
}

test("keeps a 202 submission until the job reaches succeeded", async () => {
  const root = await mkdtemp(join(tmpdir(), "tmcra-openclaw-queue-"));
  const path = join(root, "pending.json");
  const queue = new DurablePendingQueue({ path });
  await queue.enqueue(input());

  let pollCalls = 0;
  const result = await queue.drain(
    async () => ({ status: "pending", jobId: "job-success", requestId: "req-submit" }),
    async () => {
      pollCalls += 1;
      return { status: pollCalls === 1 ? "pending" : "succeeded", requestId: "req-poll" };
    },
  );
  assert.equal(result.submitted, 1);
  assert.equal(result.succeeded, 0);
  assert.equal((await fileState(path)).items[0].status, "submitted");
  assert.equal((await fileState(path)).items[0].jobId, "job-success");

  const completed = await queue.drain(
    async () => ({ status: "pending", jobId: "unexpected" }),
    async () => ({ status: "succeeded", requestId: "req-poll-2" }),
    { force: true },
  );
  assert.equal(completed.succeeded, 1);
  const state = await fileState(path);
  assert.equal(state.items.length, 0);
  assert.equal(state.receipts[0].status, "succeeded");
  assert.equal(state.receipts[0].jobId, "job-success");
});

test("preserves a failed job for retry and keeps the same idempotency key", async () => {
  const root = await mkdtemp(join(tmpdir(), "tmcra-openclaw-queue-"));
  const path = join(root, "pending.json");
  const queue = new DurablePendingQueue({ path });
  await queue.enqueue(input());

  const first = await queue.drain(
    async () => ({ status: "pending", jobId: "job-failed" }),
    async () => ({ status: "failed", error: "writer rejected" }),
  );
  assert.equal(first.retried, 1);
  let state = await fileState(path);
  assert.equal(state.items[0].status, "failed");
  assert.equal(state.items[0].lastError, "writer rejected");
  const originalKey = state.items[0].idempotencyKey;

  let retriedKey;
  const second = await queue.drain(
    async (item) => {
      retriedKey = item.idempotencyKey;
      return { status: "pending", jobId: "job-retry" };
    },
    async () => ({ status: "succeeded" }),
    { force: true },
  );
  assert.equal(second.succeeded, 1);
  assert.equal(retriedKey, originalKey);
  state = await fileState(path);
  assert.equal(state.items.length, 0);
});

test("loads submitted work after a process restart", async () => {
  const root = await mkdtemp(join(tmpdir(), "tmcra-openclaw-queue-"));
  const path = join(root, "pending.json");
  const first = new DurablePendingQueue({ path });
  await first.enqueue(input());
  await first.drain(
    async () => ({ status: "pending", jobId: "job-restart" }),
    async () => ({ status: "pending" }),
  );

  const restarted = new DurablePendingQueue({ path });
  const snapshot = await restarted.snapshot();
  assert.equal(snapshot.status, "ready");
  assert.equal(snapshot.items[0].status, "submitted");
  assert.equal(snapshot.items[0].jobId, "job-restart");
  const result = await restarted.drain(
    async () => ({ status: "pending", jobId: "unexpected" }),
    async () => ({ status: "succeeded" }),
    { force: true },
  );
  assert.equal(result.succeeded, 1);
});

test("loads a pending turn after a process restart", async () => {
  const root = await mkdtemp(join(tmpdir(), "tmcra-openclaw-queue-"));
  const path = join(root, "pending.json");
  const turn = {
    identity: { scopeName: "scope-a", sessionId: "session-a", runId: "run-a", runKey: "run-key-a" },
    prompt: "persist this prompt",
    userMessageId: "message-a",
    startedAt: Date.now(),
  };
  const first = new DurablePendingQueue({ path });
  await first.savePendingTurn("run-key-a", turn);
  const restarted = new DurablePendingQueue({ path });
  assert.deepEqual(await restarted.getPendingTurn("run-key-a"), turn);
});

test("marks a corrupt queue repair_required without starting from an empty queue", async () => {
  const root = await mkdtemp(join(tmpdir(), "tmcra-openclaw-queue-"));
  const path = join(root, "pending.json");
  await writeFile(path, "{not-json", "utf8");
  const queue = new DurablePendingQueue({ path });
  const snapshot = await queue.snapshot();
  assert.equal(snapshot.status, "repair_required");
  assert.ok(snapshot.repairRequired?.markerPath);
  await assert.rejects(
    () => queue.enqueue(input()),
    (error) => error instanceof QueueRepairRequiredError,
  );
});

test("deduplicates by idempotency key before submission", async () => {
  const root = await mkdtemp(join(tmpdir(), "tmcra-openclaw-queue-"));
  const path = join(root, "pending.json");
  const queue = new DurablePendingQueue({ path });
  assert.equal(await queue.enqueue(input("same")), true);
  assert.equal(await queue.enqueue(input("same")), false);
  assert.equal((await queue.snapshot()).items.length, 1);
});
