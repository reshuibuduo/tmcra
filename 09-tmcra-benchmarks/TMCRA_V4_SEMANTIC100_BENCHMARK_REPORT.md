# TMCRA V4 Semantic Evidence 100-Question Benchmark Report

Date: 2026-07-13

## Evaluation Contract

- Frozen question set: 100 LongMemEval questions from `v4_writer100_frozen_20260713_011551/qids.txt`.
- Candidate reservoir: the same frozen Source24 evidence used by the 77/100 baseline.
- Memory-chain generation/planning profile: the 2026-04-24 DeepSeek-V4 Preview
  API snapshot, before the 2026-08-13 update; `deepseek-v4-flash` for the
  primary Writer/high-volume path and `deepseek-v4-pro` for review,
  attribution, and evidence/semantic planning.
- Answer model: GPT-5.4 for both runs.
- Judge: official GPT-5.4 judge for both runs.
- New variable: semantic Task Contract, Source Claim Ledger, Premise Bindings,
  Resolution Program, Answerability Certificate, and semantic-bound answer protocol.
- New answer completion: 100/100, with zero final answer failures.

## Result

The semantic evidence version must not replace the baseline.

| Metric | Baseline | Semantic V4 | Delta |
|---|---:|---:|---:|
| Overall | 77/100 | 61/100 | -16 |
| Knowledge update | 15/16 | 7/16 | -8 |
| Multi-session | 15/20 | 11/20 | -4 |
| Single-session assistant | 14/14 | 14/14 | 0 |
| Single-session preference | 5/14 | 8/14 | +3 |
| Single-session user | 14/16 | 11/16 | -3 |
| Temporal reasoning | 14/20 | 10/20 | -4 |

Pairwise movement:

- Kept correct: 56
- Improved from wrong to correct: 5
- Regressed from correct to wrong: 21
- Kept wrong: 18

The net change is `5 - 21 = -16` points.

## Retrieval Boundary

All 21 regressed questions still had every gold answer session in Source24.
All 12 false-abstention regressions also had every gold answer session in Source24.
The measured regression is therefore downstream of first-stage retrieval. It is
caused by semantic resolution, operation selection, certificate construction, or
answer binding.

## Improvements

| Question ID | Type | Baseline | Semantic V4 |
|---|---|---|---|
| `830ce83f` | knowledge-update | Chicago | the suburbs |
| `0edc2aef` | preference | rejected the recommendation | usable Miami recommendation |
| `afdc33df` | preference | generic cleaning list | memory-conditioned cleaning plan |
| `gpt4_ab202e7f` | multi-session | 4 | 5 |
| `6b7dfb22` | preference | generic inspiration | memory-conditioned inspiration |

## Regressions

### False Abstentions

Twelve baseline-correct questions were rejected even though their gold sessions
were present in Source24:

`dd2973ad`, `9a707b81`, `184da446`, `94f70d80`, `45dc21b6`,
`72e3ee87`, `ef66a6e5`, `f685340e`, `cf22b7bf`, `099778bb`,
`a3045048`, `gpt4_2f56ae70`.

The failure is not simply missing claim extraction. In several cases the Claim
Ledger contains the answer but the certificate remains partial because the
resolver did not establish the required relation:

- `dd2973ad`: claims contain the doctor's appointment and `2 AM last Wednesday`,
  but the relation between the two dates was left partial.
- `9a707b81`: claims contain the class date and the birthday-cake event, but no
  valid date-difference resolution was produced.
- `184da446`: claims contain page 200 and the later page 220, but latest-state
  selection was not produced.
- `gpt4_2f56ae70`: service usage claims were found, but relative start-time
  evidence was collapsed into session dates, so latest-service selection was
  rejected.

### Wrong Semantic Programs or Claims

Nine baseline-correct questions passed the certificate but produced a wrong or
overly conservative answer:

| Question ID | Failure |
|---|---|
| `21436231` | Applied `count_distinct` to one claim whose object was already `12 largemouth bass`, producing 1 instead of 12. |
| `ed4ddc30` | Saw 30-dozen and later 20-dozen snapshots but failed latest-state selection. |
| `4b24c848` | Counted cumulative snapshots `three tops` and `five tops` as two objects instead of selecting the latest total 5. |
| `gpt4_483dd43c` | Replaced relative start times with equal session dates and concluded both shows started the same day. |
| `a08a253f` | The ledger represented only three class days and omitted the fourth day. |
| `6aeb4375_abs` | Ignored the Italian-cuisine constraint and counted four Korean restaurants. |
| `a82c026e` | Truncated `Dark Souls 3 DLC` to `Dark Souls 3`. |
| `87f22b4a` | Added price and quantity (`3 + 40 = 43`) instead of multiplying (`3 * 40 = 120`). |
| `f685340e_abs` | Treated tennis evidence as table-tennis evidence and answered a negative-control question. |

## Answer Output Tolerance

The first GPT-5.4 answer pass saved 93/100 answers and rejected seven outputs for
mechanically recoverable metadata/reference defects. A conservative normalizer
was added that can:

- remove non-memory premise IDs from memory claims;
- remove unknown or out-of-premise claim/operation IDs;
- downgrade `memory_derived` to `memory_fact` only when a valid direct Source
  claim remains;
- map `answered` to `answered_with_context` only when the certificate requires
  it and the answer already contains valid memory support.

It never changes answer text, invents support, upgrades a refusal into an answer,
or fills an unresolved premise. Replaying the persisted raw answers recovered all
seven failures with zero new answer API calls. This fixed runtime tolerance but
did not fix semantic accuracy.

## Promotion Decision

Keep Source24 as the first-stage reservoir. Keep the 77/100 baseline answer path
as the production candidate. The semantic V4 path is a shadow experiment until
its certificate and operation semantics are redesigned. Another paid 100-question
run is not justified by prompt changes alone.
