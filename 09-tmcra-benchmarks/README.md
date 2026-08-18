# TMCRA Benchmarks — reproduction and score provenance

This component reproduces the TMCRA LongMemEval-S evaluation. It contains the
orchestration scripts (`run_tmcra_v4_*.py`, `prepare_tmcra_v4_e2e_data.py`,
`score_tmcra_v4_quality_gate.py`), the test suite, and pinned copies of the
shared algorithm modules under `algorithm/`.

## Scorecard provenance rules

- The frozen official scorecard is **411/500 (82.2%)**, produced in one
  end-to-end 500-question evaluation run. Describe it as one frozen
  LongMemEval-S500 run; do not merge it with newer, separate measurements.
- Every merged input and the official judge output are published with SHA-256
  hashes. See `RELEASE_MANIFEST.json` for the pinned file set.
- Newer mainline measurements (cleanroom, no-GNN) are reported separately and
  must not be mixed with the frozen official 500-question score.

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

## Path parameterization

Several orchestration scripts default to the original build host's absolute
paths (for example `BASE = Path("/opt/tmcra/...")`). These are **defaults
only**: every script reads the same values from environment variables or
command-line flags where available. Set the corresponding variables (for
example `TMCRA_DATA_DIR`, `TMCRA_REPO_DIR`, model paths) for your checkout.

Do **not** edit the pinned files under `algorithm/` in place: their SHA-256
hashes are recorded in `shared_core_manifest.json` (component 02) and the
service fails closed on mismatch. If you must change a pinned module,
regenerate the manifest and re-run the service verification suite.

## Running

```bash
pip install -r requirements-lock.txt   # see build instructions in component 02
python run_tmcra_v4_build.py --help
python run_tmcra_v4_evaluate.py --help
```
