from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


class ConfigError(RuntimeError):
    pass


DEFAULT_BASE_URL = "https://api.tmcra.com"
INTEGRATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def default_config_path() -> Path:
    configured = os.getenv("TMCRA_CONFIG_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    # Some launchers deliberately provide a minimal environment.  Config
    # discovery must not fail before explicit TMCRA_* environment variables
    # can be validated.
    try:
        home = Path.home()
    except RuntimeError:
        home = Path.cwd()
    binding_file = Path(os.getenv("TMCRA_LOCAL_BINDING_FILE") or home / ".config/tmcra/local-memory.json")
    if binding_file.exists():
        binding = _load_config(binding_file)
        data_root = Path(str(binding.get("dataRoot") or ""))
        profile = binding.get("profile")
        if binding.get("schemaVersion") != 1 or binding.get("mode") != "local" or not data_root.is_absolute() or profile not in {"lite-cpu", "balanced-bge", "quality-qwen"}:
            raise ConfigError("Invalid local memory selection; cloud fallback is disabled")
        selected = data_root / "state" / profile / "secrets/client-plugin.json"
        if not selected.is_file() or _load_config(selected).get("deploymentMode") != "local":
            raise ConfigError("Selected local memory installation is not configured yet; cloud fallback is disabled")
        return selected
    return home / ".config" / "tmcra" / "config.json"


def assert_active_memory_connection(settings: "MCPSettings") -> None:
    if os.getenv("TMCRA_CONFIG_FILE", "").strip():
        return  # An explicit advanced configuration remains authoritative.
    current = _load_config(default_config_path())
    if current.get("deploymentMode") == "local" and (
        settings.base_url != current.get("baseUrl") or settings.api_key != current.get("apiKey")
    ):
        raise ConfigError("Memory switched to local; restart the MCP host. Previous cloud requests are blocked")


@dataclass(frozen=True)
class MCPSettings:
    base_url: str
    api_key: str
    default_scope: str | None = None
    request_timeout_seconds: float = 60.0
    max_attempts: int = 3
    default_agent_id: str | None = None
    integration_id: str | None = None
    deployment_mode: str = "service"

    @classmethod
    def from_env(cls) -> "MCPSettings":
        config = _load_config(default_config_path())
        local = config.get("deploymentMode") == "local"
        base_url = str(
            os.getenv("TMCRA_BASE_URL", "").strip()
            or config.get("baseUrl")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        api_key = str(
            os.getenv("TMCRA_API_KEY", "").strip()
            or config.get("accessToken")
            or config.get("apiKey")
            or ""
        ).strip()
        if local:
            base_url = str(config.get("baseUrl") or "").rstrip("/")
            api_key = str(config.get("apiKey") or "").strip()
        default_scope = (
            os.getenv("TMCRA_DEFAULT_SCOPE", "").strip()
            or str(config.get("defaultScope") or "").strip()
            or None
        )
        default_agent_id = (
            os.getenv("TMCRA_AGENT_ID", "").strip()
            or str(config.get("mcpAgentId") or config.get("defaultAgentId") or "").strip()
            or None
        )
        integration_id = (
            os.getenv("TMCRA_INTEGRATION_ID", "").strip()
            or str(
                (
                    config.get("integrationIds")
                    if isinstance(config.get("integrationIds"), Mapping)
                    else {}
                ).get("mcp")
                or ""
            ).strip()
            or str(config.get("integrationId") or "").strip()
            or None
        )
        parsed = urlparse(base_url)
        if (
            (parsed.scheme != "https" if not local else (
                parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
                or not parsed.port or parsed.path not in {"", "/"}))
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigError("TMCRA_BASE_URL must be an HTTPS origin, or numeric loopback HTTP for an explicit local identity, without credentials, query, or fragment")
        if not api_key:
            raise ConfigError(
                "TMCRA credential is missing; sign in with the TMCRA app or set TMCRA_API_KEY"
            )
        _validate_expiry(config)
        configured_timeout = float(config.get("timeoutMs", 60_000)) / 1000
        timeout = _positive_float("TMCRA_REQUEST_TIMEOUT_SECONDS", configured_timeout)
        attempts = _positive_int("TMCRA_MAX_ATTEMPTS", 3)
        if default_agent_id and len(default_agent_id) > 200:
            raise ConfigError("TMCRA_AGENT_ID must be at most 200 characters")
        if integration_id and not INTEGRATION_ID_PATTERN.fullmatch(integration_id):
            raise ConfigError("TMCRA_INTEGRATION_ID has an invalid format")
        return cls(
            base_url,
            api_key,
            default_scope,
            timeout,
            attempts,
            default_agent_id,
            integration_id,
            "local" if local else "service",
        )


def _load_config(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"TMCRA config is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"TMCRA config must contain a JSON object: {path}")
    return payload


def _validate_expiry(config: Mapping[str, Any]) -> None:
    raw = config.get("expiresAt")
    if not raw:
        return
    try:
        expires = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigError("TMCRA credential expiry is invalid; sign in with the TMCRA app again") from exc
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        raise ConfigError("TMCRA credential expired; sign in with the TMCRA app again")


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be positive")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be positive")
    return value
