"""Install the TMCRA provider into Hermes' user-plugin directory.

Hermes memory-provider discovery scans ``$HERMES_HOME/plugins/<name>``.  A
Python wheel alone is therefore not proof that a memory provider is active;
this CLI performs the small, profile-local copy and activation step.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

PLUGIN_NAME = "tmcra-hermes"


def _home(value: str = "") -> Path:
    return Path(value or os.getenv("HERMES_HOME", "") or Path.home() / ".hermes").expanduser().resolve()


def _asset(name: str) -> Path:
    module_dir = Path(__file__).resolve().parent
    for local in (
        module_dir / name,
        # ``pip --target`` relocates setuptools ``data-files`` below the
        # target directory, next to this top-level module.
        module_dir / "share" / "tmcra-hermes-plugin" / name,
        # A normal venv/system install places the same assets below sys.prefix.
        Path(sys.prefix) / "share" / "tmcra-hermes-plugin" / name,
    ):
        if local.is_file():
            return local
    try:
        from importlib.metadata import distribution

        dist = distribution("tmcra-hermes-plugin")
        for candidate in dist.files or ():
            if candidate.name == name:
                located = Path(dist.locate_file(candidate)).resolve()
                if located.is_file():
                    return located
    except Exception:
        pass
    raise FileNotFoundError(f"packaged Hermes asset is missing: {name}")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - Hermes includes PyYAML.
        raise RuntimeError("PyYAML is required to activate the Hermes provider") from exc
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"Hermes config must be a mapping: {path}")
    return value


def _write_yaml(path: Path, value: dict[str, Any]) -> None:
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as temporary:
        yaml.safe_dump(value, temporary, allow_unicode=True, sort_keys=False)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        temporary_path.unlink(missing_ok=True)


def _activate(home: Path, provider: str | None) -> None:
    config_path = home / "config.yaml"
    config = _read_yaml(config_path)
    memory = config.get("memory")
    if not isinstance(memory, dict):
        memory = {}
        config["memory"] = memory
    if provider:
        memory["provider"] = provider
    elif memory.get("provider") == PLUGIN_NAME:
        memory.pop("provider", None)
    _write_yaml(config_path, config)


def install(home: Path, *, activate: bool = True) -> Path:
    destination = home / "plugins" / PLUGIN_NAME
    staging = destination.with_name(f".{PLUGIN_NAME}.staging-{os.getpid()}")
    backup = destination.with_name(f".{PLUGIN_NAME}.backup-{int(time.time())}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, mode=0o700)
    assets = {
        "tmcra_plugin.py": "__init__.py",
        "plugin.yaml": "plugin.yaml",
        "README.md": "README.md",
        "README.zh-CN.md": "README.zh-CN.md",
        "INSTALL.md": "INSTALL.md",
        "INSTALL.zh-CN.md": "INSTALL.zh-CN.md",
    }
    try:
        for source, target in assets.items():
            shutil.copy2(_asset(source), staging / target)
        if destination.exists():
            os.replace(destination, backup)
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    if activate:
        _activate(home, PLUGIN_NAME)
    return destination


def uninstall(home: Path, *, deactivate: bool = True) -> bool:
    destination = home / "plugins" / PLUGIN_NAME
    removed = False
    if destination.is_dir():
        manifest = destination / "plugin.yaml"
        if not manifest.exists() or PLUGIN_NAME not in manifest.read_text(encoding="utf-8", errors="replace"):
            raise RuntimeError(f"refusing to remove an unverified directory: {destination}")
        tombstone = destination.with_name(f".{PLUGIN_NAME}.remove-{os.getpid()}")
        os.replace(destination, tombstone)
        shutil.rmtree(tombstone)
        removed = True
    if deactivate:
        _activate(home, None)
    return removed


def status(home: Path) -> dict[str, Any]:
    destination = home / "plugins" / PLUGIN_NAME
    config = _read_yaml(home / "config.yaml")
    memory = config.get("memory") if isinstance(config.get("memory"), dict) else {}
    env_file = home / ".env"
    configured_names: set[str] = set()
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            name, separator, value = line.partition("=")
            if separator and value.strip() and name.strip() in {"TMCRA_API_KEY", "TMCRA_IDENTITY_SECRET"}:
                configured_names.add(name.strip())
    return {
        "schema_version": "tmcra.hermes-install-status.1",
        "installed": (destination / "__init__.py").is_file() and (destination / "plugin.yaml").is_file(),
        "active": memory.get("provider") == PLUGIN_NAME,
        "credentials_configured": configured_names == {"TMCRA_API_KEY", "TMCRA_IDENTITY_SECRET"},
        "hermes_home": str(home),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tmcra-hermes")
    parser.add_argument("--hermes-home", default="", help="Hermes profile directory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_parser = subparsers.add_parser("install", help="install and activate the user memory provider")
    install_parser.add_argument("--no-activate", action="store_true")
    uninstall_parser = subparsers.add_parser("uninstall", help="remove the provider from this profile")
    uninstall_parser.add_argument("--keep-active-config", action="store_true")
    subparsers.add_parser("status", help="show non-secret install state as JSON")
    args = parser.parse_args(argv)
    home = _home(args.hermes_home)
    if args.command == "install":
        path = install(home, activate=not args.no_activate)
        print(f"Installed {PLUGIN_NAME} at {path}")
        print("Run `hermes memory setup`, select tmcra-hermes, then restart Hermes.")
        return 0
    if args.command == "uninstall":
        removed = uninstall(home, deactivate=not args.keep_active_config)
        print("Removed." if removed else "Provider was not installed.")
        return 0
    print(json.dumps(status(home), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
