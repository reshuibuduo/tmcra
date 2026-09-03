from __future__ import annotations

import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from tmcra_service.app import create_app
from tmcra_service.commercial import CommercialControl
from tmcra_service.control_db import ControlDB
from tmcra_service.settings import ServiceSettings
from tmcra_service.writer import UserProviderWriterClient
from tmcra_service.user_provider_client import (
    UserProviderBrokerClient,
    normalize_user_provider_execution,
)
from tmcra_service.user_provider_tasks import UserProviderTaskStore


class UserProviderTaskApiTests(unittest.TestCase):
    def settings(self, root: Path) -> ServiceSettings:
        files = {
            "writer_env": root / "writer.env",
            "native_harness": root / "harness.py",
            "node_model": root / "node.pt",
            "path_model": root / "path.pt",
            "checkpoint": root / "checkpoint.pt",
        }
        for path in files.values():
            path.write_text("test", encoding="utf-8")
        (root / "tmcra_v4_batch_writer.py").write_text("test", encoding="utf-8")
        production = root / "tmcra_service" / "writer.py"
        production.parent.mkdir()
        production.write_text("test", encoding="utf-8")
        return ServiceSettings(
            state_dir=root / "state",
            control_db=root / "state" / "control.sqlite3",
            bind_host="127.0.0.1",
            bind_port=2009,
            public_base_url="https://example.invalid",
            v4_root=root,
            integrated_repo=root,
            writer_env=files["writer_env"],
            embedding_model=root,
            native_harness=files["native_harness"],
            node_model=files["node_model"],
            path_model=files["path_model"],
            checkpoint=files["checkpoint"],
            cross_model=root,
            device="cpu",
            graph_device="cpu",
            request_body_limit=1024 * 1024,
            provider_lease_seconds=60,
            provider_key_concurrency=1,
            disk_free_min_bytes=1,
            startup_preflight_mode="off",
        )

    def test_task_is_bound_to_the_submitting_credential_and_fenced_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(self.settings(Path(directory)))
            components = app.state.components
            permissions = {"memory:read", "memory:write", "memory:consolidate"}
            components.auth.set_tenant_scopes("tenant-a", permissions)
            owner = components.auth.create_key("tenant-a", permissions)
            other = components.auth.create_key("tenant-a", permissions)
            task = components.user_provider_tasks.create(
                tenant_id="tenant-a",
                scope_name="default",
                auth_key_id=owner.key_id,
                job_id="job-user-provider-0001",
                stage_id="job-user-provider-0001:writer:attempt:1",
                task_stage="writer",
                operation="batch_flash",
                request={
                    "schema_version": "tmcra.openai-compatible-request.1",
                    "messages": [
                        {"role": "system", "content": "Return JSON."},
                        {"role": "user", "content": "{}"},
                    ],
                    "temperature": 0,
                    "max_tokens": 128,
                    "response_format": {"type": "json_object"},
                },
            )
            with TestClient(app) as client:
                other_claim = client.post(
                    "/v1/provider-tasks/claim",
                    headers={"Authorization": f"Bearer {other.api_key}"},
                    json={"stage": "writer"},
                )
                self.assertEqual(other_claim.status_code, 200)
                self.assertIsNone(other_claim.json()["task"])

                owner_headers = {"Authorization": f"Bearer {owner.api_key}"}
                claimed = client.post(
                    "/v1/provider-tasks/claim",
                    headers=owner_headers,
                    json={"stage": "writer"},
                )
                self.assertEqual(claimed.status_code, 200)
                lease = claimed.json()["task"]
                self.assertEqual(lease["task_id"], task.task_id)
                self.assertNotIn("tenant_id", lease)
                self.assertNotIn("auth_key_id", lease)

                started = client.post(
                    f"/v1/provider-tasks/{task.task_id}/started",
                    headers=owner_headers,
                    json={"lease_token": lease["lease_token"]},
                )
                self.assertEqual(started.status_code, 200)
                self.assertEqual(started.json()["state"], "running")

                completed_body = {
                    "lease_token": lease["lease_token"],
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "output": {"schema_version": "answer.1", "ok": True},
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 3,
                        "total_tokens": 13,
                        "cache_hit_tokens": 0,
                        "cache_miss_tokens": 10,
                    },
                    "provider_request_id": "request-provider-1",
                }
                completed = client.post(
                    f"/v1/provider-tasks/{task.task_id}/complete",
                    headers=owner_headers,
                    json=completed_body,
                )
                self.assertEqual(completed.status_code, 200)
                self.assertEqual(completed.json()["state"], "completed")
                self.assertFalse(completed.json()["idempotent_replay"])

                replay = client.post(
                    f"/v1/provider-tasks/{task.task_id}/complete",
                    headers=owner_headers,
                    json=completed_body,
                )
                self.assertEqual(replay.status_code, 200)
                self.assertTrue(replay.json()["idempotent_replay"])

                wrong_lease = client.post(
                    f"/v1/provider-tasks/{task.task_id}/heartbeat",
                    headers=owner_headers,
                    json={"lease_token": "x" * 40},
                )
                self.assertEqual(wrong_lease.status_code, 409)

            stored = UserProviderTaskStore(components.database).get(task.task_id)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.state, "completed")
            self.assertEqual(stored.output, completed_body["output"])

    def test_ingest_binds_both_local_execution_stages_to_the_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(self.settings(Path(directory)))
            components = app.state.components
            permissions = {"memory:read", "memory:write", "memory:consolidate"}
            components.auth.set_tenant_scopes("tenant-a", permissions)
            credential = components.auth.create_key("tenant-a", permissions)
            with TestClient(app) as client:
                response = client.post(
                    "/v1/scopes/default/ingest",
                    headers={
                        "Authorization": f"Bearer {credential.api_key}",
                        "Idempotency-Key": "local-provider-ingest-0001",
                        "X-TMCRA-Writer-Execution": "user-provider",
                        "X-TMCRA-Organizer-Execution": "user-provider",
                    },
                    json={
                        "session_id": "session-local-provider",
                        "slow_policy": "force",
                        "messages": [
                            {
                                "message_id": "message-local-provider",
                                "role": "user",
                                "content": "Remember this locally routed fact.",
                                "timestamp": "2026-09-04T00:00:00Z",
                            }
                        ],
                    },
                )
                self.assertEqual(response.status_code, 202, response.text)
                job = components.jobs.get(
                    response.json()["job_id"], tenant_id="tenant-a"
                )
            self.assertIsNotNone(job)
            self.assertEqual(
                job.payload["_provider_execution"],  # type: ignore[union-attr]
                {
                    "writer": "user-provider",
                    "organizer": "user-provider",
                    "auth_key_id": credential.key_id,
                },
            )

    def test_write_scoped_device_token_can_execute_its_ingest_organizer_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(self.settings(Path(directory)))
            components = app.state.components
            tenant_permissions = {
                "memory:read",
                "memory:write",
                "memory:consolidate",
            }
            components.auth.set_tenant_scopes("tenant-a", tenant_permissions)
            credential = components.auth.create_key(
                "tenant-a", {"memory:read", "memory:write"}
            )
            task = components.user_provider_tasks.create(
                tenant_id="tenant-a",
                scope_name="default",
                auth_key_id=credential.key_id,
                job_id="job-user-provider-organizer-0001",
                stage_id="job-user-provider-organizer-0001:slow_graph:attempt:1",
                task_stage="organizer",
                operation="slow_graph_flash",
                request={
                    "schema_version": "tmcra.openai-compatible-request.1",
                    "messages": [
                        {"role": "system", "content": "Return JSON."},
                        {"role": "user", "content": "{}"},
                    ],
                    "temperature": 0,
                    "max_tokens": 128,
                    "response_format": {"type": "json_object"},
                },
            )
            with TestClient(app) as client:
                response = client.post(
                    "/v1/provider-tasks/claim",
                    headers={"Authorization": f"Bearer {credential.api_key}"},
                    json={"stage": "organizer"},
                )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["task"]["task_id"], task.task_id)

    def test_write_scoped_device_token_can_request_only_local_consolidation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(self.settings(Path(directory)))
            components = app.state.components
            tenant_permissions = {
                "memory:read",
                "memory:write",
                "memory:consolidate",
            }
            components.auth.set_tenant_scopes("tenant-a", tenant_permissions)
            credential = components.auth.create_key(
                "tenant-a", {"memory:read", "memory:write"}
            )
            common_headers = {
                "Authorization": f"Bearer {credential.api_key}",
                "Idempotency-Key": "local-consolidate-write-token-0001",
            }
            with TestClient(app) as client:
                server_route = client.post(
                    "/v1/scopes/default/consolidate",
                    headers=common_headers,
                )
                local_route = client.post(
                    "/v1/scopes/default/consolidate",
                    headers={
                        **common_headers,
                        "Idempotency-Key": "local-consolidate-write-token-0002",
                        "X-TMCRA-Organizer-Execution": "user-provider",
                    },
                )
                self.assertEqual(server_route.status_code, 403, server_route.text)
                self.assertEqual(local_route.status_code, 202, local_route.text)
                job = components.jobs.get(
                    local_route.json()["job_id"], tenant_id="tenant-a"
                )
            self.assertIsNotNone(job)
            self.assertEqual(
                job.payload["_provider_execution"],  # type: ignore[union-attr]
                {
                    "organizer": "user-provider",
                    "auth_key_id": credential.key_id,
                },
            )

    def test_broker_waits_for_local_executor_and_returns_structured_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = ControlDB(Path(directory) / "control.sqlite3")
            broker = UserProviderBrokerClient(
                control_db=database.path,
                tenant_id="tenant-a",
                scope_name="scope-a",
                auth_key_id="key-a",
                job_id="job-a",
                stage_id="job-a:writer:attempt:1",
                task_stage="writer",
                timeout=3,
                max_tokens=128,
            )
            store = UserProviderTaskStore(database, lease_seconds=30)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    broker.complete_prompt,
                    system_prompt="Return JSON.",
                    payload={"message": "hello"},
                    operation="batch_flash",
                )
                claimed = None
                deadline = time.monotonic() + 2
                while claimed is None and time.monotonic() < deadline:
                    claimed = store.claim_next(
                        tenant_id="tenant-a",
                        auth_key_id="key-a",
                        task_stage="writer",
                        scope_allowed=lambda scope: scope == "scope-a",
                    )
                    if claimed is None:
                        time.sleep(0.01)
                self.assertIsNotNone(claimed)
                task, lease_token = claimed  # type: ignore[misc]
                store.start(
                    task.task_id,
                    tenant_id="tenant-a",
                    auth_key_id="key-a",
                    lease_token=lease_token,
                )
                store.complete(
                    task.task_id,
                    tenant_id="tenant-a",
                    auth_key_id="key-a",
                    lease_token=lease_token,
                    provider="openai-compatible",
                    model="test-model",
                    output={"accepted": True},
                    usage={
                        "input_tokens": 8,
                        "output_tokens": 2,
                        "total_tokens": 10,
                        "cache_hit_tokens": 0,
                        "cache_miss_tokens": 8,
                    },
                    provider_request_id="provider-request-a",
                )
                output, metadata = future.result(timeout=2)
            self.assertEqual(output, {"accepted": True})
            self.assertEqual(metadata["provider"], "openai-compatible")
            self.assertEqual(metadata["model"], "test-model")
            self.assertEqual(metadata["execution_route"], "user-provider")

    def test_scope_deletion_purges_provider_task_prompt_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = ControlDB(Path(directory) / "control.sqlite3")
            store = UserProviderTaskStore(database)
            task = store.create(
                tenant_id="tenant-a",
                scope_name="scope-a",
                auth_key_id="key-a",
                job_id="job-provider-delete-0001",
                stage_id="job-provider-delete-0001:writer:attempt:1",
                task_stage="writer",
                operation="batch_flash",
                request={
                    "schema_version": "tmcra.openai-compatible-request.1",
                    "messages": [
                        {"role": "system", "content": "Return JSON."},
                        {"role": "user", "content": "sensitive prompt"},
                    ],
                    "temperature": 0,
                    "max_tokens": 128,
                    "response_format": {"type": "json_object"},
                },
            )
            CommercialControl(database).complete_scope_deletion(
                "tenant-a",
                "scope-a",
                "job-delete-scope-0001",
                scope_id="scope-id-a",
            )
            self.assertIsNone(store.get(task.task_id))

    def test_combined_writer_and_organizer_execution_is_stage_scoped(self) -> None:
        value = {
            "writer": "user-provider",
            "organizer": "user-provider",
            "auth_key_id": "key-a",
        }
        self.assertEqual(
            normalize_user_provider_execution(value, stage="writer"),
            {"writer": "user-provider", "auth_key_id": "key-a"},
        )
        self.assertEqual(
            normalize_user_provider_execution(value, stage="organizer"),
            {"organizer": "user-provider", "auth_key_id": "key-a"},
        )

    def test_writer_reuses_the_pinned_reconciliation_prompt_without_core_changes(self) -> None:
        calls = []

        class FakeDeepSeekBatchClient:
            def reconcile(self, payload):
                return self._complete(
                    model=self.model,
                    system_prompt="pinned reconciliation prompt",
                    payload=payload,
                    stage="reconciliation_pro",
                )

        class FakeBroker:
            model = "client-selected"
            provider = "user-provider"

            def complete_prompt(self, **kwargs):
                calls.append(kwargs)
                return {"decision": "insert"}, {"status": "completed"}

        v4 = type(
            "FakeV4",
            (),
            {
                "DeepSeekBatchClient": FakeDeepSeekBatchClient,
                "BATCH_SYSTEM_PROMPT": "batch prompt",
                "batch_response_json_schema": staticmethod(lambda payload: {"type": "object"}),
            },
        )
        client = UserProviderWriterClient(v4=v4, broker=FakeBroker())
        result = client.reconcile({"candidate": "memory-a"})
        self.assertEqual(result[0], {"decision": "insert"})
        self.assertEqual(calls[0]["system_prompt"], "pinned reconciliation prompt")
        self.assertEqual(calls[0]["operation"], "reconciliation_pro")


if __name__ == "__main__":
    unittest.main()
