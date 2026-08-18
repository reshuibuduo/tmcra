from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tmcra_service.app import create_app
from tmcra_service.settings import ServiceSettings


class ServiceAppTests(unittest.TestCase):
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
            provider_lease_seconds=30,
            provider_key_concurrency=1,
            disk_free_min_bytes=1,
            startup_preflight_mode="off",
        )

    def test_health_auth_and_idempotent_ingest_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            app = create_app(settings)
            components = app.state.components
            components.auth.set_tenant_scopes(
                "tenant-a", {"memory:read", "memory:write", "memory:consolidate"}
            )
            key = components.auth.create_key("tenant-a").api_key
            with TestClient(app) as client:
                self.assertEqual(client.get("/healthz").status_code, 200)
                readiness = client.get("/readyz")
                self.assertNotIn(str(settings.state_dir), readiness.text)
                self.assertNotIn("control.sqlite3", readiness.text)
                self.assertIsInstance(readiness.json()["checks"]["control_db"], bool)
                oversized = client.post(
                    "/v1/scopes/default/ingest",
                    headers={"Content-Length": str(settings.request_body_limit + 1)},
                    content=b"{}",
                )
                self.assertEqual(oversized.status_code, 413)
                self.assertEqual(
                    client.post(
                        "/v1/scopes/default/ingest",
                        headers={"Idempotency-Key": "request-0001"},
                        json={
                            "session_id": "session-a",
                            "messages": [
                                {
                                    "message_id": "message-a",
                                    "role": "user",
                                    "content": "hello",
                                    "timestamp": "2026-07-14T00:00:00Z",
                                }
                            ],
                        },
                    ).status_code,
                    401,
                )
                headers = {
                    "Authorization": f"Bearer {key}",
                    "Idempotency-Key": "request-0001",
                }
                first = client.post(
                    "/v1/scopes/default/ingest",
                    headers=headers,
                    json={
                        "session_id": "session-a",
                        "messages": [
                            {
                                "message_id": "message-a",
                                "role": "user",
                                "content": "hello",
                                "timestamp": "2026-07-14T00:00:00Z",
                            }
                        ],
                    },
                )
                second = client.post(
                    "/v1/scopes/default/ingest",
                    headers=headers,
                    json={
                        "session_id": "session-a",
                        "messages": [
                            {
                                "message_id": "message-a",
                                "role": "user",
                                "content": "hello",
                                "timestamp": "2026-07-14T00:00:00Z",
                            }
                        ],
                    },
                )
                self.assertEqual(first.status_code, 202)
                self.assertEqual(second.status_code, 202)
                self.assertEqual(first.json()["job_id"], second.json()["job_id"])
                self.assertFalse(first.json()["idempotent_replay"])
                self.assertTrue(second.json()["idempotent_replay"])
                self.assertEqual(
                    first.json()["consistency_contract"]["visible_after_job_id"],
                    first.json()["job_id"],
                )
                usage = client.get(
                    "/v1/usage/costs?scope_name=default",
                    headers={"Authorization": f"Bearer {key}"},
                )
                self.assertEqual(usage.status_code, 200)
                self.assertEqual(usage.json()["scope_name"], "default")
                self.assertEqual(usage.json()["currency"], "CNY")
                self.assertEqual(
                    usage.json()["ledger_coverage"], "registered_calls_only"
                )

    def test_openapi_and_error_contract_are_sdk_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            app = create_app(settings)
            components = app.state.components
            components.auth.set_tenant_scopes(
                "tenant-a", {"memory:read", "memory:write", "memory:consolidate"}
            )
            key = components.auth.create_key("tenant-a").api_key
            with TestClient(app) as client:
                schema = client.get("/openapi.json").json()
                self.assertEqual(
                    schema["components"]["securitySchemes"]["TMCRAApiKey"]["scheme"],
                    "bearer",
                )
                self.assertEqual(
                    schema["paths"]["/v1/scopes/{scope_name}/recall"]["post"]["operationId"],
                    "recallMemory",
                )
                response = client.post(
                    "/v1/scopes/default/ingest",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Idempotency-Key": "request-invalid-0001",
                    },
                    json={"session_id": "session-a", "messages": []},
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "validation_error")
                self.assertEqual(
                    response.json()["error"]["request_id"],
                    response.headers["x-request-id"],
                )


if __name__ == "__main__":
    unittest.main()
