"""Synchronize edited shared core only after checking untouched mirror hashes."""
import hashlib
import json
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
api = root / "02-tmcra-memory-api"
algorithm = root / "01-tmcra-agent-memory-algorithm"
manifest_path = api / "tmcra_service/shared_core_manifest.json"
manifest = json.loads(manifest_path.read_text())
for item in manifest["algorithm_files"]:
    source, mirror = api / item["path"], algorithm / item["path"]
    actual = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual != item["sha256"]:
        if hashlib.sha256(mirror.read_bytes()).hexdigest() not in {item["sha256"], actual}:
            raise RuntimeError(f"independent mirror changes require review: {mirror}")
        shutil.copyfile(source, mirror)
        item["sha256"] = actual
for name in ("tmcra_local_only.py", "tmcra_local_models.py"):
    shutil.copyfile(api / name, algorithm / name)
    digest = hashlib.sha256((api / name).read_bytes()).hexdigest()
    item = next((entry for entry in manifest["algorithm_files"] if entry["path"] == name), None)
    if item:
        item["sha256"] = digest
    else:
        manifest["algorithm_files"].append({"path": name, "sha256": digest})
(algorithm / "deploy").mkdir(exist_ok=True)
shutil.copyfile(api / "deploy/local-model-profiles.json", algorithm / "deploy/local-model-profiles.json")
manifest["generated_on"] = "2026-09-06"
text = json.dumps(manifest, indent=2) + "\n"
manifest_path.write_text(text)
(algorithm / "shared_core_manifest.json").write_text(text)
print(f"synchronized {len(manifest['algorithm_files'])} shared-core files")
