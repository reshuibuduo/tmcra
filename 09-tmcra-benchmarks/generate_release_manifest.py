#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "tmcra.benchmark-release-manifest.1"
MANIFEST_NAME = "RELEASE_MANIFEST.json"
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pyc", ".pyo"}
FORBIDDEN_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache"}
TEXT_SUFFIXES = {
    ".cff",
    ".css",
    ".env",
    ".example",
    ".html",
    ".java",
    ".js",
    ".json",
    ".kt",
    ".kts",
    ".md",
    ".mjs",
    ".properties",
    ".py",
    ".service",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    payload = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES or path.name in {"LICENSE", "NOTICE"}:
        payload = payload.replace(b"\r\n", b"\n")
    digest.update(payload)
    return digest.hexdigest()


def build_manifest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    files: dict[str, str] = {}
    forbidden: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.as_posix() == MANIFEST_NAME:
            continue
        if any(part in FORBIDDEN_NAMES for part in relative.parts):
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            forbidden.append(relative.as_posix())
            continue
        files[relative.as_posix()] = _sha256(path)
    return {
        "schema_version": SCHEMA_VERSION,
        "component": "09-tmcra-benchmarks",
        "file_count": len(files),
        "files": files,
        "forbidden_runtime_state_included": bool(forbidden),
        "forbidden_runtime_state": forbidden,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify the deterministic benchmark release manifest"
    )
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    output = (args.output or (root / MANIFEST_NAME)).resolve()
    manifest = build_manifest(root)
    if args.check:
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"release manifest is unreadable: {output}: {exc}")
        if existing != manifest:
            raise SystemExit("release manifest is stale; regenerate it")
        print(f"release manifest verified: {manifest['file_count']} files")
        return 0
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"release manifest written: {manifest['file_count']} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
