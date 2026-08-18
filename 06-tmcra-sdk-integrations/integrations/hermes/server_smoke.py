#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from agent.memory_provider import MemoryProvider
from tmcra_plugin import TmcraMemoryProvider


class _BufferedResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = BytesIO(body)

    def read(self) -> bytes:
        return self._body.read()

    def __enter__(self) -> "_BufferedResponse":
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False


class _CapturingOpener:
    def __init__(self) -> None:
        self.ingest_response: dict[str, Any] = {}

    def __call__(self, request: Request, timeout: float) -> _BufferedResponse:
        with urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            body = response.read()
        if request.full_url.endswith("/ingest"):
            parsed = json.loads(body.decode("utf-8")) if body else {}
            self.ingest_response = parsed if isinstance(parsed, dict) else {}
        return _BufferedResponse(status, body)


def _get_job(base_url: str, api_key: str, job_id: str) -> dict[str, Any]:
    request = Request(
        f"{base_url.rstrip('/')}/v1/jobs/{quote(job_id, safe='')}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    with urlopen(request, timeout=60) as response:
        value = json.loads(response.read().decode("utf-8"))
    return value if isinstance(value, dict) else {}


def main() -> int:
    required = (
        "TMCRA_BASE_URL",
        "TMCRA_TENANT_ID",
        "TMCRA_API_KEY",
        "TMCRA_IDENTITY_SECRET",
        "TMCRA_HERMES_QUEUE_PATH",
    )
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError("required environment is missing: " + ",".join(missing))

    run_id = uuid.uuid4().hex
    marker = f"hermes-native-{run_id[:12]}"
    opener = _CapturingOpener()
    provider = TmcraMemoryProvider(opener=opener, start_worker=False)
    if not isinstance(provider, MemoryProvider):
        raise RuntimeError("TMCRA provider is not using the installed Hermes MemoryProvider")
    provider.initialize(
        f"server-smoke-session-{run_id}",
        hermes_home=str(Path(os.environ["TMCRA_HERMES_QUEUE_PATH"]).parent),
        platform="server-smoke",
        user_id=f"server-smoke-user-{run_id}",
        agent_identity="tmcra-launch",
    )
    provider.prefetch(f"Remember native verification code {marker}.")
    provider.sync_turn(
        f"Remember that my native verification code is {marker}.",
        f"Stored native verification code {marker}.",
    )
    drain = provider.drain_once()
    if drain["sent"] != 1:
        raise RuntimeError(f"Hermes native ingest did not send: {drain}")
    job_id = str(opener.ingest_response.get("job_id", ""))
    if not job_id:
        raise RuntimeError("Hermes native ingest response has no job_id")

    deadline = time.monotonic() + float(os.getenv("TMCRA_SMOKE_TIMEOUT_SECONDS", "1800"))
    job: dict[str, Any] = {}
    while time.monotonic() < deadline:
        job = _get_job(os.environ["TMCRA_BASE_URL"], os.environ["TMCRA_API_KEY"], job_id)
        if job.get("status") == "succeeded":
            break
        if job.get("status") in {"failed", "cancelled"}:
            raise RuntimeError(f"Hermes native ingest job ended as {job.get('status')}")
        time.sleep(1.5)
    if job.get("status") != "succeeded":
        raise RuntimeError("Hermes native ingest job timed out")

    context = provider.prefetch("What is my native verification code?")
    if marker not in context:
        raise RuntimeError("Hermes native recall did not contain the ingested marker")
    provider.shutdown()

    report = {
        "schema_version": "tmcra.hermes-server-smoke.1",
        "status": "passed",
        "provider": provider.name,
        "job_id": job_id,
        "memory_provider_abc": True,
        "recalled_marker": True,
    }
    report_path = os.getenv("TMCRA_SMOKE_REPORT", "").strip()
    if report_path:
        Path(report_path).write_text(
            json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(report_path, 0o600)
        except OSError:
            pass
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
