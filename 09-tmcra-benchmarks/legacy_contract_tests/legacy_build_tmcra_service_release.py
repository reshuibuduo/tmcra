from __future__ import annotations

import hashlib
import tarfile
import tempfile
import unittest
from pathlib import Path

from ops.build_tmcra_service_release import _scan_payload, build_release


ROOT = Path(__file__).resolve().parent


class ServiceReleaseBuilderTests(unittest.TestCase):
    def test_release_is_deterministic_and_excludes_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            first = Path(temporary_directory) / "first.tar.gz"
            second = Path(temporary_directory) / "second.tar.gz"
            build_release(ROOT, first)
            build_release(ROOT, second)

            self.assertEqual(
                hashlib.sha256(first.read_bytes()).hexdigest(),
                hashlib.sha256(second.read_bytes()).hexdigest(),
            )
            with tarfile.open(first, "r:gz") as archive:
                names = set(archive.getnames())

            self.assertIn("RELEASE_MANIFEST.json", names)
            self.assertIn("deploy/tmcra-service.env.example", names)
            self.assertIn("openapi.json", names)
            self.assertIn("ops/audit_tmcra_v4_subject_attribution.py", names)
            self.assertIn("tmcra_v3_product_writer.py", names)
            self.assertIn("tmcra_v4_route_policy.py", names)
            self.assertIn("tmcra_v4_online_runtime.py", names)
            self.assertIn("sdk/python/tmcra_client/client.py", names)
            self.assertIn("sdk/typescript/src/client.ts", names)
            self.assertIn("mcp-server/src/tmcra_mcp/server.py", names)
            self.assertIn("integrations/openclaw/index.js", names)
            self.assertIn("integrations/hermes/tmcra_plugin.py", names)
            self.assertNotIn("deploy/tmcra-service.env", names)
            self.assertFalse(any("__pycache__" in name for name in names))
            self.assertFalse(any("node_modules" in name for name in names))
            self.assertFalse(any(".pytest_cache" in name for name in names))
            self.assertFalse(any(name.endswith(".whl") for name in names))
            self.assertFalse(any(name.endswith((".sqlite3", ".log", ".pyc")) for name in names))

    def test_secret_scanner_rejects_populated_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "suspected secret"):
            fake_key = b"sk-" + b"1234567890abcdefghijklmnop"
            key_name = b"API" + b"_KEY="
            _scan_payload(Path("bad.env"), key_name + fake_key + b"\n")
        with self.assertRaisesRegex(ValueError, "populated secret assignment"):
            _scan_payload(
                Path("bad.env"),
                b"DEEPSEEK" + b"_API_KEY=not-a-placeholder\n",
            )


if __name__ == "__main__":
    unittest.main()
