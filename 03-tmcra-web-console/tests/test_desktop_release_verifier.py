from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "deploy" / "gpuhome" / "verify_desktop_release.py"
SPEC = importlib.util.spec_from_file_location("tmcra_release_verifier", MODULE_PATH)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


class DesktopReleaseVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.filename = "TMCRA-Memory-Setup-latest.exe"
        self.payload = b"TMCRA desktop installer\0" * 257
        self.sha256 = hashlib.sha256(self.payload).hexdigest()
        self.installer = self.root / self.filename
        self.installer.write_bytes(self.payload)
        self.manifest = self.root / "release.json"
        self.checksum = self.root / f"{self.filename}.sha256"
        self.write_metadata()

    def write_metadata(self, **installer_overrides: object) -> None:
        installer = {
            "latestPath": f"/downloads/{self.filename}",
            "bytes": len(self.payload),
            "sha256": self.sha256,
            **installer_overrides,
        }
        self.manifest.write_text(json.dumps({"installer": installer}), encoding="utf-8")
        self.checksum.write_text(f"{self.sha256}  {self.filename}\n", encoding="utf-8")

    def test_valid_metadata_and_regular_installer(self) -> None:
        sha256, size = verifier.release_metadata(self.manifest, self.checksum, self.filename)
        self.assertEqual((sha256, size), (self.sha256, len(self.payload)))
        verifier.verify_installer(self.installer, sha256, size)

    def test_manifest_and_checksum_must_agree(self) -> None:
        self.checksum.write_text(f"{'0' * 64}  {self.filename}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest and checksum disagree"):
            verifier.release_metadata(self.manifest, self.checksum, self.filename)

    def test_download_path_and_filename_are_fixed(self) -> None:
        self.write_metadata(latestPath="/downloads/other.exe")
        with self.assertRaisesRegex(ValueError, "unexpected download path"):
            verifier.release_metadata(self.manifest, self.checksum, self.filename)

        self.write_metadata()
        self.checksum.write_text(f"{self.sha256}  other.exe\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid format"):
            verifier.release_metadata(self.manifest, self.checksum, self.filename)

    def test_installer_size_and_hash_are_both_verified(self) -> None:
        with self.assertRaisesRegex(ValueError, "byte count"):
            verifier.verify_installer(self.installer, self.sha256, len(self.payload) + 1)

        changed = bytes([self.payload[0] ^ 1]) + self.payload[1:]
        self.installer.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            verifier.verify_installer(self.installer, self.sha256, len(self.payload))

    def test_symbolic_link_is_rejected_when_supported(self) -> None:
        link = self.root / "installer-link.exe"
        try:
            link.symlink_to(self.installer)
        except OSError:
            self.skipTest("symbolic links are unavailable for this Windows account")
        with self.assertRaisesRegex(ValueError, "regular file"):
            verifier.verify_installer(link, self.sha256, len(self.payload))

    def test_update_feed_requires_exact_manifest_bound_artifacts(self) -> None:
        version = "0.1.1"
        installer_name = f"TMCRA-Memory-Setup-{version}-x64.exe"
        payloads = {
            "latest.yml": b"version: 0.1.1\n",
            installer_name: b"versioned installer",
            f"{installer_name}.blockmap": b"block map",
        }
        update_directory = self.root / "update"
        update_directory.mkdir()
        artifacts = []
        for name, payload in payloads.items():
            (update_directory / name).write_bytes(payload)
            artifacts.append(
                {
                    "name": name,
                    "path": f"/downloads/desktop/windows/x64/{name}",
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest.update(
            {
                "version": version,
                "updater": {
                    "feedPath": "/downloads/desktop/windows/x64",
                    "artifacts": artifacts,
                },
            }
        )
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")

        expected = verifier.updater_artifacts(self.manifest)
        verifier.verify_update_directory(update_directory, expected)

        (update_directory / "unexpected.txt").write_text("no", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            verifier.verify_update_directory(update_directory, expected)

    def test_macos_feeds_are_bound_to_the_requested_architecture(self) -> None:
        version = "0.2.8"
        for architecture in ("x64", "arm64"):
            with self.subTest(architecture=architecture):
                update_directory = self.root / f"mac-{architecture}"
                update_directory.mkdir()
                dmg_name = f"TMCRA-Memory-{version}-{architecture}.dmg"
                zip_name = f"TMCRA-Memory-{version}-{architecture}.zip"
                payloads = {
                    "latest-mac.yml": b"version: 0.2.8\n",
                    dmg_name: b"disk image",
                    zip_name: b"update archive",
                    f"{zip_name}.blockmap": b"block map",
                }
                artifacts = []
                for name, payload in payloads.items():
                    (update_directory / name).write_bytes(payload)
                    artifacts.append(
                        {
                            "name": name,
                            "path": f"/downloads/desktop/macos/{architecture}/{name}",
                            "bytes": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    )
                manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
                manifest.update(
                    {
                        "version": version,
                        "updater": {
                            "feedPath": f"/downloads/desktop/macos/{architecture}",
                            "artifacts": artifacts,
                        },
                    }
                )
                self.manifest.write_text(json.dumps(manifest), encoding="utf-8")

                expected = verifier.updater_artifacts(
                    self.manifest, "macos", architecture
                )
                verifier.verify_update_directory(update_directory, expected)

                other = "arm64" if architecture == "x64" else "x64"
                with self.assertRaisesRegex(ValueError, "unexpected updater feed"):
                    verifier.updater_artifacts(self.manifest, "macos", other)


if __name__ == "__main__":
    unittest.main()
