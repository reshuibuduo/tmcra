#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${TMCRA_V4_ROOT:-/opt/tmcra}"
ENV_FILE="${TMCRA_SERVICE_ENV_FILE:-$ROOT/deploy/tmcra-service.env}"
API_CONTROL="${TMCRA_MEMORY_API_CONTROL:-$ROOT/deploy/tmcra-memory-api-control.sh}"
LOCAL_LLM_CONTROL="${TMCRA_LOCAL_LLM_CONTROL:-$ROOT/deploy/tmcra-local-llm-control.sh}"
PYTHON="${TMCRA_SERVICE_PYTHON:-/opt/tmcra_env_20260713/bin/python}"

usage() {
  echo "usage: $0 --reason <ticket-or-short-reason> -- <foreground-command> [args...]" >&2
}

REASON=""
if [[ "${1:-}" == "--reason" && -n "${2:-}" ]]; then
  REASON=$2
  shift 2
fi
[[ "${1:-}" == "--" ]] || { usage; exit 2; }
shift
[[ -n "$REASON" && ${#REASON} -le 160 && "$REASON" != *$'\n'* && "$REASON" != *$'\r'* ]] || {
  echo "maintenance reason must be 1-160 characters on one line" >&2
  exit 2
}
[[ $# -gt 0 ]] || { usage; exit 2; }
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
  echo "service environment file is missing or unsafe: $ENV_FILE" >&2
  exit 1
}
[[ -x "$API_CONTROL" && -x "$LOCAL_LLM_CONTROL" && -x "$PYTHON" ]] || {
  echo "production maintenance controls are incomplete" >&2
  exit 1
}
command -v flock >/dev/null 2>&1 || { echo "flock is required" >&2; exit 1; }
command -v setsid >/dev/null 2>&1 || { echo "setsid is required" >&2; exit 1; }

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

STATE_DIR="${TMCRA_SERVICE_STATE_DIR:?TMCRA_SERVICE_STATE_DIR is required}"
AUDIT_LOG="$STATE_DIR/maintenance-audit.jsonl"
LEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$-${RANDOM}"
COMMAND_NAME="$(basename -- "$1")"
restore_required=0
command_pid=0
command_exit_code=0

mkdir -p -- "$STATE_DIR"
chmod 700 "$STATE_DIR"
exec 8>"$STATE_DIR/release.lock"
flock -x 8
export TMCRA_DEPLOY_LOCK_HELD=1
export TMCRA_MUTATION_REASON="maintenance stop: $REASON"

append_audit() {
  local event=$1
  local result=${2:-}
  local exit_code=${3:-}
  TMCRA_MAINTENANCE_EVENT="$event" \
  TMCRA_MAINTENANCE_RESULT="$result" \
  TMCRA_MAINTENANCE_EXIT_CODE="$exit_code" \
  TMCRA_MAINTENANCE_LEASE_ID="$LEASE_ID" \
  TMCRA_MAINTENANCE_REASON="$REASON" \
  TMCRA_MAINTENANCE_COMMAND="$COMMAND_NAME" \
    "$PYTHON" - "$AUDIT_LOG" <<'PY'
import datetime as dt
import json
import os
import pathlib
import socket
import sys

path = pathlib.Path(sys.argv[1])
record = {
    "schema_version": "tmcra.production-maintenance-audit.1",
    "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    "event": os.environ["TMCRA_MAINTENANCE_EVENT"],
    "result": os.environ.get("TMCRA_MAINTENANCE_RESULT") or None,
    "exit_code": int(os.environ["TMCRA_MAINTENANCE_EXIT_CODE"])
    if os.environ.get("TMCRA_MAINTENANCE_EXIT_CODE")
    else None,
    "lease_id": os.environ["TMCRA_MAINTENANCE_LEASE_ID"],
    "reason": os.environ["TMCRA_MAINTENANCE_REASON"],
    "command": os.environ["TMCRA_MAINTENANCE_COMMAND"],
    "host": socket.gethostname(),
    "uid": os.getuid(),
    "pid": os.getppid(),
}
with path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
os.chmod(path, 0o600)
PY
}

stop_command_group() {
  local attempt
  [[ "$command_pid" =~ ^[1-9][0-9]*$ ]] || return 0
  if kill -0 -- "-$command_pid" 2>/dev/null; then
    kill -TERM -- "-$command_pid" 2>/dev/null || true
    for ((attempt = 0; attempt < 25; attempt++)); do
      kill -0 -- "-$command_pid" 2>/dev/null || break
      sleep 0.2
    done
    if kill -0 -- "-$command_pid" 2>/dev/null; then
      kill -KILL -- "-$command_pid" 2>/dev/null || true
    fi
  fi
  wait "$command_pid" 2>/dev/null || true
  command_pid=0
}

restore_production() {
  local restore_ok=0
  [[ "$restore_required" -eq 1 ]] || return 0
  if "$API_CONTROL" start && "$API_CONTROL" verify-running; then
    restore_ok=1
  else
    "$LOCAL_LLM_CONTROL" start || true
    if "$API_CONTROL" start && "$API_CONTROL" verify-running; then
      restore_ok=1
    fi
  fi
  [[ "$restore_ok" -eq 1 ]]
}

finish() {
  local original_code=$?
  local final_code=$original_code
  local result="command-failed"
  trap - EXIT INT TERM HUP
  set +e
  stop_command_group
  if [[ "$restore_required" -eq 1 ]]; then
    if restore_production; then
      result="production-restored"
    else
      result="restore-failed"
      final_code=74
    fi
  elif [[ "$original_code" -eq 0 ]]; then
    result="completed-without-downtime"
  fi
  append_audit "lease-finished" "$result" "$final_code" || true
  echo "TMCRA_MAINTENANCE_FINISHED lease=$LEASE_ID result=$result exit_code=$final_code" >&2
  exit "$final_code"
}

handle_signal() {
  local code=$1
  stop_command_group
  exit "$code"
}

trap finish EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM
trap 'handle_signal 129' HUP

"$API_CONTROL" verify-running
append_audit "lease-acquired" "production-verified" ""
restore_required=1
"$API_CONTROL" stop
"$LOCAL_LLM_CONTROL" stop

# The experiment remains in a dedicated process group. Any descendants left
# behind by a failed command are terminated before production is restored.
setsid --wait "$@" &
command_pid=$!
set +e
wait "$command_pid"
command_exit_code=$?
set -e
stop_command_group
exit "$command_exit_code"
