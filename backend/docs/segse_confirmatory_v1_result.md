# SEGSE v1.4 Confirmatory Result

Pre-registered decision: **CONFIRMATORY FAIL.**

Three of five checks pass. The two that fail are the ones that matter for the original claim.
This is the first untouched measurement of frozen v1.4, and it does not reproduce what the
development fixture suggested about corrections.

## 1. What was run

Twenty four-turn episodes, 80 turns, authored after both freezes over the same frozen tablet
catalog (117 products, 7,552 reviews) and the same review corpus the official holdout used. No
utterance is shared with any earlier fixture or holdout, and gold self-consistency was re-derived
from the deterministic Query Generator and Policy before the freeze.

| Artifact | Freeze |
| --- | --- |
| Method | `segse-v1.4-freeze`, combined `960ba072…` |
| Protocol | `segse-confirmatory-v1-protocol-freeze`, combined `4c02daf2…` |
| Holdout | `segse-confirmatory-holdout-v1-freeze`, combined `5105f628…` |

Both runs executed back to back before either result was opened. Run A is the primary
evaluation; Run B is the pre-registered test-retest and never replaces or averages with Run A.
All three freezes were re-verified after the run.

## 2. Final State

| Condition | TP | FP | FN | Precision | Recall | Final State F1 |
| --------- | -: | -: | -: | --------: | -----: | -------------: |
| SEGSE v1.4 (Run A, primary) | 40 | 11 | 12 | 0.784 | 0.769 | 0.777 |
| SEGSE v1.4 (Run B, test-retest) | 39 | 12 | 13 | 0.765 | 0.750 | 0.757 |

Graded closeness, Run A: exact match 10/20, mean error tokens 1.15, mean Jaccard 0.713.

## 3. Analysis B: the deterministic contract change under identical proposals

The same recorded raw proposals replayed through both deterministic pipelines, zero extra LLM
calls, so the model is held identical by construction.

| Deterministic contract | Exact match | Mean error tokens | Mean Jaccard |
| --- | -: | -: | -: |
| v1.3 | 0/20 | 1.650 | 0.477 |
| v1.4 | 10/20 | 1.150 | 0.713 |

Paired over 20 episodes: **13 improved, 4 unchanged, 3 worsened.** Mean Jaccard improvement
**+0.237**, 95% paired bootstrap CI **[0.137, 0.333]**. Mean error-token reduction +0.50.

So the accumulated-state gain replicates and is not a fluke of one episode.

**But the gain does not come from the correction repairs.** Under identical proposals, combined
value-and-scope correction recall is:

```text
v1.3   8/14   (0.571)
v1.4   8/14   (0.571)   <- identical
```

Every point of the Final State improvement is attributable to the **facet null-value repair**,
which is why v1.3 scored 0/20 exact (each episode was missing at least one facet token) while
v1.4 scored 10/20. The two correction repairs that the development result was built on
contributed nothing measurable here.

## 4. Correction recall by operation, Run A

| Operation | Hit / Target | Recall |
| --- | -: | -: |
| value correction | 5/6 | 0.833 |
| scope correction | 3/8 | 0.375 |
| retract | 5/10 | 0.500 |
| reactivation | 2/4 | 0.500 |
| refinement | 0/0 | n/a |
| **value + scope combined** | **8/14** | **0.571** |

Value correction largely holds. **Scope correction is the failure: 3 of 8.** Three of the four
hard-to-soft episodes ended with the display still hard, visible directly in the final state:

```text
sc13  missing display|soft   extra display|hard|12
sc16  missing display|soft   extra display|hard|10
sc19  missing display|soft   extra display|hard|13
```

The v1.4 relaxation repair can only re-type an event that exists. On these new surface forms the
model emitted no usable event for the softening turn, so there was nothing to re-type. The repair
is conditional on an upstream behaviour that did not generalize.

## 5. Supporting metrics, Run A

| Metric | Value |
| --- | -: |
| Turn output completion | 1.000 |
| Candidate precision / recall | 0.798 / 0.882 |
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
| Confirmation metadata recall | 4/4 = 1.000 |
| Top-3 hard violation rate | 0.000 |

C2U FP is 7 here against 0 on the development fixture, and Candidate FP is 17 against 3. The
sparse-grounding precision that v1.3 and v1.4 were built to protect is materially weaker on
unseen surface forms than the development numbers implied.

## 6. Dimension attribution: the secondary question is answered, and the answer is bad

All six pre-declared dimension-confusion negatives were dirty. Not one was clean.

| Turn | Confusion family | Wrong ID produced |
| --- | --- | --- |
| sc04t4 | stylus accessory | `activity_general` |
| sc08t4 | expandable storage | `storage_capacity` |
| sc12t2 | connectivity feature | `performance` |
| sc16t2 | display feature | `display` |
| sc18t2 | port or charging feature | `portability` |
| sc19t4 | external accessories | `note_taking` |

Final-state false-positive causes confirm it:

```text
novel_extraction           8
failed_retract             3
incorrect_value_or_scope   3
```

`novel_extraction` dominates. The pre-declared secondary question, "is dimension attribution now
the dominant residual error", is answered yes on untouched data. Facet ID selection is also often
wrong where a facet is produced: `goal_entertainment` came back as `activity_video` (sc07, sc12),
and `goal_replace_device` came back as `event_replacement` (sc20).

## 7. Analysis C: provider nondeterminism does not explain the failure

| Disagreement between Run A and Run B | Value |
| --- | -: |
| Compared turns | 80 |
| Raw structured proposal exact-match disagreement | 6 |
| Candidate set disagreement | 3 |
| Material operation disagreement | 4 |
| Candidate FP difference | −2 |
| C2U FP difference | −2 |
| Correction recall difference | +0.000 |
| Material State Diff F1 difference | −0.012 |
| Final State F1 difference | −0.019 |
| Turn completion difference | +0.000 |

Raw proposals differ on 6 of 80 turns, about 7.5%, close to the 5 of 24 seen in development. The
resulting metric movement is small and does not account for the two failed checks. Run A remains
the reported result; the spread is a stability observation only and the two runs are never pooled.

## 8. Pre-registered decision

| Check | Result | Observed |
| --- | --- | --- |
| Paired final-state closeness improves, CI lower bound > 0 | PASS | +0.237, CI [0.137, 0.333] |
| At most 2 of 20 episodes worsen | **FAIL** | 3 worsened (sc07, sc16, sc20) |
| At least 8 of 20 episodes improve | PASS | 13 improved |
| Combined value+scope correction recall > v1.3 | **FAIL** | 8/14 vs 8/14, identical |
| All guardrails hold | PASS | recall 0.882, retract 0.500, hard-filter 0.838, completion 1.000 |

Status: **`confirmatory_fail`.** The threshold was fixed before the data existed and is not moved
now.

## 9. What this actually establishes

Separating the claim from the mechanism matters more than the pass/fail label.

**Replicates.** Accumulated final state gets substantially closer to gold under the v1.4
contract, on untouched data, with a paired CI that excludes zero. Exact match 0/20 to 10/20 and
mean Jaccard 0.477 to 0.713 are large and consistent. Turn completion, policy lane accuracy,
evidence validity, confirmation recall, and top-3 hard-constraint compliance are all clean.

**Does not replicate.** The correction story. Value correction survives at 5/6, but scope
correction collapses to 3/8 and the paired comparison shows the correction repairs contributing
nothing over v1.3. The development finding of full correction recovery was a property of two
targets in six episodes, not of the method.

**Newly established.** Dimension attribution is the dominant residual failure on unseen surface
forms, at 0 of 6 clean negatives and 8 of 14 final-state false positives from novel extraction.
Sparse-grounding precision is weaker off the development fixture: C2U FP 0 to 7, Candidate FP
3 to 17.

## 10. What must not happen next

Do not patch v1.4 against this holdout. It is now exposed, and tuning against it would destroy
the only untouched measurement this method has. The frozen artifacts, the raw runs, and this
result stay as recorded.

The next intervention is v1.5 on the dimension-attribution layer, with its own development
iteration, its own freeze, and its own new untouched holdout. The scope-correction dependency on
upstream event emission is the second item, and it is a prompt-or-schema question rather than a
deterministic-repair question, because a repair cannot re-type an event the model never emitted.
