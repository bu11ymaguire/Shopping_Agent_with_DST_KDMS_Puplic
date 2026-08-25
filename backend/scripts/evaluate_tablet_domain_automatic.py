"""Derive the primary automatic benchmark from immutable official artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_automatic import (  # noqa: E402
    build_automatic_benchmark,
    render_poster_markdown,
    sha256_file,
    write_csv,
)
from app.evaluation.tablet_domain_holdout import load_tablet_holdout_dataset  # noqa: E402
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402

DEFAULT_GOLD = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
DEFAULT_RAW = BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1.json"
DEFAULT_PRIOR_ANALYSIS = (
    BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1_analysis.json"
)
DEFAULT_JSON = BACKEND_ROOT / "data" / "results" / "tablet_domain_automatic_benchmark_v1.json"
DEFAULT_CSV = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_automatic_benchmark_v1_scenarios.csv"
)
DEFAULT_MARKDOWN = BACKEND_ROOT / "docs" / "tablet_domain_automatic_benchmark_v1.md"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--prior-analysis", type=Path, default=DEFAULT_PRIOR_ANALYSIS)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--csv-output", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()

    gold = load_tablet_holdout_dataset(args.gold)
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    prior_analysis = json.loads(args.prior_analysis.read_text(encoding="utf-8"))
    trace_paths = {
        condition: BACKEND_ROOT / "logs" / raw["conditions"][condition]["trace_filename"]
        for condition in ("full", "no_memory", "no_review")
    }
    catalog = ExperimentalAmazonCatalog()
    if not catalog.available:
        raise RuntimeError("the frozen local tablet catalog is unavailable")

    summary, rows = build_automatic_benchmark(
        gold=gold,
        raw=raw,
        prior_analysis=prior_analysis,
        trace_paths=trace_paths,
        catalog=catalog,
    )
    summary["source_artifacts"] = {
        "gold": {"path": args.gold.relative_to(BACKEND_ROOT).as_posix(), "sha256": sha256_file(args.gold)},
        "official_raw": {"path": args.raw.relative_to(BACKEND_ROOT).as_posix(), "sha256": sha256_file(args.raw)},
        "prior_analysis": {"path": args.prior_analysis.relative_to(BACKEND_ROOT).as_posix(), "sha256": sha256_file(args.prior_analysis)},
    }
    _write_json(args.json_output, summary)
    write_csv(args.csv_output, rows)
    args.markdown_output.write_text(
        render_poster_markdown(summary), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": summary["status"],
                "json_output": str(args.json_output.resolve()),
                "json_sha256": sha256_file(args.json_output),
                "csv_output": str(args.csv_output.resolve()),
                "csv_sha256": sha256_file(args.csv_output),
                "markdown_output": str(args.markdown_output.resolve()),
                "markdown_sha256": sha256_file(args.markdown_output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
