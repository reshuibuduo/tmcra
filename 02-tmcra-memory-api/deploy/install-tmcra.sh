#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SOURCE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PREFIX="${TMCRA_INSTALL_PREFIX:-/opt/tmcra}"
DATA_DIR="${TMCRA_INSTALL_DATA_DIR:-/opt/tmcra-data}"
MODEL_ROOT="${TMCRA_INSTALL_MODEL_ROOT:-/opt/tmcra-models}"
CONFIG_DIR="${TMCRA_INSTALL_CONFIG_DIR:-/etc/tmcra}"
VENV="${TMCRA_INSTALL_VENV:-/opt/tmcra-venv}"
BIN_DIR="${TMCRA_INSTALL_BIN_DIR:-/usr/local/bin}"
PUBLIC_URL=""
BIND_HOST="127.0.0.1"
BIND_PORT="2009"
MODEL_PATH=""
MODEL_ALIAS="tmcra-qwen3.6-35b-a3b-iq3s"
LLAMA_SERVER=""
EMBEDDING_MODEL=""
CROSS_MODEL=""
PREPARE_ONLY=0
CHECK_ONLY=0
FORCE_CONFIG=0
SKIP_PYTHON_INSTALL=0

QWEN_REPO="unsloth/Qwen3.6-35B-A3B-GGUF"
QWEN_REVISION="a483e9e6cbd595906af30beda3187c2663a1118c"
QWEN_FILE="Qwen3.6-35B-A3B-UD-IQ3_S.gguf"
QWEN_SHA256="66a3ca888ce13482b40c333db2432c0ebde3a7b13754fc29f0c6f5e89703ec66"
BGE_REPO="BAAI/bge-m3"
BGE_REVISION="5617a9f61b028005a4858fdac845db406aefb181"
BGE_MODEL_SHA256="b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38"
CROSS_REPO="BAAI/bge-reranker-v2-m3"
CROSS_REVISION="953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
CROSS_MODEL_SHA256="d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286"
LLAMA_CPP_REPO="https://github.com/ggml-org/llama.cpp.git"
LLAMA_CPP_TAG="b10276"

usage() {
  cat <<'EOF'
TMCRA single-GPU installer

Default verified model stack:
  Qwen3.6-35B-A3B UD-IQ3_S + BAAI/bge-m3 + BAAI/bge-reranker-v2-m3

Usage:
  sudo ./install.sh --public-url https://memory.example.com

Use an existing OpenAI-compatible GGUF model:
  sudo ./install.sh --public-url https://memory.example.com \
    --model-path /models/model.gguf --model-alias my-model \
    --llama-server /usr/local/bin/llama-server

Options:
  --public-url URL       Required HTTPS origin used by clients.
  --model-path PATH      Existing GGUF. The verified Qwen file is downloaded when omitted.
  --model-alias NAME     OpenAI-compatible model alias exposed by llama-server.
  --llama-server PATH    Existing CUDA llama-server. A pinned build is compiled when omitted.
  --embedding-model DIR  Existing BGE-compatible embedding directory.
  --cross-model DIR      Existing cross-encoder directory.
  --prefix DIR           Service source destination (default /opt/tmcra).
  --data-dir DIR         Durable state root (default /opt/tmcra-data).
  --model-root DIR       Downloaded model root (default /opt/tmcra-models).
  --config-dir DIR       Private environment root (default /etc/tmcra).
  --venv DIR             Python environment (default /opt/tmcra-venv).
  --bin-dir DIR          Command destination (default /usr/local/bin).
  --bind-host HOST       Internal API bind host (default 127.0.0.1).
  --bind-port PORT       Internal API bind port (default 2009).
  --prepare-only         Install and configure without starting model/API processes.
  --skip-python-install  Reuse an already populated --venv.
  --force-config         Replace existing service.env and writer.env.
  --check                Validate arguments and local prerequisites without writing.
  -h, --help             Show this help.
EOF
}

need_value() {
  [[ $# -ge 2 && -n "$2" ]] || {
    echo "missing value for $1" >&2
    usage >&2
    exit 2
  }
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --public-url) need_value "$@"; PUBLIC_URL=$2; shift 2 ;;
    --model-path) need_value "$@"; MODEL_PATH=$2; shift 2 ;;
    --model-alias) need_value "$@"; MODEL_ALIAS=$2; shift 2 ;;
    --llama-server) need_value "$@"; LLAMA_SERVER=$2; shift 2 ;;
    --embedding-model) need_value "$@"; EMBEDDING_MODEL=$2; shift 2 ;;
    --cross-model) need_value "$@"; CROSS_MODEL=$2; shift 2 ;;
    --prefix) need_value "$@"; PREFIX=$2; shift 2 ;;
    --data-dir) need_value "$@"; DATA_DIR=$2; shift 2 ;;
    --model-root) need_value "$@"; MODEL_ROOT=$2; shift 2 ;;
    --config-dir) need_value "$@"; CONFIG_DIR=$2; shift 2 ;;
    --venv) need_value "$@"; VENV=$2; shift 2 ;;
    --bin-dir) need_value "$@"; BIN_DIR=$2; shift 2 ;;
    --bind-host) need_value "$@"; BIND_HOST=$2; shift 2 ;;
    --bind-port) need_value "$@"; BIND_PORT=$2; shift 2 ;;
    --prepare-only) PREPARE_ONLY=1; shift ;;
    --skip-python-install) SKIP_PYTHON_INSTALL=1; shift ;;
    --force-config) FORCE_CONFIG=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

safe_assignment_value() {
  [[ "$1" =~ ^[A-Za-z0-9_./:@%+,-]+$ ]]
}

for pair in \
  "prefix:$PREFIX" "data-dir:$DATA_DIR" "model-root:$MODEL_ROOT" \
  "config-dir:$CONFIG_DIR" "venv:$VENV" "bin-dir:$BIN_DIR" \
  "model-alias:$MODEL_ALIAS" "bind-host:$BIND_HOST"; do
  name=${pair%%:*}
  value=${pair#*:}
  safe_assignment_value "$value" || {
    echo "$name contains unsupported characters: $value" >&2
    exit 2
  }
done
[[ "$BIND_PORT" =~ ^[1-9][0-9]{0,4}$ ]] && (( BIND_PORT <= 65535 )) || {
  echo "bind port must be between 1 and 65535" >&2
  exit 2
}
if [[ -z "$PUBLIC_URL" && "$PREPARE_ONLY" -eq 0 ]]; then
  echo "--public-url is required for a started deployment" >&2
  exit 2
fi
PUBLIC_URL="${PUBLIC_URL:-https://example.invalid}"
[[ "$PUBLIC_URL" =~ ^https://[A-Za-z0-9._:-]+/?$ ]] || {
  echo "--public-url must be a credential-free HTTPS origin" >&2
  exit 2
}

for command_name in python3 git curl sha256sum stat; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "required command is missing: $command_name" >&2
    exit 1
  }
done

if [[ -n "$MODEL_PATH" ]]; then
  [[ -f "$MODEL_PATH" && ! -L "$MODEL_PATH" ]] || {
    echo "model file is missing or unsafe: $MODEL_PATH" >&2
    exit 1
  }
fi
if [[ -n "$LLAMA_SERVER" ]]; then
  [[ -x "$LLAMA_SERVER" ]] || {
    echo "llama-server is not executable: $LLAMA_SERVER" >&2
    exit 1
  }
fi
if [[ -n "$EMBEDDING_MODEL" ]]; then
  [[ -d "$EMBEDDING_MODEL" ]] || {
    echo "embedding model directory is missing: $EMBEDDING_MODEL" >&2
    exit 1
  }
fi
if [[ -n "$CROSS_MODEL" ]]; then
  [[ -d "$CROSS_MODEL" ]] || {
    echo "cross-encoder directory is missing: $CROSS_MODEL" >&2
    exit 1
  }
  [[ -f "$CROSS_MODEL/TMCRA_MODEL_MANIFEST.json" ]] || {
    echo "cross-encoder manifest is missing: $CROSS_MODEL/TMCRA_MODEL_MANIFEST.json" >&2
    exit 1
  }
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  GPU_MEMORY_MIB=$(nvidia-smi --query-gpu=memory.total \
    --format=csv,noheader,nounits 2>/dev/null | sort -nr | head -n 1 || true)
  if [[ "$GPU_MEMORY_MIB" =~ ^[0-9]+$ && "$GPU_MEMORY_MIB" -lt 30000 ]]; then
    echo "warning: the default profile recommends at least 32 GB VRAM; detected ${GPU_MEMORY_MIB} MiB" >&2
  fi
else
  echo "warning: nvidia-smi is unavailable; CUDA readiness will be checked during preflight" >&2
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "TMCRA installer check passed"
  exit 0
fi
[[ "$EUID" -eq 0 ]] || {
  echo "run this installer with sudo, or use root-owned custom destinations" >&2
  exit 1
}

SERVICE_ENV="$CONFIG_DIR/service.env"
WRITER_ENV="$CONFIG_DIR/writer.env"
if [[ "$FORCE_CONFIG" -eq 0 && ( -e "$SERVICE_ENV" || -e "$WRITER_ENV" ) ]]; then
  echo "configuration already exists; inspect it or rerun with --force-config" >&2
  exit 1
fi

install -d -m 0755 "$PREFIX" "$VENV" "$MODEL_ROOT" "$BIN_DIR"
install -d -m 0700 "$DATA_DIR" "$CONFIG_DIR" \
  "$DATA_DIR/repository" "$DATA_DIR/service-state" \
  "$DATA_DIR/local-llm/secrets"

if [[ "$SKIP_PYTHON_INSTALL" -eq 0 ]]; then
  if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv --system-site-packages "$VENV"
  fi
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -r "$SOURCE_ROOT/requirements-tmcra-service.txt"
  "$VENV/bin/python" -m pip install --upgrade "huggingface_hub>=0.34,<1.0"
fi
PYTHON="$VENV/bin/python"
[[ -x "$PYTHON" ]] || {
  echo "Python environment is incomplete: $PYTHON" >&2
  exit 1
}
HF="$VENV/bin/hf"
[[ -x "$HF" ]] || {
  echo "Hugging Face CLI is missing from $VENV; rerun without --skip-python-install" >&2
  exit 1
}

download_model() {
  local repo=$1
  local revision=$2
  local destination=$3
  shift 3
  install -d -m 0755 "$destination"
  HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}" \
    "$HF" download "$repo" --revision "$revision" --local-dir "$destination" "$@"
}

if [[ -z "$MODEL_PATH" ]]; then
  QWEN_DIR="$MODEL_ROOT/Qwen/Qwen3.6-35B-A3B-UD-IQ3_S"
  MODEL_PATH="$QWEN_DIR/$QWEN_FILE"
  if [[ ! -f "$MODEL_PATH" ]]; then
    download_model "$QWEN_REPO" "$QWEN_REVISION" "$QWEN_DIR" \
      --include "$QWEN_FILE"
  fi
  ACTUAL_QWEN_SHA256=$(sha256sum "$MODEL_PATH" | awk '{print $1}')
  [[ "$ACTUAL_QWEN_SHA256" == "$QWEN_SHA256" ]] || {
    echo "downloaded Qwen artifact failed SHA-256 verification" >&2
    exit 1
  }
fi
MODEL_PATH="$(readlink -f -- "$MODEL_PATH")"
MODEL_BYTES=$(stat -c '%s' -- "$MODEL_PATH")

if [[ -z "$EMBEDDING_MODEL" ]]; then
  EMBEDDING_MODEL="$MODEL_ROOT/BAAI/bge-m3"
  [[ -f "$EMBEDDING_MODEL/config.json" \
    && -f "$EMBEDDING_MODEL/pytorch_model.bin" \
    && -f "$EMBEDDING_MODEL/tokenizer.json" \
    && -f "$EMBEDDING_MODEL/modules.json" ]] || \
    download_model "$BGE_REPO" "$BGE_REVISION" "$EMBEDDING_MODEL" \
      1_Pooling/config.json colbert_linear.pt config.json \
      config_sentence_transformers.json modules.json pytorch_model.bin \
      sentence_bert_config.json sentencepiece.bpe.model sparse_linear.pt \
      special_tokens_map.json tokenizer.json tokenizer_config.json
  [[ "$(sha256sum "$EMBEDDING_MODEL/pytorch_model.bin" | awk '{print $1}')" \
    == "$BGE_MODEL_SHA256" ]] || {
    echo "downloaded BGE-M3 artifact failed SHA-256 verification" >&2
    exit 1
  }
fi
EMBEDDING_MODEL="$(readlink -f -- "$EMBEDDING_MODEL")"

if [[ -z "$CROSS_MODEL" ]]; then
  CROSS_MODEL="$MODEL_ROOT/BAAI/bge-reranker-v2-m3"
  [[ -f "$CROSS_MODEL/config.json" \
    && -f "$CROSS_MODEL/model.safetensors" \
    && -f "$CROSS_MODEL/tokenizer.json" ]] || \
    download_model "$CROSS_REPO" "$CROSS_REVISION" "$CROSS_MODEL" \
      config.json model.safetensors sentencepiece.bpe.model \
      special_tokens_map.json tokenizer.json tokenizer_config.json
  [[ "$(sha256sum "$CROSS_MODEL/model.safetensors" | awk '{print $1}')" \
    == "$CROSS_MODEL_SHA256" ]] || {
    echo "downloaded BGE reranker artifact failed SHA-256 verification" >&2
    exit 1
  }
  install -m 0644 -- \
    "$SOURCE_ROOT/deploy/model-manifests/bge-reranker-v2-m3.TMCRA_MODEL_MANIFEST.json" \
    "$CROSS_MODEL/TMCRA_MODEL_MANIFEST.json"
fi
CROSS_MODEL="$(readlink -f -- "$CROSS_MODEL")"

if [[ -z "$LLAMA_SERVER" ]]; then
  if command -v llama-server >/dev/null 2>&1; then
    LLAMA_SERVER=$(command -v llama-server)
  else
    command -v cmake >/dev/null 2>&1 || {
      echo "cmake is required to build llama.cpp" >&2
      exit 1
    }
    command -v nvcc >/dev/null 2>&1 || {
      echo "nvcc is required to build the CUDA llama.cpp server" >&2
      exit 1
    }
    LLAMA_ROOT="$DATA_DIR/local-llm/llama.cpp-$LLAMA_CPP_TAG"
    if [[ ! -d "$LLAMA_ROOT/.git" ]]; then
      git clone --depth 1 --branch "$LLAMA_CPP_TAG" "$LLAMA_CPP_REPO" "$LLAMA_ROOT"
    fi
    cmake -S "$LLAMA_ROOT" -B "$LLAMA_ROOT/build-cuda" \
      -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
    cmake --build "$LLAMA_ROOT/build-cuda" --config Release \
      --parallel "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 8)" \
      --target llama-server
    LLAMA_SERVER="$LLAMA_ROOT/build-cuda/bin/llama-server"
  fi
fi
LLAMA_SERVER="$(readlink -f -- "$LLAMA_SERVER")"
[[ -x "$LLAMA_SERVER" ]] || {
  echo "llama-server build is incomplete: $LLAMA_SERVER" >&2
  exit 1
}

cp -a -- "$SOURCE_ROOT/." "$PREFIX/"
chmod 0755 "$PREFIX/deploy/"*.sh "$PREFIX/deploy/tmcra"

LANE_KEY="$DATA_DIR/local-llm/secrets/model-api.key"
if [[ ! -f "$LANE_KEY" ]]; then
  "$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(48))' >"$LANE_KEY"
fi
chmod 0600 "$LANE_KEY"

if [[ "$FORCE_CONFIG" -eq 1 ]]; then
  BACKUP_SUFFIX=$(date -u +%Y%m%dT%H%M%SZ)
  [[ ! -f "$SERVICE_ENV" ]] || install -m 0600 -- "$SERVICE_ENV" \
    "$CONFIG_DIR/service.env.backup-$BACKUP_SUFFIX"
  [[ ! -f "$WRITER_ENV" ]] || install -m 0600 -- "$WRITER_ENV" \
    "$CONFIG_DIR/writer.env.backup-$BACKUP_SUFFIX"
fi

SERVICE_TMP=$(mktemp "$CONFIG_DIR/.service.env.XXXXXX")
WRITER_TMP=$(mktemp "$CONFIG_DIR/.writer.env.XXXXXX")
cleanup() { rm -f -- "$SERVICE_TMP" "$WRITER_TMP"; }
trap cleanup EXIT

cat >"$SERVICE_TMP" <<EOF
TMCRA_SERVICE_PUBLIC_BASE_URL=${PUBLIC_URL%/}
TMCRA_SERVICE_BIND_HOST=$BIND_HOST
TMCRA_SERVICE_BIND_PORT=$BIND_PORT
TMCRA_SERVICE_TLS_PROXY_MODE=trusted_proxy
TMCRA_SERVICE_STATE_DIR=$DATA_DIR/service-state
TMCRA_SERVICE_CONTROL_DB=$DATA_DIR/service-state/control.sqlite3
TMCRA_V4_ROOT=$PREFIX
TMCRA_INTEGRATED_REPO=$PREFIX
TMCRA_WRITER_ENV=$WRITER_ENV
TMCRA_SERVICE_PYTHON=$PYTHON
TMCRA_EMBEDDING_MODEL=$EMBEDDING_MODEL
TMCRA_CROSS_MODEL=$CROSS_MODEL
TMCRA_CHECKPOINT=$PREFIX/models/tmcra_v3_reranker.pt
TMCRA_LEARNED_GRAPH_ENABLED=0
TMCRA_SERVICE_DEVICE=cuda
TMCRA_SERVICE_GRAPH_DEVICE=cuda
TMCRA_SERVICE_WRITER_EXECUTION_MODE=resident
TMCRA_SERVICE_WRITER_POOL_SIZE=4
TMCRA_SERVICE_WRITER_POOL_STARTUP_TIMEOUT_SECONDS=120
TMCRA_SERVICE_WRITER_POOL_REQUEST_TIMEOUT_SECONDS=900
TMCRA_SERVICE_STARTUP_PREFLIGHT_MODE=full
TMCRA_SERVICE_STARTUP_TIMEOUT_SECONDS=900
TMCRA_SERVICE_RECALL_POOL_MIN_SIZE=2
TMCRA_SERVICE_RECALL_POOL_MAX_SIZE=2
TMCRA_SERVICE_RECALL_GLOBAL_QUEUE_LIMIT=8
TMCRA_SERVICE_RECALL_TENANT_QUEUE_LIMIT=2
TMCRA_SERVICE_RECALL_GPU_HEADROOM_BYTES=6442450944
TMCRA_SERVICE_RECALL_REPLICA_ESTIMATE_BYTES=5368709120
TMCRA_SERVICE_PRELOAD_ONLINE_ENGINE=1
TMCRA_LOCAL_LLM_ROOT=$DATA_DIR/local-llm
TMCRA_LLAMA_SERVER_BIN=$LLAMA_SERVER
TMCRA_LOCAL_LLM_MODEL=$MODEL_PATH
TMCRA_LOCAL_LLM_ALIAS=$MODEL_ALIAS
TMCRA_LOCAL_LLM_MODEL_BYTES=$MODEL_BYTES
TMCRA_LOCAL_LLM_HOST=127.0.0.1
TMCRA_LOCAL_LLM_PORT=11435
TMCRA_LOCAL_LLM_PARALLEL=3
TMCRA_LOCAL_LLM_CTX_PER_SLOT=65536
TMCRA_LOCAL_LLM_CTX_SIZE=196608
TMCRA_LOCAL_LLM_STARTUP_TIMEOUT_SECONDS=900
EOF

cat >"$WRITER_TMP" <<EOF
TMCRA_DEEPSEEK_WRITER_BASE_URL=https://api.deepseek.com/v1
TMCRA_DEEPSEEK_WRITER_KEY_POOL=local-route-disabled
TMCRA_DEEPSEEK_WRITER_KEY_POOL_COUNT=1
TMCRA_WRITER_PROVIDER=local-qwen
TMCRA_WRITER_MAX_TOKENS=16384
TMCRA_RECALL_PLANNER_PROVIDER=local-qwen
TMCRA_RECALL_PLANNER_MAX_TOKENS=512
TMCRA_RECALL_PLANNER_TIMEOUT_SECONDS=60
TMCRA_WRITER_REVIEWER_PROVIDER=local-qwen
TMCRA_SLOW_GRAPH_PROVIDER=local-qwen
TMCRA_LOCAL_WRITER_BASE_URL=http://127.0.0.1:11435/v1
TMCRA_LOCAL_WRITER_MODEL=$MODEL_ALIAS
TMCRA_LOCAL_WRITER_API_KEY_FILE=$LANE_KEY
TMCRA_LOCAL_PLANNER_MODEL=$MODEL_ALIAS
TMCRA_LOCAL_REVIEWER_MODEL=$MODEL_ALIAS
TMCRA_WRITER_PROMPT_ADAPTER=qwen36-v5
TMCRA_RECALL_PLANNER_PROMPT_ADAPTER=qwen36-planner-v1
TMCRA_WRITER_REVIEWER_PROMPT_ADAPTER=qwen36-reconciliation-v1
TMCRA_SLOW_GRAPH_PROMPT_ADAPTER=qwen36-slow-graph-v1
EOF

chmod 0600 "$SERVICE_TMP" "$WRITER_TMP"
mv -fT -- "$SERVICE_TMP" "$SERVICE_ENV"
mv -fT -- "$WRITER_TMP" "$WRITER_ENV"
trap - EXIT

CLI_WRAPPER=$(mktemp "$BIN_DIR/.tmcra.XXXXXX")
cat >"$CLI_WRAPPER" <<EOF
#!/usr/bin/env bash
export TMCRA_SERVICE_ENV_FILE=$SERVICE_ENV
exec $PREFIX/deploy/tmcra "\$@"
EOF
chmod 0755 "$CLI_WRAPPER"
mv -fT -- "$CLI_WRAPPER" "$BIN_DIR/tmcra"

echo "TMCRA files and private configuration are installed"
echo "model=$MODEL_PATH"
echo "embedding=$EMBEDDING_MODEL"
echo "cross_encoder=$CROSS_MODEL"

if [[ "$PREPARE_ONLY" -eq 1 ]]; then
  echo "prepare-only completed; run: $BIN_DIR/tmcra start"
  exit 0
fi

TMCRA_SERVICE_ENV_FILE="$SERVICE_ENV" "$PREFIX/deploy/tmcra-memory-api-control.sh" start
TMCRA_SERVICE_ENV_FILE="$SERVICE_ENV" "$PREFIX/deploy/tmcra-memory-api-control.sh" verify-running
echo "TMCRA is ready. Internal API: http://$BIND_HOST:$BIND_PORT"
echo "Status: $BIN_DIR/tmcra status"
