# Contributing

Contributions should preserve the node boundaries and state invariants documented in `AGENTS.md`
and `flow.md`.

Before opening a pull request:

1. Do not modify frozen holdouts, manifests, official results, or historical comparison artifacts
   to improve a reported metric.
2. Do not add API credentials, raw LLM traces, real review text, private conversations, local
   Parquet/model artifacts, or completed annotation workbooks.
3. Treat `presentation/` as an intentionally public archive. Before adding or replacing a deck,
   inspect it for credentials, personal data, private conversations, raw review text, and
   third-party material that cannot be redistributed.
4. Run the public, data-free verifier set in `.github/workflows/offline-smoke.yml`. Run additional
   verifiers only when their documented Git-ignored catalog/report prerequisites are available.
   Original-history ancestry checks are intentionally outside the public verifier set; see
   `PUBLIC_RELEASE_MANIFEST.json`.
5. Run `git diff --check` and inspect the staged diff for secrets and generated files.
6. Describe which branch and research question the change belongs to.

Changes that require live Luxia calls or local Amazon data should include a deterministic offline
test or fixture for the behavior being changed. Keep the private raw report outside Git and update
only the tracked manifest or aggregate result required by the documented protocol.
