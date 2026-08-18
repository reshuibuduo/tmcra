from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def run_cli(arguments: list[str]) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-m", "tmcra_service.cli", *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("TMCRA CLI returned a non-object result")
    return value


def pending_jobs(database: Path, scope: str) -> list[str]:
    connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    try:
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT job_id FROM jobs WHERE scope_name = ? AND state = 'pending' "
                "ORDER BY scope_seq",
                (scope,),
            )
        ]
    finally:
        connection.close()


def cancel_job(base_url: str, api_key: str, job_id: str) -> tuple[int, str]:
    request = Request(
        f"{base_url.rstrip('/')}/v1/jobs/{quote(job_id, safe='')}/cancel",
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
            return int(response.status), body
    except HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:2009")
    arguments = parser.parse_args()

    database = str(arguments.database.resolve())
    issued = run_cli(
        [
            "--database",
            database,
            "key-issue",
            "--tenant-id",
            arguments.tenant_id,
            "--scopes",
            "memory:write",
        ]
    )
    key_id = str(issued.get("key_id") or "")
    api_key = str(issued.get("api_key") or "")
    if not key_id or not api_key:
        raise RuntimeError("TMCRA CLI did not issue a usable temporary key")

    results: list[dict[str, object]] = []
    revoke_result: dict[str, object] = {}
    try:
        for job_id in pending_jobs(arguments.database, arguments.scope):
            status, body = cancel_job(arguments.base_url, api_key, job_id)
            error_code = ""
            if status != 200:
                try:
                    payload = json.loads(body)
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = {}
                if isinstance(payload, dict):
                    error = payload.get("error")
                    if isinstance(error, dict):
                        error_code = str(error.get("code") or "")
                    elif payload.get("detail"):
                        error_code = str(payload["detail"])
            results.append(
                {"job_id": job_id, "http_status": status, "error_code": error_code}
            )
    finally:
        revoke_result = run_cli(
            ["--database", database, "key-revoke", "--key-id", key_id]
        )

    print(
        json.dumps(
            {
                "scope": arguments.scope,
                "selected": len(results),
                "cancelled": sum(item["http_status"] == 200 for item in results),
                "safe_skipped": sum(item["http_status"] == 409 for item in results),
                "unexpected": [item for item in results if item["http_status"] not in (200, 409)],
                "temporary_key_revoked": bool(revoke_result.get("revoked")),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
