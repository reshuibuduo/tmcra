from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

from tmcra_service.adapters.v4 import V4StorageAdapter
from tmcra_service.settings import ServiceSettings


def load_projection_module(path: Path):
    spec = importlib.util.spec_from_file_location(
        "tmcra_service.graph_projection_staged",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load staged graph projection")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--tenant")
    parser.add_argument("--scope", required=True)
    args = parser.parse_args()

    module = load_projection_module(args.module.resolve())
    settings = ServiceSettings.from_env()
    tenant = args.tenant
    if not tenant:
        connection = sqlite3.connect(
            settings.control_db.as_uri() + "?mode=ro",
            uri=True,
        )
        try:
            rows = connection.execute(
                "SELECT DISTINCT tenant_id FROM jobs WHERE scope_name=? LIMIT 2",
                (args.scope,),
            ).fetchall()
        finally:
            connection.close()
        if len(rows) != 1:
            raise RuntimeError("scope does not resolve to exactly one tenant")
        tenant = str(rows[0][0])
    storage = V4StorageAdapter(settings)
    started = time.perf_counter()
    projection = module.MemoryGraphProjection.from_available_storage(
        storage,
        tenant_id=tenant,
        scope_name=args.scope,
    )
    graph = projection.overview(
        layers=("slow", "fast", "source"),
        limit=180,
    )
    print(json.dumps({
        "snapshot_state": graph["snapshot_state"],
        "provisional": graph["provisional"],
        "counts": graph["counts"],
        "resolved_layers": graph["resolved_layers"],
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
