# SEGSE v1.2 Multi-turn End-to-End Development Protocol

Status: frozen development guardrail, not untouched confirmatory evidence.

## Purpose

The module fixture showed that the provider-compatible SEGSE v1.2 contract can reduce
raw Candidate FP and prevent new Final State FP on independent one-turn transitions.
That result did not exercise the complete workflow or genuine sequential accumulation.
This protocol checks whether the same contract remains safe when its own prior outputs
become the next turn's read-only memory and when Policy, Query, catalog retrieval, and
ranking execute after every turn.

False-positive validation is required. It is reported at five different boundaries:

1. raw assertion/refinement candidates proposed by Understanding;
2. candidates surviving deterministic authorization;
3. material typed operations applied by the State Manager;
4. active accumulated state after every turn;
5. active state at each scenario's final turn.

The first turn where each Final State FP token appears is retained with a deterministic
origin label. Persistent copies of the same bad token are measured in turnwise state
exposure but are not relabelled as new origins.

## Frozen development fixture

`tablet_domain_segse_e2e_dev_v1.json` contains six new English scenarios with four turns
each. It was authored after the v1.2 module result and before this end-to-end run. The
24 turns cover:

- five event-free negative controls under accumulated state;
- a semantic confirmation with no material state mutation;
- numeric value correction;
- hard-to-soft scope correction and hard-filter removal;
- two retractions, including one that remains absent in final state;
- tombstone reactivation;
- negative OS polarity and microSD/internal-storage separation;
- preservation of unrelated accumulated preferences.

Gold is current-turn typed events plus deterministic downstream effects. Missing output
turns remain in every applicable denominator. Product IDs, rank order, and relevance
judgments are not gold labels.

## Arms and execution

The live order is frozen:

1. `b0_c_semantic_noop`: Full-Memory v2.3 Understanding plus deterministic semantic
   no-op suppression;
2. `d4_segse_v12`: Full-Memory SEGSE v1.2 flat event schema plus evidence/type/event
   authorization and the SEGSE State Manager.

Both arms use one Understanding LLM call per attempted turn, temperature zero, the same
real tablet catalog, the real deterministic Policy/Query/Retrieval/Ranking pipeline, and
the deterministic response template. Each live arm is executed once from a clean commit.
An infrastructure-only attempt with less than 95% output completion in either arm is
non-evaluable, is preserved separately, and does not consume the single semantic run.

## Primary diagnostics

- raw Candidate precision/recall/F1 and FP count;
- carryover-to-update (C2U) FP and novel Candidate FP;
- false assertion count on event-free and material-no-op turns;
- raw and authorized typed-event accuracy for SEGSE;
- material ID and typed-operation precision/recall/F1;
- turnwise accumulated-state and scenario-final-state precision/recall/F1;
- first Final FP origin and turn;
- correction, retract, reactivation, and confirmation-metadata recall;
- lexical current-turn evidence validity.

## Downstream guardrails

- turn output completion;
- Policy lane and clarification target accuracy;
- recommendation pipeline completion;
- exact deterministic hard-filter completion;
- top-three gold hard-filter violation rate.

## Development decision rule

SEGSE v1.2 advances only if all checks pass:

- both arms complete at least 95% of expected turn outputs;
- Candidate FP falls by at least 30%, or remains zero when baseline FP is zero;
- C2U FP falls by at least 50%, or remains zero when baseline C2U FP is zero;
- event-free false assertions, turnwise Final FP, and scenario-final FP do not increase;
- Candidate, correction, retract, and reactivation recall each fall by at most 2 pp;
- turnwise and scenario-final state F1 each fall by at most 0.01 absolute;
- hard-filter completion falls by at most 1 pp;
- Policy and recommendation completion each fall by at most 2 pp;
- turn output completion falls by at most 0.5 pp.

This is a method-development decision, not a research confirmation. A passing result
only authorizes freezing a candidate for a newly authored untouched confirmatory
holdout. Prompt, schema, validator, or gold changes made after seeing this result create
a new method version.
