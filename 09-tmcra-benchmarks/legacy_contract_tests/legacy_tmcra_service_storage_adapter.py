from __future__ import annotations

import json
import hashlib
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import sys
from unittest import mock

from tmcra_service.adapters.v4 import V4AdapterError, V4StorageAdapter
from tmcra_service.settings import ServiceSettings


class StorageAdapterTests(unittest.TestCase):
    @staticmethod
    def _settings(root: Path) -> ServiceSettings:
        return ServiceSettings(
            state_dir=root,
            control_db=root / "control.sqlite3",
            bind_host="127.0.0.1",
            bind_port=2009,
            public_base_url="https://example.invalid",
            v4_root=root,
            integrated_repo=root,
            writer_env=root / "writer.env",
            embedding_model=root,
            native_harness=root / "harness.py",
            node_model=root / "node.pt",
            path_model=root / "path.pt",
            checkpoint=root / "checkpoint.pt",
            cross_model=root,
            device="cpu",
            graph_device="cpu",
            request_body_limit=1024,
            provider_lease_seconds=30,
            provider_key_concurrency=1,
            disk_free_min_bytes=1,
        )

    def test_tenant_and_scope_paths_are_stable_and_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)
            adapter = V4StorageAdapter(settings)
            self.assertEqual(adapter.python, Path(sys.executable).absolute())
            first = adapter.scope_paths("tenant-a", "default")
            same = adapter.scope_paths("tenant-a", "default")
            other_scope = adapter.scope_paths("tenant-a", "other")
            other_tenant = adapter.scope_paths("tenant-b", "default")
            self.assertEqual(first, same)
            self.assertNotEqual(first.root, other_scope.root)
            self.assertNotEqual(first.root, other_tenant.root)
            self.assertTrue(first.scope_id.startswith("tmcra_v4:svc_"))

            first.database.parent.mkdir(parents=True)
            first.database.write_bytes(b"sqlite")
            first.indexes.mkdir(parents=True)
            index_path = first.indexes / "index.pt"
            index_path.write_bytes(b"atomic-index")
            report_path = first.root / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "row_count": 1,
                        "rows": [
                            {
                                "scope_id": first.scope_id,
                                "db_path": str(first.database),
                                "index_path": str(index_path),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            adapter._validate_index_artifacts(first, index_path, report_path)
            report_path.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "row_count": 1,
                        "rows": [
                            {
                                "scope_id": "other",
                                "db_path": str(first.database),
                                "index_path": str(index_path),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(V4AdapterError):
                adapter._validate_index_artifacts(first, index_path, report_path)

    def test_build_index_uses_sqlite_backup_and_hashes_generation_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = V4StorageAdapter(self._settings(root))
            paths = adapter.scope_paths("tenant-a")
            paths.database.parent.mkdir(parents=True)
            connection = sqlite3.connect(paths.database)
            connection.execute("create table memories (value text)")
            connection.execute("insert into memories values ('snapshot')")
            connection.commit()
            connection.close()

            def fake_writer(command, *, log_path, timeout=None):
                manifest = json.loads(Path(command[command.index("--scope-manifest") + 1]).read_text())
                index_path = Path(manifest["index_path"])
                report_path = Path(command[command.index("--out-report") + 1])
                index_path.parent.mkdir(parents=True, exist_ok=True)
                index_path.write_bytes(b"index-for-snapshot")
                report_path.write_text(
                    json.dumps(
                        {
                            "status": "complete",
                            "row_count": 1,
                            "rows": [
                                {
                                    "scope_id": manifest["scope_id"],
                                    "db_path": manifest["db_path"],
                                    "index_path": manifest["index_path"],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

            adapter._run_with_writer_env = fake_writer
            result = adapter.build_index(
                tenant_id="tenant-a", scope_name="default", job_id="job-1_index"
            )
            active = result["active_index"]
            snapshot = Path(active["database"])
            index = Path(active["index"])
            self.assertNotEqual(snapshot, paths.database)
            self.assertEqual(snapshot.parent, index.parent)
            self.assertEqual(active["generation_id"], "job-1_index")
            self.assertEqual(active["database_sha256"], hashlib.sha256(snapshot.read_bytes()).hexdigest())
            self.assertEqual(active["index_sha256"], hashlib.sha256(index.read_bytes()).hexdigest())
            self.assertEqual(adapter.active_snapshot("tenant-a", "default"), active)
            check = sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True)
            self.assertEqual(check.execute("select value from memories").fetchone()[0], "snapshot")
            check.close()

    def test_failed_generation_keeps_previous_active_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = V4StorageAdapter(self._settings(root))
            paths = adapter.scope_paths("tenant-a")
            paths.database.parent.mkdir(parents=True)
            connection = sqlite3.connect(paths.database)
            connection.execute("create table memories (value text)")
            connection.commit()
            connection.close()

            def successful_writer(command, *, log_path, timeout=None):
                manifest = json.loads(Path(command[command.index("--scope-manifest") + 1]).read_text())
                index_path = Path(manifest["index_path"])
                report_path = Path(command[command.index("--out-report") + 1])
                index_path.parent.mkdir(parents=True, exist_ok=True)
                index_path.write_bytes(b"stable-index")
                report_path.write_text(
                    json.dumps(
                        {
                            "status": "complete",
                            "row_count": 1,
                            "rows": [
                                {
                                    "scope_id": manifest["scope_id"],
                                    "db_path": manifest["db_path"],
                                    "index_path": manifest["index_path"],
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )

            adapter._run_with_writer_env = successful_writer
            adapter.build_index(tenant_id="tenant-a", scope_name="default", job_id="first")
            before = paths.active_index.read_bytes()

            def failing_writer(command, *, log_path, timeout=None):
                raise RuntimeError("simulated index build failure")

            adapter._run_with_writer_env = failing_writer
            with self.assertRaises(RuntimeError):
                adapter.build_index(tenant_id="tenant-a", scope_name="default", job_id="second")
            self.assertEqual(paths.active_index.read_bytes(), before)

    def test_writer_extra_env_is_passed_without_logging_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = V4StorageAdapter(self._settings(root))
            log_path = root / "writer.log"
            with mock.patch("tmcra_service.adapters.v4.subprocess.run") as run:
                adapter._run_with_writer_env(
                    ["python", "-m", "tmcra_service.writer"],
                    log_path=log_path,
                    extra_env={"TMCRA_SERVICE_TENANT_ID": "secret-value"},
                )
            environment = run.call_args.kwargs["env"]
            self.assertEqual(environment["TMCRA_SERVICE_TENANT_ID"], "secret-value")
            self.assertNotIn("secret-value", log_path.read_text(encoding="utf-8"))

    def test_ingest_transmits_unified_writer_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = V4StorageAdapter(self._settings(root))
            captured: dict[str, object] = {}
            adapter._require_compatible_writer = lambda: None

            def fake_writer(
                command, *, log_path, timeout=None, extra_env=None
            ) -> None:
                captured["command"] = [str(item) for item in command]
                captured["extra_env"] = dict(extra_env or {})
                report_path = Path(log_path).parent / "product_writer_report.json"
                report_path.write_text(
                    json.dumps({"status": "complete"}), encoding="utf-8"
                )

            adapter._run_with_writer_env = fake_writer
            adapter.ingest(
                tenant_id="tenant-a",
                scope_name="scope-a",
                session_id="session-a",
                messages=[],
                job_id="job-a",
            )

            self.assertEqual(
                captured["extra_env"],
                {
                    "TMCRA_SERVICE_TENANT_ID": "tenant-a",
                    "TMCRA_SERVICE_SCOPE_NAME": "scope-a",
                    "TMCRA_SERVICE_JOB_ID": "job-a",
                    "TMCRA_SERVICE_STAGE_ID": "job-a:writer",
                },
            )

    def test_slow_consolidation_requires_subject_gate_before_graph_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = V4StorageAdapter(self._settings(root))
            paths = adapter.scope_paths("tenant-a")
            paths.database.parent.mkdir(parents=True)
            connection = sqlite3.connect(paths.database)
            connection.execute("create table messages (message_id text primary key)")
            connection.commit()
            connection.close()
            commands: list[list[str]] = []

            def fake_writer(command, *, log_path, timeout=None):
                command = [str(item) for item in command]
                commands.append(command)
                if "tmcra_service.subject_attribution" in command:
                    report_path = Path(command[command.index("--output") + 1])
                    report_path.write_text(
                        json.dumps(
                            {
                                "status": "complete",
                                "gate_passed": True,
                                "routed_message_count": 2,
                                "unresolved_routed_message_count": 0,
                                "quarantined_count": 0,
                                "physical_api_calls": 1,
                                "estimated_cost_cny": 0.01,
                            }
                        ),
                        encoding="utf-8",
                    )

            adapter._run_with_writer_env = fake_writer
            result = adapter.consolidate_slow(
                tenant_id="tenant-a", scope_name="default", job_id="job-slow"
            )

            self.assertEqual(result["schema_version"], "tmcra.service.slow-commit.2")
            self.assertTrue(result["subject_attribution"]["gate_passed"])
            self.assertIn("tmcra_service.subject_attribution", commands[0])
            self.assertEqual(commands[1][-2:], ["enqueue", paths.scope_id])
            self.assertEqual(commands[2][-1], "drain")
            self.assertEqual(
                commands[3][-3:],
                ["audit", paths.scope_id, "--require-promotion-coverage"],
            )

            commands.clear()
            replay = adapter.consolidate_slow(
                tenant_id="tenant-a", scope_name="default", job_id="job-slow"
            )
            self.assertEqual(replay, result)
            self.assertEqual(commands, [])

    def test_slow_consolidation_fails_closed_on_unresolved_subjects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = V4StorageAdapter(self._settings(root))
            paths = adapter.scope_paths("tenant-a")
            paths.database.parent.mkdir(parents=True)
            connection = sqlite3.connect(paths.database)
            connection.execute("create table messages (message_id text primary key)")
            connection.commit()
            connection.close()
            commands: list[list[str]] = []

            def fake_writer(command, *, log_path, timeout=None):
                command = [str(item) for item in command]
                commands.append(command)
                if "tmcra_service.subject_attribution" in command:
                    report_path = Path(command[command.index("--output") + 1])
                    report_path.write_text(
                        json.dumps(
                            {
                                "status": "complete",
                                "gate_passed": False,
                                "routed_message_count": 2,
                                "unresolved_routed_message_count": 1,
                            }
                        ),
                        encoding="utf-8",
                    )

            adapter._run_with_writer_env = fake_writer
            with self.assertRaisesRegex(V4AdapterError, "subject attribution gate"):
                adapter.consolidate_slow(
                    tenant_id="tenant-a", scope_name="default", job_id="job-slow"
                )
            self.assertEqual(len(commands), 1)
            self.assertIn("tmcra_service.subject_attribution", commands[0])


if __name__ == "__main__":
    unittest.main()
