#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${TMCRA_V4_ROOT:-/opt/tmcra}"
PYTHON="${TMCRA_SERVICE_PYTHON:-/opt/tmcra_env_20260713/bin/python}"
ENV_FILE="${TMCRA_SERVICE_ENV_FILE:-$ROOT/deploy/tmcra-service.env}"
declare -r TMCRA_DRAIN_GATE_OVERRIDE_FROM_INVOKER="${TMCRA_DRAIN_GATE_EMERGENCY_OVERRIDE:-0}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "service environment file is missing: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
# A checked-in/persistent service env file must not silently bypass the
# release gate. The emergency override is accepted only from the invoking
# process environment.
TMCRA_DRAIN_GATE_EMERGENCY_OVERRIDE="$TMCRA_DRAIN_GATE_OVERRIDE_FROM_INVOKER"

TUNNEL_CONTROL="${TMCRA_PUBLIC_REVERSE_TUNNEL_CONTROL:-$ROOT/deploy/tmcra-api-reverse-tunnel-control.sh}"
CONTROL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LOCAL_LLM_CONTROL="${TMCRA_LOCAL_LLM_CONTROL:-$CONTROL_DIR/tmcra-local-llm-control.sh}"

STATE_DIR="${TMCRA_SERVICE_STATE_DIR:?TMCRA_SERVICE_STATE_DIR is required}"
CONTROL_DB="${TMCRA_SERVICE_CONTROL_DB:-}"
CONTROL_ACTION="${1:-status}"
PID_FILE="$STATE_DIR/supervisor.pid"
SERVICE_PID_FILE="$STATE_DIR/service.pid"
LOG_FILE="$STATE_DIR/supervisor.log"
READY_URL="http://127.0.0.1:${TMCRA_SERVICE_BIND_PORT:-2009}/readyz"
STARTUP_TIMEOUT_SECONDS="${TMCRA_SERVICE_STARTUP_TIMEOUT_SECONDS:-180}"
[[ "$STARTUP_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
  echo "TMCRA_SERVICE_STARTUP_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 1
}
umask 077
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

command -v flock >/dev/null 2>&1 || {
  echo "flock is required to serialize service control operations" >&2
  exit 1
}

verified_release_lease() {
  local expected_identity=""
  local actual_identity=""
  [[ "${TMCRA_DEPLOY_LOCK_HELD:-0}" == "1" ]] || return 1
  [[ -e "/proc/$$/fd/8" ]] || return 1
  expected_identity=$(stat -Lc '%d:%i' -- "$STATE_DIR/release.lock") || return 1
  actual_identity=$(stat -Lc '%d:%i' -- "/proc/$$/fd/8") || return 1
  [[ "$actual_identity" == "$expected_identity" ]] || return 1
  # Reasserting LOCK_EX succeeds only for the inherited lease or an otherwise
  # uncontended descriptor for the exact production release lock.
  flock -n -x 8 || return 1
}

PRODUCTION_MUTATION_LEASE_HELD=0
# Normal control operations take a shared release lock. The release transaction
# holds the exclusive side from its final preflight through health validation,
# preventing an operator restart in the middle of a package swap.
if [[ "${TMCRA_DEPLOY_LOCK_HELD:-0}" == "1" ]]; then
  verified_release_lease || {
    echo "refusing forged production lease: fd 8 does not hold $STATE_DIR/release.lock exclusively" >&2
    exit 73
  }
  PRODUCTION_MUTATION_LEASE_HELD=1
else
  exec 8>"$STATE_DIR/release.lock"
  flock -s 8
fi

case "$CONTROL_ACTION" in
  stop|restart)
    [[ "$PRODUCTION_MUTATION_LEASE_HELD" -eq 1 ]] || {
      echo "refusing $CONTROL_ACTION without an exclusive production maintenance lease; use tmcra-production-maintenance.sh" >&2
      exit 73
    }
    ;;
esac
exec 9>"$STATE_DIR/control.lock"
flock -x 9

read_pid_file() {
  local path=$1
  local value=""
  [[ -f "$path" && ! -L "$path" ]] || return 1
  IFS= read -r value <"$path" || return 1
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "$value"
}

process_stat_tail() {
  local pid=$1
  local line=""
  [[ "$pid" =~ ^[1-9][0-9]*$ && -r "/proc/$pid/stat" ]] || return 1
  IFS= read -r line <"/proc/$pid/stat" || return 1
  [[ "$line" == *") "* ]] || return 1
  printf '%s\n' "${line##*) }"
}

process_state() {
  local pid=$1
  local tail=""
  tail=$(process_stat_tail "$pid") || return 1
  printf '%s\n' "${tail%% *}"
}

process_start_time() {
  local pid=$1
  local tail=""
  local -a fields=()
  tail=$(process_stat_tail "$pid") || return 1
  read -r -a fields <<<"$tail"
  [[ ${#fields[@]} -ge 20 ]] || return 1
  printf '%s\n' "${fields[19]}"
}

process_parent_pid() {
  local pid=$1
  local tail=""
  local -a fields=()
  tail=$(process_stat_tail "$pid") || return 1
  read -r -a fields <<<"$tail"
  [[ ${#fields[@]} -ge 2 ]] || return 1
  printf '%s\n' "${fields[1]}"
}

process_alive() {
  local pid=$1
  local state=""
  kill -0 "$pid" 2>/dev/null || return 1
  state=$(process_state "$pid") || return 1
  [[ "$state" != "Z" && "$state" != "X" ]]
}

same_process() {
  local pid=$1
  local expected_start_time=$2
  local actual_start_time=""
  process_alive "$pid" || return 1
  actual_start_time=$(process_start_time "$pid") || return 1
  [[ "$actual_start_time" == "$expected_start_time" ]]
}

process_has_module() {
  local pid=$1
  local expected_module=$2
  local -a argv=()
  local index
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  mapfile -d '' -t argv <"/proc/$pid/cmdline" || true
  for ((index = 0; index + 1 < ${#argv[@]}; index++)); do
    if [[ "${argv[$index]}" == "-m" && "${argv[$((index + 1))]}" == "$expected_module" ]]; then
      return 0
    fi
  done
  return 1
}

supervisor_matches() {
  local pid=$1
  local -a argv=()
  local index
  process_has_module "$pid" "tmcra_service.supervisor" || return 1
  mapfile -d '' -t argv <"/proc/$pid/cmdline" || true
  for ((index = 0; index + 1 < ${#argv[@]}; index++)); do
    if [[ "${argv[$index]}" == "--env-file" && "${argv[$((index + 1))]}" == "$ENV_FILE" ]]; then
      return 0
    fi
  done
  return 1
}

service_child_matches() {
  local pid=$1
  local expected_parent=$2
  local parent=""
  process_has_module "$pid" "tmcra_service" || return 1
  parent=$(process_parent_pid "$pid") || return 1
  [[ "$parent" == "$expected_parent" ]]
}

verified_supervisor_pid() {
  local pid=""
  pid=$(read_pid_file "$PID_FILE") || return 1
  process_alive "$pid" || return 1
  supervisor_matches "$pid" || return 1
  printf '%s\n' "$pid"
}

pid_file_points_to_unverified_live_process() {
  local pid=""
  pid=$(read_pid_file "$PID_FILE") || return 1
  process_alive "$pid" || return 1
  ! supervisor_matches "$pid"
}

live_service_pid_from_file() {
  local pid=""
  pid=$(read_pid_file "$SERVICE_PID_FILE") || return 1
  process_alive "$pid" || return 1
  printf '%s\n' "$pid"
}

remove_pid_file_if_equal() {
  local path=$1
  local expected=$2
  local actual=""
  actual=$(read_pid_file "$path") || return 0
  if [[ "$actual" == "$expected" ]]; then
    rm -f -- "$path"
  fi
}

running() {
  verified_supervisor_pid >/dev/null
}

finish_verified_stop() {
  local original_supervisor_pid=$1
  local message=$2
  local replacement_pid=""
  local residual_service_pid=""

  remove_pid_file_if_equal "$PID_FILE" "$original_supervisor_pid"
  if replacement_pid=$(verified_supervisor_pid); then
    echo "service supervisor restarted unexpectedly during stop (new pid $replacement_pid)" >&2
    return 1
  fi
  if residual_service_pid=$(live_service_pid_from_file); then
    echo "supervisor stopped but service child pid $residual_service_pid is still live; refusing to claim a clean stop" >&2
    return 1
  fi
  rm -f -- "$SERVICE_PID_FILE"
  echo "$message"
}

ready() {
  curl -fsS "$READY_URL" >/dev/null 2>&1
}

start_local_llm() {
  [[ -x "$LOCAL_LLM_CONTROL" ]] || {
    echo "local model control is unavailable: $LOCAL_LLM_CONTROL" >&2
    return 1
  }
  "$LOCAL_LLM_CONTROL" start 9>&-
}

verify_local_llm() {
  [[ -x "$LOCAL_LLM_CONTROL" ]] || {
    echo "local model control is unavailable: $LOCAL_LLM_CONTROL" >&2
    return 1
  }
  "$LOCAL_LLM_CONTROL" verify-running 9>&-
}

tunnel_enabled() {
  [[ "${TMCRA_PUBLIC_REVERSE_TUNNEL_ENABLED:-0}" == "1" ]]
}

start_public_tunnel() {
  if tunnel_enabled; then
    [[ -x "$TUNNEL_CONTROL" ]] || {
      echo "public reverse tunnel control is unavailable: $TUNNEL_CONTROL" >&2
      return 1
    }
    "$TUNNEL_CONTROL" start 8>&- 9>&-
  fi
}

stop_public_tunnel() {
  if tunnel_enabled && [[ -x "$TUNNEL_CONTROL" ]]; then
    "$TUNNEL_CONTROL" stop 8>&- 9>&-
  fi
}

drain_gate_override_enabled() {
  [[ "${TMCRA_DRAIN_GATE_EMERGENCY_OVERRIDE:-0}" == "1" ]]
}

check_drain_gate() {
  local operation=$1
  local counts=""
  local running_jobs=""
  local recovering_scopes=""

  if drain_gate_override_enabled; then
    echo "$operation drain gate overridden by TMCRA_DRAIN_GATE_EMERGENCY_OVERRIDE=1" >&2
    return 0
  fi

  if [[ -z "$CONTROL_DB" || ! -f "$CONTROL_DB" || -L "$CONTROL_DB" ]]; then
    echo "$operation refused by drain gate: control database is missing or untrusted" >&2
    return 1
  fi

  # The URI mode=ro connection and query_only pragma make this a read-only,
  # fail-closed preflight. Missing tables/columns and quick_check failures are
  # blockers because an unknown live state must not permit a release stop.
  if ! counts=$("$PYTHON" - "$CONTROL_DB" <<'PY'
import sqlite3
import sys
from pathlib import Path

database = Path(sys.argv[1])
uri = f"file:{database.resolve().as_posix()}?mode=ro"
connection = None
try:
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    quick_check = connection.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or str(quick_check[0]).lower() != "ok":
        raise RuntimeError("quick_check failed")

    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    required_tables = {"jobs", "scope_quarantine_recoveries"}
    if not required_tables.issubset(tables):
        missing = ",".join(sorted(required_tables - tables))
        raise RuntimeError(f"required table missing: {missing}")

    for table in required_tables:
        columns = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if "state" not in columns:
            raise RuntimeError(f"required state column missing: {table}")

    running_jobs = int(
        connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE state='running'"
        ).fetchone()[0]
    )
    recovering_scopes = int(
        connection.execute(
            "SELECT COUNT(*) FROM scope_quarantine_recoveries "
            "WHERE state IN ('waiting','auditing','repairing','verifying','recovering')"
        ).fetchone()[0]
    )
    print(f"{running_jobs} {recovering_scopes}")
except Exception as exc:
    print(f"read-only control database check failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
finally:
    if connection is not None:
        connection.close()
PY
  ); then
    echo "$operation refused by drain gate: control database could not be read safely" >&2
    return 1
  fi

  read -r running_jobs recovering_scopes <<<"$counts"
  if [[ ! "$running_jobs" =~ ^[0-9]+$ || ! "$recovering_scopes" =~ ^[0-9]+$ ]]; then
    echo "$operation refused by drain gate: control database returned invalid counts" >&2
    return 1
  fi
  if (( running_jobs > 0 || recovering_scopes > 0 )); then
    echo "$operation refused by drain gate: running_jobs=$running_jobs recovering_scopes=$recovering_scopes; set TMCRA_DRAIN_GATE_EMERGENCY_OVERRIDE=1 only for emergency operations" >&2
    return 1
  fi
}

wait_ready() {
  local launcher_pid="${1:-}"
  local attempt
  for ((attempt = 0; attempt < STARTUP_TIMEOUT_SECONDS; attempt++)); do
    if running && ready; then
      return 0
    fi
    if [[ -n "$launcher_pid" ]] && ! process_alive "$launcher_pid" && ! running; then
      break
    fi
    sleep 1
  done
  return 1
}

readiness_failure() {
  echo "tmcra-memory-api failed the readiness gate; see $LOG_FILE" >&2
  tail -n 40 "$LOG_FILE" >&2 || true
}

stop_failed_launcher() {
  local launcher_pid=$1
  local launcher_start_time=$2
  local attempt

  if running; then
    stop_service
    return $?
  fi
  if ! process_alive "$launcher_pid"; then
    return 0
  fi
  if [[ -z "$launcher_start_time" ]] \
    || ! same_process "$launcher_pid" "$launcher_start_time" \
    || ! supervisor_matches "$launcher_pid"; then
    echo "failed launcher pid $launcher_pid is live but could not be verified; refusing to signal it" >&2
    return 1
  fi
  kill -TERM "$launcher_pid"
  for ((attempt = 0; attempt < 20; attempt++)); do
    if ! same_process "$launcher_pid" "$launcher_start_time"; then
      return 0
    fi
    sleep 0.25
  done
  same_process "$launcher_pid" "$launcher_start_time" && supervisor_matches "$launcher_pid" || {
    echo "failed launcher pid $launcher_pid changed identity before fallback termination" >&2
    return 1
  }
  kill -KILL "$launcher_pid"
  for ((attempt = 0; attempt < 20; attempt++)); do
    if ! same_process "$launcher_pid" "$launcher_start_time"; then
      return 0
    fi
    sleep 0.25
  done
  echo "failed launcher pid $launcher_pid could not be terminated safely" >&2
  return 1
}

start_service() {
  local pid=""
  local launcher_start_time=""
  start_local_llm || return 1
  if running; then
    if ready; then
      pid=$(verified_supervisor_pid)
      start_public_tunnel
      echo "tmcra-memory-api is already running and ready (pid $pid)"
      return 0
    fi
    echo "tmcra-memory-api supervisor is running but the service is not ready" >&2
    return 1
  fi
  if pid_file_points_to_unverified_live_process; then
    pid=$(read_pid_file "$PID_FILE")
    echo "refusing to start while supervisor pid file points to an unverified live process: $pid" >&2
    return 1
  fi
  if pid=$(live_service_pid_from_file); then
    echo "refusing to start while an orphaned or unverified service child is still live: $pid" >&2
    return 1
  fi
  rm -f -- "$PID_FILE" "$SERVICE_PID_FILE"
  cd "$ROOT"
  nohup env -u TMCRA_DEPLOY_LOCK_HELD \
    "$PYTHON" -m tmcra_service.supervisor --env-file "$ENV_FILE" \
    8>&- 9>&- >>"$LOG_FILE" 2>&1 </dev/null &
  local launcher_pid=$!
  launcher_start_time=$(process_start_time "$launcher_pid" 2>/dev/null || true)
  if wait_ready "$launcher_pid"; then
    start_public_tunnel
    pid=$(verified_supervisor_pid)
    echo "tmcra-memory-api started (supervisor pid $pid)"
    return 0
  fi
  readiness_failure
  if ! stop_failed_launcher "$launcher_pid" "$launcher_start_time"; then
    echo "tmcra-memory-api startup failed and its launcher could not be cleaned up safely" >&2
  fi
  return 1
}

stop_service() {
  local pid=""
  local supervisor_start_time=""
  local child_pid=""
  local child_start_time=""
  local current_child=""
  local attempt

  check_drain_gate "${TMCRA_MUTATION_REASON:-production stop}" || return 1

  if pid=$(verified_supervisor_pid); then
    supervisor_start_time=$(process_start_time "$pid")
  else
    if pid_file_points_to_unverified_live_process; then
      pid=$(read_pid_file "$PID_FILE")
      echo "refusing to stop unverified supervisor pid $pid" >&2
      return 1
    fi
    if child_pid=$(live_service_pid_from_file); then
      echo "refusing to claim a clean stop while service child pid $child_pid is still live" >&2
      return 1
    fi
    stop_public_tunnel
    rm -f -- "$PID_FILE" "$SERVICE_PID_FILE"
    echo "tmcra-memory-api is not running"
    return 0
  fi

  stop_public_tunnel
  # Identity is verified before the first signal. The start time protects every
  # later fallback from PID reuse while shutdown is in progress.
  same_process "$pid" "$supervisor_start_time" && supervisor_matches "$pid" || {
    echo "refusing to signal supervisor pid $pid after its identity changed" >&2
    return 1
  }
  kill -TERM "$pid"
  for ((attempt = 0; attempt < 60; attempt++)); do
    if ! same_process "$pid" "$supervisor_start_time"; then
      finish_verified_stop "$pid" "tmcra-memory-api stopped"
      return $?
    fi
    sleep 0.5
  done

  echo "tmcra-memory-api graceful stop exceeded 30 seconds; applying verified fallback" >&2
  if child_pid=$(read_pid_file "$SERVICE_PID_FILE"); then
    if process_alive "$child_pid"; then
      if ! service_child_matches "$child_pid" "$pid"; then
        echo "refusing to kill unverified service child pid $child_pid" >&2
        return 1
      fi
      child_start_time=$(process_start_time "$child_pid")
      same_process "$child_pid" "$child_start_time" && service_child_matches "$child_pid" "$pid" || {
        echo "refusing to signal service child pid $child_pid after its identity changed" >&2
        return 1
      }
      kill -KILL "$child_pid"
      for ((attempt = 0; attempt < 20; attempt++)); do
        if ! same_process "$child_pid" "$child_start_time"; then
          break
        fi
        sleep 0.25
      done
      if same_process "$child_pid" "$child_start_time"; then
        echo "verified service child pid $child_pid did not terminate" >&2
        return 1
      fi
    fi
  fi

  # Never kill the supervisor while it still owns an unverified live child.
  if [[ -r "/proc/$pid/task/$pid/children" ]]; then
    for current_child in $(<"/proc/$pid/task/$pid/children"); do
      if process_alive "$current_child"; then
        echo "refusing to kill supervisor pid $pid while child pid $current_child is still live" >&2
        return 1
      fi
    done
  fi

  for ((attempt = 0; attempt < 20; attempt++)); do
    if ! same_process "$pid" "$supervisor_start_time"; then
      finish_verified_stop "$pid" "tmcra-memory-api stopped after verified child termination"
      return $?
    fi
    sleep 0.25
  done
  same_process "$pid" "$supervisor_start_time" && supervisor_matches "$pid" || {
    echo "refusing to kill supervisor pid $pid after its identity changed" >&2
    return 1
  }
  kill -KILL "$pid"
  for ((attempt = 0; attempt < 20; attempt++)); do
    if ! same_process "$pid" "$supervisor_start_time"; then
      finish_verified_stop "$pid" "tmcra-memory-api stopped after verified supervisor termination"
      return $?
    fi
    sleep 0.25
  done
  echo "tmcra-memory-api could not be stopped safely (pid $pid)" >&2
  return 1
}

restart_service() {
  stop_service
  start_service
}

verify_running_service() {
  local pid=""
  local child_pid=""
  verify_local_llm || return 1
  if ! pid=$(verified_supervisor_pid); then
    if pid_file_points_to_unverified_live_process; then
      pid=$(read_pid_file "$PID_FILE")
      echo "supervisor pid file points to an unverified live process: $pid" >&2
    elif child_pid=$(live_service_pid_from_file); then
      echo "service child pid $child_pid is live without a verified supervisor" >&2
    else
      echo "tmcra-memory-api is not running" >&2
    fi
    return 1
  fi
  if ! ready; then
    echo "tmcra-memory-api supervisor is verified but the readiness probe failed (pid $pid)" >&2
    return 1
  fi
  echo "tmcra-memory-api is verified and ready (supervisor pid $pid)"
}

case "$CONTROL_ACTION" in
  start)
    start_service
    ;;
  stop)
    stop_service
    ;;
  restart)
    restart_service
    ;;
  wait-ready)
    if wait_ready; then
      echo "tmcra-memory-api is ready (supervisor pid $(verified_supervisor_pid))"
    else
      readiness_failure
      exit 1
    fi
    ;;
  verify-running)
    verify_running_service
    ;;
  status)
    verify_local_llm
    if running; then
      echo "tmcra-memory-api is running (supervisor pid $(verified_supervisor_pid))"
      curl -fsS "$READY_URL"
      echo
      if tunnel_enabled; then
        "$TUNNEL_CONTROL" status 8>&- 9>&-
      fi
    elif pid_file_points_to_unverified_live_process; then
      echo "tmcra-memory-api supervisor pid file points to an unverified live process" >&2
      exit 4
    elif child_pid=$(live_service_pid_from_file); then
      echo "tmcra-memory-api service child pid $child_pid is live without a verified supervisor" >&2
      exit 5
    else
      echo "tmcra-memory-api is not running"
      exit 3
    fi
    ;;
  *)
    echo "usage: $0 {start|stop|restart|wait-ready|verify-running|status}" >&2
    exit 2
    ;;
esac
