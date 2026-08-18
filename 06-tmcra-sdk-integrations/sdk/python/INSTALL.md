# Install and verify the Python SDK

## Requirements

- Python 3.10 or newer.
- Production base URL `https://api.tmcra.com`.
- A scoped credential containing only the operations the application needs.

## Install

Install the verified package currently distributed by `tmcra.com`:

```bash
python -m pip install https://tmcra.com/downloads/integrations/tmcra_client-0.5.0-py3-none-any.whl
```

Version 0.5.0 is not published on public PyPI.

Keep credentials in a process environment or secret manager:

```bash
export TMCRA_BASE_URL='https://api.tmcra.com'
export TMCRA_API_KEY=YOUR_ISSUED_API_KEY
```

Do not ship a root tenant key in desktop, browser, or mobile code.

## Build from a source checkout

```bash
python -m pip install build twine
python -m pytest -q
python -m build
python -m twine check dist/*
```

Install the wheel into a clean virtual environment outside the source tree:

```bash
python -m venv /tmp/tmcra-python-verify
/tmp/tmcra-python-verify/bin/python -m pip install \
  dist/tmcra_client-0.5.0-py3-none-any.whl
cd /tmp
/tmp/tmcra-python-verify/bin/python -c \
  "from tmcra_client import SyncClient, AsyncClient, SyncMemoryLifecycle; print('ok')"
```

On Windows use `Scripts/python.exe` instead of `bin/python`.

## Verify the optional lifecycle

The deterministic lifecycle tests must establish the order
`recall -> answer -> ingest`, separate user/assistant roles, project-only
automatic writes, shared project scope across Agents, separate sessions, and
recall-only private scope behavior:

```bash
python -m pytest -q tests/test_lifecycle.py
```

For a real service smoke test, use a disposable project scope and short-lived
restricted token. Verify health/readiness, seed ingest completion, recall before
the next answer, answer completion before ingest, and a second Agent recalling
the first Agent's project progress. Never print the credential or recalled
content in the report.

Set `agent_private_scope` only when the host has an explicit private boundary.
It is omitted and off by default, and automatic turn writes remain in
`project_scope`.
