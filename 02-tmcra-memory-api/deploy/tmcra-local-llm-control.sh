#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${TMCRA_V4_ROOT:-/opt/tmcra}"
ENV_FILE="${TMCRA_SERVICE_ENV_FILE:-$ROOT/deploy/tmcra-service.env}"
[[ -f "$ENV_FILE" ]] || {
  echo "service environment file is missing: $ENV_FILE" >&2
  exit 1
}

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

WRITER_ENV="${TMCRA_WRITER_ENV:-}"
[[ -n "$WRITER_ENV" && -f "$WRITER_ENV" && ! -L "$WRITER_ENV" ]] || {
  echo "writer environment file is missing or unsafe: ${WRITER_ENV:-<unset>}" >&2
  exit 1
}
set -a
# shellcheck disable=SC1090
source "$WRITER_ENV"
set +a

STATE_DIR="${TMCRA_SERVICE_STATE_DIR:?TMCRA_SERVICE_STATE_DIR is required}"
CONTROL_ACTION="${1:-status}"

LLM_ROOT="${TMCRA_LOCAL_LLM_ROOT:-/opt/tmcra-data/local-llm}"
LLAMA_ROOT="${TMCRA_LLAMA_ROOT:-$LLM_ROOT/llama.cpp-b10276}"
BINARY="${TMCRA_LLAMA_SERVER_BIN:-$LLAMA_ROOT/build-cuda-sm120a/bin/llama-server}"
MODEL="${TMCRA_LOCAL_LLM_MODEL:-$LLM_ROOT/models/qwen3.6-35b-a3b-ud-iq3-s/Qwen3.6-35B-A3B-UD-IQ3_S.gguf}"
MODEL_ALIAS="${TMCRA_LOCAL_LLM_ALIAS:-tmcra-qwen3.6-35b-a3b-iq3s}"
MODEL_BYTES="${TMCRA_LOCAL_LLM_MODEL_BYTES:-13676723168}"
HOST="${TMCRA_LOCAL_LLM_HOST:-127.0.0.1}"
PORT="${TMCRA_LOCAL_LLM_PORT:-11435}"
PARALLEL="${TMCRA_LOCAL_LLM_PARALLEL:-2}"
CTX_PER_SLOT="${TMCRA_LOCAL_LLM_CTX_PER_SLOT:-65536}"
CTX_SIZE="${TMCRA_LOCAL_LLM_CTX_SIZE:-$((CTX_PER_SLOT * PARALLEL))}"
THREADS="${TMCRA_LOCAL_LLM_THREADS:-16}"
THREADS_BATCH="${TMCRA_LOCAL_LLM_THREADS_BATCH:-32}"
STARTUP_TIMEOUT_SECONDS="${TMCRA_LOCAL_LLM_STARTUP_TIMEOUT_SECONDS:-600}"

PID_DIR="$LLM_ROOT/pids"
LOG_DIR="$LLM_ROOT/logs"
SECRET_DIR="$LLM_ROOT/secrets"
PID_FILE="$PID_DIR/qwen36-server.pid"
STATE_FILE="$PID_DIR/qwen36-server.control-state"
LOG_FILE="$LOG_DIR/qwen36-server.log"
KEY_FILE="${TMCRA_LOCAL_LLM_SERVER_KEY_FILE:-$SECRET_DIR/qwen36-server-lanes.key}"
LOCK_FILE="$PID_DIR/qwen36-server.control.lock"

umask 077
mkdir -p "$PID_DIR" "$LOG_DIR" "$SECRET_DIR"
chmod 700 "$PID_DIR" "$LOG_DIR" "$SECRET_DIR"
command -v flock >/dev/null 2>&1 || {
  echo "flock is required to serialize local model control operations" >&2
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
  flock -n -x 8 || return 1
}

PRODUCTION_MUTATION_LEASE_HELD=0
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
exec 9>"$LOCK_FILE"
flock -x 9

positive_integer() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }
for value in "$PORT" "$PARALLEL" "$CTX_PER_SLOT" "$CTX_SIZE" \
  "$THREADS" "$THREADS_BATCH" "$STARTUP_TIMEOUT_SECONDS"; do
  positive_integer "$value" || {
    echo "local model numeric configuration must contain positive integers" >&2
    exit 1
  }
done
[[ "$CTX_SIZE" -eq $((CTX_PER_SLOT * PARALLEL)) ]] || {
  echo "local model context must equal context-per-slot multiplied by parallel slots" >&2
  exit 1
}

read_pid_file() {
  local value=""
  [[ -f "$PID_FILE" && ! -L "$PID_FILE" ]] || return 1
  IFS= read -r value <"$PID_FILE" || return 1
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || return 1
  printf '%s\n' "$value"
}

process_alive() {
  local pid=$1
  local line=""
  local state=""
  kill -0 "$pid" 2>/dev/null || return 1
  [[ -r "/proc/$pid/stat" ]] || return 1
  IFS= read -r line <"/proc/$pid/stat" || return 1
  [[ "$line" == *") "* ]] || return 1
  state="${line##*) }"
  state="${state%% *}"
  [[ "$state" != "Z" && "$state" != "X" ]]
}

cmdline_has_pair() {
  local pid=$1
  local option=$2
  local expected=$3
  local -a argv=()
  local index
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  mapfile -d '' -t argv <"/proc/$pid/cmdline" || true
  for ((index = 0; index + 1 < ${#argv[@]}; index++)); do
    if [[ "${argv[$index]}" == "$option" && "${argv[$((index + 1))]}" == "$expected" ]]; then
      return 0
    fi
  done
  return 1
}

process_is_owned_model() {
  local pid=$1
  process_alive "$pid" || return 1
  [[ -r "/proc/$pid/exe" ]] || return 1
  [[ "$(readlink -f "/proc/$pid/exe")" == "$(readlink -f "$BINARY")" ]] || return 1
  cmdline_has_pair "$pid" "--model" "$MODEL"
}

process_matches_contract() {
  local pid=$1
  process_is_owned_model "$pid" || return 1
  cmdline_has_pair "$pid" "--alias" "$MODEL_ALIAS" || return 1
  cmdline_has_pair "$pid" "--host" "$HOST" || return 1
  cmdline_has_pair "$pid" "--port" "$PORT" || return 1
  cmdline_has_pair "$pid" "--api-key-file" "$KEY_FILE" || return 1
  cmdline_has_pair "$pid" "--ctx-size" "$CTX_SIZE" || return 1
  cmdline_has_pair "$pid" "--parallel" "$PARALLEL"
}

lane_key_files() {
  printf '%s\n' \
    "${TMCRA_LOCAL_WRITER_API_KEY_FILE:?TMCRA_LOCAL_WRITER_API_KEY_FILE is required}" \
    "${TMCRA_LOCAL_PLANNER_API_KEY_FILE:-$TMCRA_LOCAL_WRITER_API_KEY_FILE}" \
    "${TMCRA_LOCAL_REVIEWER_API_KEY_FILE:-$TMCRA_LOCAL_WRITER_API_KEY_FILE}" \
    "${TMCRA_LOCAL_SLOW_GRAPH_API_KEY_FILE:-$TMCRA_LOCAL_WRITER_API_KEY_FILE}"
}

read_single_key() {
  local path=$1
  local first=""
  local extra=""
  [[ -f "$path" && ! -L "$path" ]] || {
    echo "local model lane key file is missing or unsafe: $path" >&2
    return 1
  }
  IFS= read -r first <"$path" || true
  [[ "$first" =~ ^[-A-Za-z0-9._~+/=]+$ ]] || {
    echo "local model lane key file must contain one safe single-line key: $path" >&2
    return 1
  }
  extra=$(tail -n +2 "$path" | sed '/^[[:space:]]*$/d' | head -n 1)
  [[ -z "$extra" ]] || {
    echo "local model lane key file contains multiple populated lines: $path" >&2
    return 1
  }
  printf '%s\n' "$first"
}

prepare_key_file() {
  local temporary=""
  local source=""
  local key=""
  local candidate=""
  local duplicate
  local -a keys=()
  while IFS= read -r source; do
    key=$(read_single_key "$source") || return 1
    duplicate=0
    for candidate in "${keys[@]:-}"; do
      [[ "$candidate" == "$key" ]] && duplicate=1
    done
    [[ "$duplicate" -eq 1 ]] || keys+=("$key")
  done < <(lane_key_files)
  [[ ${#keys[@]} -ge 1 ]] || {
    echo "no local model lane keys were configured" >&2
    return 1
  }
  temporary=$(mktemp "$SECRET_DIR/.qwen36-server-lanes.XXXXXX")
  chmod 600 "$temporary"
  printf '%s\n' "${keys[@]}" >"$temporary"
  mv -fT -- "$temporary" "$KEY_FILE"
  chmod 600 "$KEY_FILE"
}

key_fingerprint() { sha256sum "$KEY_FILE" | awk '{print $1}'; }

state_fingerprint() {
  local value=""
  [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] || return 1
  value=$(sed -n 's/^key_sha256=//p' "$STATE_FILE" | head -n 1)
  [[ "$value" =~ ^[0-9a-f]{64}$ ]] || return 1
  printf '%s\n' "$value"
}

probe_all_lanes() {
  local source=""
  local key=""
  local slots=""
  curl -fsS --max-time 5 "http://$HOST:$PORT/health" >/dev/null || return 1
  while IFS= read -r source; do
    key=$(read_single_key "$source") || return 1
    authenticated_get "$key" "http://$HOST:$PORT/props" >/dev/null || return 1
  done < <(lane_key_files)
  key=$(head -n 1 "$KEY_FILE")
  slots=$(authenticated_get "$key" "http://$HOST:$PORT/slots") || return 1
  python3 -c \
    'import json,sys; rows=json.load(sys.stdin); n=int(sys.argv[1]); c=int(sys.argv[2]); raise SystemExit(0 if len(rows)==n and all(int(x.get("n_ctx",0))>=c for x in rows) else 1)' \
    "$PARALLEL" "$CTX_PER_SLOT" <<<"$slots"
}

authenticated_get() {
  local key=$1
  local url=$2
  printf 'fail\nsilent\nshow-error\nmax-time = 5\nheader = "Authorization: Bearer %s"\nurl = "%s"\n' \
    "$key" "$url" | curl --config -
}

verified_running_pid() {
  local pid=""
  local expected=""
  local actual=""
  pid=$(read_pid_file) || return 1
  process_matches_contract "$pid" || return 1
  expected=$(state_fingerprint) || return 1
  actual=$(key_fingerprint) || return 1
  [[ "$expected" == "$actual" ]] || return 1
  probe_all_lanes || return 1
  printf '%s\n' "$pid"
}

write_state() {
  local pid=$1
  local temporary=""
  temporary=$(mktemp "$PID_DIR/.qwen36-server-state.XXXXXX")
  chmod 600 "$temporary"
  printf 'pid=%s\nkey_sha256=%s\nport=%s\nparallel=%s\nctx_per_slot=%s\n' \
    "$pid" "$(key_fingerprint)" "$PORT" "$PARALLEL" "$CTX_PER_SLOT" >"$temporary"
  mv -fT -- "$temporary" "$STATE_FILE"
}

stop_owned_pid() {
  local pid=$1
  local attempt
  process_is_owned_model "$pid" || {
    echo "refusing to signal an unverified local model process: $pid" >&2
    return 1
  }
  kill -TERM "$pid"
  for ((attempt = 0; attempt < 60; attempt++)); do
    process_alive "$pid" || return 0
    sleep 0.5
  done
  process_is_owned_model "$pid" || {
    echo "local model process identity changed during stop: $pid" >&2
    return 1
  }
  kill -KILL "$pid"
  for ((attempt = 0; attempt < 20; attempt++)); do
    process_alive "$pid" || return 0
    sleep 0.25
  done
  echo "verified local model process did not stop: $pid" >&2
  return 1
}

start_model() {
  local pid=""
  local launcher_pid=""
  local attempt
  prepare_key_file
  [[ -x "$BINARY" ]] || { echo "llama-server is missing: $BINARY" >&2; return 1; }
  [[ -f "$MODEL" && ! -L "$MODEL" ]] || { echo "local model is missing: $MODEL" >&2; return 1; }
  [[ "$(stat -c '%s' "$MODEL")" == "$MODEL_BYTES" ]] || {
    echo "local model size does not match the pinned artifact" >&2
    return 1
  }
  if pid=$(verified_running_pid); then
    echo "tmcra local model is already verified and ready (pid $pid)"
    return 0
  fi
  if pid=$(read_pid_file); then
    if process_alive "$pid"; then
      stop_owned_pid "$pid" || return 1
    fi
    rm -f -- "$PID_FILE" "$STATE_FILE"
  fi
  if command -v ss >/dev/null 2>&1 && ss -ltnH "sport = :$PORT" | grep -q .; then
    echo "refusing to start while local model port $PORT is already occupied" >&2
    return 1
  fi
  nohup "$BINARY" \
    --model "$MODEL" --alias "$MODEL_ALIAS" --host "$HOST" --port "$PORT" \
    --api-key-file "$KEY_FILE" \
    --cors-origins "http://127.0.0.1,http://localhost" \
    --ctx-size "$CTX_SIZE" --batch-size 512 --ubatch-size 256 \
    --parallel "$PARALLEL" --cont-batching --gpu-layers all --flash-attn on \
    --cache-type-k q8_0 --cache-type-v q8_0 \
    --threads "$THREADS" --threads-batch "$THREADS_BATCH" \
    --jinja --reasoning off --reasoning-budget 0 --reasoning-format deepseek \
    --metrics --slots --no-webui 8>&- 9>&- >>"$LOG_FILE" 2>&1 </dev/null &
  launcher_pid=$!
  printf '%s\n' "$launcher_pid" >"$PID_FILE"
  write_state "$launcher_pid"
  for ((attempt = 0; attempt < STARTUP_TIMEOUT_SECONDS; attempt++)); do
    if ! process_alive "$launcher_pid"; then
      echo "local model exited before readiness" >&2
      tail -n 40 "$LOG_FILE" >&2 || true
      rm -f -- "$PID_FILE" "$STATE_FILE"
      return 1
    fi
    if verified_running_pid >/dev/null; then
      echo "tmcra local model started (pid $launcher_pid)"
      return 0
    fi
    sleep 1
  done
  echo "local model readiness timed out" >&2
  stop_owned_pid "$launcher_pid" || true
  rm -f -- "$PID_FILE" "$STATE_FILE"
  return 1
}

stop_model() {
  local pid=""
  if ! pid=$(read_pid_file); then
    rm -f -- "$PID_FILE" "$STATE_FILE"
    echo "tmcra local model is not running"
    return 0
  fi
  if ! process_alive "$pid"; then
    rm -f -- "$PID_FILE" "$STATE_FILE"
    echo "tmcra local model stale pid state was removed"
    return 0
  fi
  stop_owned_pid "$pid"
  rm -f -- "$PID_FILE" "$STATE_FILE"
  echo "tmcra local model stopped"
}

verify_model() {
  local pid=""
  prepare_key_file
  pid=$(verified_running_pid) || {
    echo "tmcra local model is not verified and ready" >&2
    return 1
  }
  echo "tmcra local model is verified and ready (pid $pid)"
}

case "$CONTROL_ACTION" in
  start) start_model ;;
  stop) stop_model ;;
  restart) stop_model; start_model ;;
  verify-running) verify_model ;;
  status) verify_model || exit 3 ;;
  *)
    echo "usage: $0 {start|stop|restart|verify-running|status}" >&2
    exit 2
    ;;
esac
