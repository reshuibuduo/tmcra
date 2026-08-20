from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmcra_service.__main__ import (
    _configure_writer_aliases,
    _load_shell_environment,
    _validate_startup,
)
from tmcra_service.settings import ServiceSettings
from tmcra_service.supervisor import build_parser


class ServiceMainTests(unittest.TestCase):
    def settings(self, root: Path, *, bind_host: str = "127.0.0.1", url: str = "https://example.invalid") -> ServiceSettings:
        files = {
            "writer_env": root / "writer.env",
            "native_harness": root / "harness.py",
            "node_model": root / "node.pt",
            "path_model": root / "path.pt",
            "checkpoint": root / "checkpoint.pt",
        }
        for path in files.values():
            path.write_text("test", encoding="utf-8")
        algorithm_files = [
            "tmcra_v4_batch_writer.py",
            "tmcra_v4_online_runtime.py",
            "tmcra_v4_slow_graph.py",
            "run_tmcra_v4_compile_evidence.py",
            "tmcra_v3_recall_planner.py",
            "tmcra_v4_recall_planner.py",
        ]
        manifest_rows = []
        for name in algorithm_files:
            path = root / name
            path.write_text(f"test algorithm: {name}\n", encoding="utf-8")
            manifest_rows.append(
                {
                    "path": name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        manifest = root / "tmcra_service" / "shared_core_manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": "tmcra.service.shared-core-manifest.1",
                    "service_version": "test",
                    "generated_on": "test",
                    "algorithm_files": manifest_rows,
                }
            ),
            encoding="utf-8",
        )
        return ServiceSettings(
            state_dir=root / "state",
            control_db=root / "state" / "control.sqlite3",
            bind_host=bind_host,
            bind_port=2009,
            public_base_url=url,
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
            request_body_limit=1024,
            provider_lease_seconds=30,
            provider_key_concurrency=1,
            disk_free_min_bytes=1,
        )

    def test_startup_rejects_public_bind_address(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"TMCRA_SERVICE_TLS_PROXY_MODE": ""}):
                with self.assertRaisesRegex(RuntimeError, "non-loopback bind"):
                    _validate_startup(
                        self.settings(Path(directory), bind_host="0.0.0.0")
                    )

    def test_startup_accepts_explicit_gpuhome_tls_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"TMCRA_SERVICE_TLS_PROXY_MODE": "gpuhome"},
                clear=False,
            ):
                _validate_startup(
                    self.settings(Path(directory), bind_host="0.0.0.0")
                )

    def test_startup_rejects_plain_http_public_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "HTTPS URL"):
                _validate_startup(self.settings(Path(directory), url="http://example.invalid"))

    def test_startup_exports_model_paths_for_native_harness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self.settings(root)
            with patch.dict(os.environ, {}, clear=False):
                _validate_startup(settings)
                self.assertEqual(os.environ["TMCRA_NODE_MODEL_PATH"], str(settings.node_model))
                self.assertEqual(os.environ["TMCRA_PATH_MODEL_PATH"], str(settings.path_model))

    def test_startup_rejects_shared_core_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self.settings(root)
            (root / "tmcra_v4_batch_writer.py").write_text(
                "drifted\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "shared-core hash mismatch"):
                _validate_startup(settings)

    def test_supervisor_parses_env_file_without_starting(self) -> None:
        parsed = build_parser().parse_args(["--env-file", "/tmp/service.env"])
        self.assertEqual(parsed.env_file, "/tmp/service.env")

    def test_missing_writer_env_has_explicit_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.env"
            with self.assertRaisesRegex(RuntimeError, "writer env file is missing"):
                _load_shell_environment(missing)

    def test_writer_key_pool_has_explicit_error(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TMCRA_DEEPSEEK_WRITER_BASE_URL": "https://api.example.invalid/v1",
                "TMCRA_DEEPSEEK_WRITER_KEY_POOL": "secret-a,,secret-b",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "empty"):
                _configure_writer_aliases()

    def test_writer_pool_is_mapped_to_recall_planner(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TMCRA_DEEPSEEK_WRITER_BASE_URL": "https://api.example.invalid/v1",
                "TMCRA_DEEPSEEK_WRITER_KEY_POOL": "secret-a,secret-b",
            },
            clear=True,
        ):
            _configure_writer_aliases()
            self.assertEqual(
                os.environ["TMCRA_RECALL_PLANNER_BASE_URL"],
                "https://api.example.invalid/v1",
            )
            self.assertEqual(
                os.environ["TMCRA_RECALL_PLANNER_API_KEY_POOL"],
                "secret-a,secret-b",
            )
            self.assertEqual(
                os.environ["TMCRA_RECALL_PLANNER_MODEL"],
                "deepseek-v4-flash",
            )

    def test_local_routes_preserve_independent_operator_model_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / "local.key"
            key_file.write_text("local-test-key\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "TMCRA_DEEPSEEK_WRITER_BASE_URL": "https://api.example.invalid/v1",
                    "TMCRA_DEEPSEEK_WRITER_KEY_POOL": "disabled-provider-key",
                    "TMCRA_WRITER_PROVIDER": "local-qwen",
                    "TMCRA_RECALL_PLANNER_PROVIDER": "local-qwen",
                    "TMCRA_WRITER_REVIEWER_PROVIDER": "local-qwen",
                    "TMCRA_SLOW_GRAPH_PROVIDER": "local-qwen",
                    "TMCRA_LOCAL_WRITER_API_KEY_FILE": str(key_file),
                    "TMCRA_WRITER_MODEL": "operator-writer-v1",
                    "TMCRA_RECALL_PLANNER_MODEL": "operator-planner-v2",
                    "TMCRA_WRITER_REVIEWER_MODEL": "operator-reviewer-v3",
                    "TMCRA_SLOW_GRAPH_MODEL": "operator-graph-v4",
                },
                clear=True,
            ):
                _configure_writer_aliases()
                self.assertEqual(os.environ["TMCRA_WRITER_MODEL"], "operator-writer-v1")
                self.assertEqual(
                    os.environ["TMCRA_RECALL_PLANNER_MODEL"], "operator-planner-v2"
                )
                self.assertEqual(
                    os.environ["TMCRA_WRITER_REVIEWER_MODEL"], "operator-reviewer-v3"
                )
                self.assertEqual(
                    os.environ["TMCRA_SLOW_GRAPH_MODEL"], "operator-graph-v4"
                )

    def test_shared_core_manifest_matches_checkout(self) -> None:
        if os.getenv("TMCRA_VERIFY_SHARED_CORE") != "1":
            self.skipTest("production shared-core verification is deployment-only")
        root = Path(__file__).resolve().parent
        manifest = json.loads(
            (root / "tmcra_service" / "shared_core_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            manifest["schema_version"],
            "tmcra.service.shared-core-manifest.1",
        )
        for item in manifest["algorithm_files"]:
            path = root / item["path"]
            self.assertTrue(path.is_file(), item["path"])
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                item["sha256"],
                item["path"],
            )


if __name__ == "__main__":
    unittest.main()
