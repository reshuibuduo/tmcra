#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: activate-site-release.sh RELEASE_ROOT RELEASE_NAME" >&2
  exit 64
fi

root="$(realpath -e "$1")"
release_name="$2"

if [[ "$root" != "/opt/tmcra-release" || ! "$release_name" =~ ^[0-9A-Za-zTZ._-]+$ ]]; then
  echo "release activation boundary is invalid" >&2
  exit 65
fi

release="$(realpath -e "$root/releases/$release_name")"
case "$release" in
  "$root"/releases/*) ;;
  *) echo "release is outside the immutable release directory" >&2; exit 65 ;;
esac

[[ -f "$release/dist/client/.vite/manifest.json" ]] || {
  echo "release has no completed client build" >&2
  exit 66
}

worker_config="$release/dist/server/wrangler.json"
deployment_env="$root/shared/deployment.env"
[[ -f "$worker_config" && -f "$deployment_env" ]] || {
  echo "release runtime configuration is incomplete" >&2
  exit 66
}

python3 - "$worker_config" "$deployment_env" <<'PY'
import json
import sys
import urllib.parse
from pathlib import Path

worker_path = Path(sys.argv[1])
environment_path = Path(sys.argv[2])
worker = json.loads(worker_path.read_text(encoding="utf-8"))
environment = {}
for raw_line in environment_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    environment[name.strip()] = value.strip()

required = {
    "TMCRA_MEMORY_API_BASE_URL",
    "TMCRA_MEMORY_API_CONTROL_BASE_URL",
    "TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK",
    "TMCRA_MEMORY_API_CONTROL_KEY",
    "TMCRA_MEMORY_API_STAFF_MONITORING_KEY",
    "TMCRA_MEMORY_API_TENANT_BINDINGS",
    "TMCRA_INTERNAL_BOOTSTRAP_OWNER_EMAIL",
    "TMCRA_DEVICE_TOKEN_ENCRYPTION_KEY",
    "TMCRA_DEVICE_FLOW_HASH_KEY",
    "TMCRA_DEVICE_MAINTENANCE_SECRET",
}
declared = set((worker.get("secrets") or {}).get("required") or [])
missing = sorted(required - declared)
if missing:
    raise SystemExit(f"release is missing Worker runtime declarations: {','.join(missing)}")

plain_vars = worker.get("vars") or {}
sensitive = {
    "TMCRA_MEMORY_API_CONTROL_KEY",
    "TMCRA_MEMORY_API_STAFF_MONITORING_KEY",
    "TMCRA_DEVICE_TOKEN_ENCRYPTION_KEY",
    "TMCRA_DEVICE_FLOW_HASH_KEY",
    "TMCRA_DEVICE_MAINTENANCE_SECRET",
}
embedded = sorted(sensitive.intersection(plain_vars))
if embedded:
    raise SystemExit(f"release embeds protected values as plain vars: {','.join(embedded)}")

if environment.get("TMCRA_MEMORY_API_CONTROL_ALLOW_HTTP_LOOPBACK") == "1":
    parsed = urllib.parse.urlsplit(environment.get("TMCRA_MEMORY_API_CONTROL_BASE_URL", ""))
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
        raise SystemExit("deployment loopback control URL is invalid")
    expected_address = f"127.0.0.1:{parsed.port}"
    bindings = (worker.get("unsafe") or {}).get("bindings") or []
    matching = [
        binding
        for binding in bindings
        if binding.get("name") == "TMCRA_MEMORY_API_CONTROL_FETCHER"
    ]
    if len(matching) != 1 or matching[0].get("address") != expected_address:
        raise SystemExit("release is missing the production loopback API binding")
PY

previous="$(readlink -f "$root/current")"
activated=0

rollback() {
  if [[ "$activated" -ne 1 ]]; then return; fi
  activated=0
  local rollback_link="$root/.current.rollback.$$"
  ln -s "$previous" "$rollback_link"
  mv -Tf "$rollback_link" "$root/current"
  sudo systemctl restart tmcra-site
}
trap rollback EXIT

next_link="$root/.current.$release_name.$$"
ln -s "$release" "$next_link"
mv -Tf "$next_link" "$root/current"
activated=1

sudo systemctl restart tmcra-site
sudo systemctl is-active --quiet tmcra-site

for attempt in {1..20}; do
  if curl --silent --show-error --fail --max-time 5 --header "Host: tmcra.com" --output /dev/null http://127.0.0.1:2000/download; then
    activated=0
    trap - EXIT
    echo "SITE_RELEASE_ACTIVE release=$release_name previous=$previous"
    exit 0
  fi
  sleep 1
done

echo "site did not pass the local release probe" >&2
exit 67
