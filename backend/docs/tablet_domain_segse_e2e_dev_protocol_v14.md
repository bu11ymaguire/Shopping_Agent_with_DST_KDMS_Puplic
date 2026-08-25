# SEGSE v1.4 Exposed-Fixture Development Protocol

Status: post-v1.3 method development; never confirmatory evidence.

## What v1.3 proved and what it broke

v1.3 completed all 24 turns, cut raw Candidate FP 12 to 3 and C2U FP 8 to 0, and raised
raw Candidate recall to .905. It still failed the gate because correction recall fell from
.500 to .000 and turnwise accumulated-state F1 fell to .745 against B0 .783.

The v1.3 trace attributes both losses to deterministic interpretation, not to the model:

- the only remaining Final FP was a stale 128 GB storage floor left after a correct
  128-to-256 correction was rejected, because the dimension word `internal-storage` sat
  outside the exact numeric anchor the model chose;
- the hard-to-soft display relaxation arrived as RETRACT, so the hard filter was deleted
  instead of downgraded and the desired soft preference disappeared;
- five explicit facet proposals carried `value_after=null` and were rejected wholesale,
  which cost accumulated-state recall on every later turn of those scenarios.

## Bounded changes

v1.4 keeps the v1.3 system prompt and the v1.2 output schema unchanged. Only deterministic
interpretation between the raw proposal and the State Manager moves.

1. **Facet repair.** A facet event with a missing value is completed from its own current
   evidence anchor when that anchor names the facet's canonical dimension. Facet scope and
   relation are always normalized to null, because a facet never carries hard/soft scope.
   A facet whose dimension is not lexically touched stays incomplete and is still rejected
   by the frozen authorization contract, so an unsupported ID cannot slip through.
2. **Two-tier hard dimension evidence.** The exact anchor is still checked first. When the
   anchor omits the dimension word, a narrower unambiguous dimension pattern may be found
   anywhere in the current utterance. The fallback is refused when a competing canonical
   dimension shares the utterance, so `8 GB of RAM and 256 GB of storage` cannot silently
   assign 8 to storage. Numbers, units, and value ranges stay anchored.
3. **Hard-to-soft scope correction.** A RETRACT against an active hard fact is re-typed as
   `assert(scope_after="soft")` only when the utterance negates the obligation *and* a
   separate clause states a positive preference naming the same canonical dimension *and*
   the closed vocabulary admits a soft scope for that ID.

## What deliberately did not change

- The v1.3 instruction text and the v1.2 provider-compatible schema are byte-identical.
- Previous state remains read-only reference memory; no event may cite it as evidence.
- Every event still needs an exact current-utterance anchor.
- The deterministic semantic no-op suppression (C) in the State Manager is untouched and
  remains the lower-layer defense shared by all arms.
- Negative polarity, microSD expansion, unrepresentable negative OS, trade-off compromised
  sides, and illegal preference scopes are rejected exactly as in v1.3.
- Hard-only IDs such as `budget` and `storage_capacity` keep retraction under a relaxation,
  because the closed vocabulary has no soft counterpart for the same canonical ID. This is
  a vocabulary limit that the result document must state, not a validator repair.

## Evaluation status

The same six-scenario/24-turn fixture is reused intentionally after exposure. No
generalization claim is permitted. The frozen B0+C live metrics from
`segse-e2e-dev-20260818T043715Z-248dcf` are reused with zero new baseline calls, and the
recorded v1.3 summary is reused as a second reference with zero new v1.3 calls. Only the
v1.4 treatment is called live, once, at one Understanding call per attempted turn.

A failed turn stays a missing output and a failure in every applicable denominator; the
next gold utterance runs against the unchanged actual state.

## Frozen development gate

v1.4 is judged by the identical predeclared gate v1.3 used, against the same frozen B0
reference: at least 95% turn output completion; Candidate FP reduction at least 30%; C2U FP
reduction at least 50%; no increase in event-free false assertions or Final FP; Candidate,
correction, retract, and reactivation recall drop no more than 2 pp; turnwise and
scenario-final state F1 drop no more than .01; hard-filter completion drop no more than
1 pp; Policy and recommendation completion drop no more than 2 pp.

A second, non-gating regression view records the v1.3 to v1.4 movement of Candidate FP,
C2U FP, Final FP, recall, and completion, so an FP/correction trade-off is visible as
numbers rather than as a single aggregate F1.

A passing result permits freezing a candidate method only. A new untouched confirmatory
holdout must still be authored after the method freeze.
