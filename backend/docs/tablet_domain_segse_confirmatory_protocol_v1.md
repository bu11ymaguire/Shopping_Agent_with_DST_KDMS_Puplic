# SEGSE v1.4 Confirmatory Evaluation Protocol

Status: frozen before the untouched holdout is authored and before any confirmatory run.

This protocol is fixed first so the holdout cannot be shaped by a result and the result
cannot be reinterpreted after the fact. The method it evaluates is pinned by
`data/manifests/segse_v14_method_freeze.json` under tag `segse-v1.4-freeze`.

Revision 2. The protocol is amendable only while no holdout data and no run exist; both held
at amendment time. `amendment_history` in the manifest records what changed and why.

## 1. Primary question

> Can SEGSE v1.4 reduce false current-turn state updates while preserving genuine value and
> scope corrections and the resulting accumulated state?

### Primary outcome: how close the Final DST gets to the Gold State

The headline is a **graded** per-episode score, not a token-perfect match.

> How close is the final accumulated `DialogueState` produced by frozen v1.4 to the frozen Gold
> State, and is it closer than the v1.3 contract on the identical raw proposals?

Two closeness measures per episode, over the active final-state token set (hard facts as
`canonical_id|hard|normalized_value`, qualitative facts as `canonical_id|scope`, superseded facts
excluded):

```text
error tokens = |predicted △ gold| = FP + FN        lower is better
jaccard      = |predicted ∩ gold| / |predicted ∪ gold|   1.0 means identical
```

**Exact match is reported but is not the headline.** It is all-or-nothing and has poor
resolution. On the development fixture:

| arm | exact | mean error tokens | mean Jaccard |
| --- | ----: | ----------------: | -----------: |
| B0+C | 2/6 | 1.667 | .608 |
| v1.3 | 0/6 | 1.333 | .569 |
| v1.4 | 4/6 | 0.333 | .889 |

v1.3 scored 0/6 exact while its mean Jaccard was .569, so exact match reported a total failure
where the state was more than half right. Both v1.4 misses were single-token, and one of them came
from provider variance rather than the method. A single token should not move the headline.

The research claim is that false updates fall while genuine corrections survive. That is a graded
movement of the final state toward gold, not a demand for token-perfect episodes.

### Second primary: paired improvement over the v1.3 contract

The holdout has no live v1.3 run, but Analysis B replays the identical recorded raw proposals
through both deterministic pipelines, which produces a paired v1.3 final state on the same data
with zero extra LLM calls and the LLM held identical by construction.

```text
per episode:  error_tokens( D_v1.3(R) )  vs  error_tokens( D_v1.4(R) )
              jaccard( D_v1.3(R) )       vs  jaccard( D_v1.4(R) )
```

Reported as mean error-token reduction, mean Jaccard improvement, the count of episodes improved
/ unchanged / worsened, and a paired bootstrap 95% CI over the 20 episodes. On the development
fixture this was 4 improved, 2 unchanged, **0 worsened**.

### Pre-registered decision rule

The confirmatory claim is **comparative, not absolute**. PASS requires all of:

- mean per-episode Jaccard improvement over v1.3 is positive with a paired bootstrap 95% CI lower
  bound above 0;
- at most 2 of 20 episodes worsen in error-token count;
- at least 8 of 20 episodes improve in error-token count;
- combined value and scope correction recall is strictly greater than the v1.3 paired value on the
  same proposals;
- every guardrail holds.

Reported descriptively with no threshold: absolute mean Jaccard, absolute mean error tokens,
scenario final-state micro P/R/F1, and exact match out of 20. No absolute bar is set because no
untouched measurement of this method exists, so any such bar would be invented rather than
justified.

Forbidden: declaring success from exact match alone, declaring success from an aggregate F1 alone,
or moving a threshold after seeing the result.

Supporting decompositions, reported but never substituted for the headline:

| Layer | Metric | Note |
| ----- | ------ | ---- |
| Per episode | error tokens, Jaccard, P/R/F1, exact match | the headline is the graded pair |
| Per turn | turnwise accumulated state P/R/F1 | persistence-amplified |
| Per decision | material State Diff and material operation P/R/F1 | the honest per-decision view |

One question, one confirmatory dataset. Secondary observations may be reported but cannot be
promoted to a primary claim afterwards.

One secondary, descriptive question is declared now so it cannot be invented later:

> Is dimension attribution now the dominant residual error?

Its evidence is the distribution of Final FP origin causes, with `carryover_to_update` kept
separate from `novel_extraction` and `incorrect_value_or_scope`. If confirmed, v1.5 becomes a
semantic dimension grounding / attribution gate as an independent intervention. It cannot be
promoted to a primary claim after the run.

## 2. Two analyses, deliberately separated

The v1.4 development run showed that raw proposals differ across runs even at temperature 0:
5 of 24 turns changed. An end-to-end comparison therefore mixes two effects.

```text
Observed difference = method effect + LLM run variation
```

The confirmatory stage reports both analyses side by side.

**Analysis A — End-to-End.** The untouched holdout runs once through the frozen v1.4 pipeline
with live Understanding. This measures the deployed system and is the source of every absolute
number: FP, correction recall, state diff, accumulated state, policy, hard filters, completion.

```text
utterance -> live gpt-4o-mini (temperature 0) -> v1.4 validator/manager -> metrics
```

**Analysis B — Fixed-Upstream Paired.** The raw structured proposals recorded during Analysis A
are replayed through both deterministic pipelines against the same pre-turn state. No new LLM
call is made, so the LLM is held identical by construction and the only varying factor is the
deterministic contract.

```text
recorded R_t  ->  D_v1.3(R_t, S_{t-1})
              ->  D_v1.4(R_t, S_{t-1})   -> paired per-turn comparison
```

This is legitimate without a second live run precisely because v1.3 and v1.4 share the prompt
text and the output schema byte-for-byte; the freeze manifest asserts that equality. Its
per-turn pairing also supports a paired bootstrap over scenarios.

**Analysis C — Test-Retest Provider Stability.** The identical frozen method runs a second time on
the identical holdout. Development already showed raw proposals differing on 5 of 24 turns at
temperature 0, so provider nondeterminism is a real nuisance factor rather than a hypothetical
one. Analysis C measures it and nothing else.

**What each analysis is allowed to claim.**

| Analysis | What it supports |
| -------- | ---------------- |
| A. End-to-End | the actual confirmatory performance of the whole frozen v1.4 system |
| B. Fixed-Upstream | the effect of the v1.3 to v1.4 deterministic contract change under an identical raw proposal |
| C. Test-Retest | the provider-level nondeterminism of the frozen method on identical data |

### Run roles

> Run A is the sole primary confirmatory evaluation. Run B is a pre-registered test-retest
> analysis of provider-level nondeterminism and shall not replace, average with, or retroactively
> redefine Run A.

```text
Run A  = primary confirmatory run   -> decides PASS / FAIL, is the reported headline
Run B  = test-retest robustness run -> measures provider nondeterminism only
```

**Both runs execute back to back before either result is opened.**

```text
holdout freeze
  ↓
execute Run A
  ↓
Run A results NOT opened
  ↓
execute Run B
  ↓
both runs complete
  ↓
open and analyse
```

Deciding whether to do Run B after seeing Run A would be a selection bias, so the order is fixed.

Forbidden without exception: averaging Run A and Run B into one number, reporting whichever run
is better, pooling the two as independent samples for a confidence interval, replacing a Run A
number with a Run B number, or using Run B to redefine the Run A decision after the fact. The two
runs are repeated measurements of the same 80 turns; they support stability analysis only. If
Run A gives .90 and Run B gives .94, the reported number is .90 and the spread is a stability
observation.

Reported disagreements between the two runs:

```text
raw structured proposal exact-match disagreement
candidate set disagreement
material operation disagreement
Candidate FP / C2U FP difference
correction recall difference
material State Diff F1 difference
Final State F1 and exact-match difference
schema / turn completion difference
```

Analysis B proves a **deterministic interpretation-layer effect**, not the causal effect of the
whole v1.4 method. It answers "given the same LLM proposal, how much does the downstream
contract change the outcome". It does not answer how often the upstream emits the proposal that
makes the contract matter. Every claim must name its analysis.

**Provider variation is measured, not assumed.** There is no live v1.3 run on the holdout, so an
end-to-end version difference is not computable; provider variation can only be observed by
repeating the identical frozen method on the identical data, which is what Analysis C does. Until
that measurement exists, no holdout difference may be attributed to provider variance.

## 3. Predeclared metrics

Denominator rule for both analyses: every authored turn counts. A missing output or a schema
failure is a failure in every applicable denominator and never removed from it. A failed turn
does not suppress later turns; the next utterance runs against the unchanged actual state.

### Primary

| Group | Metric |
| ----- | ------ |
| Final state closeness | mean per-episode error tokens, mean per-episode Jaccard, scenario final-state P/R/F1, FP, FN, exact match out of 20 |
| Closeness improvement over v1.3 | mean error-token reduction, mean Jaccard improvement, episodes improved / unchanged / worsened, paired bootstrap 95% CI |
| False update | Candidate FP count, C2U FP count, novel candidate FP count |
| Correction | Value-correction recall, scope-correction recall, reported separately and combined |
| Material behavior | Material State Diff precision, recall, F1; material operation precision, recall, F1 |
| Persistence | Final State precision, recall, F1, turnwise and scenario-level |

Final State is primary but must be labelled persistence-amplified: one corrected event repairs
every later snapshot of its scenario, so accumulated-state deltas are larger than the number of
event-level decisions that produced them. Material-layer numbers are the per-decision view and
must be quoted with any accumulated-state claim.

### Guardrail

| Metric | Reason |
| ------ | ------ |
| Candidate recall | precision must not be bought with lost acquisition |
| Retract / delete recall | corrections must not be recovered by weakening deletion |
| Hard-filter completion | state must still reach the deterministic query correctly |
| Schema / turn output completion | reliability must not degrade |

A guardrail regression is reported as a failure of the confirmatory claim even when a primary
metric improves.

### Reporting form

The headline is graded closeness plus the paired movement, never exact match on its own:

```text
Final-state error tokens per episode   1.35 -> 0.45   (v1.3 contract -> v1.4, identical proposals)
Mean Jaccard to Gold                    .62  ->  .88
Episodes improved / unchanged / worsened  14 / 6 / 0
Exact match (strictest cut)             11/20
```

Eighty turns is still small for correction metrics, so an aggregate rate is not an acceptable
report on its own. Every correction and retract metric is written as a numerator over its
denominator, split by operation:

```text
value correction            6/6
scope correction hard->soft 7/8
scope correction soft->hard 4/4
retract                     6/7
reactivation                3/3
confirmation metadata       4/4
```

Final State always carries the persistence-amplified label. Every claim names its analysis.

### Reported but not claimed

Policy lane accuracy, question-target accuracy, recommendation pipeline completion, evidence
span validity, dimension-evidence scope distribution, top-3 hard-constraint violation rate,
confirmation metadata recall.

Provenance accuracy is `not_evaluable` until the evaluator computes explicit/implicit/inferred
agreement. Product and review relevance are out of scope; this is a state experiment.

## 4. Holdout authoring rules

The holdout is authored after the method freeze and frozen before the first run.

Frozen before execution: utterances, gold typed state events, gold material operations, gold
accumulated state, gold policy lane and question target, expected hard constraints.

Never frozen and never authored: recommended products, system rankings, human relevance grades.

Reuse rules:

- No utterance may be copied, at any casing or whitespace, from the SEGSE module fixture, the
  SEGSE multi-turn development fixture, the tablet-domain understanding dev set, the official
  20-scenario / 81-turn holdout, or the confirmatory 20-scenario / 80-turn holdout. A
  deterministic overlap check runs before the freeze.
- Failure families discovered during development are included as *new surface forms*, never as
  the development sentence. Specifically the `microSD slot that accepts large cards` sentence is
  forbidden; the same family appears as something like an expandable-storage request phrased for
  file transfer, and its gold must contain no `note_taking` and no `storage_capacity` event.
- Gold is authored from the state contract, not from any recorded system output.

### Size

20 scenarios by 4 turns, 80 turns. This matches the scale of the earlier official and
confirmatory tablet holdouts so comparisons are straightforward, and it leaves headroom above
the 56 constrained turns below. A smaller set would let one or two cases dominate
value-correction, scope-correction, and retract or reactivation recall.

### Coverage constraints, not turn quotas

The table below is a **coverage constraint**. The holdout is 20 coherent four-turn shopping
episodes that happen to cover these transitions; it is not 80 independent failure templates. A
scenario assembled from a single family is rejected, every scenario carries at least two
distinct transition families, and families are distributed across scenarios rather than
concentrated in a few. The turn order has to read as a natural dialogue.

| Family | Minimum turns |
| ------ | ------------- |
| Value correction on a hard numeric fact | 6 |
| Scope correction hard to soft on a soft-capable ID | 4 |
| Scope correction soft to hard | 4 |
| Explicit retract of a hard fact | 4 |
| Explicit retract of a soft fact | 3 |
| Reactivation after retract | 3 |
| Explicit confirmation of an active fact, no material change | 4 |
| Unchanged-requirement turn with zero gold events | 6 |
| Dimension attribution negative | 6 |
| Negative polarity, including a negative operating system | 3 |
| Trade-off where the compromised side must not become a preference | 3 |
| Compound utterance with two or more independent facts | 6 |
| Relaxation on a hard-only ID, where gold is retract by vocabulary limit | 3 |

Every scenario keeps `environment.category=tablet`. An explicit non-tablet request routes to
`unsupported_category` and mutates nothing.

### Dimension-attribution negatives

Six turns, and they must be six **dimension-confusion opportunities across six different
semantic families**, not six paraphrases of the development microSD sentence and not six turns
in one family:

```text
expandable storage
external accessories
display feature
connectivity feature
port or charging feature
stylus accessory
```

Each turn states a real, current-utterance product signal and checks whether it gets
over-canonicalized into an unrelated subjective facet or preference ID. Gold contains no
preference or facet ID that the utterance does not lexically support, and the plausible wrong
IDs are listed in `forbidden_event_ids`.

This is a pre-declared generalization test of a residual failure family already found in
development. It is not an adversarial attempt to fail v1.4, and exposure is a recorded
limitation of the frozen method rather than a trigger to patch it.

Gold self-consistency is machine-checked before the freeze: the declared hard filters must equal
the deterministic query generator's output on the gold state, and the declared policy lane and
question target must equal the deterministic policy's output. A disagreement is a gold bug, not
a system finding.

## 5. Authoring sequence

The order is fixed and no step may be reordered.

```text
Protocol freeze
  ↓
Holdout utterances authored
  ↓
Gold events / material operations / accumulated state annotated
  ↓
Static consistency validation
  ↓
Holdout manifest + hashes
  ↓
HOLDOUT FREEZE
  ↓
First v1.4 execution
  ↓
Results read
```

**v1.4 is never executed while the holdout is being authored,** not even once on a candidate
sentence to check whether it behaves. The moment an utterance is run, it becomes development
data. Gold is annotated from the frozen semantic contract only, without looking at any v1.4
output.

## 6. Execution contract

- One Understanding call per attempted turn, temperature 0.
- Two live runs total: Run A then Run B, back to back, before either result is opened.
- Analysis A runs exactly once. No re-run for a better number. An infrastructure failure is
  recorded, and a re-run is permitted only when no turn produced a scored output.
- Analysis B runs offline against the recorded Analysis A proposals, with zero LLM calls.
- No intermediate aggregation is inspected before the full run completes.
- The frozen method fingerprint is re-verified immediately before and after the run.
- Raw output, traces, and hashes are recorded; the manifest pins the raw report and trace
  hashes even though those files stay out of Git.

## 7. What happens after this

Frozen v1.4 is not edited again. The next intervention forks a new version.

```text
v1.5 = Semantic Dimension Grounding / Attribution Gate
       own development iteration -> own method freeze -> own untouched confirmatory holdout
```

Folding a dimension gate into v1.4 would blur which failure layer v1.4 resolved, which is the
whole point of the layered progression:

```text
C2U / no-op  ->  correction semantics  ->  [ dimension attribution ]
   solved            solved by v1.4              next version
```

## 8. Interpretation limits fixed in advance

- v1.4 was tuned on an exposed development fixture. The confirmatory result is the first
  untouched measurement of it, and the development numbers are never quoted as evidence of
  generalization.
- The dimension-attribution weakness is known and unfixed. If the holdout exposes it, that is a
  recorded limitation of the frozen method, not a reason to patch and re-run.
- Analysis B compares deterministic contracts under an identical upstream, so it measures the
  interpretation-layer effect only. It is not the causal effect of the whole method and it says
  nothing about how often the upstream produces the proposal that makes the contract matter.
- Provider variation is not estimated on the holdout, so no holdout difference may be attributed
  to it.
- Eighty turns is small for correction metrics. Every correction and retract number is reported
  as a numerator over a denominator, split by operation.
- Passing this protocol supports a claim about state-update correctness. It supports no claim
  about recommendation quality or human-perceived relevance.
