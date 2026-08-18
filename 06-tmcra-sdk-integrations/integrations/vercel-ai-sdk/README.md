# TMCRA for the Vercel AI SDK

`createTMCRAMiddleware` returns official `LanguageModelV3Middleware`: recall is
performed in `transformParams`, generated text is observed in `wrapGenerate`
and `wrapStream`, and completed turns are written with idempotency keys.

```ts
const memory = createTMCRAMiddleware({
  client: tmcra,
  scopeName: "person_123",
  sessionId: "chat_456",
});

const model = wrapLanguageModel({ model: providerModel, middleware: memory });
```

Model calls that emit tool calls are not treated as completed turns. For
multi-step agents with a higher-level final-result lifecycle, set
`writeMode: "external"` and use `createTMCRAOnFinish(...)` on the outer
`generateText` or `streamText` call. This prevents intermediate tool-loop
outputs from becoming durable memory.

Use a token restricted to `memory:read`, `memory:write`, and one persona scope.
