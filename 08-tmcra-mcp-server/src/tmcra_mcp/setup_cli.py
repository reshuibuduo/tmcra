from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from .config import ConfigError, MCPSettings, default_config_path


DEFAULT_SERVER_NAME = "tmcra-memory"
MODE_EXPLICIT = "explicit"
MODE_CODEX_HOOKS = "codex-hooks"
CODEX_PLUGIN_ID = "tmcra-memory@tmcra-local"


def _run(
    arguments: Sequence[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _require_device_config(path: Path) -> None:
    if not path.is_file():
        raise ConfigError(
            "TMCRA device authorization is missing; sign in once with the TMCRA app first"
        )
    # The normal MCP loader validates the HTTPS endpoint, credential, expiry,
    # timeout, and retry settings without echoing the credential.
    names = ("TMCRA_CONFIG_FILE", "TMCRA_API_KEY", "TMCRA_ACCESS_TOKEN")
    previous = {name: os.environ.get(name) for name in names}
    os.environ["TMCRA_CONFIG_FILE"] = str(path)
    os.environ.pop("TMCRA_API_KEY", None)
    os.environ.pop("TMCRA_ACCESS_TOKEN", None)
    try:
        MCPSettings.from_env()
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _codex_command(value: str) -> str:
    configured = value.strip() or os.getenv("TMCRA_CODEX_COMMAND", "codex").strip()
    resolved = shutil.which(configured)
    if resolved and "windowsapps" not in resolved.lower():
        return resolved

    # Windows can expose an unlaunchable App Execution Alias before the real
    # Codex runtime. These are the same locations used by the existing plugin
    # installer, and resolving them here also keeps explicit MCP setup usable.
    candidates = [Path.home() / ".codex" / "plugins" / ".plugin-appserver" / "codex.exe"]
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        runtime_root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
        if runtime_root.is_dir():
            candidates.extend(runtime_root.rglob("codex.exe"))
    available = [candidate for candidate in candidates if candidate.is_file()]
    if available:
        return str(max(available, key=lambda candidate: candidate.stat().st_mtime))
    raise RuntimeError("Codex CLI is not installed or is not on PATH")


def _json_output(result: subprocess.CompletedProcess[str], fallback: Any) -> Any:
    if result.returncode != 0:
        return fallback
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError):
        return fallback


def _is_python_mcp_registration(result: subprocess.CompletedProcess[str]) -> bool:
    """Recognize only the standalone server installed by this package."""

    payload = _json_output(result, None)
    if payload is None:
        return False
    serialized = json.dumps(payload, ensure_ascii=True).lower()
    return "tmcra_mcp" in serialized and ("-m" in serialized or "tmcra-mcp" in serialized)


def _enabled_codex_plugin(executable: str) -> dict[str, Any] | None:
    result = _run([executable, "plugin", "list", "--json"])
    payload = _json_output(result, {})
    installed = payload.get("installed", []) if isinstance(payload, dict) else []
    return next(
        (
            item
            for item in installed
            if isinstance(item, dict)
            and item.get("pluginId") == CODEX_PLUGIN_ID
            and item.get("enabled", True) is True
        ),
        None,
    )


def _installer_candidates() -> list[Path]:
    roots: list[Path] = [Path.cwd()]
    roots.extend(Path(__file__).resolve().parents)
    candidates: list[Path] = []
    for root in roots:
        powershell = [
            root / "Install-TMCRA.ps1",
            root / "plugins" / "tmcra-memory" / "scripts" / "install.ps1",
        ]
        shell = [root / "plugins" / "tmcra-memory" / "scripts" / "install.sh"]
        candidates.extend(powershell + shell if os.name == "nt" else shell + powershell)
    seen: set[Path] = set()
    unique: list[Path] = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _resolve_plugin_installer(value: str) -> Path:
    requested = value.strip() or os.getenv("TMCRA_CODEX_PLUGIN_INSTALLER", "").strip()
    if requested:
        candidate = Path(requested).expanduser().resolve()
        if not candidate.is_file():
            raise RuntimeError(f"TMCRA Codex plugin installer was not found: {candidate}")
        return candidate
    for candidate in _installer_candidates():
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError(
        "TMCRA Codex plugin installer was not found; extract the TMCRA release package "
        "and pass --plugin-installer with its Install-TMCRA.ps1 path"
    )


def _plugin_installer_command(path: Path) -> tuple[list[str], bool]:
    suffix = path.suffix.lower()
    if suffix == ".ps1":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            raise RuntimeError("PowerShell is required to run the TMCRA Codex plugin installer")
        return (
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(path),
                "-SkipConfigure",
            ],
            True,
        )
    if suffix == ".sh":
        shell = shutil.which("sh")
        if not shell:
            raise RuntimeError("A POSIX shell is required to run the TMCRA Codex plugin installer")
        # The existing POSIX installer owns its browser authorization flow and
        # currently has no SkipConfigure option. Delegating to it is still safer
        # than duplicating plugin installation and hook activation here.
        return [shell, str(path)], False
    raise RuntimeError("TMCRA Codex plugin installer must be Install-TMCRA.ps1 or install.sh")


def _installer_environment(*, config_path: Path, codex: str) -> dict[str, str]:
    environment = dict(os.environ)
    # Automatic setup consumes the protected device file. Do not accidentally
    # promote a developer credential from the caller environment into the
    # delegated normal-user installation flow.
    for name in ("TMCRA_API_KEY", "TMCRA_ACCESS_TOKEN", "TMCRA_SETUP_API_KEY"):
        environment.pop(name, None)
    environment["TMCRA_CONFIG_FILE"] = str(config_path)
    codex_parent = str(Path(codex).resolve().parent)
    environment["PATH"] = codex_parent + os.pathsep + environment.get("PATH", "")
    return environment


def install_explicit(
    *,
    name: str,
    codex: str,
    config_path: Path,
    replace: bool,
) -> dict[str, Any]:
    _require_device_config(config_path)
    executable = _codex_command(codex)
    existing = _run([executable, "mcp", "get", name, "--json"])
    removed_plugin = False
    if existing.returncode == 0 and not _is_python_mcp_registration(existing):
        plugin = _enabled_codex_plugin(executable)
        if plugin:
            if not replace:
                raise RuntimeError(
                    "Codex automatic Hooks mode is active; rerun explicit setup with "
                    "--replace to remove the TMCRA plugin and switch modes"
                )
            removed = _run([executable, "plugin", "remove", CODEX_PLUGIN_ID, "--json"])
            if removed.returncode != 0:
                raise RuntimeError("Codex could not remove the TMCRA automatic Hooks plugin")
            removed_plugin = True
            existing = _run([executable, "mcp", "get", name, "--json"])
    if existing.returncode == 0:
        if not replace:
            raise RuntimeError(
                f"Codex MCP server {name!r} already exists; inspect it or rerun with --replace"
            )
        removed = _run([executable, "mcp", "remove", name])
        if removed.returncode != 0:
            raise RuntimeError(f"Codex could not replace MCP server {name!r}")

    added = _run(
        [
            executable,
            "mcp",
            "add",
            name,
            "--env",
            f"TMCRA_CONFIG_FILE={config_path}",
            "--",
            sys.executable,
            "-m",
            "tmcra_mcp",
        ]
    )
    if added.returncode != 0:
        restored = True
        if removed_plugin:
            restored = (
                _run([executable, "plugin", "add", CODEX_PLUGIN_ID, "--json"]).returncode
                == 0
            )
        suffix = "" if restored else "; the automatic plugin could not be restored"
        raise RuntimeError(f"Codex could not add MCP server {name!r}{suffix}")
    return {
        "schema_version": "tmcra.mcp-setup.1",
        "status": "installed",
        "mode": MODE_EXPLICIT,
        "name": name,
        "config_path": str(config_path),
        "credential_embedded_in_codex_config": False,
        "transport": "stdio",
        "automatic_lifecycle_configured": False,
        "automatic_lifecycle_verified": False,
        "lifecycle_boundary": "explicit_mcp_tools",
        "scope_model": "caller_selected_shared_scope",
        "agent_attribution": "optional_tool_argument_or_TMCRA_AGENT_ID",
    }


def _restore_explicit_registration(
    *,
    executable: str,
    name: str,
    config_path: Path,
) -> bool:
    result = _run(
        [
            executable,
            "mcp",
            "add",
            name,
            "--env",
            f"TMCRA_CONFIG_FILE={config_path}",
            "--",
            sys.executable,
            "-m",
            "tmcra_mcp",
        ]
    )
    return result.returncode == 0


def install_codex_hooks(
    *,
    name: str,
    codex: str,
    config_path: Path,
    plugin_installer: str,
) -> dict[str, Any]:
    """Delegate automatic lifecycle setup to the existing Codex plugin installer.

    MCP itself remains an explicit tool protocol. The plugin supplies the
    UserPromptSubmit/Stop hooks and its own MCP inspection tools.
    """

    _require_device_config(config_path)
    executable = _codex_command(codex)
    installer = _resolve_plugin_installer(plugin_installer)
    command, reuses_authorization = _plugin_installer_command(installer)

    # A previous `--mode explicit` installation shadows the plugin's bundled
    # MCP server under the same name. Remove only registrations that are
    # positively identified as this Python package, and restore one if the
    # delegated installation fails.
    existing = _run([executable, "mcp", "get", name, "--json"])
    removed_explicit = _is_python_mcp_registration(existing)
    if removed_explicit:
        removed = _run([executable, "mcp", "remove", name])
        if removed.returncode != 0:
            raise RuntimeError(
                f"Codex could not replace explicit MCP server {name!r} with automatic hooks"
            )

    delegated = _run(
        command,
        env=_installer_environment(config_path=config_path, codex=executable),
    )
    if delegated.returncode != 0:
        restored = (
            _restore_explicit_registration(
                executable=executable,
                name=name,
                config_path=config_path,
            )
            if removed_explicit
            else True
        )
        suffix = "" if restored else "; the previous explicit registration could not be restored"
        raise RuntimeError(f"TMCRA Codex plugin installation failed{suffix}")

    return {
        "schema_version": "tmcra.mcp-setup.1",
        "status": "installed",
        "mode": MODE_CODEX_HOOKS,
        "name": name,
        "config_path": str(config_path),
        "credential_embedded_in_codex_config": False,
        "transport": "plugin_stdio",
        "automatic_lifecycle_configured": True,
        "automatic_lifecycle_verified": False,
        "automatic_lifecycle_state": "pending_hook_trust_and_runtime_verification",
        "lifecycle_provider": CODEX_PLUGIN_ID,
        "scope_model": "user_global_plus_project_shared",
        "agent_attribution": "codex_hook_managed",
        "authorization_reused": reuses_authorization,
        "hook_trust_required": True,
        "restart_required": True,
    }


# Backwards-compatible import for callers that used setup_cli.install directly.
install = install_explicit


def status_explicit(*, name: str, codex: str) -> dict[str, Any]:
    executable = _codex_command(codex)
    result = _run([executable, "mcp", "get", name, "--json"])
    return {
        "schema_version": "tmcra.mcp-setup.1",
        "mode": MODE_EXPLICIT,
        "name": name,
        "installed": _is_python_mcp_registration(result),
        "automatic_lifecycle_configured": False,
        "automatic_lifecycle_verified": False,
        "scope_model": "caller_selected_shared_scope",
    }


def status_codex_hooks(*, name: str, codex: str) -> dict[str, Any]:
    executable = _codex_command(codex)
    plugins_result = _run([executable, "plugin", "list", "--json"])
    features_result = _run([executable, "features", "list"])
    mcp_result = _run([executable, "mcp", "list", "--json"])

    plugins = _json_output(plugins_result, {})
    installed_plugins = plugins.get("installed", []) if isinstance(plugins, dict) else []
    plugin = next(
        (
            item
            for item in installed_plugins
            if isinstance(item, dict) and item.get("pluginId") == CODEX_PLUGIN_ID
        ),
        None,
    )
    plugin_enabled = bool(plugin and plugin.get("enabled", True) is True)
    hooks_enabled = bool(
        features_result.returncode == 0
        and re.search(r"^hooks\s+\S+\s+true\s*$", features_result.stdout, re.MULTILINE)
    )
    servers = _json_output(mcp_result, [])
    mcp_available = bool(
        isinstance(servers, list)
        and any(isinstance(item, dict) and item.get("name") == name for item in servers)
    )
    configured = plugin_enabled and hooks_enabled and mcp_available
    return {
        "schema_version": "tmcra.mcp-setup.1",
        "mode": MODE_CODEX_HOOKS,
        "name": name,
        "installed": configured,
        "automatic_lifecycle_configured": configured,
        "automatic_lifecycle_verified": None,
        "automatic_lifecycle_state": (
            "configured_requires_hook_trust_verification" if configured else "not_configured"
        ),
        "lifecycle_provider": CODEX_PLUGIN_ID,
        "scope_model": "user_global_plus_project_shared",
        "checks": {
            "plugin_enabled": plugin_enabled,
            "hooks_feature_enabled": hooks_enabled,
            "plugin_mcp_available": mcp_available,
        },
        # Codex deliberately requires the user to inspect and trust hooks. CLI
        # status cannot safely assert that interactive trust has been granted.
        "hook_trust_verification_required": True,
    }


def status(*, name: str, codex: str) -> dict[str, Any]:
    return status_explicit(name=name, codex=codex)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tmcra-mcp-setup",
        description=(
            "Configure TMCRA for Codex without copying the access token. "
            "Generic MCP is explicit; Codex automatic lifecycle requires the TMCRA hooks plugin."
        ),
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--codex", default="", help="Codex CLI executable")
    parser.add_argument("--name", default=DEFAULT_SERVER_NAME)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--replace", action="store_true")
    install_parser.add_argument(
        "--mode",
        choices=(MODE_EXPLICIT, MODE_CODEX_HOOKS),
        default=MODE_EXPLICIT,
        help="explicit MCP tools (default) or the Codex lifecycle hooks integration",
    )
    install_parser.add_argument(
        "--plugin-installer",
        default="",
        help=(
            "TMCRA release Install-TMCRA.ps1/install.sh; auto-discovered from an "
            "extracted release when --mode codex-hooks"
        ),
    )
    install_parser.add_argument(
        "--config",
        default="",
        help="Protected TMCRA device config; defaults to the shared user config",
    )
    status_parser = subparsers.add_parser("status")
    status_parser.add_argument(
        "--mode",
        choices=(MODE_EXPLICIT, MODE_CODEX_HOOKS),
        default=MODE_EXPLICIT,
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "install":
            config_path = Path(args.config or default_config_path()).expanduser().resolve()
            if args.mode == MODE_CODEX_HOOKS:
                result = install_codex_hooks(
                    name=args.name,
                    codex=args.codex,
                    config_path=config_path,
                    plugin_installer=args.plugin_installer,
                )
            else:
                result = install_explicit(
                    name=args.name,
                    codex=args.codex,
                    config_path=config_path,
                    replace=args.replace,
                )
        else:
            result = (
                status_codex_hooks(name=args.name, codex=args.codex)
                if args.mode == MODE_CODEX_HOOKS
                else status_explicit(name=args.name, codex=args.codex)
            )
    except (ConfigError, RuntimeError) as exc:
        if args.json_output:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=True))
        else:
            print(f"TMCRA MCP setup failed: {exc}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    elif args.command == "install":
        if result["mode"] == MODE_CODEX_HOOKS:
            print(
                "Installed the TMCRA Codex lifecycle integration. Restart Codex, "
                "run /hooks, inspect the three TMCRA hooks, and trust them."
            )
        else:
            print(
                f"Installed explicit Codex MCP server {result['name']!r}. "
                "Restart Codex to load it."
            )
    else:
        print("installed" if result["installed"] else "not installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
