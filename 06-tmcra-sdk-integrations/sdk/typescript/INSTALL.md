# Install and verify the TypeScript/JavaScript SDK

## Requirements

- Node.js 18.17 or newer, or another fetch-capable ESM runtime.
- Production base URL `https://api.tmcra.com`.
- A scoped credential containing only the operations the application needs.

## Install

```bash
npm install https://tmcra.com/downloads/integrations/tmcra-typescript-0.5.0.tgz
```

Version 0.5.0 is not published on the public npm registry.

JavaScript and TypeScript use the same package. Keep credentials in a server
environment or secret manager:

```bash
export TMCRA_BASE_URL='https://api.tmcra.com'
export TMCRA_API_KEY=YOUR_ISSUED_API_KEY
```

Do not embed root tenant credentials in browser, desktop, or mobile bundles.

## Build from a source checkout

```bash
npm ci
npm test
npm run typecheck
npm pack --pack-destination artifacts
```

Install the tarball into a clean project outside the source tree:

```bash
mkdir /tmp/tmcra-typescript-verify
cd /tmp/tmcra-typescript-verify
npm init -y
npm install /absolute/path/tmcra-typescript-0.5.0.tgz
node --input-type=module -e \
  "import { TMCRAClient, TMCRAMemoryLifecycle } from '@tmcra/typescript'; console.log(typeof TMCRAClient, typeof TMCRAMemoryLifecycle)"
```

The expected output is `function function`. This proves Node resolved the
packed ESM and declarations rather than repository source files.

## Verify the optional lifecycle

The deterministic lifecycle tests must establish the order
`recall -> answer -> ingest`, separate user/assistant roles, project-only
automatic writes, shared project scope across Agents, separate sessions, and
recall-only private scope behavior:

```bash
node --experimental-strip-types --test test/lifecycle.test.ts
```

For a real service check, use a disposable project scope and short-lived
restricted token. Verify seed ingest completion, recall before the next answer,
answer completion before ingest, and a second Agent recalling the first
Agent's project progress. Do not print credentials or recalled content.

`agentPrivateScope` is omitted and off by default. When configured it is a
recall source only; automatic writes remain in `projectScope`.
