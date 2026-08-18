#!/usr/bin/env python3
"""Run the production startup gate without binding an HTTP listener.

The environment file is read locally and its values are never printed. The
underlying preflight performs model, storage, provider-pool, and shared-core
checks but deliberately makes no paid provider request.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from tmcra_service.app import build_components
from tmcra_service.settings import ServiceSettings


ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def load_env_file(path: Path) -> None:
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = ENV_LINE.match(line)
        if match is None:
            raise ValueError(f"invalid environment assignment at line {line_number}")
        key, raw_value = match.groups()
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, type=Path)
    args = parser.parse_args()
    load_env_file(args.env_file)
    settings = ServiceSettings.from_env()
    components = build_components(settings)
    try:
        report = components.startup.run(components.storage, components.online)
    finally:
        components.storage.stop()
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
