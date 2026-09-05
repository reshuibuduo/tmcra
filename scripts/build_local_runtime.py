"""Build a source-only offline-runtime preview; no models, keys, env, or user state."""
import argparse
import hashlib
import json
import zipfile
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
api = root / "02-tmcra-memory-api"
parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, default=root / "release/tmcra-local-windows-preview.zip")
parser.add_argument("--vendor", type=Path, action="append", default=[], help="Embed the checked runtime in a Codex or DSH plugin")
args = parser.parse_args()
files = [p for p in api.glob("*.py")]
for directory in ("tmcra_service", "core", "ops"):
    files += [p for p in (api / directory).rglob("*")
              if p.is_file() and p.suffix in {".py", ".json"}
              and not {"__pycache__", "tests", "test-artifacts"}.intersection(p.relative_to(api).parts)]
files += [api / "requirements-tmcra-service.txt", api / "models/tmcra_v3_reranker.pt",
          api / "deploy/local-model-profiles.json", api / "deploy/Install-TmcraLocal.ps1",
          api / "deploy/Start-TmcraLocal.ps1", api / "deploy/local-bootstrap/sitecustomize.py"]
files.append(api / "deploy/Local-SetupHelpers.ps1")
files = sorted(set(files))
payloads = {path: path.read_bytes() if path.suffix == '.pt' else path.read_bytes().replace(b'\r\n', b'\n') for path in files}
inventory = {path.relative_to(api).as_posix(): hashlib.sha256(payloads[path]).hexdigest() for path in files}
inventory_text = json.dumps(inventory, indent=2) + "\n"
for plugin in args.vendor:
    plugin = plugin.resolve()
    if not ((plugin / '.codex-plugin/plugin.json').is_file() or (plugin / 'package.json').is_file()):
        raise RuntimeError(f"not a plugin checkout: {plugin}")
    target = plugin / 'runtime/memory-api'
    old_manifest = target / 'runtime-files.json'
    old = json.loads(old_manifest.read_text()) if old_manifest.is_file() else {}
    for path in files:
        relative = path.relative_to(api).as_posix()
        destination = target / relative
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() not in {old.get(relative), inventory[relative]}:
            raise RuntimeError(f"independent vendored runtime changes require review: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payloads[path])
    old_manifest.write_text(inventory_text, encoding='utf-8', newline='\n')
    shutil.copyfile(root / 'LICENSE', plugin / 'runtime/LICENSE')
args.output.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as archive:
    for path in files:
        if not path.is_file():
            raise RuntimeError(f"runtime source missing: {path}")
        archive.writestr("memory-api/" + path.relative_to(api).as_posix(), payloads[path])
    archive.write(root / "LICENSE", "LICENSE")
    archive.write(root / "docs/LOCAL_DEPLOYMENT_PREVIEW.zh-CN.md", "README.zh-CN.md")
    archive.writestr("Install.ps1", '''[CmdletBinding()]
param(
  [ValidateSet('lite-cpu','balanced-bge','quality-qwen')][string]$Profile='lite-cpu',
  [string]$DataDir=(Join-Path $env:LOCALAPPDATA 'TMCRA/local'),
  [ValidateSet('auto','cpu','cuda')][string]$Device='auto',
  [switch]$PrepareOnly,
  [switch]$WaitReady
)
$ErrorActionPreference='Stop'
& "$PSScriptRoot/memory-api/deploy/Install-TmcraLocal.ps1" @PSBoundParameters
if ($LASTEXITCODE) { exit $LASTEXITCODE }
''')
    archive.writestr("Install-Local.cmd", '@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install.ps1" -WaitReady\r\nif errorlevel 1 pause\r\n')
    archive.writestr("Start.ps1", '''[CmdletBinding()]
param([string]$DataDir=(Join-Path $env:LOCALAPPDATA 'TMCRA/local'))
$ErrorActionPreference='Stop'
& "$PSScriptRoot/memory-api/deploy/Start-TmcraLocal.ps1" @PSBoundParameters
''')
    archive.writestr("runtime-files.json", inventory_text)
    archive.writestr("memory-api/runtime-files.json", inventory_text)
print(json.dumps({"archive": str(args.output), "files": len(files), "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}))
