# SEGSE v1.2 Historical Freeze Audit

Status: expected historical mismatch, recorded and not repaired.

## What happened

```text
b67e7be  Add provider-compatible SEGSE v1.2 arm
         → wrote data/manifests/tablet_domain_segse_dev_v12_protocol.json
         → pinned app/segse_experiment_v12.py at 31cdff6f…

9ea2063  Freeze SEGSE multi-turn development guardrail
         → modified app/segse_experiment_v12.py (+48 lines)
         → file hash became 5408f8a9…
```

The v1.2 freeze manifest still pins the pre-change hash, so
`scripts/verify_tablet_domain_segse_dev_v12.py` fails on
`frozen v1.2 hash: app/segse_experiment_v12.py`.

## Interpretation

The original v1.2 freeze manifest is historically preserved exactly as it was written. The
current working-tree copy no longer matches it because the source was modified in a later commit.
The mismatch is a true statement about the history of this repository, not a defect to silence.

```text
original frozen expected hash:  31cdff6ffa8ec3df…
current file hash:              5408f8a9a1a801bb…
source changed at:              9ea2063
classification:                 FAIL_AS_HISTORICAL_MISMATCH
```

## Impact on v1.3 and v1.4

None. The v1.3 and v1.4 result manifests, and the v1.4 method freeze, all independently pin the
current hash `5408f8a9…`, so those freezes are internally consistent and verify clean.

## What was deliberately not done

- The expected hash in the historical v1.2 manifest was not changed.
- The historical verifier was not adjusted to pass.
- No freeze tag was moved or retagged.
- The later source change was not reverted.
- The failure was not reclassified as a current pass.

A freeze that can be rewritten after the fact is not a freeze. Leaving the mismatch visible and
explained is the higher-provenance choice.

## Expected verifier behaviour

| Script | Expected |
| ------ | -------- |
| `verify_tablet_domain_segse_dev_v12.py` | **fails** with the recorded assertion, by design |
| `verify_segse_v12_historical_audit.py` | passes, and asserts the mismatch still has exactly the recorded shape |

If the second script ever starts failing, the history changed and this note is stale.
