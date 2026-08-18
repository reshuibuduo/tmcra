from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmcra_service.health import readiness
from tmcra_service.settings import ServiceSettings


class HealthProbeTests(unittest.TestCase):
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
            request_body_limit=1024,
            provider_lease_seconds=30,
            provider_key_concurrency=1,
            disk_free_min_bytes=1,
        )

    def test_sqlite_quick_check_must_be_exactly_ok(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self.settings(root)
            settings.control_db.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(settings.control_db)
            try:
                connection.execute("CREATE TABLE probe (value TEXT)")
            finally:
                connection.close()

            class FakeCursor:
                def fetchone(self):
                    return ("not ok",)

            class FakeConnection:
                def execute(self, query: str):
                    if query == "PRAGMA quick_check":
                        return FakeCursor()
                    raise AssertionError("SELECT 1 must not run after a failed quick_check")

                def close(self):
                    return None

            with patch("tmcra_service.health.sqlite3.connect", return_value=FakeConnection()):
                ready, report = readiness(settings)

            self.assertFalse(ready)
            self.assertFalse(report["checks"]["control_db"]["ok"])
            self.assertEqual(report["checks"]["control_db"]["quick_check"], "not ok")
            self.assertIn("expected 'ok'", report["checks"]["control_db"]["error"])

    def test_provider_pool_reports_empty_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            with patch.dict(
                os.environ,
                {
                    "TMCRA_WRITER_API_KEY_POOL": "secret-a,,secret-b",
                    "TMCRA_WRITER_BASE_URL": "https://api.example.invalid/v1",
                },
                clear=False,
            ):
                _, report = readiness(settings)
            provider = report["checks"]["provider_pool"]
            self.assertFalse(provider["ok"])
            self.assertEqual(provider["key_count"], 3)
            self.assertIn("empty entry", provider["error"])


if __name__ == "__main__":
    unittest.main()
