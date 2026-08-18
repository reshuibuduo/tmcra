import asyncio
import json

import httpx
import pytest

from tmcra_client import (
    APIError,
    AsyncClient,
    ConflictError,
    IngestRequest,
    MemoryMessage,
    NotFoundError,
    RecallRequest,
    SyncClient,
)


JOB = {
    "job_id": "job-1",
    "tenant_id": "tenant-1",
    "scope_name": "scope-1",
    "job_type": "ingest",
    "status": "pending",
    "attempts": 1,
    "created_at": 1710000000.0,
    "updated_at": 1710000000.0,
    "started_at": None,
    "finished_at": None,
    "heartbeat_at": None,
    "lease_expires_at": None,
    "result": None,
    "error": None,
    "status_url": "https://api.tmcra.com/v1/jobs/job-1",
}


def completed_job() -> dict[str, object]:
    result = dict(JOB)
    result["status"] = "succeeded"
    result["result"] = {"ok": True}
    return result


def graph_response(view: str = "overview") -> dict[str, object]:
    return {
        "schema_version": "tmcra.memory-graph.1",
        "scope_name": "scope-1",
        "snapshot_id": "snapshot-1",
        "view": view,
        "requested_layers": ["slow"],
        "resolved_layers": ["slow"],
        "fallback_layer": None,
        "nodes": [],
        "edges": [],
        "counts": {"nodes": 0, "edges": 0, "slow": 0, "fast": 0, "source": 0},
        "page": {"limit": 20, "offset": 0, "truncated": False},
        "root_id": None,
        "depth": None,
        "selected_memory_ids": [],
        "missing_memory_ids": [],
    }


def ingest_request() -> IngestRequest:
    return IngestRequest(
        session_id="session-1",
        messages=[
            MemoryMessage(
                message_id="message-1",
                role="user",
                content="hello",
                timestamp="2026-01-01T00:00:00Z",
            )
        ],
    )


def test_ingest_sends_auth_body_and_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == "/v1/scopes/scope-1/ingest"
        assert request.headers["authorization"] == "Bearer secret"
        assert request.headers["idempotency-key"] == "ingest-0001"
        payload = json.loads(request.content)
        assert payload["session_id"] == "session-1"
        return httpx.Response(202, json=JOB, request=request)

    with SyncClient("https://api.tmcra.com", api_key="secret", transport=httpx.MockTransport(handler)) as client:
        response = client.ingest("scope-1", ingest_request(), idempotency_key="ingest-0001")
    assert response.job_id == "job-1"
    assert len(seen) == 1


def test_client_sets_ledger_attribution_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-tmcra-client-platform"] == "python"
        assert request.headers["x-tmcra-integration-id"] == "int_local_python"
        assert request.headers["x-tmcra-agent-id"] == "planner"
        return httpx.Response(
            200,
            json={"status": "ok", "service": "tmcra-memory", "version": "1"},
            request=request,
        )

    with SyncClient(
        "https://api.tmcra.com",
        integration_id="int_local_python",
        agent_id="planner",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.healthz().status == "ok"


def test_usage_costs_forwards_attribution_group() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["scope_prefix"] == "scope-"
        assert request.url.params["from_timestamp"] == "100.0"
        assert request.url.params["to_timestamp"] == "200.0"
        assert request.url.params["group_by"] == "platform"
        return httpx.Response(
            200,
            json={
                "tenant_id": "tenant-1",
                "scope_name": None,
                "scope_prefix": "scope-",
                "source_ledger_coverage": "scope_evolution_totals",
                "currency": "CNY",
                "ledger_coverage": "registered_calls_only",
                "complete_for_registered_calls": True,
                "source": {},
                "calls": {},
                "known_cost_cny": 0,
                "uncertain_cost_call_count": 0,
                "by_stage": {},
                "quota_events": {"ingest_raw_tokens": 0, "recall_requests": 0},
                "quota_event_scope_coverage": {
                    "ingest_raw_tokens": "scope_attributed",
                    "recall_requests": "scope_attributed_since_usage_attribution_v1",
                },
                "attribution_coverage": {
                    "system_derived": {
                        "provider_call_count": 0,
                        "usage_event_count": 0,
                        "ingest_raw_tokens": 0,
                        "recall_requests": 0,
                        "known_cost_micro_cny": 0,
                    }
                },
                "group_by": "platform",
                "buckets": [],
            },
            request=request,
        )

    with SyncClient(
        "https://api.tmcra.com", transport=httpx.MockTransport(handler)
    ) as client:
        response = client.usage_costs(
            scope_prefix="scope-",
            from_timestamp=100.0,
            to_timestamp=200.0,
            group_by="platform",
        )
    assert response.group_by == "platform"
    assert response.scope_name is None
    assert response.scope_prefix == "scope-"
    assert "system_derived" in response.attribution_coverage


def test_only_idempotent_requests_are_retried() -> None:
    get_calls = 0
    post_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_calls, post_calls
        if request.method == "GET":
            get_calls += 1
            if get_calls == 1:
                return httpx.Response(503, json={"error": {"message": "busy"}}, request=request)
            return httpx.Response(200, json={"status": "ok", "service": "tmcra-memory", "version": "1"}, request=request)
        post_calls += 1
        return httpx.Response(503, json={"error": {"message": "busy"}}, request=request)

    transport = httpx.MockTransport(handler)
    with SyncClient("https://api.tmcra.com", max_retries=1, retry_initial_delay=0, transport=transport) as client:
        assert client.healthz().status == "ok"
        with pytest.raises(APIError):
            client.recall("scope-1", RecallRequest(query="hello"))
    assert get_calls == 2
    assert post_calls == 1


def test_memory_graph_methods_keep_source_text_behind_explicit_evidence_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/evidence"):
            payload = {
                "schema_version": "tmcra.memory-graph.1",
                "scope_name": "scope-1",
                "snapshot_id": "snapshot-1",
                "memory_id": "node-1",
                "items": [],
                "page": {"limit": 10, "offset": 0, "truncated": False},
            }
        elif request.url.path.endswith("/trace"):
            payload = {
                **graph_response("recall_trace"),
                "query_id": "query-1",
                "index_job_id": "job-1",
                "retrieval_summary": {},
                "debug": None,
            }
        else:
            payload = graph_response("neighbors" if request.url.path.endswith("/neighbors") else "overview")
        return httpx.Response(200, json=payload, request=request)

    with SyncClient("https://api.tmcra.com", transport=httpx.MockTransport(handler)) as client:
        client.memory_graph("scope-1", layers=("slow", "fast"), limit=20, query="launch")
        client.memory_graph_neighbors("scope-1", "node-1", depth=2)
        client.memory_graph_evidence("scope-1", "node-1")
        client.trace_memory_recall("scope-1", {"query": "what changed?"})

    assert seen[0].url.params["layers"] == "slow,fast"
    assert seen[0].url.params["query"] == "launch"
    assert seen[1].url.path.endswith("/nodes/node-1/neighbors")
    assert seen[1].url.params["depth"] == "2"
    assert seen[2].url.path.endswith("/nodes/node-1/evidence")
    assert seen[3].method == "POST"
    assert json.loads(seen[3].content) == {
        "query": "what changed?",
        "max_windows": 8,
        "debug": False,
    }


def test_idempotency_key_allows_retry_for_job_creation() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers.get("content-length") == "0"
        if calls == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}}, request=request)
        return httpx.Response(202, json=JOB, request=request)

    with SyncClient(
        "https://api.tmcra.com",
        max_retries=1,
        retry_initial_delay=0,
        transport=httpx.MockTransport(handler),
    ) as client:
        response = client.consolidate("scope-1", idempotency_key="consolidate-1")
    assert response.job_id == "job-1"
    assert calls == 2


def test_error_mapping_preserves_request_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "idempotency_conflict",
                    "message": "conflict",
                    "request_id": "request-from-body",
                    "details": {"field": "idempotency_key"},
                }
            },
            headers={"x-request-id": "request-from-header"},
            request=request,
        )

    with SyncClient("https://api.tmcra.com", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ConflictError) as caught:
            client.consolidate("scope-1", idempotency_key="consolidate-1")
    assert caught.value.code == "idempotency_conflict"
    assert caught.value.request_id == "request-from-body"
    assert caught.value.detail == {"field": "idempotency_key"}


def test_wait_for_job_polls_until_terminal() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=completed_job() if calls == 2 else JOB, request=request)

    with SyncClient("https://api.tmcra.com", retry_initial_delay=0, transport=httpx.MockTransport(handler)) as client:
        response = client.wait_for_job("job-1", timeout=1, poll_interval=0.001, max_poll_interval=0.002)
    assert response.succeeded
    assert calls == 2


def test_not_found_is_typed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "job not found"}, request=request)

    with SyncClient("https://api.tmcra.com", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(NotFoundError):
            client.get_job("job-1")


def test_readyz_returns_not_ready_payload_on_http_503() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "status": "not_ready",
                "service": "tmcra-memory",
                "version": "1",
                "checks": {"worker": False},
            },
            request=request,
        )

    with SyncClient("https://api.tmcra.com", max_retries=0, transport=httpx.MockTransport(handler)) as client:
        response = client.readyz()
    assert response.status == "not_ready"
    assert response.checks == {"worker": False}


def test_async_client_matches_sync_surface() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok", "service": "tmcra-memory", "version": "1"}, request=request)

        async with AsyncClient("https://api.tmcra.com", transport=httpx.MockTransport(handler)) as client:
            result = await client.healthz()
        assert result.service == "tmcra-memory"

    asyncio.run(run())


def test_commercial_contract_methods_preserve_scope_guards_and_binary_exports() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/ingest/batch"):
            return httpx.Response(202, json={"scope_name": "scope-1", "jobs": [JOB]}, request=request)
        if path.endswith("/exports/export-1"):
            return httpx.Response(200, content=b"PK\x03\x04archive", request=request)
        if path.endswith("/retention"):
            return httpx.Response(
                200,
                json={
                    "scope_name": "scope-1",
                    "enabled": True,
                    "inactive_days": 30,
                    "created_at": 1.0,
                    "updated_at": 2.0,
                },
                request=request,
            )
        if request.method == "DELETE" and path.endswith("/scope-1"):
            return httpx.Response(202, json=JOB, request=request)
        if request.method == "POST" and path.endswith("/access-tokens"):
            return httpx.Response(
                201,
                json={
                    "token_id": "token-1",
                    "tenant_id": "tenant-1",
                    "access_token": "secret",
                    "permissions": ["memory:read"],
                    "scope_names": [],
                    "scope_prefixes": ["scope-"],
                    "label": "Codex",
                    "subject": None,
                    "created_by_key_id": "key-1",
                    "created_at": 1.0,
                    "expires_at": 2.0,
                    "revoked_at": None,
                    "last_used_at": None,
                },
                request=request,
            )
        raise AssertionError(f"unexpected request {request.method} {path}")

    with SyncClient("https://api.tmcra.com", transport=httpx.MockTransport(handler)) as client:
        batch = client.bulk_ingest(
            "scope-1",
            {
                "items": [
                    {
                        **ingest_request().model_dump(mode="json"),
                        "idempotency_key": "batch-item-1",
                    }
                ]
            },
        )
        archive = client.download_scope_export("scope-1", "export-1")
        policy = client.set_retention_policy(
            "scope-1", {"enabled": True, "inactive_days": 30}
        )
        client.delete_scope("scope-1", idempotency_key="delete-scope-1")
        client.issue_access_token(
            {
                "label": "Codex",
                "permissions": ["memory:read"],
                "scope_prefixes": ["scope-"],
                "expires_in_seconds": 3600,
            },
            idempotency_key="issue-token-1",
        )

    assert batch.jobs[0].job_id == "job-1"
    assert archive.startswith(b"PK")
    assert policy.inactive_days == 30
    assert seen[0].headers["idempotency-key"] == "batch-item-1"
    assert next(
        request.headers["x-tmcra-confirm-scope"]
        for request in seen
        if request.method == "DELETE" and request.url.path.endswith("/scope-1")
    ) == "scope-1"
    assert seen[-1].headers["idempotency-key"] == "issue-token-1"


def test_discovery_session_and_quota_cover_public_control_plane_contract() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/v1/session":
            return httpx.Response(200, json={
                "ok": True,
                "authenticated": True,
                "service": {"name": "tmcra-memory", "version": "1", "capabilities": ["recall"]},
                "credential": {
                    "type": "scope_token",
                    "tenant_id": "tenant-1",
                    "principal": "token:1",
                    "permissions": ["memory:read"],
                    "scope_restrictions": {"unrestricted": False, "names": ["scope-1"], "prefixes": []},
                    "subject": "user-1",
                    "expires_at": 2.0,
                },
            }, request=request)
        if path == "/v1/scopes":
            return httpx.Response(200, json=[{
                "scope_name": "scope-1", "created_at": 1.0, "last_seen_at": 2.0,
                "session_count": 1, "ingest_request_count": 2,
                "recall_request_count": 3, "message_count": 4,
                "last_ingest_at": 2.0, "last_recall_at": 2.0,
            }], request=request)
        if path.endswith("/summary"):
            return httpx.Response(200, json={
                "scope": {
                    "scope_name": "scope-1", "created_at": 1.0, "last_seen_at": 2.0,
                    "session_count": 1, "ingest_request_count": 2,
                    "recall_request_count": 3, "message_count": 4,
                },
                "sessions": [{
                    "session_id": "session-1", "created_at": 1.0,
                    "last_ingest_at": 2.0, "ingest_request_count": 2, "message_count": 4,
                }],
            }, request=request)
        if path == "/v1/billing/profile":
            return httpx.Response(200, json={
                "tenant_id": "tenant-1",
                "subject": "user-1",
                "consumer_principal": "subject:user-1",
                "quota_principal": "billing:team-1:period-1",
                "membership": {"group_id": "team-1", "role": "member", "status": "active"},
                "quota": {
                    "tenant_id": "tenant-1",
                    "principal": "billing:team-1:period-1",
                    "plan": "team",
                    "plan_version": "2026-08",
                    "billing_group": {
                        "group_id": "team-1", "display_name": "Team 1", "status": "active",
                        "period_id": "period-1", "period_status": "active",
                        "billing_interval": "monthly", "starts_at": 1.0, "ends_at": 2.0,
                        "max_members": 5, "currency": "CNY", "price_minor_units": 9900,
                    },
                    "ingest_raw_tokens": {"used": 10, "limit": 100, "remaining": 90},
                    "recall_requests": {"used": 2, "limit": 20, "remaining": 18},
                    "member_usage": {"subject:user-1": {"ingest_raw_tokens": 10, "recall_requests": 2}},
                },
            }, request=request)
        if path in {"/v1/usage/quota", "/v1/usage/entitlements/user-1"}:
            return httpx.Response(200, json={
                "tenant_id": "tenant-1", "principal": "user-1", "plan": "pilot",
                "plan_version": None, "billing_group": None, "member_usage": {},
                "ingest_raw_tokens": {"used": 10, "limit": 100, "remaining": 90},
                "recall_requests": {"used": 2, "limit": 20, "remaining": 18},
            }, request=request)
        raise AssertionError(path)

    with SyncClient(api_key="secret", transport=httpx.MockTransport(handler)) as client:
        assert client.authenticated_session().credential.subject == "user-1"
        assert client.list_scopes(prefix="scope-", limit=10)[0].message_count == 4
        assert client.scope_summary("scope-1").sessions[0].session_id == "session-1"
        assert client.quota().recall_requests.remaining == 18
        assert client.billing_profile().quota.billing_group.group_id == "team-1"
        assert client.set_entitlement("user-1", {
            "ingest_raw_tokens": 100,
            "recall_requests": 20,
        }).ingest_raw_tokens.limit == 100
        client.set_quota_entitlement("user-1", {
            "ingest_raw_tokens": None,
            "recall_requests": 20,
        })

    assert all(request.url.host == "api.tmcra.com" for request in seen)
    assert seen[1].url.params["prefix"] == "scope-"
    assert seen[1].url.params["limit"] == "10"
    assert json.loads(seen[5].content) == {"ingest_raw_tokens": 100, "recall_requests": 20}
    assert seen[6].url.params["subject"] == "user-1"
