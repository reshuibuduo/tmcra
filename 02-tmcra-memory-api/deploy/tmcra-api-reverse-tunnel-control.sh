#!/usr/bin/env bash
set -euo pipefail

ROOT="${TMCRA_V4_ROOT:-/opt/tmcra}"
ENV_FILE="${TMCRA_SERVICE_ENV_FILE:-$ROOT/deploy/tmcra-service.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "service environment file is missing: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

ENABLED="${TMCRA_PUBLIC_REVERSE_TUNNEL_ENABLED:-0}"
STATE_ROOT="${TMCRA_PUBLIC_REVERSE_TUNNEL_STATE_DIR:-${TMCRA_SERVICE_STATE_DIR:?TMCRA_SERVICE_STATE_DIR is required}/public-reverse-tunnel}"
SUPERVISOR_PID_FILE="$STATE_ROOT/supervisor.pid"
CHILD_PID_FILE="$STATE_ROOT/ssh.pid"
LOG_FILE="$STATE_ROOT/tunnel.log"
SSH_BIN="${TMCRA_PUBLIC_REVERSE_TUNNEL_SSH_BIN:-/usr/bin/ssh}"
TARGET_HOST="${TMCRA_PUBLIC_REVERSE_TUNNEL_HOST:-}"
TARGET_PORT="${TMCRA_PUBLIC_REVERSE_TUNNEL_PORT:-22}"
TARGET_USER="${TMCRA_PUBLIC_REVERSE_TUNNEL_USER:-ubuntu}"
KEY_FILE="${TMCRA_PUBLIC_REVERSE_TUNNEL_KEY_FILE:-/opt/tmcra/.ssh/tmcra_vm_reverse}"
KNOWN_HOSTS_FILE="${TMCRA_PUBLIC_REVERSE_TUNNEL_KNOWN_HOSTS_FILE:-/opt/tmcra/.ssh/tmcra_vm_reverse_known_hosts}"
REMOTE_BIND_HOST="${TMCRA_PUBLIC_REVERSE_TUNNEL_BIND_HOST:-127.0.0.1}"
REMOTE_BIND_PORT="${TMCRA_PUBLIC_REVERSE_TUNNEL_BIND_PORT:-22019}"
LOCAL_HOST="${TMCRA_PUBLIC_REVERSE_TUNNEL_LOCAL_HOST:-127.0.0.1}"
LOCAL_PORT="${TMCRA_PUBLIC_REVERSE_TUNNEL_LOCAL_PORT:-${TMCRA_SERVICE_BIND_PORT:-2009}}"
VERIFY_URL="${TMCRA_PUBLIC_REVERSE_TUNNEL_VERIFY_URL:-${TMCRA_SERVICE_PUBLIC_BASE_URL%/}/healthz}"

umask 077
mkdir -p "$STATE_ROOT"
chmod 700 "$STATE_ROOT"

enabled() {
  [[ "$ENABLED" == "1" ]]
}

process_running() {
  local pid_file=$1
  [[ -s "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

running() {
  process_running "$SUPERVISOR_PID_FILE"
}

child_running() {
  process_running "$CHILD_PID_FILE"
}

validate_configuration() {
  [[ -x "$SSH_BIN" ]] || { echo "reverse tunnel ssh binary is unavailable: $SSH_BIN" >&2; return 1; }
  [[ -n "$TARGET_HOST" ]] || { echo "TMCRA_PUBLIC_REVERSE_TUNNEL_HOST is required" >&2; return 1; }
  [[ -f "$KEY_FILE" ]] || { echo "reverse tunnel key is unavailable: $KEY_FILE" >&2; return 1; }
  [[ -f "$KNOWN_HOSTS_FILE" ]] || { echo "reverse tunnel known-hosts file is unavailable: $KNOWN_HOSTS_FILE" >&2; return 1; }
  [[ "$TARGET_PORT" =~ ^[0-9]+$ && "$REMOTE_BIND_PORT" =~ ^[0-9]+$ && "$LOCAL_PORT" =~ ^[0-9]+$ ]] || {
    echo "reverse tunnel ports must be numeric" >&2
    return 1
  }
}

public_ready() {
  curl -fsS --connect-timeout 5 --max-time 15 "$VERIFY_URL" >/dev/null 2>&1
}

supervise() {
  validate_configuration
  local child_pid=""
  cleanup() {
    if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
      kill -TERM "$child_pid" 2>/dev/null || true
      wait "$child_pid" 2>/dev/null || true
    fi
    rm -f "$CHILD_PID_FILE" "$SUPERVISOR_PID_FILE"
  }
  trap cleanup EXIT
  trap 'exit 0' INT TERM
  printf '%s\n' "$$" >"$SUPERVISOR_PID_FILE"

  while true; do
    "$SSH_BIN" -N -T -p "$TARGET_PORT" \
      -i "$KEY_FILE" \
      -o BatchMode=yes \
      -o ExitOnForwardFailure=yes \
      -o IdentitiesOnly=yes \
      -o ConnectTimeout=10 \
      -o ConnectionAttempts=3 \
      -o ServerAliveInterval=10 \
      -o ServerAliveCountMax=3 \
      -o StrictHostKeyChecking=yes \
      -o "UserKnownHostsFile=$KNOWN_HOSTS_FILE" \
      -R "$REMOTE_BIND_HOST:$REMOTE_BIND_PORT:$LOCAL_HOST:$LOCAL_PORT" \
      "$TARGET_USER@$TARGET_HOST" &
    child_pid=$!
    printf '%s\n' "$child_pid" >"$CHILD_PID_FILE"
    set +e
    wait "$child_pid"
    exit_code=$?
    set -e
    rm -f "$CHILD_PID_FILE"
    printf '%s reverse tunnel exited with status %s; retrying\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$exit_code"
    sleep 2
  done
}

start_tunnel() {
  if ! enabled; then
    echo "tmcra-api-reverse-tunnel is disabled"
    return 0
  fi
  validate_configuration
  if running; then
    if child_running; then
      echo "tmcra-api-reverse-tunnel is already running (pid $(cat "$SUPERVISOR_PID_FILE"))"
      return 0
    fi
    echo "reverse tunnel supervisor is running without an ssh child" >&2
    return 1
  fi
  rm -f "$SUPERVISOR_PID_FILE" "$CHILD_PID_FILE"
  # Release/deploy scripts keep their lock descriptors on fd 8/9.  A daemon
  # must never inherit those descriptors or it can pin a production release
  # lock for its entire lifetime.
  nohup "$0" supervise 8>&- 9>&- >>"$LOG_FILE" 2>&1 </dev/null &
  launcher_pid=$!
  for _ in $(seq 1 20); do
    if running && child_running; then
      echo "tmcra-api-reverse-tunnel started (pid $(cat "$SUPERVISOR_PID_FILE"))"
      return 0
    fi
    kill -0 "$launcher_pid" 2>/dev/null || break
    sleep 0.25
  done
  echo "tmcra-api-reverse-tunnel failed to start; see $LOG_FILE" >&2
  tail -n 30 "$LOG_FILE" >&2 || true
  return 1
}

stop_tunnel() {
  if ! running; then
    rm -f "$SUPERVISOR_PID_FILE" "$CHILD_PID_FILE"
    echo "tmcra-api-reverse-tunnel is not running"
    return 0
  fi
  pid=$(cat "$SUPERVISOR_PID_FILE")
  kill -TERM "$pid"
  for _ in $(seq 1 40); do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$SUPERVISOR_PID_FILE" "$CHILD_PID_FILE"
      echo "tmcra-api-reverse-tunnel stopped"
      return 0
    fi
    sleep 0.25
  done
  if child_running; then
    kill -TERM "$(cat "$CHILD_PID_FILE")" 2>/dev/null || true
  fi
  kill -KILL "$pid" 2>/dev/null || true
  rm -f "$SUPERVISOR_PID_FILE" "$CHILD_PID_FILE"
  echo "tmcra-api-reverse-tunnel required forced shutdown (pid $pid)" >&2
  return 0
}

wait_ready() {
  for _ in $(seq 1 30); do
    if running && child_running && public_ready; then
      echo "tmcra-api-reverse-tunnel is ready"
      return 0
    fi
    sleep 1
  done
  echo "tmcra-api-reverse-tunnel failed the public readiness gate" >&2
  tail -n 30 "$LOG_FILE" >&2 || true
  return 1
}

case "${1:-status}" in
  start)
    start_tunnel
    ;;
  stop)
    stop_tunnel
    ;;
  restart)
    stop_tunnel
    start_tunnel
    ;;
  supervise)
    supervise
    ;;
  wait-ready)
    if enabled; then wait_ready; else echo "tmcra-api-reverse-tunnel is disabled"; fi
    ;;
  status)
    if ! enabled; then
      echo "tmcra-api-reverse-tunnel is disabled"
    elif running && child_running; then
      echo "tmcra-api-reverse-tunnel is running (pid $(cat "$SUPERVISOR_PID_FILE"), ssh $(cat "$CHILD_PID_FILE"))"
      if public_ready; then
        echo "tmcra-api-reverse-tunnel public readiness probe passed"
      else
        echo "warning: reverse tunnel is running but the public readiness probe timed out or failed" >&2
      fi
    else
      echo "tmcra-api-reverse-tunnel is not running"
      exit 3
    fi
    ;;
  *)
    echo "usage: $0 {start|stop|restart|wait-ready|status}" >&2
    exit 2
    ;;
esac
