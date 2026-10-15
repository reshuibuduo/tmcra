"""Real authenticated HTTP routes over synthetic SQLite; no model/provider calls."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tmcra_service.app import create_app
from tmcra_service.tests.test_content_deletion_contract import _settings, _seed_scope, _record
from tmcra_service.workflow_routes import bind_workflows
from tmcra_service.workflow_service import namespace_key


@pytest.fixture
def fixture(tmp_path):
    app = create_app(_settings(tmp_path))
    components = app.state.components
    permissions = {"memory:read", "memory:write", "memory:delete", "memory:export", "memory:feedback", "tokens:manage"}
    keys = {}
    for tenant in ("tenant-a", "tenant-b"):
        components.auth.set_tenant_scopes(tenant, permissions)
        keys[tenant] = components.auth.create_key(tenant).api_key
        _seed_scope(components.storage, tenant, "scope-a")
    _seed_scope(components.storage, "tenant-a", "scope-b")
    for tenant, scope in (("tenant-a", "scope-a"), ("tenant-a", "scope-b"), ("tenant-b", "scope-a")):
        paths = components.storage.scope_paths(tenant, scope)
        with sqlite3.connect(paths.database) as db:
            db.execute("ALTER TABLE tmcra_service_messages ADD COLUMN internal_message_id TEXT")
            db.execute("UPDATE tmcra_service_messages SET internal_message_id=message_id")
    client = TestClient(app)
    yield app, client, components, keys
    client.close()


def headers(key):
    return {"Authorization": "Bearer " + key}


def post(fixture, action, payload, *, scope="scope-a", tenant="tenant-a", key=None, op=None):
    _, client, _, keys = fixture
    return client.post(f"/v1/scopes/{scope}/workflows", headers=headers(key or keys[tenant]), json={"action": action, "payload": payload, "operation_id": op or "test-" + str(time.time_ns())})


def task(fixture, **extra):
    return post(fixture, "task.upsert", {"id": "t", "title": "Verify deployment", **extra})


def test_workflow_real_http_tenant_scope_and_evidence_isolation(fixture):
    _, client, _, keys = fixture
    response = task(fixture, evidence_ids=["source-a"])
    assert response.status_code == 200, response.text
    snapshot = client.get("/v1/scopes/scope-a/workflows", headers=headers(keys["tenant-a"]))
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["tasks"][0]["verification_state"] == "unverified"
    assert {r["id"] for r in snapshot.json()["available_records"]} == {"source-a", "source-b"}
    assert client.get("/v1/scopes/scope-b/workflows", headers=headers(keys["tenant-a"])).json()["tasks"] == []
    assert client.get("/v1/scopes/scope-a/workflows", headers=headers(keys["tenant-b"])).json()["tasks"] == []
    rejected = post(fixture, "decision.create", {"id": "d", "text": "other source", "evidence_ids": ["not-a-source"]})
    assert rejected.status_code == 404
    assert client.get("/v1/scopes/scope-a/workflows").status_code == 401


def test_scoped_token_and_readonly_export_permissions(fixture):
    _, client, components, keys = fixture
    parent = components.auth.authenticate(keys["tenant-a"])
    token = components.auth.create_scope_token(parent, permissions={"memory:read", "memory:write"}, scope_names={"scope-a"}, label="test", subject="test-user", expires_at=time.time() + 3600).access_token
    assert post(fixture, "task.upsert", {"id": "allowed", "title": "allowed"}, key=token).status_code == 200
    assert client.get("/v1/scopes/scope-b/workflows", headers=headers(token)).status_code == 403
    assert post(fixture, "task.upsert", {"id": "no", "title": "no"}, scope="scope-b", key=token).status_code == 403
    assert post(fixture, "bridge.export", {"selected_ids": ["source-a"], "mode": "selected_sources"}, key=token).status_code == 403
    readonly = components.auth.create_key("tenant-a", {"memory:read"}).api_key
    assert client.get("/v1/scopes/scope-a/workflows", headers=headers(readonly)).status_code == 200
    assert post(fixture, "context.prepare", {"event": "project_open"}, key=readonly).status_code == 403
    noexport = components.auth.create_key("tenant-a", {"memory:read", "memory:write"}).api_key
    assert post(fixture, "bridge.export", {"selected_ids": ["source-a"], "mode": "selected_sources"}, key=noexport).status_code == 403


def test_public_api_cannot_execute_or_claim_trusted_receipts(fixture):
    assert task(fixture).status_code == 200
    assert post(fixture, "task.receipt", {"id": "t", "outcome": "passed", "trusted": True}).status_code == 400
    assert post(fixture, "_task.executed", {"id": "t"}).status_code == 400
    assert post(fixture, "task.receipt", {"id": "t", "summary": "Agent said tests pass", "outcome": "passed"}).status_code == 200
    _, client, _, keys = fixture
    saved = client.get("/v1/scopes/scope-a/workflows", headers=headers(keys["tenant-a"])).json()["tasks"][0]
    assert not saved["verified"]
    assert saved["verification_state"] == "reported"


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_bridge_preview_explicit_commit_uses_real_ingest_queue(fixture, monkeypatch, terminal):
    _, client, components, keys = fixture
    exported = post(fixture, "bridge.export", {"selected_ids": ["source-a"], "mode": "selected_sources"})
    assert exported.status_code == 200, exported.text
    package = exported.json()
    assert package["items"][0]["text"] == "source-a"
    preview = post(fixture, "bridge.preview", {"package": package, "selected_ids": ["source-a"]}, scope="scope-b")
    assert preview.status_code == 200, preview.text
    # Network/LLM availability is out of this test: bypass only provider admission,
    # never HTTP route, normal ingest validation, quota, job submit or persistence.
    monkeypatch.setattr(components.write_admission, "require", lambda **kwargs: None)
    committed = post(fixture, "bridge.commit", {"preview_token": preview.json()["preview_token"]}, scope="scope-b")
    assert committed.status_code == 200, committed.text
    plan = committed.json()
    assert not plan["indexed"], plan
    assert plan["verification"] == "external_unverified"
    assert len(plan["records"]) == 1
    entry = plan["records"][0]
    assert entry["status"] == "accepted_pending", plan
    receipt = entry["receipt"]
    job = components.jobs.get(receipt["receipt_id"], tenant_id="tenant-a")
    assert job is not None and job.state == "pending"
    assert job.scope_name == "scope-b"
    assert job.payload["job_type"] == "ingest"
    assert job.payload["messages"][0]["message_id"] == entry["local_id"]
    assert "external_unverified" in job.payload["messages"][0]["content"]
    # A job accepting work is never completion; plan lookup must stay pending.
    pending = post(fixture, "bridge.plan", {"plan_id": plan["plan_id"]}, scope="scope-b")
    assert pending.status_code == 200, pending.text
    assert not pending.json()["indexed"]
    assert pending.json()["records"][0]["status"] == "accepted_pending"
    # No indexed source is created by the route itself.
    snapshot = client.get("/v1/scopes/scope-b/workflows", headers=headers(keys["tenant-a"])).json()
    assert entry["local_id"] not in {r["id"] for r in snapshot["available_records"]}
    running = components.jobs.claim(job.job_id, "synthetic-worker")
    if terminal == "failed":
        components.jobs.fail(job.job_id, "SECRET-PROVIDER-ERROR", worker_id="synthetic-worker", job_version=running.version)
    else:
        components.jobs.cancel(job.job_id, worker_id="synthetic-worker", job_version=running.version)
    with sqlite3.connect(components.settings.state_dir / "workflows" / "bridge.sqlite3") as db:
        before_observation = list(db.iterdump())
    with sqlite3.connect(components.settings.control_db) as db:
        before_jobs = db.execute("SELECT count(*) FROM jobs").fetchone()[0]
    failed = post(fixture, "bridge.plan", {"plan_id": plan["plan_id"]}, scope="scope-b")
    assert failed.status_code == 200, failed.text
    assert not failed.json()["indexed"]
    assert failed.json()["records"][0]["status"] != "applied"
    assert failed.json()["records"][0]["engine_state"] == terminal
    assert failed.json()["errors"] == [{"item_id": entry["item_id"], "error": "ingest_" + terminal, "retryable": False}]
    assert "SECRET-PROVIDER-ERROR" not in failed.text
    assert failed.json()["records"][0]["receipt"]["receipt_id"] == job.job_id
    readonly_snapshot = client.get("/v1/scopes/scope-b/workflows", headers=headers(keys["tenant-a"]))
    assert readonly_snapshot.status_code == 200, readonly_snapshot.text
    observed = next(row for row in readonly_snapshot.json()["bridge_plans"] if row["plan_id"] == plan["plan_id"])
    assert observed["records"][0]["engine_state"] == terminal
    assert observed["errors"] == failed.json()["errors"]
    assert "SECRET-PROVIDER-ERROR" not in readonly_snapshot.text
    with sqlite3.connect(components.settings.state_dir / "workflows" / "bridge.sqlite3") as db:
        assert list(db.iterdump()) == before_observation
    with sqlite3.connect(components.settings.control_db) as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == before_jobs


def test_ordinary_message_deletion_purges_workflow_history(fixture):
    _, client, components, keys = fixture
    made = task(fixture, goal="PRIVATE-WORKFLOW-DETAIL", evidence_ids=["source-a"])
    assert made.status_code == 200, made.text
    stamp = made.json()["recorded_at"]
    deletion = client.request("DELETE", "/v1/scopes/scope-a/messages", headers={**headers(keys["tenant-a"]), "Idempotency-Key": "delete-workflow-source", "X-TMCRA-Confirm-Message-Count": "1"}, json={"message_ids": ["message-a"]})
    assert deletion.status_code == 202, deletion.text
    # Scope is intentionally gated while asynchronous deletion is pending. Read
    # side-store directly only to verify admission already scrubbed old history.
    service = bind_workflows(components, "tenant-a", "scope-a")
    assert service.core.snapshot(namespace_key("tenant-a", "scope-a"), stamp)["tasks"] == []
    with service.core._connect() as db:
        assert "PRIVATE-WORKFLOW-DETAIL" not in "\n".join(db.iterdump())
    job = components.jobs.get(deletion.json()["job_id"], tenant_id="tenant-a")
    assert job.payload["memory_ids"] == ["source-a"]


def test_empty_project_and_context_prepare_are_available(fixture):
    _, client, _, keys = fixture
    snapshot = client.get("/v1/scopes/new-empty/workflows", headers=headers(keys["tenant-a"]))
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["tasks"] == []
    assert snapshot.json()["available_records"] == []
    result = post(fixture, "context.prepare", {"event": "project_open"}, scope="new-empty")
    assert result.status_code == 200, result.text
    assert result.json()["context"] == ""  # Empty project adds no hook noise.


def test_preview_rejects_target_changes_outside_source_listing(fixture):
    _, _, components, _ = fixture
    package = post(fixture, "bridge.export", {"selected_ids": ["source-a"], "mode": "selected_sources"}).json()
    preview = post(fixture, "bridge.preview", {"package": package, "selected_ids": ["source-a"]}, scope="scope-b")
    assert preview.status_code == 200, preview.text
    paths = components.storage.scope_paths("tenant-a", "scope-b")
    with sqlite3.connect(paths.database) as db:
        db.execute("UPDATE meta SET value_json='2' WHERE scope_id=? AND key='storage_revision'", (paths.scope_id,))
    response = post(fixture, "bridge.commit", {"preview_token": preview.json()["preview_token"]}, scope="scope-b")
    assert response.status_code == 409, response.text
    assert "revision changed" in response.text


def test_bridge_successful_job_without_committed_index_stays_pending(fixture, monkeypatch):
    _, _, components, _ = fixture
    package = post(fixture, "bridge.export", {"selected_ids": ["source-a"], "mode": "selected_sources"}).json()
    preview = post(fixture, "bridge.preview", {"package": package, "selected_ids": ["source-a"]}, scope="scope-b").json()
    monkeypatch.setattr(components.write_admission, "require", lambda **kwargs: None)
    committed = post(fixture, "bridge.commit", {"preview_token": preview["preview_token"]}, scope="scope-b")
    assert committed.status_code == 200, committed.text
    plan = committed.json()
    entry = plan["records"][0]
    job = components.jobs.get(entry["receipt"]["receipt_id"], tenant_id="tenant-a")
    running = components.jobs.claim(job.job_id, "synthetic-worker")
    paths = components.storage.scope_paths("tenant-a", "scope-b")
    local_id = entry["local_id"]
    with sqlite3.connect(paths.database) as db:
        db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _record(paths.scope_id, "source-imported", metadata={"content_variant": "source_message", "message_id": local_id, "source_record_id": "source-imported"}))
        db.execute("INSERT INTO tmcra_service_messages(scope_id,message_id,session_id,message_index,role,timestamp,content_sha256,internal_message_id) VALUES(?,?,?,?,?,?,?,?)", (paths.scope_id, local_id, "memory-package-import", 0, "user", "2026-10-15T00:00:00+00:00", "digest", local_id))
    components.jobs.succeed(job.job_id, {"ok": True}, worker_id="synthetic-worker", job_version=running.version)
    response = post(fixture, "bridge.plan", {"plan_id": plan["plan_id"]}, scope="scope-b")
    assert response.status_code == 200, response.text
    assert not response.json()["indexed"]
    assert response.json()["records"][0]["status"] == "accepted_pending"


def test_bridge_preview_token_cannot_cross_tenant_or_scope(fixture):
    package = post(fixture, "bridge.export", {"selected_ids": ["source-a"], "mode": "selected_sources"}).json()
    preview = post(fixture, "bridge.preview", {"package": package, "selected_ids": ["source-a"]}, scope="scope-b").json()
    wrong_scope = post(fixture, "bridge.commit", {"preview_token": preview["preview_token"]}, scope="scope-a")
    assert wrong_scope.status_code == 409, wrong_scope.text
    wrong_tenant = post(fixture, "bridge.commit", {"preview_token": preview["preview_token"]}, scope="scope-a", tenant="tenant-b")
    assert wrong_tenant.status_code == 409, wrong_tenant.text


def test_corrected_evidence_marks_decision_for_review(fixture):
    _, client, _, keys = fixture
    made = post(fixture, "decision.create", {"id": "d", "text": "Original conclusion", "evidence_ids": ["source-a"]})
    assert made.status_code == 200, made.text
    feedback = client.post("/v1/scopes/scope-a/feedback", headers={**headers(keys["tenant-a"]), "Idempotency-Key": "ignore-workflow-source"}, json={"action": "ignore", "rating": "incorrect", "memory_ids": ["source-a"]})
    assert feedback.status_code == 201, feedback.text
    current = client.get("/v1/scopes/scope-a/workflows", headers=headers(keys["tenant-a"]))
    assert current.status_code == 200, current.text
    decision = current.json()["decisions"][0]
    assert decision["needs_review"]
    assert not decision["evidence_available"]
    assert decision["text"] == "Original conclusion"
    blocked_export = post(fixture, "bridge.export", {"selected_ids": ["decision:d"], "mode": "knowledge_only"})
    assert blocked_export.status_code == 409


def test_session_deletion_removes_only_target_session_history(fixture):
    _, client, components, keys = fixture
    first = post(fixture, "task.upsert", {"id": "first", "title": "PRIVATE-SESSION-A", "evidence_ids": ["source-a"]})
    second = post(fixture, "task.upsert", {"id": "second", "title": "KEEP-SESSION-B", "evidence_ids": ["source-b"]})
    assert first.status_code == second.status_code == 200
    deletion = client.request("DELETE", "/v1/scopes/scope-a/sessions/session-a", headers={**headers(keys["tenant-a"]), "Idempotency-Key": "delete-workflow-session", "X-TMCRA-Confirm-Session": "session-a"})
    assert deletion.status_code == 202, deletion.text
    service = bind_workflows(components, "tenant-a", "scope-a")
    current = service.core.snapshot(namespace_key("tenant-a", "scope-a"))
    assert [task["id"] for task in current["tasks"]] == ["second"]
    historical = service.core.snapshot(namespace_key("tenant-a", "scope-a"), first.json()["recorded_at"])
    assert historical["tasks"] == []
    with service.core._connect() as db:
        dump = "\n".join(db.iterdump())
    assert "PRIVATE-SESSION-A" not in dump
    assert "KEEP-SESSION-B" in dump


def test_session_revocation_has_no_50_source_limit(fixture):
    from tmcra_service.workflow_routes import revoke_deleted_session
    _, _, components, _ = fixture
    paths = components.storage.scope_paths("tenant-a", "scope-a")
    with sqlite3.connect(paths.database) as db:
        for i in range(55):
            db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _record(paths.scope_id, f"source-many-{i}", metadata={"content_variant": "source_message", "source_record_id": f"source-many-{i}", "session_id": "session-many"}))
    bound = post(fixture, "task.upsert", {"id": "many", "title": "PRIVATE-LAST-SOURCE", "evidence_ids": ["source-many-54"]})
    assert bound.status_code == 200, bound.text
    result = revoke_deleted_session(components, "tenant-a", "scope-a", "session-many")
    assert result["source_count"] == 55
    service = bind_workflows(components, "tenant-a", "scope-a")
    assert service.core.snapshot(namespace_key("tenant-a", "scope-a"))["tasks"] == []


def test_revocation_authorization_precedes_preview_and_irreversible_commit(fixture):
    _, _, components, keys = fixture
    package = post(fixture, "bridge.export_revocations", {"selected_ids": ["source-a"]}).json()
    lowkey = components.auth.create_key("tenant-a", {"memory:read", "memory:write"}).api_key
    context = components.auth.authenticate(keys["tenant-a"])
    scoped = components.auth.create_scope_token(context, permissions={"memory:read", "memory:write"}, scope_names={"scope-b"}, label="test", subject="test-user", expires_at=time.time() + 3600).access_token
    namespace = namespace_key("tenant-a", "scope-b")
    service = bind_workflows(components, "tenant-a", "scope-b")

    def stored_state():
        with sqlite3.connect(service.bridge.path) as db:
            return {table: db.execute(f"SELECT * FROM {table} WHERE namespace=? ORDER BY 1", (namespace,)).fetchall()
                    for table in ("bridge_previews", "bridge_tombstones", "bridge_items", "bridge_plans", "bridge_commits", "bridge_revisions")}

    initial = stored_state()
    for key in (lowkey, scoped):
        denied = post(fixture, "bridge.preview", {"package": package, "selected_ids": ["source-a"]}, scope="scope-b", key=key)
        assert denied.status_code == 403, denied.text
        assert stored_state() == initial
    highpreview = post(fixture, "bridge.preview", {"package": package, "selected_ids": ["source-a"]}, scope="scope-b")
    assert highpreview.status_code == 200, highpreview.text
    approved = stored_state()
    for key in (lowkey, scoped):
        denied = post(fixture, "bridge.commit", {"preview_token": highpreview.json()["preview_token"]}, scope="scope-b", key=key)
        assert denied.status_code == 403, denied.text
        assert stored_state() == approved
    committed = post(fixture, "bridge.commit", {"preview_token": highpreview.json()["preview_token"]}, scope="scope-b")
    assert committed.status_code == 200, committed.text
    assert committed.json()["records"][0]["record"]["kind"] == "revocation"
    final = stored_state()
    assert len(final["bridge_tombstones"]) == 1
    for key in (lowkey, scoped):
        for action, payload in (("bridge.plan", {"plan_id": committed.json()["plan_id"]}),
                                ("bridge.commit", {"preview_token": highpreview.json()["preview_token"]})):
            denied = post(fixture, action, payload, scope="scope-b", key=key)
            assert denied.status_code == 403, denied.text
            assert stored_state() == final


def test_memory_write_key_can_import_ordinary_source_without_delete(fixture, monkeypatch):
    _, _, components, _ = fixture
    package = post(fixture, "bridge.export", {"selected_ids": ["source-a"], "mode": "selected_sources"}).json()
    lowkey = components.auth.create_key("tenant-a", {"memory:read", "memory:write"}).api_key
    preview = post(fixture, "bridge.preview", {"package": package, "selected_ids": ["source-a"]}, scope="scope-b", key=lowkey)
    assert preview.status_code == 200, preview.text
    monkeypatch.setattr(components.write_admission, "require", lambda **kwargs: None)
    commit = post(fixture, "bridge.commit", {"preview_token": preview.json()["preview_token"]}, scope="scope-b", key=lowkey)
    assert commit.status_code == 200, commit.text
    assert commit.json()["records"][0]["status"] == "accepted_pending"
    assert commit.json()["records"][0]["record"]["kind"] == "source"


def _failed_import(fixture, monkeypatch):
    _, _, components, _ = fixture
    package = post(fixture, "bridge.export", {"selected_ids": ["source-a"], "mode": "selected_sources"}).json()
    preview = post(fixture, "bridge.preview", {"package": package, "selected_ids": ["source-a"]}, scope="scope-b").json()
    monkeypatch.setattr(components.write_admission, "require", lambda **kwargs: None)
    commit = post(fixture, "bridge.commit", {"preview_token": preview["preview_token"]}, scope="scope-b")
    assert commit.status_code == 200, commit.text
    plan = commit.json()
    entry = plan["records"][0]
    job_id = entry["receipt"]["receipt_id"]
    running = components.jobs.claim(job_id, "synthetic-worker")
    failed = components.jobs.fail(job_id, "synthetic-failure", worker_id="synthetic-worker", job_version=running.version)
    return plan, entry, failed


def test_retry_requires_confirmation_scope_and_native_recovery_audit(fixture, monkeypatch):
    _, _, components, keys = fixture
    plan, entry, failed = _failed_import(fixture, monkeypatch)
    request = {"plan_id": plan["plan_id"], "item_id": entry["item_id"], "confirmed": True}
    no_confirmation = post(fixture, "bridge.retry_failed", {**request, "confirmed": False}, scope="scope-b")
    assert no_confirmation.status_code in {400, 409}, no_confirmation.text
    read_key = components.auth.create_key("tenant-a", {"memory:read"}).api_key
    assert post(fixture, "bridge.retry_failed", request, scope="scope-b", key=read_key).status_code == 403
    other_scope = post(fixture, "bridge.retry_failed", request)
    assert other_scope.status_code == 409, other_scope.text
    other_tenant = post(fixture, "bridge.retry_failed", request, tenant="tenant-b")
    assert other_tenant.status_code == 409, other_tenant.text
    # This synthetic failed job has no durable Writer journals; the real native
    # endpoint must reject rather than quietly resubmitting a new ingest job.
    rejected = post(fixture, "bridge.retry_failed", request, scope="scope-b", op="retry-no-artifacts")
    assert rejected.status_code == 409, rejected.text
    assert components.jobs.get(failed.job_id, tenant_id="tenant-a").state == "failed"
    with sqlite3.connect(components.settings.control_db) as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def test_explicit_retry_uses_real_job_state_machine_and_committed_projection(fixture, monkeypatch):
    _, _, components, keys = fixture
    plan, entry, failed = _failed_import(fixture, monkeypatch)
    # Only the storage adapter's offline writer-artifact audit result is a
    # fixture. Auth, HTTP handlers, resume authorization fingerprint, job state
    # machine, same-job retry and graph/receipt resolution remain real.
    monkeypatch.setattr(components.storage, "audit_scope_recovery", lambda **kwargs: {
        "integrity_ok": True, "failed_operation_ids": [failed.job_id]})
    monkeypatch.setattr(components.storage, "ingest_recovery_plan", lambda **kwargs: {
        "resumable": True, "mode": "complete_writer_artifacts", "parallel_safe": True,
        "external_api_calls_expected": False, "deterministic_local_repair": True})
    request = {"plan_id": plan["plan_id"], "item_id": entry["item_id"], "confirmed": True}
    first = post(fixture, "bridge.retry_failed", request, scope="scope-b", op="explicit-retry-1")
    assert first.status_code == 200, first.text
    pending = components.jobs.get(failed.job_id, tenant_id="tenant-a")
    assert pending.state == "pending"
    assert pending.version > failed.version
    duplicate = post(fixture, "bridge.retry_failed", request, scope="scope-b", op="explicit-retry-1")
    assert duplicate.status_code == 200, duplicate.text
    assert components.jobs.get(failed.job_id, tenant_id="tenant-a").version == pending.version
    extra = post(fixture, "bridge.retry_failed", request, scope="scope-b", op="explicit-retry-2")
    assert extra.status_code == 409, extra.text
    assert components.jobs.get(failed.job_id, tenant_id="tenant-a").version == pending.version
    # Simulate only worker output: persist the imported source and a synthetic
    # committed legacy graph manifest. This tests completion boundary, not model
    # extraction quality or embedding-index search quality.
    paths = components.storage.scope_paths("tenant-a", "scope-b")
    local_id = entry["local_id"]
    with sqlite3.connect(paths.database) as db:
        db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", _record(paths.scope_id, "source-retried", metadata={"content_variant": "source_message", "message_id": local_id, "source_record_id": "source-retried"}))
        db.execute("INSERT INTO tmcra_service_messages(scope_id,message_id,session_id,message_index,role,timestamp,content_sha256,internal_message_id) VALUES(?,?,?,?,?,?,?,?)", (paths.scope_id, local_id, "memory-package-import", 0, "user", "2026-10-15T00:00:00+00:00", "digest", local_id))
    paths.indexes.mkdir(parents=True, exist_ok=True)
    index = paths.indexes / "synthetic-fixture.index"
    index.write_bytes(b"synthetic committed projection fixture, not model output")
    paths.active_index.write_text(json.dumps({"scope_id": paths.scope_id, "database": str(paths.database), "index": str(index), "job_id": failed.job_id}))
    running = components.jobs.claim(failed.job_id, "synthetic-worker-2")
    components.jobs.succeed(failed.job_id, {"ok": True}, worker_id="synthetic-worker-2", job_version=running.version)
    observed = post(fixture, "bridge.plan", {"plan_id": plan["plan_id"]}, scope="scope-b")
    assert observed.status_code == 200, observed.text
    assert observed.json()["indexed"]
    assert observed.json()["records"][0]["status"] == "applied"
    assert observed.json()["records"][0]["receipt"]["receipt_id"] == failed.job_id
    with sqlite3.connect(components.settings.control_db) as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def test_plugin_stdio_to_real_fastapi_workflow_chain(fixture, monkeypatch, tmp_path):
    """Real MCP stdio -> real HTTP -> SQLite/ordinary ingest queue; no workers."""
    import os
    import queue
    import shutil
    import socket
    import subprocess
    import threading
    import uvicorn

    plugin = Path(__file__).resolve().parents[3] / "07-tmcra-codex-plugins" / "tmcra-memory"
    node = shutil.which("node") or str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node")
    if not Path(node).is_file():
        pytest.skip("Node.js is not installed; plugin stdio integration requires Node")
    app, _, components, keys = fixture
    monkeypatch.setattr(components.write_admission, "require", lambda **kwargs: None)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    # Lifespan disabled deliberately: this test uses real handlers/queue but
    # must not start production model workers or a background scheduler.
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    worker.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    config = tmp_path / "mcp-config.json"
    def write_config(scope):
        config.write_text(json.dumps({"baseUrl": f"http://127.0.0.1:{port}", "apiKey": keys["tenant-a"], "projectScope": scope, "timeoutMs": 5000}))
        config.chmod(0o600)
    write_config("scope-a")
    env = {k:v for k,v in os.environ.items() if not k.startswith(("TMCRA_", "CLAUDE_PLUGIN_OPTION_"))}
    env.update({"TMCRA_CONFIG_FILE": str(config), "TMCRA_LOCAL_BINDING_FILE": str(tmp_path / "absent-binding"), "TMCRA_LOCAL_PROVIDER_CONFIG": str(tmp_path / "absent-providers"), "PLUGIN_DATA": str(tmp_path / "plugin-data"), "TMCRA_MEMORY_STATE_DIR": str(tmp_path / "controls")})
    child = subprocess.Popen([node, str(plugin / "scripts/mcp_server.mjs")], cwd=plugin, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    responses = queue.Queue()
    reader = threading.Thread(target=lambda: [responses.put(json.loads(line)) for line in child.stdout], daemon=True)
    reader.start()
    sequence = 0
    elicited = []
    def send(value):
        child.stdin.write(json.dumps({"jsonrpc":"2.0", **value}) + "\n")
        child.stdin.flush()
    def rpc(method, params, *, accept=True):
        nonlocal sequence
        sequence += 1
        request_id = sequence
        send({"id":request_id,"method":method,"params":params})
        while True:
            message = responses.get(timeout=10)
            if message.get("method") == "elicitation/create":
                elicited.append(message)
                send({"id":message["id"],"result":{"action":"accept" if accept else "decline", "content":{"confirm":accept}}})
                continue
            assert message.get("id") == request_id, message
            assert "error" not in message, message
            return message["result"]
    def workflow(operation="snapshot", *, action=None, payload=None, accept=True, **extra):
        args = {"session_id":"real-http-mcp-session", "project_path":str(tmp_path), "operation":operation, **extra}
        if action is not None:
            args.update({"action":action,"payload":payload,"operation_id":"mcp-"+str(sequence+1)})
        return rpc("tools/call", {"name":"tmcra_workflows","arguments":args}, accept=accept)
    try:
        rpc("initialize", {"protocolVersion":"2025-11-25", "capabilities":{"elicitation":{"form":{}}}, "clientInfo":{"name":"real-http-fixture","version":"1"}})
        tools = rpc("tools/list", {})["tools"]
        assert any(tool["name"] == "tmcra_workflows" for tool in tools)
        task_result = workflow("execute", action="task.upsert", payload={"id":"mcp-task","title":"Actual MCP task","goal":"check queue","evidence_ids":["source-a"]})
        assert not task_result["isError"], task_result
        when = task_result["structuredContent"]["data"]["recorded_at"]
        assert not workflow("execute", action="task.receipt", payload={"id":"mcp-task","summary":"Agent report only","outcome":"passed"})["isError"]
        assert not workflow("execute", action="decision.create", payload={"id":"mcp-decision","text":"Prefer explicit queue receipts","evidence_ids":["source-a"]})["isError"]
        assert not workflow("execute", action="reminder.upsert", payload={"id":"mcp-reminder","title":"Check real job state","reason":"Queued is not indexed","events":["project_open"]})["isError"]
        evaluated = workflow("execute", action="reminder.evaluate", payload={"event":"project_open"})
        assert not evaluated["isError"], evaluated
        assert len(evaluated["structuredContent"]["data"]["reminders"]) == 1
        current = workflow()["structuredContent"]["data"]
        assert current["tasks"][0]["verification_state"] == "reported"
        assert not current["tasks"][0]["verified"]
        historical = workflow("timeline", as_of=when)["structuredContent"]["data"]
        assert historical["decisions"] == []
        rejected = workflow("execute", action="record.delete", payload={"kind":"task","id":"mcp-task"}, accept=False)
        assert rejected["isError"]
        assert workflow()["structuredContent"]["data"]["tasks"][0]["id"] == "mcp-task"
        exported = workflow("execute", action="bridge.export", payload={"selected_ids":["source-a"],"mode":"selected_sources"})
        assert not exported["isError"], exported
        package = exported["structuredContent"]["data"]
        assert package["items"][0]["text"] == "source-a"
        write_config("scope-b")
        preview = workflow("execute", action="bridge.preview", payload={"package":package,"selected_ids":["source-a"]})
        assert not preview["isError"], preview
        token = preview["structuredContent"]["data"]["preview_token"]
        committed = workflow("execute", action="bridge.commit", payload={"preview_token":token})
        assert not committed["isError"], committed
        plan = committed["structuredContent"]["data"]
        assert not plan["indexed"]
        assert plan["records"][0]["status"] == "accepted_pending"
        job = components.jobs.get(plan["records"][0]["receipt"]["receipt_id"], tenant_id="tenant-a")
        assert job.state == "pending" and job.scope_name == "scope-b"
        assert job.payload["job_type"] == "ingest"
        assert len(elicited) == 3  # declined delete, export, explicit commit
        assert "source-a" in elicited[-1]["params"]["message"]
        assert keys["tenant-a"] not in json.dumps(elicited)
    finally:
        child.stdin.close()
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=2)
        server.should_exit = True
        worker.join(timeout=5)
        listener.close()
