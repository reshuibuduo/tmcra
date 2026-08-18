from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from ops.export_tmcra_openapi import main, write_openapi
from test_tmcra_service_app import ServiceAppTests
from tmcra_service.app import create_app


class ExportOpenApiTests(unittest.TestCase):
    def test_cli_export_does_not_require_service_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "openapi.json"
            self.assertEqual(
                main(
                    [
                        "--output",
                        str(output),
                        "--server-url",
                        "https://memory.example",
                    ]
                ),
                0,
            )
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8"))["servers"][0]["url"],
                "https://memory.example",
            )

    def test_contract_is_deterministic_and_has_bearer_security(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = ServiceAppTests().settings(root)
            app = create_app(settings)
            first = root / "first.json"
            second = root / "second.json"
            write_openapi(app, first, server_url="https://memory.example/v1/")
            write_openapi(app, second, server_url="https://memory.example/v1/")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            schema = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(schema["servers"][0]["url"], "https://memory.example/v1")
            self.assertEqual(
                schema["components"]["securitySchemes"]["TMCRAApiKey"]["type"],
                "http",
            )
            self.assertEqual(
                schema["paths"]["/v1/scopes/{scope_name}/ingest"]["post"]["operationId"],
                "ingestMemory",
            )
            with TestClient(app) as client:
                self.assertEqual(client.get("/openapi.json").status_code, 200)


if __name__ == "__main__":
    unittest.main()
