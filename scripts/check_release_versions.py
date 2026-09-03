#!/usr/bin/env python3
"""Fail when a publishable TMCRA component drifts from the root release version."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "0.3.0-rc2"
NPM = "0.3.0-rc.2"
CODEX = "0.3.0-rc.8"
PYTHON = "0.3.0rc2"
APPLE = "0.3.0"
CLAUDE = "0.3.0-rc.8+claude.20260904"


def nested_value(document: Any, keys: tuple[str, ...]) -> Any:
    current = document
    for key in keys:
        current = current[key]
    return current


def check_json(
    errors: list[str], path: str, keys: tuple[str, ...], expected: Any
) -> None:
    document = json.loads((ROOT / path).read_text(encoding="utf-8"))
    actual = nested_value(document, keys)
    if actual != expected:
        errors.append(f"{path}:{'.'.join(keys)} = {actual!r}; expected {expected!r}")


def check_toml(errors: list[str], path: str, expected: str) -> None:
    document = tomllib.loads((ROOT / path).read_text(encoding="utf-8"))
    actual = document["project"]["version"]
    if actual != expected:
        errors.append(f"{path}:project.version = {actual!r}; expected {expected!r}")


def check_text(
    errors: list[str], path: str, expected_fragment: str, *, count: int = 1
) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    actual_count = text.count(expected_fragment)
    if actual_count != count:
        errors.append(
            f"{path}: expected {expected_fragment!r} exactly {count} time(s); "
            f"found {actual_count}"
        )


def main() -> int:
    errors: list[str] = []
    root_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if root_version != CANONICAL:
        errors.append(f"VERSION = {root_version!r}; expected {CANONICAL!r}")

    package_jsons = [
        "03-tmcra-web-console/deploy/gpuhome/miniflare-loopback-api-plugin/package.json",
        "03-tmcra-web-console/desktop/tmcra-memory/package.json",
        "03-tmcra-web-console/package.json",
        "04-tmcra-desktop/package.json",
        "05-tmcra-mobile/package.json",
        "06-tmcra-sdk-integrations/integrations/openclaw/package.json",
        "06-tmcra-sdk-integrations/integrations/vercel-ai-sdk/package.json",
        "06-tmcra-sdk-integrations/sdk/typescript/package.json",
    ]
    package_locks = [
        "03-tmcra-web-console/desktop/tmcra-memory/package-lock.json",
        "03-tmcra-web-console/package-lock.json",
        "04-tmcra-desktop/package-lock.json",
        "05-tmcra-mobile/package-lock.json",
        "06-tmcra-sdk-integrations/integrations/openclaw/package-lock.json",
        "06-tmcra-sdk-integrations/integrations/vercel-ai-sdk/package-lock.json",
        "06-tmcra-sdk-integrations/sdk/typescript/package-lock.json",
    ]
    for path in package_jsons:
        check_json(errors, path, ("version",), NPM)
    for path in [
        "03-tmcra-web-console/desktop/tmcra-memory/package.json",
        "04-tmcra-desktop/package.json",
    ]:
        check_json(errors, path, ("tmcra", "fallbackPluginVersion"), CODEX)
    for path in package_locks:
        check_json(errors, path, ("version",), NPM)
        check_json(errors, path, ("packages", "", "version"), NPM)
        lock_text = (ROOT / path).read_text(encoding="utf-8")
        if "npmmirror.com" in lock_text:
            errors.append(f"{path}: dependency URLs must use the public npm registry")

    check_json(
        errors,
        "03-tmcra-web-console/package.json",
        ("name",),
        "@tmcra/web-console",
    )
    check_json(
        errors,
        "03-tmcra-web-console/package-lock.json",
        ("name",),
        "@tmcra/web-console",
    )
    check_json(
        errors,
        "03-tmcra-web-console/package-lock.json",
        ("packages", "", "name"),
        "@tmcra/web-console",
    )

    python_projects = [
        "06-tmcra-sdk-integrations/integrations/hermes/pyproject.toml",
        "06-tmcra-sdk-integrations/integrations/langgraph/pyproject.toml",
        "06-tmcra-sdk-integrations/integrations/openai-agents/pyproject.toml",
        "06-tmcra-sdk-integrations/sdk/python/pyproject.toml",
        "08-tmcra-mcp-server/pyproject.toml",
    ]
    for path in python_projects:
        check_toml(errors, path, PYTHON)

    check_json(
        errors,
        "01-tmcra-agent-memory-algorithm/shared_core_manifest.json",
        ("service_version",),
        CANONICAL,
    )
    check_json(
        errors,
        "02-tmcra-memory-api/tmcra_service/shared_core_manifest.json",
        ("service_version",),
        CANONICAL,
    )
    check_json(
        errors,
        "03-tmcra-web-console/public/openapi.json",
        ("info", "version"),
        CANONICAL,
    )
    check_json(
        errors,
        "07-tmcra-codex-plugins/tmcra-memory/.codex-plugin/plugin.json",
        ("version",),
        CODEX,
    )
    check_json(
        errors,
        "03-tmcra-web-console/public/downloads/tmcra-codex-release.json",
        ("plugin", "version"),
        CODEX,
    )
    check_json(
        errors,
        "07-tmcra-codex-plugins/tmcra-memory/.claude-plugin/plugin.json",
        ("version",),
        CLAUDE,
    )

    text_checks = [
        ("CITATION.cff", f"version: {CANONICAL}", 1),
        ("02-tmcra-memory-api/tmcra_service/__init__.py", f'__version__ = "{PYTHON}"', 1),
        ("06-tmcra-sdk-integrations/integrations/hermes/plugin.yaml", f"version: {PYTHON}", 1),
        ("06-tmcra-sdk-integrations/sdk/python/tmcra_client/__init__.py", f'__version__ = "{PYTHON}"', 1),
        ("08-tmcra-mcp-server/src/tmcra_mcp/__init__.py", f'__version__ = "{PYTHON}"', 1),
        ("06-tmcra-sdk-integrations/integrations/microsoft-agent-framework/src/TMCRA.AgentFramework/TMCRA.AgentFramework.csproj", f"<Version>{NPM}</Version>", 1),
        ("05-tmcra-mobile/android/app/build.gradle", 'versionCode 4', 1),
        ("05-tmcra-mobile/android/app/build.gradle", f'versionName "{CANONICAL}"', 1),
        ("05-tmcra-mobile/android/app/src/main/java/com/tmcra/memory/mobile/net/TmcraApiClient.java", f'CLIENT_VERSION = "{CANONICAL}"', 1),
        ("05-tmcra-mobile/ios/App/App.xcodeproj/project.pbxproj", f"MARKETING_VERSION = {APPLE};", 2),
        ("03-tmcra-web-console/desktop/tmcra-memory/src/renderer/index.html", f"v{CANONICAL}", 1),
        ("04-tmcra-desktop/src/renderer/index.html", f"v{CANONICAL}", 1),
        ("07-tmcra-codex-plugins/tmcra-memory/scripts/device_login.mjs", f'"{CODEX}"', 2),
        ("07-tmcra-codex-plugins/tmcra-memory/scripts/smoke_mcp.mjs", f'version: "{CODEX}"', 1),
        ("08-tmcra-mcp-server/src/tmcra_mcp/client.py", f"tmcra-mcp/{CANONICAL}", 1),
        ("08-tmcra-mcp-server/src/tmcra_mcp/server.py", f'"integration_version": "{CANONICAL}"', 1),
    ]
    for path, fragment, count in text_checks:
        check_text(errors, path, fragment, count=count)

    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    if not re.search(r"(?m)^date-released: 2026-08-20$", citation):
        errors.append("CITATION.cff: date-released must match the release-candidate date")

    if errors:
        print("Release version check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(
        "Release versions are aligned: "
        f"root={CANONICAL}, npm/nuget={NPM}, codex={CODEX}, python={PYTHON}, apple={APPLE}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
