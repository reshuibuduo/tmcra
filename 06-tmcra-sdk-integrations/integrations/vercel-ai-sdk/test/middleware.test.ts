import assert from "node:assert/strict";
import { test } from "node:test";
import type { LanguageModelV3CallOptions, LanguageModelV3GenerateResult } from "@ai-sdk/provider";
import { createTMCRAMiddleware, type TMCRAMemoryClient } from "../src/index.ts";
import { MemoryPendingTurnQueue } from "../../../sdk/typescript/src/queue.ts";

test("middleware recalls before generation and commits one final text result", async () => {
  const calls: unknown[] = [];
  const client: TMCRAMemoryClient = {
    async recall(scopeName, body) {
      calls.push(["recall", scopeName, body]);
      return { prompt_evidence: { content: "The user prefers concise reports." } };
    },
    async ingest(scopeName, body, options) {
      calls.push(["ingest", scopeName, body, options]);
      return {};
    },
  };
  const middleware = createTMCRAMiddleware({ client, scopeName: "person-1", sessionId: "session-1" });
  const original = {
    prompt: [{ role: "user", content: [{ type: "text", text: "How should this look?" }] }],
  } as LanguageModelV3CallOptions;
  const transformed = await middleware.transformParams!({ type: "generate", params: original, model: {} as never });
  assert.equal(transformed.prompt[0]?.role, "system");
  const result = {
    content: [{ type: "text", text: "Keep it concise." }],
    finishReason: "stop",
    usage: { inputTokens: { total: 1 }, outputTokens: { total: 1 } },
    warnings: [],
  } as unknown as LanguageModelV3GenerateResult;
  await middleware.wrapGenerate!({
    params: transformed,
    model: {} as never,
    doGenerate: async () => result,
    doStream: async () => { throw new Error("unused"); },
  });
  assert.equal(calls.length, 2);
  assert.equal((calls[1] as any[])[0], "ingest");
});

test("tool-call model steps are not committed as completed turns", async () => {
  let ingests = 0;
  const client: TMCRAMemoryClient = {
    async recall() { return { prompt_evidence: { content: "Memory" } }; },
    async ingest() { ingests += 1; return {}; },
  };
  const middleware = createTMCRAMiddleware({ client, scopeName: "p", sessionId: "s" });
  const params = await middleware.transformParams!({
    type: "generate",
    params: { prompt: [{ role: "user", content: [{ type: "text", text: "Use a tool" }] }] },
    model: {} as never,
  });
  await middleware.wrapGenerate!({
    params,
    model: {} as never,
    doGenerate: async () => ({ content: [{ type: "tool-call", toolCallId: "1", toolName: "x", input: {} }], warnings: [] } as any),
    doStream: async () => { throw new Error("unused"); },
  });
  assert.equal(ingests, 0);
});

test("response loss keeps a durable record and reconcile reuses the key", async () => {
  const calls: string[] = [];
  let fail = true;
  const client: TMCRAMemoryClient = {
    async recall() { return { prompt_evidence: { content: "Memory" } }; },
    async ingest(_scope, _body, options) {
      calls.push(options?.idempotencyKey ?? "");
      if (fail) { fail = false; throw new Error("response lost"); }
      return { job_id: "job-2", status: "submitted" };
    },
  };
  const queue = new MemoryPendingTurnQueue();
  const middleware = createTMCRAMiddleware({ client, scopeName: "p", sessionId: "s", failureMode: "continue", pendingQueue: queue });
  const params = await middleware.transformParams!({
    type: "generate",
    params: { prompt: [{ role: "user", content: [{ type: "text", text: "Remember this" }] }] },
    model: {} as never,
  });
  await middleware.wrapGenerate!({
    params,
    model: {} as never,
    doGenerate: async () => ({ content: [{ type: "text", text: "Stored" }], warnings: [] } as any),
    doStream: async () => { throw new Error("unused"); },
  });
  const receipts = await middleware.reconcilePending();
  assert.equal(receipts[0]?.status, "submitted");
  assert.equal(calls[0], calls[1]);
});
