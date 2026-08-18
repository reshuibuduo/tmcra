from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import installer


class InstallerTests(unittest.TestCase):
    def test_pip_target_data_files_are_discovered_and_installed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "share" / "tmcra-hermes-plugin"
            assets.mkdir(parents=True)
            values = {
                "tmcra_plugin.py": "PLUGIN = True\n",
                "plugin.yaml": "name: tmcra-hermes\nversion: 0.4.1\n",
                "README.md": "English runtime notes\n",
                "README.zh-CN.md": "中文运行说明\n",
                "INSTALL.md": "English install notes\n",
                "INSTALL.zh-CN.md": "中文安装说明\n",
            }
            for name, content in values.items():
                (assets / name).write_text(content, encoding="utf-8")

            home = root / "hermes-profile"
            with patch.object(installer, "__file__", str(root / "installer.py")):
                destination = installer.install(home)

            self.assertEqual(destination, home / "plugins" / "tmcra-hermes")
            self.assertTrue((destination / "README.zh-CN.md").is_file())
            self.assertEqual(
                (destination / "__init__.py").read_text(encoding="utf-8"),
                values["tmcra_plugin.py"],
            )
            self.assertIn(
                "provider: tmcra-hermes",
                (home / "config.yaml").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
