#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

: "${TMCRA_RELEASE:?TMCRA_RELEASE is required}"
: "${TMCRA_ARCHIVE:?TMCRA_ARCHIVE is required}"
: "${TMCRA_ARCHIVE_SHA256:?TMCRA_ARCHIVE_SHA256 is required}"

case "$TMCRA_RELEASE" in
  .|..) echo "invalid release name" >&2; exit 2 ;;
  *[!0-9A-Za-zTZ._-]*) echo "invalid release name" >&2; exit 2 ;;
esac
[[ "$TMCRA_ARCHIVE_SHA256" =~ ^[0-9A-Fa-f]{64}$ ]] || {
  echo "TMCRA_ARCHIVE_SHA256 must be exactly 64 hexadecimal characters" >&2
  exit 2
}

root=/opt/tmcra
python=/opt/tmcra_env_20260713/bin/python
control="$root/deploy/tmcra-memory-api-control.sh"
local_llm_control="$root/deploy/tmcra-local-llm-control.sh"
maintenance_control="$root/deploy/tmcra-production-maintenance.sh"
deployer="$root/deploy/deploy_tmcra_service_release.sh"
env_file="$root/deploy/tmcra-service.env"
backup_root=/opt/tmcra-data/tmcra_service_state/releases
backup="$backup_root/$TMCRA_RELEASE"
backup_staging="$backup_root/.incomplete-$TMCRA_RELEASE-$$"
service_assets_root=/opt/tmcra-data/tmcra_service_assets
checkpoint_asset="$service_assets_root/tmcra_v3_reranker.pt"
checkpoint_asset_sha256=380d4ce4949697110b963b1ac253bb29369b3e58f283515512c3d33c61f9d58e
stage="$root/.service-release-$TMCRA_RELEASE"
next_package="$root/.tmcra_service-next-$TMCRA_RELEASE"
old_package="$root/.tmcra_service-old-$TMCRA_RELEASE"
restore_package="$root/.tmcra_service-restore-$TMCRA_RELEASE"
failed_package="$root/.tmcra_service-failed-$TMCRA_RELEASE"
runtime_files=(
  prepare_tmcra_v4_e2e_data.py
  run_tmcra_v4_build.py
  run_tmcra_v4_compile_evidence.py
  tmcra_v3_online_runtime.py
  tmcra_v3_product_writer.py
  tmcra_v3_recall_planner.py
  tmcra_v3_slow_graph.py
  tmcra_v4_batch_writer.py
  tmcra_v4_cost_report.py
  tmcra_v4_evidence_operations.py
  tmcra_v4_evidence_planner.py
  tmcra_v4_recall_planner.py
  tmcra_v4_online_runtime.py
  tmcra_v4_route_policy.py
  tmcra_v4_slow_graph.py
  tmcra_v4_task_contract.py
  tmcra_v4_typed_semantics.py
)
atomic_temps=()
backup_complete=0
service_was_running=0
service_state_touched=0
service_stopped=0
mutation_started=0
control_bootstrapped=0
maintenance_control_existed=0
new_service_may_be_running=0

set -a
# shellcheck disable=SC1091
source "$env_file"
set +a
previous_service_release_id="${TMCRA_SERVICE_RELEASE_ID:-}"
release_channel="${TMCRA_RELEASE_CHANNEL:-stable}"
release_canary_percent="${TMCRA_RELEASE_CANARY_PERCENT:-0}"
control_db="${TMCRA_SERVICE_CONTROL_DB:?TMCRA_SERVICE_CONTROL_DB is required}"
scope_token_key="${control_db}.scope-token-key"

remove_known_tree() {
  local target=$1
  case "$target" in
    "$stage"|"$next_package"|"$old_package"|"$restore_package"|"$failed_package")
      rm -rf -- "$target"
      ;;
    *)
      echo "refusing to remove unexpected release path: $target" >&2
      return 1
      ;;
  esac
}

remove_backup_staging() {
  local target=$1
  case "$target" in
    "$backup_root"/.incomplete-"$TMCRA_RELEASE"-*) rm -rf -- "$target" ;;
    *)
      echo "refusing to remove unexpected backup staging path: $target" >&2
      return 1
      ;;
  esac
}

atomic_install() {
  local mode=$1
  local source=$2
  local destination=$3
  local directory=""
  local base=""
  local temporary=""
  directory=$(dirname -- "$destination")
  base=$(basename -- "$destination")
  temporary="$directory/.$base.release-$TMCRA_RELEASE-$$"
  atomic_temps+=("$temporary")
  rm -f -- "$temporary"
  install -m "$mode" -- "$source" "$temporary"
  mv -fT -- "$temporary" "$destination"
}

cleanup_atomic_temps() {
  local temporary
  for temporary in "${atomic_temps[@]}"; do
    case "$temporary" in
      "$root"/.*.release-"$TMCRA_RELEASE"-*|"$root"/deploy/.*.release-"$TMCRA_RELEASE"-*|"$service_assets_root"/.*.release-"$TMCRA_RELEASE"-*)
        rm -f -- "$temporary"
        ;;
      *) echo "refusing to remove unexpected atomic temporary: $temporary" >&2 ;;
    esac
  done
}

restore_release_files() {
  local runtime_file
  local restore_source=""

  [[ "$backup_complete" -eq 1 && -d "$backup/tmcra_service" ]] || {
    echo "cannot roll back without a complete release backup" >&2
    return 1
  }

  remove_known_tree "$restore_package"
  remove_known_tree "$failed_package"
  if [[ -d "$old_package" ]]; then
    restore_source="$old_package"
  else
    cp -a -- "$backup/tmcra_service" "$restore_package"
    restore_source="$restore_package"
  fi

  if [[ -d "$root/tmcra_service" ]]; then
    mv -- "$root/tmcra_service" "$failed_package"
  fi
  if ! mv -- "$restore_source" "$root/tmcra_service"; then
    if [[ -d "$failed_package" && ! -d "$root/tmcra_service" ]]; then
      mv -- "$failed_package" "$root/tmcra_service" || true
    fi
    echo "package rollback failed; the previous on-disk package was preserved when possible" >&2
    return 1
  fi

  atomic_install 0644 "$backup/openapi.json" "$root/openapi.json"
  atomic_install 0600 "$backup/tmcra_v3_reranker.pt" "$checkpoint_asset"
  for runtime_file in "${runtime_files[@]}"; do
    atomic_install 0644 "$backup/$runtime_file" "$root/$runtime_file"
  done
  atomic_install 0600 "$backup/tmcra-service.env" "$env_file"
  atomic_install 0755 "$backup/tmcra-memory-api-control.sh" "$control"
  atomic_install 0755 "$backup/tmcra-local-llm-control.sh" "$local_llm_control"
  if [[ -f "$backup/tmcra-production-maintenance.sh" ]]; then
    atomic_install 0755 "$backup/tmcra-production-maintenance.sh" "$maintenance_control"
  else
    rm -f -- "$maintenance_control"
  fi
  atomic_install 0755 "$backup/deploy_tmcra_service_release.sh" "$deployer"
  remove_known_tree "$failed_package"
  remove_known_tree "$restore_package"
}

cleanup_release_work() {
  cleanup_atomic_temps || true
  remove_known_tree "$stage" || true
  remove_known_tree "$next_package" || true
  remove_known_tree "$restore_package" || true
  remove_known_tree "$failed_package" || true
  remove_backup_staging "$backup_staging" || true
}

rollback() {
  local code=${1:-1}
  local rollback_control="$control"
  trap - ERR INT TERM
  set +e

  if [[ "$new_service_may_be_running" -eq 1 ]]; then
    if ! "$rollback_control" stop; then
      echo "ROLLBACK_HALTED: the new service could not be stopped safely; files were not changed under a live process" >&2
      exit 70
    fi
    service_stopped=1
  elif [[ "$service_state_touched" -eq 1 && "$service_stopped" -eq 0 ]]; then
    # An interrupt may arrive while the first stop is in progress. If the old
    # service is no longer healthy, finish a verified stop before restoring.
    if "$stage/deploy/tmcra-memory-api-control.sh" verify-running >/dev/null 2>&1; then
      # stop_service disables the public tunnel first. Calling start on an
      # already-ready service restores that tunnel without spawning a process.
      if ! "$stage/deploy/tmcra-memory-api-control.sh" start; then
        echo "ROLLBACK_INCOMPLETE: the old service is ready but its public tunnel could not be restored" >&2
        exit 71
      fi
      service_stopped=0
    elif "$stage/deploy/tmcra-memory-api-control.sh" stop; then
      service_stopped=1
    else
      echo "ROLLBACK_HALTED: service state could not be verified after an interrupted stop" >&2
      exit 70
    fi
  fi

  if [[ "$mutation_started" -eq 1 ]]; then
    if [[ "$service_stopped" -ne 1 ]]; then
      echo "ROLLBACK_HALTED: refusing to restore release files while the service may still be running" >&2
      exit 70
    fi
    if ! restore_release_files; then
      echo "ROLLBACK_INCOMPLETE: release files could not be restored; service remains stopped" >&2
      exit 70
    fi
    control_bootstrapped=0
  elif [[ "$control_bootstrapped" -eq 1 ]]; then
    if ! atomic_install 0755 "$backup/tmcra-memory-api-control.sh" "$control" \
      || ! atomic_install 0755 "$backup/tmcra-local-llm-control.sh" "$local_llm_control"; then
      echo "ROLLBACK_INCOMPLETE: the original control scripts could not be restored" >&2
      exit 70
    fi
    if [[ "$maintenance_control_existed" -eq 1 ]]; then
      if ! atomic_install 0755 "$backup/tmcra-production-maintenance.sh" "$maintenance_control"; then
        echo "ROLLBACK_INCOMPLETE: the original maintenance control could not be restored" >&2
        exit 70
      fi
    else
      rm -f -- "$maintenance_control"
    fi
    control_bootstrapped=0
  fi

  if [[ "$service_was_running" -eq 1 && "$service_stopped" -eq 1 ]]; then
    if ! "$control" start; then
      echo "ROLLBACK_INCOMPLETE: previous release was restored but failed to restart" >&2
      exit 71
    fi
    service_stopped=0
  fi

  cleanup_release_work
  echo "DEPLOY_FAILED_ROLLED_BACK release=$TMCRA_RELEASE exit_code=$code" >&2
  exit "$code"
}
trap 'rollback $?' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

[[ -x "$python" ]]
[[ -x "$control" ]]
[[ -x "$local_llm_control" ]]
if [[ -e "$maintenance_control" ]]; then
  [[ -x "$maintenance_control" && ! -L "$maintenance_control" ]]
  maintenance_control_existed=1
fi
[[ -f "$deployer" ]]
[[ -f "$TMCRA_ARCHIVE" && ! -L "$TMCRA_ARCHIVE" ]]
[[ ! -e "$backup" && ! -e "$backup_staging" ]]
[[ ! -e "$stage" && ! -e "$next_package" && ! -e "$old_package" ]]
[[ ! -e "$restore_package" && ! -e "$failed_package" ]]
[[ -d "$root/tmcra_service" ]]
[[ -f "$root/openapi.json" ]]
[[ -f "$env_file" ]]
[[ -f "$control_db" ]]
[[ -d "$service_assets_root" ]]
[[ -f "$checkpoint_asset" && ! -L "$checkpoint_asset" ]]
for runtime_file in "${runtime_files[@]}"; do
  [[ -f "$root/$runtime_file" ]]
done
printf '%s  %s\n' "$TMCRA_ARCHIVE_SHA256" "$TMCRA_ARCHIVE" | sha256sum -c -

mkdir -p -- "$stage"
"$python" - "$TMCRA_ARCHIVE" <<'PY'
from pathlib import PurePosixPath
import sys
import tarfile

archive = sys.argv[1]
seen: set[str] = set()
with tarfile.open(archive, "r:gz") as handle:
    for member in handle.getmembers():
        path = PurePosixPath(member.name)
        if (
            not member.name
            or path.is_absolute()
            or ".." in path.parts
            or member.name in seen
            or not (member.isfile() or member.isdir())
        ):
            raise SystemExit(f"unsafe archive member: {member.name!r}")
        seen.add(member.name)
PY
tar -xzf "$TMCRA_ARCHIVE" --no-same-owner --no-same-permissions -C "$stage"
[[ -f "$stage/tmcra_service/app.py" ]]
[[ -f "$stage/tmcra_service/api_models.py" ]]
[[ -f "$stage/tmcra_service/control_plane.py" ]]
[[ -f "$stage/openapi.json" ]]
[[ -f "$stage/production_assets/tmcra_v3_reranker.pt" ]]
[[ -f "$stage/deploy/recall-pool.env.patch" ]]
[[ -f "$stage/deploy/apply_recall_env_patch.py" ]]
[[ -x "$stage/deploy/tmcra-memory-api-control.sh" ]]
[[ -x "$stage/deploy/tmcra-local-llm-control.sh" ]]
[[ -x "$stage/deploy/tmcra-production-maintenance.sh" ]]
[[ -x "$stage/deploy/deploy_tmcra_service_release.sh" ]]
for runtime_file in "${runtime_files[@]}"; do
  [[ -f "$stage/$runtime_file" ]]
done
printf '%s  %s\n' "$checkpoint_asset_sha256" \
  "$stage/production_assets/tmcra_v3_reranker.pt" | sha256sum -c -
"$python" -m compileall -q "$stage/tmcra_service"
for runtime_file in "${runtime_files[@]}"; do
  "$python" -m py_compile "$stage/$runtime_file"
done
"$python" -m py_compile "$stage/deploy/apply_recall_env_patch.py"
"$python" -m py_compile "$stage/deploy/stamp_service_release_env.py"
bash -n "$stage/deploy/tmcra-memory-api-control.sh"
bash -n "$stage/deploy/tmcra-local-llm-control.sh"
bash -n "$stage/deploy/tmcra-production-maintenance.sh"
bash -n "$stage/deploy/deploy_tmcra_service_release.sh"

# Validate the env transformation against a private copy before stopping the
# service. The production env is patched only after the verified stop.
install -m 0600 "$env_file" "$stage/tmcra-service.env.preflight"
"$python" "$stage/deploy/apply_recall_env_patch.py" \
  "$stage/tmcra-service.env.preflight" "$stage/deploy/recall-pool.env.patch"
release_stamp_args=(
  --release-id "$TMCRA_RELEASE"
  --archive-sha256 "$TMCRA_ARCHIVE_SHA256"
  --channel "$release_channel"
  --canary-percent "$release_canary_percent"
)
if [[ -n "$previous_service_release_id" && "$previous_service_release_id" != "$TMCRA_RELEASE" ]]; then
  release_stamp_args+=(--rollback-release-id "$previous_service_release_id")
fi
"$python" "$stage/deploy/stamp_service_release_env.py" \
  "$stage/tmcra-service.env.preflight" "${release_stamp_args[@]}"

# Serialize the entire live-state transaction against ordinary control-script
# start/stop/restart commands. Child control invocations inherit this lock.
command -v flock >/dev/null 2>&1
deploy_lock="${TMCRA_SERVICE_STATE_DIR:?TMCRA_SERVICE_STATE_DIR is required}/release.lock"
mkdir -p -- "${TMCRA_SERVICE_STATE_DIR}"
chmod 700 "${TMCRA_SERVICE_STATE_DIR}"
exec 8>"$deploy_lock"
flock -x 8
export TMCRA_DEPLOY_LOCK_HELD=1
export TMCRA_MUTATION_REASON="release stop: $TMCRA_RELEASE"

"$stage/deploy/tmcra-memory-api-control.sh" verify-running
service_was_running=1

mkdir -p -- "$backup_root"
chmod 700 "$backup_root"
mkdir -- "$backup_staging"
cp -a -- "$root/tmcra_service" "$backup_staging/tmcra_service"
install -m 0644 "$root/openapi.json" "$backup_staging/openapi.json"
install -m 0600 "$checkpoint_asset" "$backup_staging/tmcra_v3_reranker.pt"
install -m 0600 "$env_file" "$backup_staging/tmcra-service.env"
install -m 0755 "$control" "$backup_staging/tmcra-memory-api-control.sh"
install -m 0755 "$local_llm_control" "$backup_staging/tmcra-local-llm-control.sh"
if [[ "$maintenance_control_existed" -eq 1 ]]; then
  install -m 0755 "$maintenance_control" "$backup_staging/tmcra-production-maintenance.sh"
fi
install -m 0755 "$deployer" "$backup_staging/deploy_tmcra_service_release.sh"
for runtime_file in "${runtime_files[@]}"; do
  install -m 0644 "$root/$runtime_file" "$backup_staging/$runtime_file"
done
# Keep a consistent control-plane snapshot for manual disaster recovery. Code
# rollback deliberately does not overwrite this mutable database: doing so
# would discard requests committed between the snapshot and the verified stop.
"$python" - "$control_db" "$backup_staging/control.sqlite3" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY
chmod 0600 "$backup_staging/control.sqlite3"
if [[ -f "$scope_token_key" ]]; then
  install -m 0600 "$scope_token_key" "$backup_staging/control.sqlite3.scope-token-key"
fi
printf 'release=%s\narchive_sha256=%s\ncomplete=1\n' \
  "$TMCRA_RELEASE" "$TMCRA_ARCHIVE_SHA256" >"$backup_staging/RELEASE_BACKUP"
chmod 0600 "$backup_staging/RELEASE_BACKUP"
mv -- "$backup_staging" "$backup"
backup_complete=1

# Bootstrap the audited controller atomically while the old service is still
# running. From this point, normal operator commands honor the release lock.
# This is tracked separately because replacing a shell script does not require
# stopping or rewriting the live Python process during an early rollback.
control_bootstrapped=1
atomic_install 0755 "$stage/deploy/tmcra-memory-api-control.sh" "$control"
atomic_install 0755 "$stage/deploy/tmcra-local-llm-control.sh" "$local_llm_control"
atomic_install 0755 "$stage/deploy/tmcra-production-maintenance.sh" "$maintenance_control"
"$control" verify-running

# Move the already-validated package beside the live package before downtime.
mv -- "$stage/tmcra_service" "$next_package"

service_state_touched=1
"$stage/deploy/tmcra-memory-api-control.sh" stop
service_stopped=1

mutation_started=1
"$python" "$stage/deploy/apply_recall_env_patch.py" \
  "$env_file" "$stage/deploy/recall-pool.env.patch"
"$python" "$stage/deploy/stamp_service_release_env.py" \
  "$env_file" "${release_stamp_args[@]}"
mv -- "$root/tmcra_service" "$old_package"
mv -- "$next_package" "$root/tmcra_service"
atomic_install 0600 "$stage/production_assets/tmcra_v3_reranker.pt" "$checkpoint_asset"
for runtime_file in "${runtime_files[@]}"; do
  atomic_install 0644 "$stage/$runtime_file" "$root/$runtime_file"
done
atomic_install 0644 "$stage/openapi.json" "$root/openapi.json"
atomic_install 0755 "$stage/deploy/tmcra-memory-api-control.sh" "$control"
atomic_install 0755 "$stage/deploy/tmcra-local-llm-control.sh" "$local_llm_control"
atomic_install 0755 "$stage/deploy/tmcra-production-maintenance.sh" "$maintenance_control"
atomic_install 0755 "$stage/deploy/deploy_tmcra_service_release.sh" "$deployer"

new_service_may_be_running=1
"$control" start
service_stopped=0
"$control" verify-running
health_url=$(bash -c 'set -a; source "$1"; printf "http://127.0.0.1:%s/healthz" "${TMCRA_SERVICE_BIND_PORT:-2009}"' _ "$env_file")
curl -fsS "$health_url" >/dev/null

# Refuse a nominally healthy but mixed-version release. This catches operator
# drift where an older deployer omits one member of the bound V3/V4 runtime.
for runtime_file in "${runtime_files[@]}"; do
  cmp -s -- "$stage/$runtime_file" "$root/$runtime_file" || {
    echo "installed runtime differs from staged release: $runtime_file" >&2
    false
  }
done
cmp -s -- "$stage/openapi.json" "$root/openapi.json" || {
  echo "installed OpenAPI differs from staged release" >&2
  false
}
cmp -s -- "$stage/production_assets/tmcra_v3_reranker.pt" "$checkpoint_asset" || {
  echo "installed reranker checkpoint differs from staged release" >&2
  false
}
printf '%s  %s\n' "$checkpoint_asset_sha256" "$checkpoint_asset" | sha256sum -c -
cmp -s -- "$stage/deploy/deploy_tmcra_service_release.sh" "$deployer" || {
  echo "installed deployer differs from staged release" >&2
  false
}
cmp -s -- "$stage/deploy/tmcra-local-llm-control.sh" "$local_llm_control" || {
  echo "installed local model control differs from staged release" >&2
  false
}
cmp -s -- "$stage/deploy/tmcra-production-maintenance.sh" "$maintenance_control" || {
  echo "installed production maintenance control differs from staged release" >&2
  false
}

remove_known_tree "$old_package"
remove_known_tree "$stage"
cleanup_atomic_temps
rm -f -- "$TMCRA_ARCHIVE"
trap - ERR INT TERM
echo "DEPLOY_COMPLETE release=$TMCRA_RELEASE backup=$backup"
