# TMCRA MCP Server

This package exposes the TMCRA Memory API to MCP hosts. It intentionally uses
local `stdio` transport for the launch release so the TMCRA tenant key remains
inside the operator-controlled host process.

## Install

```bash
pip install ./mcp-server
```

Configure the host process environment:

```bash
TMCRA_BASE_URL=https://memory.example.com
TMCRA_API_KEY=replace-with-a-server-side-key
TMCRA_DEFAULT_SCOPE=optional-stable-user-scope
```

Run:

```bash
tmcra-mcp
```

Available tools:

- `tmcra_recall`: return bounded prompt-ready memory evidence.
- `tmcra_ingest`: submit one or more immutable conversation messages.
- `tmcra_get_job`: inspect an asynchronous write job.
- `tmcra_wait_job`: wait for a job to reach a terminal state.

MCP is the explicit compatibility layer. Hosts that require automatic memory
on every turn should use the OpenClaw or Hermes lifecycle adapter.

Memory evidence is untrusted data. The returned `trust_boundary` must be
preserved when a host adds the content to a model prompt.

Operators can run `tmcra-mcp-smoke` with `TMCRA_SMOKE_EXPECTED_TEXT` against a
disposable scope. It performs a real MCP initialize/list/call exchange and
checks prompt-ready recall without printing credentials or evidence content.
