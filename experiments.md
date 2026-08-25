# Full-Memory State Accuracy: Experiment Program Summary

Scope: everything after `origin/main` on the state-accuracy line of work, through SEGSE v1.4.
Stops at v1.4 by decision; v1.5 is not started.

Canonical detail stays in `flow.md` and in the per-stage documents linked below. This file is the
map and the numbers, not a replacement for them.

---

## 1. The problem this program chased

The official 20-episode / 81-turn holdout produced a result that did not make sense:

```text
Full-Memory     State Diff micro F1  0.562      Final State micro F1  0.758
No-Memory       State Diff micro F1  0.700      Final State micro F1  0.500
```

Adding persistent dialogue memory made the **per-turn State Diff worse**. The diagnosis was that
previous state was leaking into the current turn as if the user had just said it, so every turn
re-asserted facts that had not changed. Two layers were named:

```text
carryover-to-update   previous state copied into the current-turn delta
material no-op        an identical repeat recorded as a state change
```

Everything below is the attempt to remove those layers without breaking genuine corrections.

---

## 2. Branch and stage map

| Stage | Branch | Question | Outcome |
| --- | --- | --- | --- |
| 0 | `main` | frozen v2.3 system, official holdout, poster benchmark | baseline of record |
| 0 | `exp/0-intent-extraction` | intent extraction | landed; no unique commits vs `main` |
| 1 | `exp/1-state-diff-strategies` | which of A–E reduces false state updates | C selected, then confirmed |
| 2 | `exp/2-segse-final-state` | can sparse grounded events fix accumulated state | v1.4 frozen, confirmatory failed |

---

## 3. Stage 1 — A–E strategies, then M0 vs C

Branch `exp/1-state-diff-strategies`.

Six arms were compared on an exploratory set. This selection was **post-hoc and exploratory**, not
evidence.

| Strategy | State Diff F1 | Final State F1 | Turn completion |
| --- | -: | -: | -: |
| `m0_baseline` | 0.601 | 0.763 | 0.852 |
| `a_som_operation` | 0.479 | 0.638 | 0.383 |
| `b_sparse_delta` | 0.455 | 0.584 | 0.383 |
| **`c_semantic_noop`** | **0.739** | **0.796** | **0.877** |
| `d_full_state_diff` | 0.361 | 0.444 | 0.309 |
| `e_compact_context` | 0.713 | 0.785 | 0.926 |

A, B and D collapsed on turn completion as well as accuracy. C was selected: **deterministic
semantic no-op suppression** in the State Manager, which declines to record a merge when the
canonical ID, scope, normalized value and status all already match.

C was then tested on its own untouched 20-episode / 80-turn holdout with a pre-specified estimand.

| Condition | State Diff F1 | Final State F1 | Turn completion |
| --- | -: | -: | -: |
| `m0_baseline` | 0.552 | 0.752 | 0.700 |
| `c_semantic_noop` | 0.688 | 0.752 | 0.700 |
| `c_fixed_upstream_replay` | 0.671 | 0.752 | 0.700 |

Paired mean scenario State Diff F1 difference, C minus M0, fixed-upstream replay:
**+0.100, 95% CI [0.058, 0.146]**, 20 scenarios, 10,000 resamples. Both the fixed-upstream replay
and the independent live replication passed every pre-specified check.

Status: **confirmatory pass.** C is a real, replicated improvement to the per-turn State Diff.

**The detail that set up Stage 2.** Final State F1 is *identical* at 0.752 across M0 and C. C
stopped spurious per-turn writes but did not move the accumulated state at all. So the accumulated
state was still wrong for a different reason, and that is what SEGSE went after.

Documents: `backend/docs/tablet_domain_extended_experiment_protocol_v1.md`,
`backend/docs/tablet_domain_extended_experiment_results_posthoc_v1.md`,
`backend/docs/tablet_domain_m0_c_confirmatory_protocol_v1.md`,
`backend/docs/tablet_domain_m0_c_confirmatory_results_v1.md`.

---

## 4. Stage 2 — SEGSE, v1 through v1.4

Branch `exp/2-segse-final-state`.

SEGSE = Sparse Evidence-Grounded State Events. The contract: persistent state is **read-only
reference memory**, and the only authority for mutation is a sparse list of state events that the
**current utterance** actually triggers. Absence means carryover. C stays underneath as the
lower-layer defense.

### Development iterations on a six-episode / 24-turn fixture

| Version | Result |
| --- | --- |
| v1 | first live arm; infrastructure and completion problems |
| v1.1 | smaller LLM write surface; schema preflight failed on the provider |
| v1.2 | provider-compatible flat schema; completion and acquisition problems |
| v1.3 | FP suppression worked, **correction recall collapsed to 0** |
| v1.4 | corrections recovered on the fixture without giving back FP suppression |

**v1.3 was rejected.** It cut Candidate FP 12 to 3 and C2U FP 8 to 0, but correction recall went
0.500 to 0.000 and turnwise state F1 fell to 0.745 against a 0.783 baseline.

**v1.4 root cause.** All three failures were in deterministic interpretation, not the model:

1. dimension evidence was searched only inside the exact evidence anchor, so a correction whose
   dimension word sat elsewhere in the same utterance was rejected;
2. a hard-to-soft relaxation arrived typed as `retract`, and nothing downstream could tell a
   deletion from a downgrade;
3. one required-field set was applied to every act, so facet events with a null value were
   discarded even when the dimension and evidence were valid.

**v1.4 fix.** The v1.3 prompt and the v1.2 schema were reused byte-for-byte; only deterministic
interpretation changed. Facet repair, two-tier dimension evidence with a competing-dimension
guard, and a narrowly conditioned retract-to-`assert(soft)` re-typing.

Development fixture result, 24 turns:

| Metric | B0+C | v1.3 | v1.4 |
| --- | -: | -: | -: |
| Correction recall | .500 | .000 | **1.000** |
| Candidate FP | 12 | 3 | 3 |
| C2U FP | 8 | 0 | 0 |
| Material operation F1 | .744 | .800 | **.957** |
| Turnwise state F1 | .783 | .745 | **.939** |
| Final State F1 | .722 | .714 | **.941** |
| Hard-filter completion | .750 | .958 | **1.000** |

An offline 2x2 replay attributed six improved turns to the code change and the single new false
positive to provider variance, with zero extra LLM calls.

Decision recorded as **PASS — freeze v1.4 for confirmatory evaluation**, explicitly not a system
adoption decision.

Documents: `backend/docs/tablet_domain_segse_e2e_dev_protocol_v14.md`,
`backend/docs/tablet_domain_segse_e2e_dev_result_v14.md`.

---

## 5. Stage 3 — re-measuring the original defect

The v1.3/v1.4 work was validated on a six-episode fixture, so the number that started the program
had never been re-measured. Frozen v1.4 was run once on the official 20-episode / 81-turn holdout,
scored with the unmodified official scorer and the unmodified benchmark aggregator, same denominator.

### Final State

| Condition | TP | FP | FN | Precision | Recall | Final State F1 |
| --- | -: | -: | -: | -: | -: | -: |
| Full-Memory (official v2.3) | 72 | 15 | 31 | 0.828 | 0.699 | 0.758 |
| No-Memory (official v2.3) | 35 | 2 | 68 | 0.946 | 0.340 | 0.500 |
| **SEGSE v1.4 (Full-Memory)** | **81** | **3** | **22** | **0.964** | **0.786** | **0.866** |

### State Diff

| Condition | TP | FP | FN | Precision | Recall | State Diff F1 |
| --- | -: | -: | -: | -: | -: | -: |
| Full-Memory (official v2.3) | 61 | 57 | 38 | 0.517 | 0.616 | 0.562 |
| No-Memory (official v2.3) | 63 | 18 | 36 | 0.778 | 0.636 | 0.700 |
| **SEGSE v1.4 (Full-Memory)** | **71** | **4** | **28** | **0.947** | **0.717** | **0.816** |

State Diff false positives fell 57 to 4. The original paradox inverted: Full-Memory 0.816 now
exceeds No-Memory 0.700.

**Completion confound.** The official Full arm failed to finish 5 of 20 episodes, so its missing
turns counted as failures. Restricted to the 15 episodes it did finish:

| Metric | official Full | SEGSE v1.4 |
| --- | -: | -: |
| State Diff F1 | 0.620 | 0.809 |
| Final State F1 | 0.803 | 0.853 |

The gain survives, but the Final State part shrinks from +0.108 to +0.051. In that restricted view
recall is slightly *lower* for v1.4 (state diff 0.770 to 0.716, final state 0.797 to 0.772): the
precision gain is partly bought with recall.

**Two limits.** This holdout was already consumed by the official batch, so it is not untouched
evidence; and the official Full arm used the v2.3 Understanding prompt while v1.4 uses the SEGSE
prompt, so the delta mixes prompt and state-manager effects. The method was hash-frozen before the
run, so it is a measurement rather than tuning.

Result: `backend/data/results/official_holdout_segse_v14.json`.

---

## 6. Stage 4 — untouched confirmatory, and it failed

A new 20-episode / 80-turn holdout was authored after both freezes, over the same frozen tablet
catalog (117 products, 7,552 reviews) and the same review corpus. Zero utterance overlap with any
earlier fixture. Gold hard filters, policy lanes and final tokens were re-derived from the
deterministic Query Generator and Policy with zero mismatches before the freeze. Run A and Run B
executed back to back before either result was opened; both completed 80/80 turns.

### Final State

| Condition | TP | FP | FN | Precision | Recall | Final State F1 |
| --- | -: | -: | -: | -: | -: | -: |
| SEGSE v1.4 (Run A, primary) | 40 | 11 | 12 | 0.784 | 0.769 | 0.777 |
| SEGSE v1.4 (Run B, test-retest) | 39 | 12 | 13 | 0.765 | 0.750 | 0.757 |

Exact match 10/20, mean error tokens 1.15, mean Jaccard 0.713.

### Pre-registered decision: FAIL

| Check | Result | Observed |
| --- | --- | --- |
| Paired closeness improves, CI lower bound > 0 | PASS | +0.237, CI [0.137, 0.333] |
| At most 2 of 20 episodes worsen | **FAIL** | 3 |
| At least 8 of 20 episodes improve | PASS | 13 |
| Value+scope correction recall > v1.3 | **FAIL** | 8/14 vs 8/14, identical |
| All guardrails hold | PASS | recall .882, retract .500, hard-filter .838, completion 1.000 |

### What the failure actually means

Replaying the same recorded proposals through both deterministic pipelines, zero extra LLM calls:

| Deterministic contract | Exact match | Mean error tokens | Mean Jaccard |
| --- | -: | -: | -: |
| v1.3 | 0/20 | 1.650 | 0.477 |
| v1.4 | 10/20 | 1.150 | 0.713 |

The accumulated-state improvement **replicates** — 13 improved, 4 unchanged, 3 worsened, paired CI
excludes zero. But value+scope correction recall is **identical** to v1.3 at 8/14, so the gain
comes entirely from the **facet null-value repair**, not from the two correction repairs the
development story was built on.

| Operation | Hit / Target | Recall |
| --- | -: | -: |
| value correction | 5/6 | 0.833 |
| **scope correction** | **3/8** | **0.375** |
| retract | 5/10 | 0.500 |
| reactivation | 2/4 | 0.500 |

Three of four hard-to-soft episodes left the display hard. The relaxation repair can only re-type
an event that exists, and on new surface forms the model emitted nothing usable for the softening
turn. That is a prompt-or-schema limit, not a deterministic-repair limit.

Sparse-grounding precision also weakened off the development fixture: C2U FP 0 to 7, Candidate FP
3 to 17.

### The pre-declared secondary question is answered: yes

Dimension attribution is now the dominant residual failure. All six pre-declared
dimension-confusion negatives were dirty, 0 of 6 clean:

```text
stylus accessory       -> activity_general
expandable storage     -> storage_capacity
connectivity feature   -> performance
display feature        -> display
port/charging feature  -> portability
external accessories   -> note_taking

final-state FP causes:  novel_extraction 8 > failed_retract 3 = incorrect_value_or_scope 3
```

Facet ID selection is also often wrong where a facet is produced.

### Provider nondeterminism does not explain it

Run A versus Run B on the same 80 turns: raw proposal disagreement 6/80 (7.5%), Final State F1
difference −0.019, correction recall difference 0.000. Run A stands as the reported result; the two
runs are never averaged or pooled.

Documents: `backend/docs/segse_confirmatory_v1_result.md`,
`backend/docs/segse_confirmatory_v1_tables.md`.

---

## 7. What is established, and what is not

**Established.**

- C, deterministic semantic no-op suppression, improves per-turn State Diff and is confirmed on
  its own untouched holdout: +0.100 paired, CI [0.058, 0.146].
- On the data where the original defect was measured, State Diff F1 moved 0.562 to 0.816 with
  false positives 57 to 4, and the Full-versus-No-Memory paradox inverted. The gain survives the
  completion confound at 0.620 to 0.809.
- On untouched data, the accumulated final state moves substantially closer to gold under the v1.4
  contract, with a paired CI that excludes zero.
- Turn completion, policy lane accuracy, evidence-span validity, confirmation recall and top-3
  hard-constraint compliance are clean throughout.

**Not established.**

- The correction story. Value correction largely holds at 5/6, but scope correction is 3/8 and the
  paired comparison shows the correction repairs contributing nothing over v1.3. The development
  finding of full correction recovery was a property of two targets in six episodes.
- Any generalization of the development-fixture precision numbers. C2U FP and Candidate FP are
  materially worse on unseen surface forms.
- Human-perceived recommendation quality. No human relevance grades were collected anywhere in
  this program.

**Newly identified.**

- Semantic dimension attribution is the dominant residual failure: a real current-utterance signal
  mapped to the wrong canonical dimension. It is a different layer from carryover and no-op, and it
  is untouched by everything above.

---

## 8. Frozen artifacts

| Tag | Pins |
| --- | --- |
| `tablet-domain-v2.3-freeze` | the v2.3 Understanding method |
| `tablet-domain-holdout-v1-freeze` | the official 20-episode holdout |
| `tablet-domain-official-v1` | the official three-condition result |
| `segse-v1.4-freeze` | SEGSE v1.4 method, combined `960ba072…` |
| `segse-confirmatory-v1-protocol-freeze` | the confirmatory protocol, combined `4c02daf2…` |
| `segse-confirmatory-holdout-v1-freeze` | the untouched 20-episode holdout, combined `5105f628…` |

Every freeze is recomputed by a verifier rather than trusted as text. The v1.2 freeze manifest and
the current v1.2 source deliberately disagree; that mismatch is recorded as
`FAIL_AS_HISTORICAL_MISMATCH` in `backend/docs/segse_v12_historical_freeze_audit.md` and is not
repaired, because a freeze that can be rewritten afterwards is not a freeze.

---

## 9. Stopping here

v1.4 is frozen and is not edited again. The confirmatory holdout is now exposed, so it must not be
used to patch v1.4 or re-run for a better number.

The next intervention, if taken, is a separate version targeting the dimension-attribution layer,
with its own development iteration, its own freeze, and a new untouched holdout. Two items are open:

1. dimension attribution, 0 of 6 clean negatives and the largest source of final-state false
   positives;
2. scope correction's dependency on the model emitting a usable event at all, which no
   deterministic repair can supply.
