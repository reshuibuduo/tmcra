from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class MCPSettings:
    base_url: str
    api_key: str
    default_scope: str | None = None
    request_timeout_seconds: float = 60.0
    max_attempts: int = 3

    @classmethod
    def from_env(cls) -> "MCPSettings":
        base_url = os.getenv("TMCRA_BASE_URL", "").strip().rstrip("/")
        api_key = os.getenv("TMCRA_API_KEY", "").strip()
        default_scope = os.getenv("TMCRA_DEFAULT_SCOPE", "").strip() or None
        if not base_url:
            raise ConfigError("TMCRA_BASE_URL is required")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigError("TMCRA_BASE_URL must be an absolute HTTPS URL")
        if not api_key:
            raise ConfigError("TMCRA_API_KEY is required")
        timeout = _positive_float("TMCRA_REQUEST_TIMEOUT_SECONDS", 60.0)
        attempts = _positive_int("TMCRA_MAX_ATTEMPTS", 3)
        return cls(base_url, api_key, default_scope, timeout, attempts)


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
