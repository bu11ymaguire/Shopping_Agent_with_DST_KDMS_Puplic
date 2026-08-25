# SEGSE v1.3 Exposed-Fixture Development Protocol

Status: post-v1.2 method development; never confirmatory evidence.

## Motivation and bounded changes

SEGSE v1.2 removed Final FP on the multi-turn development fixture but failed recall and
completion guards. v1.3 changes only the failure boundaries observed in that run:

1. the prompt explicitly requires all independent facts in compound utterances and
   enumerates the supported activity/goal facet mappings;
2. hard scope on a closed-vocabulary soft-only preference ID is conservatively normalized
   to soft and audited;
3. hard events must satisfy deterministic value dimension and range checks before they
   can reach the State Manager or Query Generator.

Facet events with illegal scope are still rejected rather than normalized because a wrong
facet ID plus a wrong scope is not safe to promote. microSD/removable storage is still
rejected, and negative OS remains unrepresentable. The v1.2 output schema remains unchanged
because it is already provider-compatible.

## Evaluation status

The same six-scenario/24-turn fixture is reused intentionally for development after it was
exposed. No generalization claim is permitted. The frozen B0+C live metrics from run
`segse-e2e-dev-20260818T043715Z-248dcf` are reused without new baseline LLM calls. Only the
v1.3 treatment is called live.

Unlike the v1 runner, a failed turn does not suppress all later scenario turns. The failed
turn remains a missing output and a failure in every applicable denominator; the next gold
utterance executes against the unchanged actual state. This measures recovery after a
single malformed turn and avoids converting one schema failure into several unattempted
outputs.

## Frozen development gate

The treatment must satisfy all of the following against the frozen B0 reference:

- at least 95% turn output completion;
- Candidate FP reduction at least 30%;
- C2U FP reduction at least 50%;
- no increase in event-free false assertions or Final FP;
- Candidate, correction, retract, and reactivation recall drop no more than 2 pp;
- turnwise and scenario-final state F1 drop no more than .01;
- hard-filter completion drop no more than 1 pp;
- Policy and recommendation completion drop no more than 2 pp.

A passing result permits freezing a candidate method only. A new untouched confirmatory
holdout must still be authored after the method freeze.
