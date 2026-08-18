#!/usr/bin/env bash
set -euo pipefail

BASE=/opt/tmcra
RUNS="$BASE/runs"
OUT="$(cat "$RUNS/current_v4_current100_path.txt")"
ANSWER_ENV=/opt/tmcra-data/migration/legacy/tmcra_longmemeval/env/answer-vectorengine-gpt54.env
DATA=/opt/tmcra-data/migration/legacy/tmcra_longmemeval/data/longmemeval_s_cleaned.json
LOG="$OUT/retrieval_current_compiled.official_judge.isolated.log"

nohup bash -lc "
  set -euo pipefail
  set -a
  source '$ANSWER_ENV'
  set +a
  cd '$BASE'
  exec /opt/tmcra_env_20260713/bin/python run_tmcra_v4_parallel_official_judge.py \\
    --metric-model gpt-5.4 \\
    --hyp-file '$OUT/answer_retrieval_current_compiled/answers.jsonl' \\
    --ref-file '$DATA' \\
    --result-file '$OUT/retrieval_current_compiled.official_judge.jsonl' \\
    --base-url \"\$TMCRA_ANSWER_BASE_URL\" \\
    --api-key-env TMCRA_ANSWER_API_KEY \\
    --timeout 180 \\
    --process-timeout 240 \\
    --max-retries 0 \\
    --workers 8 \\
    --resume
" >"$LOG" 2>&1 &

PID=$!
printf '%s\n' "$PID" > "$RUNS/current_v4_current100_judge_pid.txt"
printf '%s\n' "$LOG" > "$RUNS/current_v4_current100_judge_log.txt"
printf 'PID=%s\nLOG=%s\n' "$PID" "$LOG"
