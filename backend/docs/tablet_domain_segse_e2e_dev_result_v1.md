# SEGSE v1.2 Multi-turn End-to-End Development Result

Status: execution invalid under the frozen completion gate; development diagnosis only.

The network-enabled run used the frozen six-scenario, 24-turn development fixture and
the real 117-product/7,552-review tablet catalog. B0+C ran first, followed by SEGSE v1.2.
Both arms used one live GPT-4o-mini Understanding call per attempted turn and a
deterministic response composer.

## Result

| Metric | B0 + C | SEGSE v1.2 |
| --- | ---: | ---: |
| completed turns | 22/24 | 21/24 |
| raw Candidate precision | .586 | .867 |
| raw Candidate recall | .810 | .619 |
| raw Candidate F1 | .680 | .722 |
| raw Candidate FP | 12 | 2 |
| C2U FP | 8 | 0 |
| material-operation precision | .800 | 1.000 |
| material-operation recall | .696 | .522 |
| material-operation F1 | .744 | .686 |
| turnwise accumulated-state precision | .758 | 1.000 |
| turnwise accumulated-state recall | .810 | .500 |
| turnwise accumulated-state F1 | .783 | .667 |
| turnwise accumulated-state FP | 15 | 0 |
| scenario-final precision | .684 | 1.000 |
| scenario-final recall | .765 | .529 |
| scenario-final F1 | .722 | .692 |
| scenario-final FP | 6 | 0 |
| exact hard-filter completion | .750 | .875 |
| top-3 hard-filter violation | 0 | 0 |

SEGSE reduced raw Candidate FP by 83.3% and C2U FP by 100%. No false token entered
either its turnwise accumulated state or scenario-final state. This is the intended
effect of sparse current-turn events plus deterministic authorization.

That precision result is not sufficient for selection. Raw Candidate recall fell by
19.0 pp, material-operation recall fell by 17.4 pp, and accumulated-state recall fell
to .500. The final-state F1 difference was -0.030. The frozen development gate therefore
failed even without the completion problem.

## Execution validity

The protocol requires at least 95% output completion in each arm. B0 completed 22/24
turns and SEGSE completed 21/24, so `execution_valid=false` and the result status is
`development_run_invalid_insufficient_output`.

- B0 failed on `se04` turn 3 because its strict full Understanding object contained a
  duplicated canonical ID after two schema attempts.
- SEGSE failed on `se04` turn 2 after the LLM emitted `min_rating=11 inches`. The event
  passed the then-current ID/scope validator, and the deterministic Query Generator
  correctly rejected an impossible rating above 5. This exposed a missing value
  dimension/range gate at the Understanding boundary.

The earlier sandbox attempt with 0/48 output is separately classified as infrastructure
failure and is not part of these metrics.

## False-positive origin analysis

B0 introduced seven distinct first Final FP tokens:

- four novel extractions: entertainment goal, microSD-as-storage, positive Windows from
  negative polarity, and a fabricated 500 g limit;
- two failed retractions: operating system and battery;
- one failed hard-to-soft display scope correction.

SEGSE introduced no Final FP token. Its two raw Candidate FP events were rejected before
state mutation, which is why raw Candidate precision (.867) and authorized Candidate
precision (1.000) differ.

## Recall diagnosis

The remaining failure is predominantly over-restraint and schema interpretation, not
stale-state copying:

- the treatment omitted the explicit activity/goal facet on five compound first turns;
- `activity_note_taking` was proposed for digital sheet music with an illegal hard scope
  and rejected;
- battery and portability were proposed with hard scope even though the closed ontology
  supports only soft scope, so authorization rejected them;
- the invalid `min_rating=11 inches` event aborted one scenario;
- one of two retracts was recovered, while value/scope correction recall stayed at .500.

Confirmation metadata and reactivation both achieved recall 1.000 on their single
targets. These successes do not offset the broad acquisition recall loss.

## Decision

Do not open a confirmatory holdout for v1.2. The next development version must keep the
FP defenses but address the following bounded issues:

1. reject impossible value dimensions/ranges before state mutation so malformed events
   cannot crash Query generation;
2. make multi-fact extraction and facet mappings explicit in the provider-compatible
   prompt;
3. handle soft-only preference IDs conservatively when the model supplies an illegal
   hard scope, without promoting microSD or other unsupported dimensions;
4. continue to report Candidate/Material/Final FP and correction/retract recall together.

Any v1.3 run on this now-exposed fixture remains development-only. Generalization still
requires a new untouched confirmatory holdout after the method is frozen.
