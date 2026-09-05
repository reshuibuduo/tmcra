from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmcra_mcp import setup_cli


def completed(
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class SetupCliTests(unittest.TestCase):
    def device_config(self, root: Path) -> Path:
        path = root / "config.json"
        path.write_text(
            json.dumps(
                {
                    "baseUrl": "https://api.tmcra.com",
                    "accessToken": "protected-device-token",
                    "expiresAt": "2999-01-01T00:00:00Z",
                    "globalScope": "device-global",
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_install_registers_stdio_with_config_path_not_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config = self.device_config(Path(directory))
            with patch.object(setup_cli.shutil, "which", return_value="codex"), patch.object(
                setup_cli, "_run", side_effect=[completed(1), completed(0)]
            ) as run:
                result = setup_cli.install(
                    name="tmcra-memory",
                    codex="codex",
                    config_path=config,
                    replace=False,
                )
            arguments = run.call_args_list[1].args[0]
            serialized = " ".join(arguments)
            self.assertIn(f"TMCRA_CONFIG_FILE={config}", serialized)
            self.assertIn("-m tmcra_mcp", serialized)
            self.assertNotIn("protected-device-token", serialized)
            self.assertFalse(result["credential_embedded_in_codex_config"])
            self.assertEqual(result["mode"], setup_cli.MODE_EXPLICIT)
            self.assertFalse(result["automatic_lifecycle_configured"])

    def test_existing_registration_requires_explicit_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config = self.device_config(Path(directory))
            with patch.object(setup_cli.shutil, "which", return_value="codex"), patch.object(
                setup_cli, "_run", return_value=completed(0)
            ):
                with self.assertRaisesRegex(RuntimeError, "--replace"):
                    setup_cli.install(
                        name="tmcra-memory",
                        codex="codex",
                        config_path=config,
                        replace=False,
                    )

    def test_explicit_replace_can_switch_off_codex_hooks_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            config = self.device_config(Path(directory))
            plugin_mcp = completed(
                0,
                json.dumps(
                    {
                        "name": "tmcra-memory",
                        "transport": {"command": "node", "args": ["mcp_server.mjs"]},
                    }
                ),
            )
            plugin_list = completed(
                0,
                json.dumps(
                    {
                        "installed": [
                            {"pluginId": setup_cli.CODEX_PLUGIN_ID, "enabled": True}
                        ]
                    }
                ),
            )
            with patch.object(setup_cli.shutil, "which", return_value="codex"), patch.object(
                setup_cli,
                "_run",
                side_effect=[
                    plugin_mcp,
                    plugin_list,
                    completed(0),
                    completed(1),
                    completed(0),
                ],
            ) as run:
                result = setup_cli.install_explicit(
                    name="tmcra-memory",
                    codex="codex",
                    config_path=config,
                    replace=True,
                )
            self.assertEqual(
                run.call_args_list[2].args[0],
                ["codex", "plugin", "remove", setup_cli.CODEX_PLUGIN_ID, "--json"],
            )
            self.assertEqual(result["mode"], setup_cli.MODE_EXPLICIT)

    def test_device_config_validation_does_not_fall_back_to_environment_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(
                json.dumps({"baseUrl": "https://api.tmcra.com"}),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"TMCRA_API_KEY": "developer-token-must-not-mask-missing-device-auth"},
                clear=True,
            ):
                with self.assertRaisesRegex(setup_cli.ConfigError, "credential is missing"):
                    setup_cli._require_device_config(config)

    def test_codex_hooks_delegates_to_existing_plugin_installer(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            config = self.device_config(root)
            installer = root / "Install-TMCRA.ps1"
            installer.write_text("# fake installer", encoding="utf-8")
            existing = completed(
                0,
                json.dumps(
                    {
                        "name": "tmcra-memory",
                        "command": "python",
                        "args": ["-m", "tmcra_mcp"],
                    }
                ),
            )

            def which(name: str) -> str | None:
                return {
                    "codex": str(root / "codex.exe"),
                    "pwsh": str(root / "pwsh.exe"),
                }.get(name)

            with patch.object(setup_cli.shutil, "which", side_effect=which), patch.object(
                setup_cli,
                "_run",
                side_effect=[existing, completed(0), completed(0)],
            ) as run:
                result = setup_cli.install_codex_hooks(
                    name="tmcra-memory",
                    codex="codex",
                    config_path=config,
                    plugin_installer=str(installer),
                )

            self.assertEqual(run.call_args_list[1].args[0], [str(root / "codex.exe"), "mcp", "remove", "tmcra-memory"])
            delegated = run.call_args_list[2]
            arguments = delegated.args[0]
            environment = delegated.kwargs["env"]
            self.assertTrue(any(Path(argument).resolve() == installer.resolve() for argument in arguments))
            self.assertIn("-SkipConfigure", arguments)
            self.assertEqual(environment["TMCRA_CONFIG_FILE"], str(config))
            self.assertNotIn("TMCRA_API_KEY", environment)
            self.assertNotIn("protected-device-token", " ".join(arguments))
            self.assertEqual(result["mode"], setup_cli.MODE_CODEX_HOOKS)
            self.assertTrue(result["automatic_lifecycle_configured"])
            self.assertFalse(result["automatic_lifecycle_verified"])
            self.assertEqual(result["lifecycle_provider"], setup_cli.CODEX_PLUGIN_ID)
            self.assertTrue(result["hook_trust_required"])

    def test_codex_hooks_restores_explicit_registration_when_plugin_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            config = self.device_config(root)
            installer = root / "Install-TMCRA.ps1"
            installer.write_text("# fake installer", encoding="utf-8")
            existing = completed(
                0,
                json.dumps({"command": "python", "args": ["-m", "tmcra_mcp"]}),
            )

            def which(name: str) -> str | None:
                return {"codex": "codex", "pwsh": "pwsh"}.get(name)

            with patch.object(setup_cli.shutil, "which", side_effect=which), patch.object(
                setup_cli,
                "_run",
                side_effect=[existing, completed(0), completed(1), completed(0)],
            ) as run:
                with self.assertRaisesRegex(RuntimeError, "plugin installation failed"):
                    setup_cli.install_codex_hooks(
                        name="tmcra-memory",
                        codex="codex",
                        config_path=config,
                        plugin_installer=str(installer),
                    )

            restored_arguments = " ".join(run.call_args_list[3].args[0])
            self.assertIn("mcp add tmcra-memory", restored_arguments)
            self.assertIn(f"TMCRA_CONFIG_FILE={config}", restored_arguments)
            self.assertNotIn("protected-device-token", restored_arguments)

    def test_codex_hooks_status_checks_plugin_feature_and_mcp(self) -> None:
        plugin_list = completed(
            0,
            json.dumps(
                {
                    "installed": [
                        {
                            "pluginId": setup_cli.CODEX_PLUGIN_ID,
                            "enabled": True,
                        }
                    ]
                }
            ),
        )
        features = completed(0, "hooks             beta       true\n")
        mcp_list = completed(0, json.dumps([{"name": "tmcra-memory"}]))
        with patch.object(setup_cli.shutil, "which", return_value="codex"), patch.object(
            setup_cli,
            "_run",
            side_effect=[plugin_list, features, mcp_list],
        ):
            result = setup_cli.status_codex_hooks(name="tmcra-memory", codex="codex")

        self.assertTrue(result["installed"])
        self.assertTrue(result["automatic_lifecycle_configured"])
        self.assertIsNone(result["automatic_lifecycle_verified"])
        self.assertEqual(
            result["checks"],
            {
                "plugin_enabled": True,
                "hooks_feature_enabled": True,
                "plugin_mcp_available": True,
            },
        )
        self.assertTrue(result["hook_trust_verification_required"])

    def test_generic_mcp_mode_never_claims_automatic_lifecycle(self) -> None:
        with patch.object(setup_cli.shutil, "which", return_value="codex"), patch.object(
            setup_cli,
            "_run",
            return_value=completed(
                0,
                json.dumps({"command": "python", "args": ["-m", "tmcra_mcp"]}),
            ),
        ):
            result = setup_cli.status_explicit(name="tmcra-memory", codex="codex")
        self.assertTrue(result["installed"])
        self.assertFalse(result["automatic_lifecycle_configured"])


if __name__ == "__main__":
    unittest.main()
