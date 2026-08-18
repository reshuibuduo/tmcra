from __future__ import annotations

import importlib.util
import json
import pathlib
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock


MAINTENANCE_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "deploy" / "gpuhome" / "maintenance.py"
)
SPEC = importlib.util.spec_from_file_location("tmcra_gpuhome_maintenance", MAINTENANCE_PATH)
assert SPEC and SPEC.loader
maintenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintenance)


class MaintenanceHandler(BaseHTTPRequestHandler):
    authorization = ""
    path_seen = ""
    body = b""

    def do_POST(self) -> None:  # noqa: N802
        type(self).authorization = self.headers.get("Authorization", "")
        type(self).path_seen = self.path
        type(self).body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        payload = json.dumps(
            {"ok": True, "attempted": 2, "pending": 1, "processing": 0, "due": 0}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class GPUHomeMaintenanceTests(unittest.TestCase):
    def test_configuration_requires_loopback_and_strong_secret(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "TMCRA_UPSTREAM_HOST": "public.example.com",
                "TMCRA_DEVICE_MAINTENANCE_SECRET": "s" * 48,
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "local application"):
                maintenance.require_configuration()

        with mock.patch.dict(
            "os.environ",
            {"TMCRA_UPSTREAM_HOST": "127.0.0.1", "TMCRA_DEVICE_MAINTENANCE_SECRET": "short"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "missing or too short"):
                maintenance.require_configuration()

    def test_run_once_authenticates_and_validates_counters(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), MaintenanceHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            secret = "m" * 48
            result = maintenance.run_once(
                f"http://127.0.0.1:{server.server_address[1]}/api/device/v1/maintenance",
                secret,
            )
            self.assertEqual(result["attempted"], 2)
            self.assertEqual(MaintenanceHandler.authorization, f"Bearer {secret}")
            self.assertEqual(MaintenanceHandler.path_seen, "/api/device/v1/maintenance")
            self.assertEqual(MaintenanceHandler.body, b"{}")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
