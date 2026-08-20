# TMCRA Benchmarks — reproducible evaluation and score provenance

[中文说明](README.zh-CN.md)

This component publishes the evaluation-side implementation used by TMCRA V4:

- end-to-end LongMemEval build, retrieval, evidence compilation, answer, judge,
  audit, and staged quality-gate commands;
- semantic-evidence planning and the zero-API regression gate;
- repair, migration, recovery, cost, and slow-graph coverage tools;
- pinned copies of the shared production algorithm under `algorithm/`; and
- a release suite with 581 locally verified tests.

## Models used by the recorded benchmark

The recorded benchmark memory chain used the **DeepSeek-V4 Preview API snapshot
released on 2026-04-24**, before the 2026-08-13 DeepSeek update. The exact two
generation/planning models were:

| Frozen model | API model ID | Benchmark role |
|---|---|---|
| DeepSeek-V4-Flash Preview (284B total / 13B active) | `deepseek-v4-flash` | Primary Writer, recall-role planning, primary slow-graph work, and other high-volume generation stages. |
| DeepSeek-V4-Pro Preview (1.6T total / 49B active) | `deepseek-v4-pro` | Writer review/reconciliation, subject attribution, evidence and semantic planning, and higher-assurance review stages. |

Dense retrieval used local BGE-M3 and BGE reranker V2 M3. The current product
default, locally downloaded `Qwen3.6-35B-A3B`, is a later self-hosted production
profile and must not be retroactively attached to the recorded benchmark
scores or to the later 2026-08-13 DeepSeek revision. The Semantic100 comparison
additionally used GPT-5.4 for the answer and
official judge layers in both compared runs; those evaluation roles are separate
from the DeepSeek memory chain. See
[BENCHMARK_MODEL_PROFILE.md](BENCHMARK_MODEL_PROFILE.md) for the full boundary.

Model aliases and endpoints remain configurable for new reproductions. Other
local or OpenAI-compatible models are accepted without an exact-name allowlist,
but a changed model requires a new score record and may need prompt,
context-window, or threshold tuning.

## Scorecard provenance rules

- The frozen official scorecard is **411/500 (82.2%)**, produced in one
  end-to-end 500-question evaluation run. Describe it as one frozen
  LongMemEval-S500 run; do not merge it with newer, separate measurements.
- Newer mainline measurements (cleanroom, no-GNN) are reported separately and
  must not be mixed with the frozen official 500-question score.
- `TMCRA_V4_SEMANTIC100_BENCHMARK_REPORT.md` records the published 100-question
  comparison and its exact regression-case IDs.

The upstream benchmark dataset and provider-generated judge responses are not
vendored here. Obtain the dataset under its upstream terms, configure its local
path, and preserve the generated plan/report hashes with your run artifacts.
`RELEASE_MANIFEST.json` pins this component's source snapshot; it does not claim
to contain third-party datasets or model weights.

## LoCoMo recorded scorecard

LoCoMo uses separate score protocols and denominators. Do not combine these
numbers into a single accuracy figure or compare them directly with LongMemEval.

| Metric | Recorded result | Evaluation boundary |
|---|---:|---|
| Mem0-style LLM Judge | **80.92%** | Auxiliary five-run mean, Categories 1–4 only, `N = 1,540`; it excludes Category 5 and is not full-set official accuracy. |
| Official Token F1 | **55.20** | Deterministic scorer over all 1,986 questions. |
| Evidence recall | **82.00%** | Evidence-retrieval coverage over all 1,986 questions. |

See [LOCOMO_BENCHMARK_REPORT.md](LOCOMO_BENCHMARK_REPORT.md) for the public
result record and reporting rules.

## Requirements and paths

Python 3.11 is the verified interpreter. Install the same runtime dependencies
as the memory API from the monorepo root:

```bash
python -m pip install -r 02-tmcra-memory-api/requirements-tmcra-service.txt
```

Several orchestration scripts default to the original build host's absolute
paths (for example `BASE = Path("/opt/tmcra/...")`). These are **defaults
only**: every script reads the same values from environment variables or
command-line flags where available. Set the corresponding variables (for
example `TMCRA_DATA_DIR`, `TMCRA_REPO_DIR`, model paths) for your checkout.

Do **not** edit the pinned files under `algorithm/` in place: their SHA-256
hashes are recorded in `shared_core_manifest.json` (component 02) and the
service fails closed on mismatch. If you must change a pinned module,
regenerate the service manifest and re-run both component suites.

## Reproduction flow

```bash
# From 09-tmcra-benchmarks
export PYTHONPATH="$PWD/algorithm:$PWD/../02-tmcra-memory-api"

# Inspect the complete build and evaluation contracts.
python run_tmcra_v4_build.py --help
python run_tmcra_v4_retrieve.py --help
python run_tmcra_v4_compile_semantic_evidence.py --help
python run_tmcra_v4_gpt54_answers.py --help
python run_tmcra_v4_evaluate.py --help

# Freeze cumulative 20/50/500 quality gates, then score completed run shards.
python plan_tmcra_v4_quality_gates.py --help
python score_tmcra_v4_quality_gate.py --help

# Compare a candidate judge file with the frozen regression cases.
python tmcra_v4_regression_gate.py --help
```

Every command exposes its dataset, run-directory, model, endpoint, and output
arguments through `--help`. Generated run directories carry completion markers,
chain audits, ordered question IDs, source traces, evidence windows, cost
reports, and evaluation reports so a score can be traced back to its source
sessions and immutable evidence records.

## Release verification

Linux/macOS:

```bash
export PYTHONPATH="$PWD/algorithm:$PWD/../02-tmcra-memory-api"
python -m unittest discover -s . -p 'test*.py'
python generate_release_manifest.py --check
```

Windows PowerShell:

```powershell
$env:PYTHONPATH="$(Resolve-Path algorithm);$(Resolve-Path ../02-tmcra-memory-api)"
python -m unittest discover -s . -p 'test*.py'
python generate_release_manifest.py --check
```

Pre-extraction V3 and older service contract snapshots are preserved under
`legacy_contract_tests/` for design provenance. They are intentionally named
outside test discovery; component 02 owns the current memory-service release
checks.
