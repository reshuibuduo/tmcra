# tmcra-client

Typed synchronous and asynchronous Python clients for the TMCRA Memory API.

## Install

```bash
pip install .
```

The client requires Python 3.10+, `httpx`, and Pydantic 2.

## Usage

```python
from datetime import datetime, timezone

from tmcra_client import IngestRequest, MemoryMessage, SyncClient

request = IngestRequest(
    session_id="session-1",
    messages=[
        MemoryMessage(
            message_id="message-1",
            role="user",
            content="Remember this fact.",
            timestamp=datetime.now(timezone.utc),
        )
    ],
    consistency="read_your_writes",
)

with SyncClient("https://memory.example.com", api_key="tmcra_...") as client:
    job = client.ingest("customer-a-user-1", request, idempotency_key="ingest-0001")
    completed = client.wait_for_job(job.job_id)
    recall = client.recall(
        "customer-a-user-1",
        {"query": "What should I remember?", "wait_for_job_id": completed.job_id},
    )
    print(recall.prompt_evidence.content)
```

The async client has the same endpoint methods and uses `async with` and
`await`:

```python
from tmcra_client import AsyncClient, RecallRequest

async with AsyncClient("https://memory.example.com", api_key="tmcra_...") as client:
    result = await client.recall("customer-a-user-1", RecallRequest(query="..."))
```

## Client behavior

- API keys are sent as `Authorization: Bearer ...`.
- `ingest`, `consolidate`, and `retry_job` require an idempotency key because
  the service requires one. Those requests may be retried safely when the
  configured retry policy is enabled.
- `recall` and `cancel_job` are never retried automatically. Read-only GET
  operations are retried for transient HTTP statuses and transport failures.
- `timeout`, `max_retries`, and retry backoff can be configured on either
  client. The default request timeout is 30 seconds and the default is two
  retries.
- `wait_for_job` / `poll_job` polls `get_job` until `succeeded`, `failed`, or
  `cancelled`, with a bounded timeout and increasing poll interval.
- Non-success responses raise typed exceptions such as `NotFoundError`,
  `ConflictError`, `RateLimitError`, or `APIError`. The exception exposes the
  HTTP status, service error code, request ID, response headers, and body.

## Tests

```bash
pip install -e ".[test]"
pytest
```

Tests use `httpx.MockTransport`; no running TMCRA service or credentials are
needed.
