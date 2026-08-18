from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse


KEY_NAMES = (
    "TMCRA_DEEPSEEK_WRITER_KEY_POOL",
    "TMCRA_WRITER_API_KEY_POOL",
)
BASE_URL_NAMES = (
    "TMCRA_DEEPSEEK_WRITER_BASE_URL",
    "TMCRA_WRITER_BASE_URL",
)


def fingerprints(raw: str) -> list[str]:
    return [
        hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:12]
        for value in raw.split(",")
        if value.strip()
    ]


def safe_host(raw: str) -> str:
    parsed = urlparse(raw.strip())
    return parsed.hostname or ""


def read_process_environment(pid: int) -> dict[str, str]:
    entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    result: dict[str, str] = {}
    for entry in entries:
        if b"=" not in entry:
            continue
        key, value = entry.split(b"=", 1)
        result[key.decode("utf-8", errors="replace")] = value.decode(
            "utf-8", errors="replace"
        )
    return result


def first(environment: dict[str, str], names: tuple[str, ...]) -> str:
    return next((environment[name] for name in names if environment.get(name)), "")


def parse_env_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    assignment = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = assignment.match(line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        result[match.group(1)] = value
    return result


def describe(environment: dict[str, str]) -> dict[str, object]:
    key_pool = first(environment, KEY_NAMES)
    base_url = first(environment, BASE_URL_NAMES)
    values = fingerprints(key_pool)
    return {
        "base_url_host": safe_host(base_url),
        "key_count": len(values),
        "key_fingerprints": values,
        "model": environment.get("TMCRA_WRITER_MODEL", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--env-file", type=Path, action="append", default=[])
    arguments = parser.parse_args()

    files: list[dict[str, object]] = []
    for path in arguments.env_file:
        resolved = path.resolve()
        if not resolved.is_file():
            files.append({"path": str(resolved), "exists": False})
            continue
        stat = resolved.stat()
        files.append(
            {
                "path": str(resolved),
                "exists": True,
                "mtime": stat.st_mtime,
                **describe(parse_env_file(resolved)),
            }
        )

    output = {
        "pid": arguments.pid,
        "process": describe(read_process_environment(arguments.pid)),
        "files": files,
    }
    print(json.dumps(output, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
