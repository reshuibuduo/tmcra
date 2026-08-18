from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tmcra_service.app import create_app
from tmcra_service.control_db import ControlDB
from tmcra_service.jobs import JobStore
from tmcra_service.settings import ServiceSettings


def _record_usage(
    database: ControlDB,
    jobs: JobStore,
    *,
    tenant_id: str,
    scope_name: str,
    call_id: str,
    cost_micro_cny: int,
    raw_tokens: int,
) -> None:
    jobs.record_provider_call(
        tenant_id,
        "test-provider",
        "test-model",
        scope_name=scope_name,
        call_id=call_id,
        status="completed",
        input_tokens=raw_tokens,
        output_tokens=1,
        total_tokens=raw_tokens + 1,
        cost_micro_cny=cost_micro_cny,
        usage_state="complete",
    )
    database.record_committed_source_events(
        tenant_id,
        scope_name,
        1,
        raw_token_estimate=raw_tokens,
        user_turns=1,
    )


class UsageCostScopePrefixStoreTests(unittest.TestCase):
    def test_prefix_filter_escapes_like_metacharacters_and_tenant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = ControlDB(Path(directory) / "control.sqlite3")
            jobs = JobStore(database)
            records = (
                ("tenant-a", "family_1-main", "under-target", 11, 101),
                ("tenant-a", "familyX1-main", "under-spoof", 12, 102),
                ("tenant-a", "pct%prefix-main", "percent-target", 21, 201),
                ("tenant-a", "pct-any-prefix-main", "percent-spoof", 22, 202),
                ("tenant-a", "slash\\prefix-main", "slash-target", 31, 301),
                ("tenant-a", "slashprefix-main", "slash-spoof", 32, 302),
                ("tenant-b", "family_1-main", "other-tenant", 1000, 1001),
            )
            for tenant_id, scope_name, call_id, cost, raw_tokens in records:
                _record_usage(
                    database,
                    jobs,
                    tenant_id=tenant_id,
                    scope_name=scope_name,
                    call_id=call_id,
                    cost_micro_cny=cost,
                    raw_tokens=raw_tokens,
                )

            cases = (
                ("family_1-", 11, 101),
                ("pct%prefix-", 21, 201),
                ("slash\\prefix-", 31, 301),
            )
            for prefix, expected_cost, expected_tokens in cases:
                with self.subTest(prefix=prefix):
                    summary = jobs.usage_cost_summary(
                        "tenant-a", scope_prefix=prefix
                    )
                    self.assertIsNone(summary["scope_name"])
                    self.assertEqual(summary["scope_prefix"], prefix)
                    self.assertEqual(summary["calls"]["registered_call_count"], 1)
                    self.assertEqual(
                        summary["calls"]["known_cost_micro_cny"], expected_cost
                    )
                    self.assertEqual(summary["source"]["scope_count"], 1)
                    self.assertEqual(
                        summary["source"]["ingested_raw_token_estimate"],
                        expected_tokens,
                    )

    def test_scope_name_and_scope_prefix_are_mutually_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            jobs = JobStore(ControlDB(Path(directory) / "control.sqlite3"))
            with self.assertRaisesRegex(ValueError, "mutually exclusive"):
                jobs.usage_cost_summary(
                    "tenant-a",
                    scope_name="scope-a",
                    scope_prefix="scope-",
                )


class UsageCostScopePrefixApiTests(unittest.TestCase):
    @staticmethod
    def _settings(root: Path) -> ServiceSettings:
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

    def test_prefix_query_requires_managing_api_key_and_tenant_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(self._settings(Path(directory)))
            components = app.state.components
            components.auth.set_tenant_scopes(
                "tenant-a", {"memory:read", "tokens:manage"}
            )
            manager_key = components.auth.create_key("tenant-a").api_key
            read_key = components.auth.create_key(
                "tenant-a", scopes={"memory:read"}
            ).api_key
            scoped = components.auth.create_scope_token(
                components.auth.authenticate(manager_key),
                permissions=["memory:read"],
                scope_prefixes=["personal-a-"],
                label="personal terminal",
                subject="personal-a",
                expires_at=time.time() + 3600,
            ).access_token

            for scope_name, call_id, cost, raw_tokens in (
                ("personal-a-one", "personal-one", 10, 100),
                ("personal-a-two", "personal-two", 20, 200),
                ("other-scope", "other", 30, 300),
            ):
                _record_usage(
                    components.database,
                    components.jobs,
                    tenant_id="tenant-a",
                    scope_name=scope_name,
                    call_id=call_id,
                    cost_micro_cny=cost,
                    raw_tokens=raw_tokens,
                )
            _record_usage(
                components.database,
                components.jobs,
                tenant_id="tenant-b",
                scope_name="personal-a-one",
                call_id="other-tenant",
                cost_micro_cny=1000,
                raw_tokens=1000,
            )

            manager_headers = {"Authorization": f"Bearer {manager_key}"}
            read_headers = {"Authorization": f"Bearer {read_key}"}
            scoped_headers = {"Authorization": f"Bearer {scoped}"}
            with TestClient(app) as client:
                prefix = client.get(
                    "/v1/usage/costs?scope_prefix=personal-a-",
                    headers=manager_headers,
                )
                self.assertEqual(prefix.status_code, 200, prefix.text)
                self.assertEqual(prefix.json()["scope_prefix"], "personal-a-")
                self.assertIsNone(prefix.json()["scope_name"])
                self.assertEqual(
                    prefix.json()["calls"]["registered_call_count"], 2
                )
                self.assertEqual(
                    prefix.json()["calls"]["known_cost_micro_cny"], 30
                )
                self.assertEqual(
                    prefix.json()["source"]["ingested_raw_token_estimate"], 300
                )

                exact = client.get(
                    "/v1/usage/costs?scope_name=personal-a-one",
                    headers=read_headers,
                )
                self.assertEqual(exact.status_code, 200, exact.text)
                self.assertEqual(exact.json()["scope_name"], "personal-a-one")
                self.assertIsNone(exact.json()["scope_prefix"])

                tenant_wide = client.get(
                    "/v1/usage/costs",
                    headers=read_headers,
                )
                self.assertEqual(tenant_wide.status_code, 200, tenant_wide.text)
                self.assertEqual(
                    tenant_wide.json()["calls"]["registered_call_count"], 3
                )

                denied_read_key = client.get(
                    "/v1/usage/costs?scope_prefix=personal-a-",
                    headers=read_headers,
                )
                self.assertEqual(denied_read_key.status_code, 403)

                denied_scoped_token = client.get(
                    "/v1/usage/costs?scope_prefix=personal-a-",
                    headers=scoped_headers,
                )
                self.assertEqual(denied_scoped_token.status_code, 403)

                denied_scoped_tenant_wide = client.get(
                    "/v1/usage/costs",
                    headers=scoped_headers,
                )
                self.assertEqual(denied_scoped_tenant_wide.status_code, 403)

                scoped_exact = client.get(
                    "/v1/usage/costs?scope_name=personal-a-one",
                    headers=scoped_headers,
                )
                self.assertEqual(scoped_exact.status_code, 200, scoped_exact.text)
                scoped_other = client.get(
                    "/v1/usage/costs?scope_name=other-scope",
                    headers=scoped_headers,
                )
                self.assertEqual(scoped_other.status_code, 403)

                both = client.get(
                    "/v1/usage/costs?scope_name=personal-a-one&scope_prefix=personal-a-",
                    headers=manager_headers,
                )
                self.assertEqual(both.status_code, 422)
                self.assertIn("mutually exclusive", both.text)

                components.auth.set_tenant_scopes("tenant-a", {"memory:read"})
                denied_by_policy = client.get(
                    "/v1/usage/costs?scope_prefix=personal-a-",
                    headers=manager_headers,
                )
                self.assertEqual(denied_by_policy.status_code, 403)

    def test_answer_provider_call_is_costed_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(self._settings(Path(directory)))
            components = app.state.components
            components.auth.set_tenant_scopes(
                "tenant-a", {"memory:read", "tokens:manage"}
            )
            manager_key = components.auth.create_key("tenant-a").api_key
            read_key = components.auth.create_key(
                "tenant-a", scopes={"memory:read"}
            ).api_key
            components.jobs.upsert_provider_price(
                "openai-compatible",
                "test-chat-model",
                input_micro_cny_per_million=2_000_000,
                cache_hit_input_micro_cny_per_million=100_000,
                output_micro_cny_per_million=3_000_000,
                effective_at=1,
            )
            payload = {
                "call_id": "chat-call-0001",
                "provider": "openai-compatible",
                "model": "test-chat-model",
                "operation": "chat_answer",
                "status": "completed",
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "cache_hit_tokens": 40,
                "request_sha256": "1" * 64,
                "response_sha256": "2" * 64,
                "started_at": 99,
                "finished_at": 100,
            }
            manager_headers = {
                "Authorization": f"Bearer {manager_key}",
                "X-TMCRA-Client-Platform": "vercel_ai_sdk",
                "X-TMCRA-Agent-ID": "tmcra-chat",
            }
            read_headers = {
                "Authorization": f"Bearer {read_key}",
                "X-TMCRA-Client-Platform": "vercel_ai_sdk",
            }
            with TestClient(app) as client:
                created = client.post(
                    "/v1/scopes/personal-a-chat/provider-calls",
                    headers=manager_headers,
                    json=payload,
                )
                self.assertEqual(created.status_code, 201, created.text)
                self.assertEqual(created.json()["cost_micro_cny"], 184)
                self.assertEqual(created.json()["cache_miss_tokens"], 60)
                self.assertFalse(created.json()["idempotent_replay"])

                replay = client.post(
                    "/v1/scopes/personal-a-chat/provider-calls",
                    headers=manager_headers,
                    json=payload,
                )
                self.assertEqual(replay.status_code, 201, replay.text)
                self.assertTrue(replay.json()["idempotent_replay"])

                conflict_payload = dict(payload)
                conflict_payload["output_tokens"] = 21
                conflict_payload["total_tokens"] = 121
                conflict = client.post(
                    "/v1/scopes/personal-a-chat/provider-calls",
                    headers=manager_headers,
                    json=conflict_payload,
                )
                self.assertEqual(conflict.status_code, 409, conflict.text)

                denied = client.post(
                    "/v1/scopes/personal-a-chat/provider-calls",
                    headers=read_headers,
                    json={**payload, "call_id": "chat-call-0002"},
                )
                self.assertEqual(denied.status_code, 403, denied.text)

                usage = client.get(
                    "/v1/usage/costs?scope_name=personal-a-chat",
                    headers=read_headers,
                )
                self.assertEqual(usage.status_code, 200, usage.text)
                self.assertEqual(usage.json()["calls"]["registered_call_count"], 1)
                self.assertEqual(usage.json()["calls"]["known_cost_micro_cny"], 184)


if __name__ == "__main__":
    unittest.main()
