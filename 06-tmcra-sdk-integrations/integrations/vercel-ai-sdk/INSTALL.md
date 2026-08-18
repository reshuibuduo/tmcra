# Install TMCRA for the Vercel AI SDK

```bash
npm install @tmcra/vercel-ai-sdk
```

Wrap the application model with `createTMCRAMiddleware`. Supply a scope-bound
client, scope, session, and a durable `FilePendingTurnQueue` when recovery is
required. For tool agents, commit from the outer `onFinish` callback rather
than intermediate tool steps.

Run `npm run typecheck`, `npm test`, and `npm run build` locally. These checks
do not configure a provider or prove a real `generateText`/`streamText` E2E.
