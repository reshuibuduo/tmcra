from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmcra_mcp.config import ConfigError, MCPSettings, assert_active_memory_connection


class MCPSettingsTests(unittest.TestCase):
    def test_local_installer_discovery_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "local-memory.json"
            marker.write_text(json.dumps({"schemaVersion": 1, "mode": "local", "dataRoot": str(root), "profile": "lite-cpu"}))
            with patch.dict(os.environ, {"TMCRA_LOCAL_BINDING_FILE": str(marker), "TMCRA_BASE_URL": "https://cloud.example.invalid", "TMCRA_API_KEY": "synthetic-cloud-key"}, clear=True):
                with self.assertRaises(ConfigError):
                    MCPSettings.from_env()
                selected = root / "state/lite-cpu/secrets/client-plugin.json"
                selected.parent.mkdir(parents=True)
                selected.write_text(json.dumps({"deploymentMode": "local", "baseUrl": "http://127.0.0.1:2059", "apiKey": "synthetic-local-key", "defaultScope": "personal"}))
                settings = MCPSettings.from_env()
                self.assertEqual(settings.api_key, "synthetic-local-key")
                self.assertEqual(settings.default_scope, "personal")
                assert_active_memory_connection(settings)
                with self.assertRaises(ConfigError):
                    assert_active_memory_connection(MCPSettings("https://cloud.example.invalid", "synthetic-cloud-key"))

    def load(self, base_url: str) -> MCPSettings:
        with patch.dict(
            os.environ,
            {"TMCRA_BASE_URL": base_url, "TMCRA_API_KEY": "test-key"},
            clear=True,
        ):
            return MCPSettings.from_env()

    def test_accepts_https(self) -> None:
        self.assertEqual(self.load("https://api.tmcra.com").base_url, "https://api.tmcra.com")

    def test_rejects_http(self) -> None:
        for base_url in ("http://127.0.0.1:18089", "http://api.tmcra.com"):
            with self.subTest(base_url=base_url), self.assertRaises(ConfigError):
                self.load(base_url)

    def test_loads_optional_agent_attribution_without_changing_scope(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TMCRA_BASE_URL": "https://api.tmcra.com",
                "TMCRA_API_KEY": "test-key",
                "TMCRA_DEFAULT_SCOPE": "shared-project",
                "TMCRA_AGENT_ID": "review-agent",
                "TMCRA_INTEGRATION_ID": "int_local_mcp",
            },
            clear=True,
        ):
            settings = MCPSettings.from_env()
        self.assertEqual(settings.default_scope, "shared-project")
        self.assertEqual(settings.default_agent_id, "review-agent")
        self.assertEqual(settings.integration_id, "int_local_mcp")

    def test_device_global_scope_is_not_an_implicit_project_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "baseUrl": "https://api.tmcra.com",
                        "accessToken": "device-token",
                        "globalScope": "user-global-must-not-receive-project-chat",
                        "integrationIds": {"mcp": "int_device_mcp"},
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"TMCRA_CONFIG_FILE": str(config)},
                clear=True,
            ):
                settings = MCPSettings.from_env()
        self.assertIsNone(settings.default_scope)
        self.assertEqual(settings.integration_id, "int_device_mcp")


if __name__ == "__main__":
    unittest.main()
