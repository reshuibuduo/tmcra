from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
from types import ModuleType


def _candidate_paths() -> list[Path]:
    current = Path(__file__).resolve()
    candidates: list[Path] = []
    env_path = os.environ.get("TMCRA_MEMORY_KIT_SRC", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(
        [
            current.parents[4] / "tmcra-memory-kit" / "src",
            current.parents[3] / "tmcra-memory-kit" / "src",
            current.parents[2] / "tmcra-memory-kit" / "src",
            current.parents[1] / "tmcra-memory-kit" / "src",
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def load_tmcra_memory() -> ModuleType:
    try:
        return importlib.import_module("tmcra_memory")
    except ModuleNotFoundError:
        pass

    for candidate in _candidate_paths():
        if not candidate.exists():
            continue
        candidate_str = str(candidate)
        if candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)
        try:
            return importlib.import_module("tmcra_memory")
        except ModuleNotFoundError:
            continue
    raise ModuleNotFoundError(
        "tmcra_memory is not available. Install the package or set TMCRA_MEMORY_KIT_SRC to the package src directory."
    )


def tmcra_memory_available() -> bool:
    try:
        load_tmcra_memory()
        return True
    except ModuleNotFoundError:
        return False
