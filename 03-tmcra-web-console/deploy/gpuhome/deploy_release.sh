#!/usr/bin/env bash
set -Eeuo pipefail

: "${TMCRA_RELEASE:?TMCRA_RELEASE is required}"
: "${TMCRA_ARCHIVE:?TMCRA_ARCHIVE is required}"
: "${TMCRA_ARCHIVE_SHA256:?TMCRA_ARCHIVE_SHA256 is required}"

case "$TMCRA_RELEASE" in
  *[!0-9A-Za-zTZ._-]*) echo "invalid release name" >&2; exit 2 ;;
esac

root="${TMCRA_DEPLOY_ROOT:-/opt/tmcra/tmcra-official}"
shared="$root/shared"
release_root="${TMCRA_RELEASE_ROOT:-/opt/tmcra-data/tmcra-official/releases}"
release="$release_root/$TMCRA_RELEASE"
temp_release="$release_root/.$TMCRA_RELEASE.tmp.$$"
node_root="${TMCRA_NODE_ROOT:-/opt/tmcra/.local/node-v24.18.0}"
node="$node_root/bin/node"
npm="$node_root/bin/npm"
systemd_service="${TMCRA_SYSTEMD_SERVICE:-}"
installer_input="${TMCRA_INSTALLER:-}"
installer_input_sha256="${TMCRA_INSTALLER_SHA256:-}"
desktop_update_input="${TMCRA_DESKTOP_UPDATE_DIR:-}"
preinstalled="${TMCRA_PREINSTALLED:-0}"
previous="$(readlink -f "$root/current" || true)"
activated=0
installer_name=TMCRA-Memory-Setup-latest.exe
installer_final="$shared/downloads/$installer_name"
installer_stage=""
installer_backup=""
installer_activated=0
desktop_update_final="$shared/downloads/desktop/windows/x64"
desktop_update_stage=""
desktop_update_backup=""
desktop_update_activated=0
deployment_env="$shared/deployment.env"
deployment_env_backup=""
deployment_env_activated=0

case "$preinstalled" in
  0|1) ;;
  *) echo "TMCRA_PREINSTALLED must be 0 or 1" >&2; exit 2 ;;
esac

for directory in "$root" "$release_root" "$node_root"; do
  case "$directory" in
    /*) ;;
    *) echo "deployment paths must be absolute" >&2; exit 2 ;;
  esac
done

if [ -n "$installer_input" ] || [ -n "$installer_input_sha256" ]; then
  [ -n "$installer_input" ] && [ -n "$installer_input_sha256" ] || {
    echo "TMCRA_INSTALLER and TMCRA_INSTALLER_SHA256 must be provided together" >&2
    exit 2
  }
  case "$installer_input" in
    /*) ;;
    *) echo "TMCRA_INSTALLER must be an absolute path" >&2; exit 2 ;;
  esac
  if ! [[ "$installer_input_sha256" =~ ^[0-9a-fA-F]{64}$ ]]; then
    echo "TMCRA_INSTALLER_SHA256 must be a SHA-256 digest" >&2
    exit 2
  fi
fi

if [ -n "$desktop_update_input" ]; then
  case "$desktop_update_input" in
    /*) ;;
    *) echo "TMCRA_DESKTOP_UPDATE_DIR must be an absolute path" >&2; exit 2 ;;
  esac
  [ -d "$desktop_update_input" ] && [ ! -L "$desktop_update_input" ] || {
    echo "TMCRA_DESKTOP_UPDATE_DIR must be a real directory" >&2
    exit 2
  }
fi

stop_supervisor() {
  if [ -n "$systemd_service" ]; then
    sudo -n systemctl stop -- "$systemd_service"
    return
  fi

  supervisor_pids=()
  child_pids=()
  if [ -f "$shared/supervisor.pid" ]; then
    recorded_pid="$(cat "$shared/supervisor.pid" || true)"
    if [ -n "$recorded_pid" ] && kill -0 "$recorded_pid" 2>/dev/null; then
      recorded_command="$(tr '\0' ' ' <"/proc/$recorded_pid/cmdline" 2>/dev/null || true)"
      case "$recorded_command" in
        *"$root/current/deploy/gpuhome/supervisor.py"*) supervisor_pids+=("$recorded_pid") ;;
        *)
          echo "refusing to stop an unverified supervisor PID: $recorded_pid" >&2
          return 1
          ;;
      esac
    fi
  fi

  # A failed activation can leave the PID file pointing at a newer supervisor
  # while an older, still-valid instance owns the ports. Discover every
  # supervisor for this deployment root and stop all verified process trees.
  for command_file in /proc/[0-9]*/cmdline; do
    [ -r "$command_file" ] || continue
    candidate_pid="${command_file#/proc/}"
    candidate_pid="${candidate_pid%/cmdline}"
    candidate_command="$(tr '\0' ' ' <"$command_file" 2>/dev/null || true)"
    case "$candidate_command" in
      *"$root/current/deploy/gpuhome/supervisor.py"*)
        case " ${supervisor_pids[*]} " in
          *" $candidate_pid "*) ;;
          *) supervisor_pids+=("$candidate_pid") ;;
        esac
        ;;
    esac
  done

  for supervisor_pid in "${supervisor_pids[@]}"; do
    for child_pid in $(ps -o pid= --ppid "$supervisor_pid" 2>/dev/null || true); do
      child_pids+=("$child_pid")
    done
    kill -TERM "$supervisor_pid" 2>/dev/null || true
  done

  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    supervisors_running=0
    for supervisor_pid in "${supervisor_pids[@]}"; do
      kill -0 "$supervisor_pid" 2>/dev/null && supervisors_running=1
    done
    [ "$supervisors_running" -eq 0 ] && break
    sleep 1
  done

  for child_pid in "${child_pids[@]}"; do
    if kill -0 "$child_pid" 2>/dev/null; then
      child_group="$(ps -o pgid= -p "$child_pid" | tr -d '[:space:]')"
      if [ -n "$child_group" ] && [ "$child_group" = "$child_pid" ]; then
        kill -TERM -- "-$child_group" 2>/dev/null || true
      else
        kill -TERM "$child_pid" 2>/dev/null || true
      fi
    fi
  done
  sleep 1

  for supervisor_pid in "${supervisor_pids[@]}"; do
    kill -KILL "$supervisor_pid" 2>/dev/null || true
  done
  for child_pid in "${child_pids[@]}"; do
    if kill -0 "$child_pid" 2>/dev/null; then
      child_group="$(ps -o pgid= -p "$child_pid" | tr -d '[:space:]')"
      if [ -n "$child_group" ] && [ "$child_group" = "$child_pid" ]; then
        kill -KILL -- "-$child_group" 2>/dev/null || true
      else
        kill -KILL "$child_pid" 2>/dev/null || true
      fi
    fi
  done
  sleep 1

  for supervisor_pid in "${supervisor_pids[@]}"; do
    kill -0 "$supervisor_pid" 2>/dev/null && {
      echo "supervisor did not stop: $supervisor_pid" >&2
      return 1
    }
  done
  rm -f -- "$shared/supervisor.pid"
}

restart_supervisor() {
  if [ -n "$systemd_service" ]; then
    sudo -n systemctl restart -- "$systemd_service"
    return
  fi
  stop_supervisor
  nohup python3 -u "$root/current/deploy/gpuhome/supervisor.py" \
    >>"$shared/logs/supervisor.log" 2>&1 </dev/null &
  echo $! >"$shared/supervisor.pid"
}

persist_release_id() {
  mkdir -p "$shared/backups"
  deployment_env_backup="$shared/backups/deployment.env.$TMCRA_RELEASE.$$.backup"
  install -m 0600 "$deployment_env" "$deployment_env_backup"
  python3 - "$deployment_env" "$TMCRA_RELEASE" <<'PY'
import os
import pathlib
import sys
import tempfile

path = pathlib.Path(sys.argv[1])
release = sys.argv[2]
stat = path.stat()
result = []
seen = False
for line in path.read_text(encoding="utf-8").splitlines():
    if line.startswith("TMCRA_RELEASE_ID="):
        if not seen:
            result.append(f"TMCRA_RELEASE_ID={release}")
            seen = True
        continue
    result.append(line)
if not seen:
    result.append(f"TMCRA_RELEASE_ID={release}")

with tempfile.NamedTemporaryFile(
    "w", encoding="utf-8", dir=path.parent, delete=False
) as handle:
    handle.write("\n".join(result) + "\n")
    temporary = pathlib.Path(handle.name)
os.chmod(temporary, 0o600)
os.chown(temporary, stat.st_uid, stat.st_gid)
os.replace(temporary, path)
PY
  deployment_env_activated=1
}

rollback() {
  code="${1:-$?}"
  trap - ERR INT TERM
  set +e
  if [ "$activated" -eq 1 ]; then
    if [ -n "$previous" ] && [ -d "$previous" ]; then
      rollback_link="$root/.current.rollback.$$"
      ln -s "$previous" "$rollback_link"
      mv -Tf "$rollback_link" "$root/current"
    else
      current_target="$(readlink -f "$root/current" || true)"
      if [ "$current_target" = "$release" ]; then
        rm -f -- "$root/current"
      fi
    fi
  fi
  if [ "$installer_activated" -eq 1 ]; then
    if [ -n "$installer_backup" ] && [ -f "$installer_backup" ]; then
      mv -f -- "$installer_backup" "$installer_final"
    else
      rm -f -- "$installer_final"
    fi
  fi
  if [ "$desktop_update_activated" -eq 1 ]; then
    case "$desktop_update_final" in
      "$shared/downloads/desktop/windows/x64") rm -rf -- "$desktop_update_final" ;;
      *) echo "refusing to remove unexpected desktop update path" >&2 ;;
    esac
    if [ -n "$desktop_update_backup" ] && [ -d "$desktop_update_backup" ]; then
      mv -- "$desktop_update_backup" "$desktop_update_final"
    fi
  fi
  if [ "$deployment_env_activated" -eq 1 ] && [ -f "$deployment_env_backup" ]; then
    mv -f -- "$deployment_env_backup" "$deployment_env"
    deployment_env_backup=""
    deployment_env_activated=0
  fi
  if [ "$activated" -eq 1 ]; then
    if [ -n "$previous" ] && [ -d "$previous" ]; then
      restart_supervisor
    else
      stop_supervisor
    fi
  fi
  [ -z "$installer_stage" ] || rm -f -- "$installer_stage"
  [ -z "$installer_backup" ] || rm -f -- "$installer_backup"
  [ -z "$desktop_update_stage" ] || rm -rf -- "$desktop_update_stage"
  [ -z "$desktop_update_backup" ] || rm -rf -- "$desktop_update_backup"
  [ -z "$deployment_env_backup" ] || rm -f -- "$deployment_env_backup"
  case "$temp_release" in
    "$release_root"/.*.tmp.*) rm -rf -- "$temp_release" ;;
    *) echo "refusing to remove unexpected temporary path: $temp_release" >&2 ;;
  esac
  exit "$code"
}
trap 'rollback $?' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

[ -x "$node" ]
[ -x "$npm" ]
[ -f "$TMCRA_ARCHIVE" ]
[ -f "$shared/deployment.env" ]
[ ! -e "$release" ]
chmod 600 "$shared/deployment.env"
printf '%s  %s\n' "$TMCRA_ARCHIVE_SHA256" "$TMCRA_ARCHIVE" | sha256sum -c -

# The Cloudflare Vite adapter snapshots Worker bindings while it builds the
# preview bundle. Load the protected server environment without shell-evaluating
# its values so those bindings are available to workerd after activation.
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    ""|\#*) continue ;;
  esac
  name="${line%%=*}"
  value="${line#*=}"
  if [ "$name" = "$line" ]; then
    echo "invalid deployment environment line" >&2
    exit 2
  fi
  case "$name" in
    [A-Za-z_][A-Za-z0-9_]*) export "$name=$value" ;;
    *) echo "invalid deployment environment name" >&2; exit 2 ;;
  esac
done <"$shared/deployment.env"
export TMCRA_RELEASE_ID="$TMCRA_RELEASE"

# Command-line deployment settings take precedence, while stable host-specific
# process management can live in the protected deployment environment.
if [ -z "$systemd_service" ]; then
  systemd_service="${TMCRA_SYSTEMD_SERVICE:-}"
fi
case "$systemd_service" in
  "") ;;
  -*|*[!0-9A-Za-z@_.-]*)
    echo "invalid systemd service name" >&2
    exit 2
    ;;
  *.service) ;;
  *) echo "invalid systemd service name" >&2; exit 2 ;;
esac

mkdir -p "$release_root" "$shared/logs" "$shared/wrangler" "$shared/auth"
chmod 700 "$shared/auth"
mkdir "$temp_release"
chmod 700 "$temp_release"
tar -xzf "$TMCRA_ARCHIVE" -C "$temp_release"
for runtime_path in \
  "$temp_release/.wrangler" \
  "$temp_release/deploy/gpuhome/deployment.env"; do
  [ ! -e "$runtime_path" ] && [ ! -L "$runtime_path" ] || {
    echo "release archive contains reserved runtime path: $runtime_path" >&2
    false
  }
done

if [ "$preinstalled" -eq 1 ]; then
  preinstalled_manifest="$temp_release/.tmcra-preinstalled.json"
  [ -f "$preinstalled_manifest" ] && [ ! -L "$preinstalled_manifest" ] || {
    echo "preinstalled release is missing its provenance manifest" >&2
    false
  }
  [ -d "$temp_release/node_modules" ] && [ ! -L "$temp_release/node_modules" ] || {
    echo "preinstalled release is missing a real node_modules directory" >&2
    false
  }
  [ -f "$temp_release/node_modules/.package-lock.json" ] || {
    echo "preinstalled release is missing npm lock evidence" >&2
    false
  }
  python3 - "$preinstalled_manifest" "$temp_release/package-lock.json" \
    "$temp_release/package.json" "$node" "$(uname -m)" <<'PY'
import hashlib
import json
import pathlib
import subprocess
import sys

manifest_path, lock_path, package_path, node_path, machine = sys.argv[1:]
manifest = json.loads(pathlib.Path(manifest_path).read_text(encoding="utf-8"))

expected_machine = {"x86_64": "x64", "aarch64": "arm64"}.get(machine)
if expected_machine is None:
    raise SystemExit(f"unsupported deployment architecture: {machine}")

node_major = int(
    subprocess.check_output(
        [node_path, "-p", "process.versions.node.split('.')[0]"],
        text=True,
    ).strip()
)

def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()

expected = {
    "schema": 1,
    "platform": "linux",
    "architecture": expected_machine,
    "nodeMajor": node_major,
    "packageLockSha256": sha256(lock_path),
    "packageJsonSha256": sha256(package_path),
}
for key, value in expected.items():
    if manifest.get(key) != value:
        raise SystemExit(
            f"preinstalled release provenance mismatch for {key}: "
            f"expected {value!r}, got {manifest.get(key)!r}"
        )
PY
fi

ln -sT "$shared/wrangler" "$temp_release/.wrangler"
ln -sT "$shared/deployment.env" "$temp_release/deploy/gpuhome/deployment.env"

# Large desktop installers are served directly by the GPUHome gateway. Keeping
# one under public/ makes Miniflare reject the whole site because Workers assets
# are limited to 25 MiB per file.
mkdir -p "$shared/downloads"
bundled_installer="$temp_release/public/downloads/$installer_name"
installer_checksum="$temp_release/public/downloads/$installer_name.sha256"
installer_manifest="$temp_release/public/downloads/tmcra-memory-desktop-release.json"
installer_verifier="$temp_release/deploy/gpuhome/verify_desktop_release.py"
installer_metadata="$(python3 "$installer_verifier" \
  "$installer_manifest" "$installer_checksum" "$installer_name")"
read -r installer_expected_sha installer_expected_bytes <<<"$installer_metadata"

verify_installer() {
  candidate="$1"
  python3 "$installer_verifier" \
    "$installer_manifest" "$installer_checksum" "$installer_name" "$candidate" \
    >/dev/null
}

stage_installer() {
  source_installer="$1"
  verify_installer "$source_installer"
  installer_stage="$shared/downloads/.$installer_name.$TMCRA_RELEASE.$$.stage"
  install -m 0644 "$source_installer" "$installer_stage"
  verify_installer "$installer_stage"
}

verify_desktop_update() {
  candidate="$1"
  python3 "$installer_verifier" \
    "$installer_manifest" "$installer_checksum" "$installer_name" \
    --update-dir "$candidate" >/dev/null
}

stage_desktop_update() {
  source_directory="$1"
  verify_desktop_update "$source_directory"
  desktop_update_stage="$shared/downloads/.desktop-update.$TMCRA_RELEASE.$$.stage"
  mkdir "$desktop_update_stage"
  chmod 0755 "$desktop_update_stage"
  for source_file in "$source_directory"/*; do
    [ -f "$source_file" ] && [ ! -L "$source_file" ] || {
      echo "desktop update source contains a non-regular file" >&2
      false
    }
    install -m 0644 "$source_file" "$desktop_update_stage/$(basename "$source_file")"
  done
  verify_desktop_update "$desktop_update_stage"
}

if [ -n "$installer_input" ]; then
  [ ! -e "$bundled_installer" ] && [ ! -L "$bundled_installer" ] || {
    echo "desktop installer was supplied both externally and inside the site archive" >&2
    false
  }
  [ "$installer_input" != "$installer_final" ]
  printf '%s  %s\n' "$installer_input_sha256" "$installer_input" | sha256sum -c -
  if [ "${installer_input_sha256,,}" != "$installer_expected_sha" ]; then
    echo "TMCRA_INSTALLER_SHA256 does not match the desktop release manifest" >&2
    false
  fi
  stage_installer "$installer_input"
elif [ -f "$bundled_installer" ]; then
  stage_installer "$bundled_installer"
  rm -f -- "$bundled_installer"
else
  verify_installer "$installer_final"
fi
if [ -n "$desktop_update_input" ]; then
  stage_desktop_update "$desktop_update_input"
else
  verify_desktop_update "$desktop_update_final"
fi
oversized_asset="$(find "$temp_release/public" -type f -size +25M -print -quit)"
if [ -n "$oversized_asset" ]; then
  echo "public asset exceeds the 25 MiB runtime limit: $oversized_asset" >&2
  false
fi

export PATH="$node_root/bin:$PATH"
export npm_config_registry="${npm_config_registry:-https://registry.npmmirror.com}"
export npm_config_audit=false
export npm_config_fund=false
export CI=1
export NODE_OPTIONS="${TMCRA_BUILD_NODE_OPTIONS:-${NODE_OPTIONS:---max-old-space-size=4096}}"

cd "$temp_release"
if [ "$preinstalled" -eq 0 ]; then
  "$npm" ci --no-audit --no-fund
fi
"$node" node_modules/wrangler/bin/wrangler.js d1 migrations apply site-creator-d1 \
  --local --config deploy/gpuhome/wrangler.jsonc --persist-to .wrangler/state
"$npm" run build

# Vite bundles its TypeScript config again when preview starts. Keep the
# immutable release read-only under systemd and redirect only that temporary
# runtime output into the shared writable area.
vite_temp="$shared/vite-temp/$TMCRA_RELEASE"
mkdir -p "$vite_temp"
chmod 700 "$vite_temp"
rm -rf -- "$temp_release/node_modules/.vite-temp"
ln -s "$vite_temp" "$temp_release/node_modules/.vite-temp"

mv "$temp_release" "$release"
if ! (
  cd "$release"
  "$node" --input-type=module -e '
    const { plugins } = await import("@tmcra/miniflare-loopback-api-plugin");
    if (!plugins?.TMCRA_LOOPBACK_API) throw new Error("TMCRA loopback API plugin is unavailable after release activation.");
  '
); then
  mv "$release" "$temp_release"
  false
fi
# The release tree has moved, so every release-scoped desktop asset path must
# follow it before the staged update is verified again during activation.
bundled_installer="$release/public/downloads/$installer_name"
installer_checksum="$release/public/downloads/$installer_name.sha256"
installer_manifest="$release/public/downloads/tmcra-memory-desktop-release.json"
installer_verifier="$release/deploy/gpuhome/verify_desktop_release.py"
printf '%s\n' "$previous" >"$root/previous-release"
next_link="$root/.current.$TMCRA_RELEASE"
ln -s "$release" "$next_link"
mv -Tf "$next_link" "$root/current"
activated=1
if [ -n "$installer_stage" ]; then
  if [ -e "$installer_final" ] || [ -L "$installer_final" ]; then
    [ ! -L "$installer_final" ]
    installer_backup="$shared/downloads/.$installer_name.$TMCRA_RELEASE.$$.backup"
    ln "$installer_final" "$installer_backup"
  fi
  mv -f -- "$installer_stage" "$installer_final"
  installer_stage=""
  installer_activated=1
fi
if [ -n "$desktop_update_stage" ]; then
  mkdir -p "$(dirname "$desktop_update_final")"
  if [ -e "$desktop_update_final" ] || [ -L "$desktop_update_final" ]; then
    [ -d "$desktop_update_final" ] && [ ! -L "$desktop_update_final" ]
    desktop_update_backup="$shared/downloads/.desktop-update.$TMCRA_RELEASE.$$.backup"
    mv -- "$desktop_update_final" "$desktop_update_backup"
  fi
  desktop_update_activated=1
  mv -- "$desktop_update_stage" "$desktop_update_final"
  desktop_update_stage=""
  verify_desktop_update "$desktop_update_final"
fi
persist_release_id
restart_supervisor

health_port="${TMCRA_PUBLIC_PORT:-2000}"
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  health_payload="$(curl -fsS "http://127.0.0.1:$health_port/__deployment/health" 2>/dev/null || true)"
  if [ -n "$health_payload" ] && python3 -c '
import json
import sys
payload = json.loads(sys.argv[2])
raise SystemExit(0 if payload.get("ok") is True and payload.get("release") == sys.argv[1] else 1)
' "$TMCRA_RELEASE" "$health_payload" 2>/dev/null; then
    printf '%s\n' "$health_payload"
    printf 'complete\n' >"$root/deploy.status"
    rm -f -- "$TMCRA_ARCHIVE"
    [ -z "$installer_input" ] || rm -f -- "$installer_input"
    [ -z "$installer_backup" ] || rm -f -- "$installer_backup"
    [ -z "$desktop_update_backup" ] || rm -rf -- "$desktop_update_backup"
    [ -z "$deployment_env_backup" ] || rm -f -- "$deployment_env_backup"
    deployment_env_backup=""
    trap - ERR INT TERM
    echo "DEPLOY_COMPLETE release=$TMCRA_RELEASE"
    exit 0
  fi
  sleep 2
done

echo "deployment health check did not become ready" >&2
false
