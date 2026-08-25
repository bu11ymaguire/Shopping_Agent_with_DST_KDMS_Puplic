# SEGSE v1.3 Exposed-Fixture Development Result

Status: development gate failed; not confirmatory evidence.

v1.3 completed all 24 turns and removed the v1.2 Query crash. Against the frozen B0+C
reference, raw Candidate FP fell 12→3 and C2U FP fell 8→0 while raw Candidate recall
rose from .810 to .905. Exact hard-filter completion improved from .750 to .958.

The method still failed the predeclared gate:

- correction recall fell from .500 to .000;
- turnwise accumulated-state F1 was .745 versus B0 .783, a -0.039 difference;
- scenario-final F1 was .714 versus B0 .722, within the .01 margin but not enough to
  offset the turnwise failure.

Final FP was mostly controlled rather than eliminated. v1.3 had one turnwise and one
scenario-final FP, both the stale 128 GB value left after a missed 128→256 correction.
Material-operation precision/recall/F1 were .941/.696/.800. Retraction, reactivation,
and confirmation metadata recall were each 1.000 on their small target counts.

## Remaining causes

Five explicit facet proposals used `value_after=null`. The validator correctly rejected
them because a facet still needs a descriptive value even though `scope_after` is null.
Four of those IDs were otherwise correct; one mapped sheet music to note-taking and should
remain rejected unless the ID itself is corrected.

The storage correction used an evidence anchor containing `minimum 256 GB` but omitted
the `internal-storage` words elsewhere in the same current utterance. The value-dimension
gate rejected it, leaving the old 128 GB active and causing the only Final FP.

The hard-to-soft display correction was emitted as RETRACT because the model focused on
“no longer a strict requirement” and ignored the simultaneous positive phrase “would be
nice.” This removed the hard filter safely but lost the desired soft preference.

## Decision

Do not freeze v1.3. A final bounded development iteration may repair only:

1. null facet values when the proposed facet ID is lexically supported by current evidence;
2. hard-value dimension validation using the complete current utterance in addition to the
   exact anchor;
3. explicit hard-to-soft wording as UPDATE_SCOPE rather than RETRACT.

This fixture is already exposed, so any such v1.4 result remains development-only and must
be followed by a new untouched confirmatory holdout.
