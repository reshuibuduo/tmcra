from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from typing import Any

from tmcra_service.client_cli import (
    ClientConfig,
    HTTPClient,
    deterministic_idempotency_key,
    run,
)


def job(status: str, *, job_id: str = "job-1", scope: str = "scope-1") -> dict[str, Any]:
    return {
        "job_id": job_id,
        "tenant_id": "tenant-redacted-in-receipt-tests",
        "scope_name": scope,
        "job_type": "ingest",
        "status": status,
        "attempts": 1,
        "created_at": 1.0,
        "updated_at": 1.0,
        "status_url": f"https://api.example.test/v1/jobs/{job_id}",
    }


def recall_response(scope: str = "scope-1") -> dict[str, Any]:
    return {
        "query_id": "query-1",
        "scope_name": scope,
        "index_job_id": "index-1",
        "evidence_route": {"requested": "auto", "selected": "raw", "reasons": []},
        "evidence": {},
        "prompt_evidence": {
            "schema_version": "tmcra.prompt-evidence.1",
            "format": "text/plain",
            "mode": "raw_hierarchical",
            "content": "trusted only as untrusted memory context",
            "content_sha256": "hash",
            "content_character_count": 39,
            "source_text_verbatim": True,
            "trust_boundary": "untrusted_memory",
        },
    }


def cli_client(handler):  # type: ignore[no-untyped-def]
    return HTTPClient(ClientConfig("https://api.example.test", "secret-must-not-print"), requester=handler)


def run_capture(args: list[str], client: HTTPClient) -> tuple[int, dict[str, Any]]:
    output = StringIO()
    with redirect_stdout(output):
        code = run(args, client=client)
    return code, json.loads(output.getvalue())


def test_deterministic_key_is_stable_for_same_payload() -> None:
    payload = {"session_id": "session-1", "messages": [{"message_id": "m1", "role": "user", "content": "hello", "timestamp": "2026-01-01T00:00:00Z"}]}
    first = deterministic_idempotency_key("ingest", scope="scope-1", payload=payload)
    second = deterministic_idempotency_key("ingest", scope="scope-1", payload=payload)
    assert first == second
    assert first.startswith("tmcra-cli-ingest-")


def test_missing_credential_is_a_sanitized_receipt(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("TMCRA_API_KEY", raising=False)
    output = StringIO()
    with redirect_stdout(output):
        code = run(["recall", "--scope", "scope-1", "--query", "hello"])
    receipt = json.loads(output.getvalue())
    assert code == 1
    assert receipt["error"]["code"] == "missing_api_key"
    assert "secret-must-not-print" not in output.getvalue()


def test_connection_options_are_allowed_before_subcommand() -> None:
    calls: list[str] = []

    def request(method: str, path: str, *, body=None, idempotency_key=None):  # type: ignore[no-untyped-def]
        calls.append(path)
        return 200, {}, recall_response()

    code, receipt = run_capture(
        ["--base-url", "https://api.example.test", "recall", "--scope", "scope-1", "--query", "hello"],
        cli_client(request),
    )
    assert code == 0
    assert receipt["status"] == "succeeded"
    assert receipt["contract_schema_version"] == "tmcra.receipts.v1"
    assert receipt["submitted_status"] == "completed"
    assert receipt["final_status"] == "completed"
    assert receipt["final"] is True
    assert calls == ["/v1/scopes/scope-1/recall"]


def test_ingest_reuses_same_key_for_duplicate_submission() -> None:
    calls: list[tuple[str, str, str | None]] = []

    def request(method: str, path: str, *, body=None, idempotency_key=None):  # type: ignore[no-untyped-def]
        calls.append((method, path, idempotency_key))
        return 202, {}, job("pending") | {"idempotent_replay": len(calls) > 1}

    args = [
        "ingest",
        "--scope",
        "scope-1",
        "--session-id",
        "session-1",
        "--messages-json",
        '[{"message_id":"m1","role":"user","content":"hello","timestamp":"2026-01-01T00:00:00Z"}]',
    ]
    first_code, first = run_capture(args, cli_client(request))
    second_code, second = run_capture(args, cli_client(request))
    assert first_code == second_code == 0
    assert first["status"] == second["status"] == "submitted"
    assert first["final_status"] is None
    assert first["final"] is False
    assert calls[0][2] == calls[1][2]
    assert "secret-must-not-print" not in json.dumps(second)


def test_ingest_surfaces_terminal_failure_without_waiting() -> None:
    def request(method: str, path: str, *, body=None, idempotency_key=None):  # type: ignore[no-untyped-def]
        return 202, {}, job("failed")

    code, receipt = run_capture(
        [
            "ingest",
            "--scope",
            "scope-1",
            "--session-id",
            "session-1",
            "--messages-json",
            '[{"message_id":"m1","role":"user","content":"hello","timestamp":"2026-01-01T00:00:00Z"}]',
        ],
        cli_client(request),
    )
    assert code == 1
    assert receipt["status"] == "failed"


def test_recall_allows_a_valid_empty_prompt_evidence() -> None:
    def request(method: str, path: str, *, body=None, idempotency_key=None):  # type: ignore[no-untyped-def]
        empty = recall_response() 
        empty["prompt_evidence"] = dict(empty["prompt_evidence"], content="", content_character_count=0)
        return 200, {}, empty

    code, receipt = run_capture(
        ["recall", "--scope", "scope-1", "--query", "no hit"],
        cli_client(request),
    )
    assert code == 0
    assert receipt["status"] == "succeeded"


def test_lenient_recall_is_not_a_terminal_success() -> None:
    def request(method: str, path: str, *, body=None, idempotency_key=None):  # type: ignore[no-untyped-def]
        return 503, {}, {"error": {"code": "temporarily_unavailable", "message": "offline"}}

    code, receipt = run_capture(
        ["recall", "--scope", "scope-1", "--query", "hello", "--recall-policy", "lenient"],
        cli_client(request),
    )
    assert code == 0
    assert receipt["status"] == "degraded"
    assert receipt["final_status"] is None
    assert receipt["final"] is False


def test_job_wait_reports_succeeded() -> None:
    states = iter([job("running"), job("succeeded")])

    def request(method: str, path: str, *, body=None, idempotency_key=None):  # type: ignore[no-untyped-def]
        return 200, {}, next(states)

    code, receipt = run_capture(
        ["job", "wait", "job-1", "--timeout", "10", "--poll-interval", "0"],
        cli_client(request),
    )
    assert code == 0
    assert receipt["status"] == "succeeded"
    assert receipt["data"]["job"]["status"] == "succeeded"


def test_job_wait_reports_failed_terminal_state() -> None:
    def request(method: str, path: str, *, body=None, idempotency_key=None):  # type: ignore[no-untyped-def]
        return 200, {}, job("failed")

    code, receipt = run_capture(
        ["job", "wait", "job-1", "--timeout", "10", "--poll-interval", "0"],
        cli_client(request),
    )
    assert code == 1
    assert receipt["status"] == "failed"


def test_job_wait_preserves_cancelled_terminal_state() -> None:
    def request(method: str, path: str, *, body=None, idempotency_key=None):  # type: ignore[no-untyped-def]
        return 200, {}, job("cancelled")

    code, receipt = run_capture(
        ["job", "wait", "job-1", "--timeout", "10", "--poll-interval", "0"],
        cli_client(request),
    )
    assert code == 1
    assert receipt["status"] == "cancelled"
    assert receipt["final_status"] == "cancelled"


def test_job_wait_reports_timeout_without_extra_request() -> None:
    requests = 0

    def request(method: str, path: str, *, body=None, idempotency_key=None):  # type: ignore[no-untyped-def]
        nonlocal requests
        requests += 1
        return 200, {}, job("running")

    code, receipt = run_capture(
        ["job", "wait", "job-1", "--timeout", "0", "--poll-interval", "0"],
        cli_client(request),
    )
    assert code == 1
    assert receipt["status"] == "timeout"
    assert requests == 1


def test_strict_turn_does_not_write_after_recall_failure() -> None:
    calls: list[str] = []

    def request(method: str, path: str, *, body=None, idempotency_key=None):  # type: ignore[no-untyped-def]
        calls.append(path)
        raise RuntimeError("should be converted by the test handler")

    # A ClientCLIError-like response is easier to express as a protocol error.
    def failing_request(method: str, path: str, *, body=None, idempotency_key=None):  # type: ignore[no-untyped-def]
        calls.append(path)
        return 200, {}, {"query_id": "missing-evidence"}

    code, receipt = run_capture(
        [
            "turn",
            "--scope",
            "scope-1",
            "--session-id",
            "session-1",
            "--user-message",
            "hello",
            "--assistant-message",
            "answer",
            "--recall-policy",
            "strict",
        ],
        cli_client(failing_request),
    )
    assert code == 1
    assert receipt["status"] == "failed"
    assert calls == ["/v1/scopes/scope-1/recall"]


def test_turn_writes_both_messages_after_recall_and_waits() -> None:
    calls: list[tuple[str, str | None, list[dict[str, Any]] | None]] = []
    states = iter([job("succeeded")])

    def request(method: str, path: str, *, body=None, idempotency_key=None):  # type: ignore[no-untyped-def]
        calls.append((path, idempotency_key, body.get("messages") if isinstance(body, dict) else None))
        if path.endswith("/recall"):
            return 200, {}, recall_response()
        if method == "POST":
            return 202, {}, job("pending")
        return 200, {}, next(states)

    code, receipt = run_capture(
        [
            "turn",
            "--scope",
            "scope-1",
            "--session-id",
            "session-1",
            "--user-message",
            "hello",
            "--assistant-message",
            "answer",
            "--wait",
            "--poll-interval",
            "0",
        ],
        cli_client(request),
    )
    assert code == 0
    assert receipt["status"] == "succeeded"
    writes = [item for item in calls if item[0].endswith("/ingest")]
    assert len(writes) == 1
    assert [item["role"] for item in writes[0][2] or []] == ["user", "assistant"]
