from __future__ import annotations

import json
import os
import signal
import threading
import urllib.error
import urllib.request
import uuid
from typing import Any


MINIMUM_SECRET_LENGTH = 43
MAXIMUM_RESPONSE_BYTES = 16_384


def require_configuration() -> tuple[str, str, float]:
    host = os.environ.get("TMCRA_UPSTREAM_HOST", "127.0.0.1").strip()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("TMCRA device maintenance must target the local application")
    port = int(os.environ.get("TMCRA_UPSTREAM_PORT", "2001"))
    if port < 1 or port > 65_535:
        raise RuntimeError("TMCRA_UPSTREAM_PORT is invalid")
    secret = os.environ.get("TMCRA_DEVICE_MAINTENANCE_SECRET", "").strip()
    if len(secret) < MINIMUM_SECRET_LENGTH:
        raise RuntimeError("TMCRA_DEVICE_MAINTENANCE_SECRET is missing or too short")
    interval = float(os.environ.get("TMCRA_DEVICE_MAINTENANCE_INTERVAL_SECONDS", "30"))
    if interval < 5 or interval > 300:
        raise RuntimeError("TMCRA_DEVICE_MAINTENANCE_INTERVAL_SECONDS must be between 5 and 300")
    return f"http://{host}:{port}/api/device/v1/maintenance", secret, interval


def run_once(url: str, secret: str, timeout: float = 10.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "X-Request-ID": str(uuid.uuid4()),
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"maintenance endpoint returned HTTP {response.status}")
        body = response.read(MAXIMUM_RESPONSE_BYTES + 1)
    if len(body) > MAXIMUM_RESPONSE_BYTES:
        raise RuntimeError("maintenance endpoint returned an oversized response")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("maintenance endpoint returned an invalid response")
    for field in ("attempted", "pending", "processing", "due"):
        value = payload.get(field)
        if not isinstance(value, int) or value < 0:
            raise RuntimeError("maintenance endpoint returned invalid counters")
    return payload


def main() -> None:
    url, secret, interval = require_configuration()
    stopping = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    last_summary: tuple[int, int, int] | None = None
    consecutive_failures = 0
    while not stopping.is_set():
        try:
            result = run_once(url, secret)
            summary = (
                int(result["pending"]),
                int(result["processing"]),
                int(result["due"]),
            )
            attempted = int(result["attempted"])
            if attempted or summary != last_summary:
                level = "warning" if summary[2] else "ok"
                print(
                    "device maintenance "
                    f"{level} attempted={attempted} pending={summary[0]} "
                    f"processing={summary[1]} due={summary[2]}",
                    flush=True,
                )
            last_summary = summary
            consecutive_failures = 0
        except (OSError, ValueError, RuntimeError, urllib.error.HTTPError) as error:
            consecutive_failures += 1
            print(
                "device maintenance failed "
                f"attempt={consecutive_failures} error={type(error).__name__}",
                flush=True,
            )
        stopping.wait(interval)


if __name__ == "__main__":
    main()
