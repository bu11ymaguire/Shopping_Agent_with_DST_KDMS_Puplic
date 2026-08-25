# SEGSE confirmatory holdout tables

Untouched 20-episode / 80-turn holdout over the same frozen tablet catalog (117 products, 7,552 reviews) and the same review corpus as the official holdout. Method and protocol were hash-frozen before the run.

## Final State, Analysis A (frozen v1.4, end to end)

| Condition | TP | FP | FN | Precision | Recall | Final State F1 |
| --------- | -: | -: | -: | --------: | -----: | -------------: |
| SEGSE v1.4 (Run A, primary) | 40 | 11 | 12 | 0.784 | 0.769 | 0.777 |
| SEGSE v1.4 (Run B, test-retest) | 39 | 12 | 13 | 0.765 | 0.750 | 0.757 |

Run B is a provider-stability measurement. It never replaces, averages with, or redefines Run A.

## Final State, Analysis B (identical raw proposals)

| Deterministic contract | Exact match | Mean error tokens | Mean Jaccard |
| --- | -: | -: | -: |
| v1.3 | 0/20 | 1.650 | 0.477 |
| v1.4 | 10/20 | 1.150 | 0.713 |

Paired over 20 episodes: 13 improved, 4 unchanged, 3 worsened. Mean Jaccard improvement +0.237, 95% paired bootstrap CI [0.137, 0.333].

## Correction recall by operation, Analysis A

| Operation | Hit / Target | Recall |
| --- | -: | -: |
| value_correction | 5/6 | 0.833 |
| scope_correction | 3/8 | 0.375 |
| refinement | 0/0 | n/a |
| retract | 5/10 | 0.500 |
| reactivation | 2/4 | 0.500 |
| **value + scope combined** | **8/14** | **0.571** |

Under identical raw proposals the same metric is v1.3 8/14 and v1.4 8/14.

## Supporting metrics, Analysis A

| Metric | Value |
| --- | -: |
| Turn output completion | 1.000 |
| Candidate precision | 0.798 |
| Candidate recall | 0.882 |
| Candidate FP | 17 |
| C2U FP | 7 |
| Novel candidate FP | 10 |
| Forbidden predictions | 9 |
| Material State Diff F1 | 0.817 |
| Material operation F1 | 0.780 |
| Turnwise accumulated state F1 | 0.822 |
| Evidence-span validity | 0.990 |
| Policy lane accuracy | 1.000 |
| Hard-filter completion | 0.838 |
| Confirmation metadata recall | 1.000 |
| Top-3 hard violation rate | 0.028 |

## Dimension-attribution negatives, Analysis A

| Turn | Confusion family | Forbidden IDs predicted | Candidate FP | Clean |
| --- | --- | --- | --- | --- |
| sc04t4 | stylus accessory | - | ['activity_general'] | no |
| sc08t4 | expandable storage | ['storage_capacity'] | ['storage_capacity'] | no |
| sc12t2 | connectivity feature | ['performance'] | ['performance'] | no |
| sc16t2 | display feature | ['display'] | ['display'] | no |
| sc18t2 | port or charging feature | - | ['portability'] | no |
| sc19t4 | external accessories | ['note_taking'] | ['note_taking'] | no |

## Analysis C, test-retest

| Disagreement | Value |
| --- | -: |
| compared turns | 80 |
| raw structured proposal exact match disagreement | 6 |
| candidate set disagreement | 3 |
| material operation disagreement | 4 |
| candidate fp difference | -2 |
| c2u fp difference | -2 |
| correction recall difference | +0.000 |
| material state diff f1 difference | -0.012 |
| final state f1 difference | -0.019 |
| turn completion difference | +0.000 |

## Pre-registered decision

Status: **confirmatory_fail**

| Check | Result |
| --- | --- |
| paired final state closeness improves | PASS |
| regression is bounded | FAIL |
| improvement is broad | PASS |
| correction recall recovered | FAIL |
| guardrails hold | PASS |
