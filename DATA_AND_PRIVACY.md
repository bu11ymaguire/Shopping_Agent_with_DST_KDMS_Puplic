# Data and privacy boundary

The runtime and reproducibility portion of this public release contains source code, synthetic
fixtures, frozen labels, aggregate metrics, and reproducibility manifests. It does not contain API
credentials, raw LLM trace files, local Parquet/model artifacts, real user conversations, or
verbatim Amazon Reviews 2023 review text. Selected presentation artifacts are documented separately
below.

## Intentionally excluded artifacts

- `backend/.env`
- `backend/logs/` and `backend/reports/`
- `backend/data/amazon_reviews_2023/`
- generated semantic indexes and local model caches
- internal episode audit exports that embedded review excerpts in LLM request payloads

These artifacts are not required to inspect the state, policy, evaluation, and ranking contracts.
Where a frozen result depends on an excluded artifact, a tracked manifest records the source
revision, configuration, and SHA-256 values needed for an integrity check.

## Intentionally included presentation materials

`presentation/` contains selected PPTX and PDF artifacts that document the development process.
They are publication artifacts, not runtime inputs or reproducibility fixtures, and may retain
author names and statements from the point in time when each deck was prepared. Review every new
or replacement file for credentials, private conversations, raw review text, personal data, and
third-party redistribution rights before committing it.

## Reproducing the Amazon Reviews 2023 path

Follow `backend/data/README.md` to obtain the dataset from its original distributor and generate
the local tablet catalog. The upstream dataset does not provide this repository with a license to
redistribute review text. Users are responsible for checking the upstream terms and applicable
ethical and legal requirements before downloading, processing, or publishing derived data.

Do not submit real customer conversations, credentials, raw review corpora, or private annotation
workbooks in issues or pull requests. The presence of selected public presentations does not change
these exclusions.
