#!/usr/bin/env python3
"""Validate that the published desktop metadata and installer are identical."""

from __future__ import annotations

import hashlib
import argparse
import json
import pathlib
import re
import stat
import sys


def release_metadata(
    manifest_path: pathlib.Path,
    checksum_path: pathlib.Path,
    filename: str,
) -> tuple[str, int]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    installer = manifest.get("installer")
    if not isinstance(installer, dict):
        raise ValueError("desktop release manifest has no installer object")

    sha256 = installer.get("sha256")
    size = installer.get("bytes")
    latest_path = installer.get("latestPath")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise ValueError("desktop release manifest has an invalid SHA-256")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("desktop release manifest has an invalid byte count")
    if latest_path != f"/downloads/{filename}":
        raise ValueError("desktop release manifest has an unexpected download path")

    checksum_parts = checksum_path.read_text(encoding="utf-8").strip().split()
    if len(checksum_parts) != 2 or checksum_parts[1] != filename:
        raise ValueError("desktop installer checksum file has an invalid format")
    if checksum_parts[0].lower() != sha256.lower():
        raise ValueError("desktop installer manifest and checksum disagree")
    return sha256.lower(), size


def verify_installer(path: pathlib.Path, expected_sha256: str, expected_size: int) -> None:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("desktop installer must be a regular file")
    if metadata.st_size != expected_size:
        raise ValueError("desktop installer byte count does not match the manifest")

    digest = hashlib.sha256()
    with path.open("rb") as installer:
        while chunk := installer.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ValueError("desktop installer SHA-256 does not match the manifest")


def updater_artifacts(
    manifest_path: pathlib.Path,
    platform: str = "windows",
    architecture: str = "x64",
) -> list[tuple[str, str, int]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    updater = manifest.get("updater")
    if not isinstance(updater, dict):
        raise ValueError("desktop release manifest has no updater object")
    expected_feed = f"/downloads/desktop/{platform}/{architecture}"
    if updater.get("feedPath") != expected_feed:
        raise ValueError("desktop release manifest has an unexpected updater feed path")
    values = updater.get("artifacts")
    if not isinstance(values, list) or not values:
        raise ValueError("desktop updater must declare artifacts")

    result: list[tuple[str, str, int]] = []
    names: list[str] = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("desktop updater artifact must be an object")
        name = value.get("name")
        sha256 = value.get("sha256")
        size = value.get("bytes")
        path = value.get("path")
        if not isinstance(name, str) or pathlib.PurePath(name).name != name:
            raise ValueError("desktop updater artifact has an unsafe filename")
        if path != f"{expected_feed}/{name}":
            raise ValueError("desktop updater artifact has an unexpected path")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
            raise ValueError("desktop updater artifact has an invalid SHA-256")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError("desktop updater artifact has an invalid byte count")
        names.append(name)
        result.append((name, sha256.lower(), size))

    version = manifest.get("version")
    if not isinstance(version, str) or not re.fullmatch(
        r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?",
        version,
    ):
        raise ValueError("desktop release manifest has an invalid version")
    if platform == "windows" and architecture == "x64":
        installer_name = f"TMCRA-Memory-Setup-{version}-x64.exe"
        expected_names = ["latest.yml", installer_name, f"{installer_name}.blockmap"]
        if names != expected_names:
            raise ValueError("desktop updater artifact names or order are invalid")
    elif platform == "macos" and architecture in ("x64", "arm64"):
        dmg_name = f"TMCRA-Memory-{version}-{architecture}.dmg"
        zip_name = f"TMCRA-Memory-{version}-{architecture}.zip"
        required_names = ["latest-mac.yml", dmg_name, zip_name]
        if names[:3] != required_names:
            raise ValueError("macOS updater artifact names or order are invalid")
        allowed_optional = {f"{dmg_name}.blockmap", f"{zip_name}.blockmap"}
        if len(names) > 5 or any(name not in allowed_optional for name in names[3:]):
            raise ValueError("macOS updater contains an unexpected artifact")
    else:
        raise ValueError("unsupported desktop updater platform or architecture")
    return result


def verify_update_directory(path: pathlib.Path, artifacts: list[tuple[str, str, int]]) -> None:
    metadata = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("desktop updater directory must be a real directory")
    expected_names = {name for name, _sha256, _size in artifacts}
    actual_names = {entry.name for entry in path.iterdir()}
    if actual_names != expected_names:
        raise ValueError("desktop updater directory contains missing or unexpected files")
    for name, sha256, size in artifacts:
        verify_installer(path / name, sha256, size)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("checksum")
    parser.add_argument("filename")
    parser.add_argument("installer", nargs="?")
    parser.add_argument("--update-dir")
    parser.add_argument("--platform", choices=("windows", "macos"), default="windows")
    parser.add_argument("--architecture", choices=("x64", "arm64"), default="x64")
    arguments = parser.parse_args(argv[1:])
    manifest_path = pathlib.Path(arguments.manifest)
    checksum_path = pathlib.Path(arguments.checksum)
    filename = arguments.filename
    try:
        sha256, size = release_metadata(manifest_path, checksum_path, filename)
        if arguments.installer:
            verify_installer(pathlib.Path(arguments.installer), sha256, size)
        if arguments.update_dir:
            verify_update_directory(
                pathlib.Path(arguments.update_dir),
                updater_artifacts(
                    manifest_path, arguments.platform, arguments.architecture
                ),
            )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(sha256, size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
