# TMCRA LoCoMo Benchmark Result Record

This record publishes the LoCoMo results used by TMCRA's public benchmark
materials. Each metric retains its original protocol and denominator; the
figures must not be collapsed into a single LoCoMo accuracy score.

## Recorded results

| Metric | Result | Coverage and protocol |
|---|---:|---|
| Mem0-style LLM Judge | **80.92%** | Auxiliary five-run mean for Categories 1–4; `N = 1,540`. Category 5 is excluded. |
| Official Token F1 | **55.20** | Deterministic scoring across all 1,986 questions. |
| Evidence recall | **82.00%** | Evidence-retrieval coverage across all 1,986 questions. |

## Reporting boundary

- The 80.92% Judge result is an auxiliary subset metric. It is not a full-set
  official accuracy claim.
- Token F1 and evidence recall cover the full 1,986-question set, but measure
  different properties: answer overlap and evidence coverage, respectively.
- LongMemEval uses a different benchmark, question set, and score protocol.
  Its results are reported separately in [README.md](README.md).

## Public implementation checks

The Web Console's benchmark page presents the same values and labels. Its
contract test verifies that the Judge protocol, both denominators, Token F1,
and evidence recall remain visible in public benchmark copy.
