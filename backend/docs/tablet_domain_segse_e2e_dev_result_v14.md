# SEGSE v1.4 Exposed-Fixture Development Result

Development decision: **PASS — freeze v1.4 for confirmatory evaluation.**

This is a development-gate outcome on an exposed fixture. It is not a system adoption
decision and it carries no generalization claim.

## 1. What v1.3 failed

v1.3 kept everything it had won on sparse grounding and lost the corrections. It completed
24/24 turns with Candidate FP 3, C2U FP 0, and Candidate recall .905, but correction recall
fell to .000 and turnwise accumulated-state F1 fell to .745 against B0 .783. Three concrete
losses appeared in its trace:

- `se02t4` "Actually make the internal-storage minimum 256 GB." The 128-to-256 correction
  was rejected, so the stale 128 GB floor stayed active and produced the only Final FP.
- `se04t3` "An 11-inch display would be nice, but screen size is no longer a strict
  requirement." The relaxation was applied as a full RETRACT, so the hard filter was deleted
  and the intended soft preference vanished.
- Five explicit facet proposals carried `value_after=null` and were rejected outright, which
  cost accumulated-state recall on every later turn of those scenarios.

## 2. Root cause

All three losses sat in deterministic interpretation, not in the model output.

**Value correction.** `_hard_value_reason` in v1.3 assembled `combined = value_after + " " +
trigger_evidence_text` and required the canonical dimension word inside that anchor. For
`se02t4` the model anchored on `"minimum 256 GB."`, so `combined` was `"256 minimum 256 gb."`
The unit matched but the dimension word did not, even though `internal-storage` was present a
few words earlier in the same current utterance. The gate returned
`storage_requires_internal_capacity_dimension` and the correction was dropped. The rule was
not wrong about needing dimension evidence; it was wrong about where that evidence may sit.

**Scope correction.** There was no deterministic path from `retract` to
`assert(scope_after="soft")`. The v1.3 prompt said softening is an assert, but the model typed
the obligation-negating clause as a retraction. `authorize_segse_events` accepted it as a
legal retraction, so `update_segse_dialogue_state` tombstoned the fact. Nothing downstream
could tell a deletion from a downgrade, so the same canonical ID could never survive a
hard-to-soft transition.

**Null facet values.** `authorize_segse_events` applies one required-field set to every
`assert`, so `assert_requires_value_after` fired on facet events that already named the right
canonical dimension and carried valid current evidence. A facet ID legitimately has null
scope, and its value is descriptive rather than comparative, so a single required-field set
for `ADD`/`UPDATE_VALUE`/`UPDATE_SCOPE`/`CONFIRM`/`RETRACT` and facets was the defect.

## 3. What changed

New module `app/segse_experiment_v14.py`. The v1.3 system prompt and the v1.2
provider-compatible schema are reused byte-identically; a unit check asserts the prompt
equality. Only deterministic interpretation between the raw proposal and the State Manager
moved.

1. **Facet repair** (`repair_facet_events`). A facet event with a missing value is completed
   from its own evidence anchor when that anchor names the facet's canonical dimension. Facet
   scope and relation are always normalized to null. A facet whose dimension is not touched
   stays incomplete and is still rejected by the unchanged authorization contract.
2. **Two-tier hard dimension evidence** (`dimension_evidence_scope`, `hard_value_decision`).
   The exact anchor is still checked first with the v1.3 pattern. When the anchor omits the
   dimension word, a narrower unambiguous pattern may match anywhere in the current
   utterance. The fallback is refused when a competing canonical dimension shares the
   utterance, so `8 GB of RAM and 256 GB of internal storage` cannot assign 8 to storage.
   Numbers, units, and the 0-to-5 rating range stay anchored. Every acceptance records
   whether the dimension word came from the anchor or from the utterance.
3. **Hard-to-soft scope correction** (`repair_scope_relaxations`). A RETRACT against an
   active hard fact is re-typed as `assert(scope_after="soft", relation="prefer")` only when
   the utterance negates the obligation, a separate clause states a positive preference
   naming the same canonical dimension, and the closed vocabulary admits a soft scope for
   that ID. The new value and anchor come from that positive clause, so the event stays
   current-utterance grounded.

The dimension vocabulary is versioned closed-vocabulary metadata covering all 17 facet IDs
and the 7 hard-capable preference IDs. It answers only "does the current utterance talk about
this dimension"; it never decides a value, a scope, or an operation. No fixture phrase,
product ID, or scenario number appears in the code.

## 4. What deliberately did not change

- The v1.3 instruction text and the v1.2 schema are unchanged, so this iteration is a pure
  deterministic-interpretation experiment.
- Previous state remains read-only reference memory. No event may cite it as evidence.
- Every event still requires an exact current-utterance anchor. Evidence-span validity stayed
  1.000.
- The C semantic no-op suppression in `update_segse_dialogue_state` is untouched, and a unit
  test still asserts that a semantically identical repeated hard value is suppressed.
- Negative polarity, microSD expansion under `storage_capacity`, unrepresentable negative OS,
  trade-off compromised sides, and illegal preference scopes are rejected exactly as before.
- No blanket dimension gate was added to soft preference asserts. Section 8 explains why.

**Vocabulary limit, not a repair.** Hard-only IDs keep retraction under a relaxation. The
closed vocabulary has no soft counterpart for `budget`, `storage_capacity`,
`memory_capacity`, `max_weight`, `min_rating`, or `operating_system`, so
`256 GB would be nice, but it doesn't have to be a requirement` still ends as a retraction of
the hard floor. Only `display` admits both scopes on the same canonical ID. A `budget` to
`budget_flexibility` mapping would be a different canonical ID and therefore a different
research question, not a same-ID scope correction.

## 5. Metrics

Same fixture, same 24 turns, same frozen B0+C reference. B0 is reused with zero new calls;
v1.3 is the recorded live run reused with zero new calls.

| Metric                        |  B0+C |  v1.3 |  v1.4 |
| ----------------------------- | ----: | ----: | ----: |
| Expected turns                |    24 |    24 |    24 |
| Completed turns               |    22 |    24 |    24 |
| Turn output completion        |  .917 | 1.000 | 1.000 |
| Route accuracy                |  .917 | 1.000 | 1.000 |
| Intent exact                  |  .750 |  .917 |  .917 |
| Candidate TP / FP / FN        | 17/12/4 | 19/3/2 | 19/3/2 |
| Candidate precision           |  .586 |  .864 |  .864 |
| Candidate recall              |  .810 |  .905 |  .905 |
| Candidate F1                  |  .680 |  .884 |  .884 |
| C2U FP                        |     8 |     0 |     0 |
| Novel candidate FP            |     4 |     3 |     3 |
| Forbidden predictions         |     7 |     2 |     1 |
| False assertion on no-event   |     3 |     2 |     2 |
| Evidence-span validity        |  .759 | 1.000 | 1.000 |
| Material delta TP / FP / FN   | 16/4/7 | 17/0/6 | 22/1/1 |
| Material delta F1             |  .744 |  .850 |  .957 |
| Material operation TP / FP / FN | 16/4/7 | 16/1/7 | 22/1/1 |
| Material operation P / R / F1  | .800/.696/.744 | .941/.696/.800 | .957/.957/.957 |
| Turnwise state TP / FP / FN   | 47/15/11 | 35/1/23 | 54/3/4 |
| Turnwise state P / R / F1     | .758/.810/.783 | .972/.603/.745 | .947/.931/.939 |
| Final state TP / FP / FN      | 13/6/4 | 10/1/7 | 16/1/1 |
| Final state P / R / F1        | .684/.765/.722 | .909/.588/.714 | .941/.941/.941 |
| Correction recall (2 targets) |  .500 |  .000 | 1.000 |
| Retract recall (2 targets)    |  .000 | 1.000 | 1.000 |
| Reactivation recall (1)       |  .000 | 1.000 | 1.000 |
| Confirmation recall (1)       |   n/a | 1.000 | 1.000 |
| Event rejections              |     0 |     9 |     3 |
| Policy lane accuracy          |  .917 |  .958 |  .958 |
| Question target accuracy      |  .917 |  .958 |  .958 |
| Recommendation completion     |  .917 |  .958 |  .958 |
| Hard-filter completion        |  .750 |  .958 | 1.000 |
| Top-3 hard violation rate     |  .000 |  .044 |  .000 |
| Final FP origin causes        | failed_retract 2, incorrect_value_or_scope 1, novel_extraction 4 | incorrect_value_or_scope 1 | novel_extraction 1 |

All fifteen predeclared gate checks pass, so the recorded status is
`development_gate_passed_confirmatory_method_freeze_allowed`.

**Read the state numbers as persistence-amplified, not as independent discoveries.**
Turnwise accumulated state is scored on every turn's full snapshot, so one corrected event
keeps paying off on every later snapshot of that scenario. The 54 turnwise TP are not 54
independent findings: fixing the `se02t4` storage value removes the same FP and FN from that
scenario's remaining snapshots, and repairing a turn-1 facet adds the same TP to all four
snapshots of four scenarios. The 19 recovered turnwise TP and the 19 removed turnwise FN trace
back to 6 event-level corrections. Final-state F1 moving .714 to .941 is the same amplification
measured once per scenario. The event-level layers, material delta and material operation
(F1 .850 to .957), are the honest per-decision view and should be quoted alongside any
accumulated-state claim.

Dimension-evidence validity in the live run: 12 accepted hard values, all with `anchor`
dimension evidence, 0 utterance-level fallbacks. The fallback did not fire live because the
provider happened to include `internal-storage` inside the anchor for `se02t4` this time. Its
effect is demonstrated by the offline attribution replay in section 7 and by the unit test,
not by the live run.

Deterministic audit counts in the live run: 4 facet values derived from current dimension
evidence, 2 facet proposals correctly left incomplete, 1 illegal facet scope cleared, 1
hard-to-soft relaxation re-typed. Rejections fell from 9 to 3 while precision held.

Provenance is only partially measurable here. The frozen evaluator scores confirmation
metadata recall, which is 1.000 on its single target, but it computes no separate
explicit/implicit/inferred provenance accuracy, so that metric is
`not_evaluable_with_current_evaluator` rather than reported as a number.

## 6. Correction cases

| Case | Utterance | Expected | B0+C | v1.3 | v1.4 |
| ---- | --------- | -------- | ---- | ---- | ---- |
| `se02t4` value correction | "Actually make the internal-storage minimum 256 GB." | `storage_capacity` update_value 128 to 256 | hit | miss (dimension gate) | hit |
| `se04t3` scope correction | "An 11-inch display would be nice, but screen size is no longer a strict requirement." | `display` update_scope hard to soft | miss | miss (typed as retract) | hit |
| `se03t3` retract | "I no longer require any particular operating system." | `operating_system` retract | miss | hit | hit |
| `se06t3` soft retract | "Battery life is no longer a priority for me." | `battery` retract | miss | hit | hit |
| `se03t4` reactivation | "Android is mandatory again because of those apps." | `operating_system` reactivate | miss | hit | hit |
| `se02t3` confirmation | "Keep that 128 GB internal-storage floor exactly as set." | support only, no material change | n/a | hit | hit |

Negative controls all held in v1.4: `se01t3` and `se05t4` unchanged-requirement turns emitted
no event; `se05t2` microSD produced no `storage_capacity` event; `se05t3` "Windows is the only
system I do not want" produced no positive OS requirement; `se04t4` browse-only turn produced
no facet; `se06t4` low weight grounded `portability` and not `max_weight`.

Soft-to-hard promotion is covered by unit test rather than by this fixture, which contains no
soft-to-hard turn. `_classify_event` already yields `update_scope` in that direction and the
new test asserts it.

## 7. Regressions and attribution

One new token appeared: `note_taking|soft` in `se05t2` "A microSD slot that accepts large
cards would be convenient." It persists through `se05t3` and `se05t4`, which is why turnwise
Final FP rose from 1 to 3 while scenario-final FP stayed at 1. Candidate FP, C2U FP, novel
candidate FP, candidate precision, candidate recall, turn completion, and event-free false
assertions are all unchanged from v1.3.

The offline attribution replay
(`scripts/analyze_tablet_domain_segse_v14_attribution.py`,
`data/results/tablet_domain_segse_e2e_dev_v14_attribution.json`) settles the cause with zero
new LLM calls. It replays every recorded raw proposal through both deterministic pipelines
against that run's own recorded pre-turn state:

- the raw proposal differed between the two runs on 5 of 24 turns, so provider variance is
  present and is not separable from the code change by the headline table alone;
- the code change altered the outcome on 6 turns, all of them recoveries:
  `se02t1`, `se03t1`, `se05t1`, `se06t1` facet repairs, `se02t4` value correction, `se04t3`
  scope correction;
- exactly 1 turn changed outcome from provider variance alone: `se05t2`. Under the v1.3 raw
  proposal both pipelines reject the event with `assert_scope_is_illegal_for_canonical_id`.
  Under the v1.4 raw proposal both pipelines accept `note_taking|add`. The v1.3 run's model
  mapped microSD to `storage_capacity` with an illegal soft scope, and the v1.4 run's model
  mapped it to `note_taking`, a soft-only ID whose scope is legal.

So the only new FP is attributable to the provider, not to the three deterministic changes.
That is an explanation, not an excuse: the run exposed a real weakness. The existing microSD
guard is keyed to `storage_capacity`, so an expansion-storage phrase can still ground some
other soft preference that has no lexical support in the utterance.

That weakness is deliberately left unfixed in this iteration. Adding a soft-preference
dimension gate after observing this run would fit the method to an already exposed fixture
and would require another run on the same data to measure. It is recorded as the next
development step instead.

**It is also a different failure layer than the one this line of work has been removing.**
The earlier failures were about memory leaking into the current turn and about the state
machine mishandling a correctly identified dimension:

```text
carryover-to-update   ->  previous state copied into the current delta        (solved by SEGSE + C)
material no-op        ->  identical repeats recorded as changes              (solved by C)
correction semantics  ->  value/scope corrections suppressed or mistyped     (solved by v1.4)
dimension attribution ->  the wrong canonical dimension is chosen for a real
                          current-utterance signal                           (open)
```

`microSD slot -> note_taking` is not a carryover and not a no-op. The signal is genuinely in
the current utterance and the anchor is a valid substring; the canonicalization is what is
wrong. So this belongs to a novel-candidate grounding / dimension-attribution layer that sits
downstream of everything v1.4 addresses, and it should be stated as the next research step
rather than folded into the current claim.

## 8. Development decision

**PASS — freeze v1.4 for confirmatory evaluation.**

The predeclared gate passes on every check, correction recall is fully recovered without
giving back any of v1.3's sparse-grounding precision, and the offline attribution shows the six
improved turns are caused by the code change while the single new FP is not.

The wording matters. This is not "v1.4 is adopted as the system"; there is no untouched
confirmatory result yet. It means the method is good enough to stop tuning, pin by hash, and
test once on data it has never seen. The method fingerprint is frozen in
`data/manifests/segse_v14_method_freeze.json` under tag `segse-v1.4-freeze` and re-checked by
`scripts/verify_segse_v14_freeze.py`.

## 9. Next steps

1. Freeze v1.4 as the SEGSE method and author a new untouched confirmatory holdout with
   turn-level gold events, material operations, accumulated state, policy, and hard filters.
   Do not reuse any utterance from this fixture or from the official and confirmatory
   holdouts.
2. Before that holdout runs, decide whether a soft-preference dimension gate belongs in the
   frozen method. If it does, it must be added and unit-tested against the contract, not
   against `se05t2`.
3. Consider whether the closed vocabulary should express a soft counterpart for hard-only
   dimensions. Until it does, a hard-to-soft relaxation on `storage_capacity` or `budget`
   remains a retraction and the gold must say so.
4. Add a provenance-accuracy metric to the evaluator if explicit/implicit/inferred accuracy
   is to be claimed; it is currently not evaluable.
