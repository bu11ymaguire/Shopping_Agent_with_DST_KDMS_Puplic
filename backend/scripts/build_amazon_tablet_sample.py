"""Amazon Reviews 2023 Electronics에서 실제 태블릿 표본을 만든다.

전체 원본을 다운로드하지 않고 metadata Parquet의 각 shard에서 동일한 수를 읽고,
공식 gzip review stream의 앞부분을 제한적으로 순회한다. 결과 파일은 Git에서 제외된다.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import requests
from datasets import load_dataset
from huggingface_hub import dataset_info, hf_hub_url

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.data.amazon_reviews import (  # noqa: E402
    classify_tablet_metadata,
    compute_quantile_scores,
    deduplicate_review_groups,
    normalize_metadata,
    normalize_review,
    select_balanced_reviews,
)

REPOSITORY_ID = "McAuley-Lab/Amazon-Reviews-2023"
METADATA_PREFIX = "raw_meta_Electronics"
METADATA_SHARDS = 10
REVIEW_URL = (
    "https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/"
    "review_categories/Electronics.jsonl.gz"
)
ARTIFACT_FILENAMES = {
    "candidate_metadata": "candidate_metadata.jsonl.gz",
    "metadata_profile": "metadata_profile.json",
    "source_metadata": "source_metadata.jsonl.gz",
    "source_reviews": "source_reviews.jsonl.gz",
    "products": "products.parquet",
    "reviews": "reviews.parquet",
    "normalization": "normalization.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-rows", type=int, default=100_000)
    parser.add_argument("--review-rows", type=int, default=500_000)
    parser.add_argument("--min-rating-number", type=int, default=20)
    parser.add_argument("--min-reviews-per-product", type=int, default=5)
    parser.add_argument("--reviews-per-product", type=int, default=50)
    parser.add_argument("--review-pool-per-product", type=int, default=250)
    parser.add_argument("--max-products", type=int, default=100)
    parser.add_argument(
        "--reuse-metadata-checkpoint",
        action="store_true",
        help="같은 output-dir의 candidate metadata를 검증 후 재사용한다.",
    )
    parser.add_argument(
        "--reuse-final-review-checkpoint",
        action="store_true",
        help=(
            "같은 output-dir의 최종 source_reviews를 재정규화한다. "
            "리뷰 선택 조건과 dataset revision이 같을 때만 허용한다."
        ),
    )
    parser.add_argument(
        "--finalize-only",
        action="store_true",
        help="기존 산출물의 manifest 해시만 다시 계산한다.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=BACKEND_ROOT / "data" / "amazon_reviews_2023" / "tablet_pilot",
    )
    return parser.parse_args()


def _json_default(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default)
        + "\n",
        encoding="utf-8",
    )


def _write_gzip_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def finalize_manifest(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"finalize할 manifest가 없습니다: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files: dict[str, Any] = {}
    for name, filename in ARTIFACT_FILENAMES.items():
        path = (output_dir / filename).resolve()
        if not path.is_file():
            continue
        files[name] = {
            "path": str(path.relative_to(BACKEND_ROOT.resolve())),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    manifest["files"] = files
    _write_json(manifest_path, manifest)
    return manifest


def _metadata_urls(revision: str) -> list[str]:
    return [
        hf_hub_url(
            REPOSITORY_ID,
            f"{METADATA_PREFIX}/full-{index:05d}-of-{METADATA_SHARDS:05d}.parquet",
            repo_type="dataset",
            revision=revision,
        )
        for index in range(METADATA_SHARDS)
    ]


def _metadata_quality_ok(normalized: Mapping[str, Any], minimum_rating_number: int) -> bool:
    return bool(
        normalized["parent_asin"]
        and normalized["title"]
        and normalized["rating_number"] >= minimum_rating_number
        and (normalized["features"] or normalized["description"])
        and normalized["image_url"]
        and normalized["storage_gb"] is not None
        and normalized["screen_inches"] is not None
    )


def collect_metadata(
    *,
    revision: str,
    maximum_rows: int,
    minimum_rating_number: int,
) -> tuple[dict[str, tuple[dict[str, Any], dict[str, Any]]], dict[str, Any]]:
    per_shard = max(1, (maximum_rows + METADATA_SHARDS - 1) // METADATA_SHARDS)
    candidates: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    counters: Counter[str] = Counter()
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scanned = 0
    for shard_index, url in enumerate(_metadata_urls(revision)):
        dataset = load_dataset(
            "parquet",
            data_files={"full": url},
            split="full",
            streaming=True,
        )
        shard_rows = 0
        for row in dataset:
            if scanned >= maximum_rows or shard_rows >= per_shard:
                break
            scanned += 1
            shard_rows += 1
            classification = classify_tablet_metadata(row)
            counters["tablet_signal"] += int(
                classification["category_tablet_signal"]
                or classification["title_tablet_signal"]
            )
            counters["accessory_signal"] += int(classification["accessory_signal"])
            if not classification["is_tablet_candidate"]:
                continue
            counters["tablet_candidate_before_quality"] += 1
            normalized = normalize_metadata(row)
            if not _metadata_quality_ok(normalized, minimum_rating_number):
                counters["tablet_candidate_quality_rejected"] += 1
                continue
            parent_asin = normalized["parent_asin"]
            candidates[parent_asin] = (dict(row), normalized)
            if len(examples["accepted"]) < 10:
                examples["accepted"].append(
                    {
                        "parent_asin": parent_asin,
                        "title": normalized["title"],
                        "categories": normalized["categories"],
                    }
                )
        print(
            f"metadata shard {shard_index + 1}/{METADATA_SHARDS}: "
            f"scanned={scanned:,}, candidates={len(candidates):,}",
            flush=True,
        )
        if scanned >= maximum_rows:
            break
    profile = {
        "rows_scanned": scanned,
        "rows_per_shard_limit": per_shard,
        "candidate_count": len(candidates),
        "signals": dict(sorted(counters.items())),
        "examples": examples,
    }
    return candidates, profile


def load_metadata_checkpoint(
    output_dir: Path,
    *,
    revision: str,
    metadata_rows: int,
    minimum_rating_number: int,
) -> tuple[dict[str, tuple[dict[str, Any], dict[str, Any]]], dict[str, Any]]:
    checkpoint_path = output_dir / ARTIFACT_FILENAMES["candidate_metadata"]
    profile_path = output_dir / ARTIFACT_FILENAMES["metadata_profile"]
    if not checkpoint_path.is_file() or not profile_path.is_file():
        raise RuntimeError("재사용할 metadata checkpoint 또는 profile이 없습니다.")
    checkpoint_profile = json.loads(profile_path.read_text(encoding="utf-8"))
    expected_selection = {
        "metadata_rows": metadata_rows,
        "min_rating_number": minimum_rating_number,
    }
    if checkpoint_profile.get("dataset_revision") != revision:
        raise RuntimeError("metadata checkpoint의 dataset revision이 현재 revision과 다릅니다.")
    if checkpoint_profile.get("selection") != expected_selection:
        raise RuntimeError("metadata checkpoint의 selection 조건이 현재 인자와 다릅니다.")
    candidates: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    checkpoint_rows = 0
    with gzip.open(checkpoint_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            raw = item["raw"]
            checkpoint_rows += 1
            # 체크포인트는 다운로드 절약용 raw cache다. 가공 규칙이 바뀌면 이전
            # normalized 값을 신뢰하지 않고 현재 코드로 다시 계산한다.
            normalized = normalize_metadata(raw)
            if not normalized["is_tablet_candidate"] or not _metadata_quality_ok(
                normalized, minimum_rating_number
            ):
                continue
            candidates[normalized["parent_asin"]] = (raw, normalized)
    stored_profile = checkpoint_profile["profile"]
    if checkpoint_rows != stored_profile["candidate_count"]:
        raise RuntimeError("metadata checkpoint 행 수가 저장된 profile과 일치하지 않습니다.")
    profile = dict(stored_profile)
    profile["candidate_count"] = len(candidates)
    profile["checkpoint_input_count"] = checkpoint_rows
    profile["checkpoint_reprocessed"] = True
    print(
        "metadata checkpoint reused and reprocessed: "
        f"input={checkpoint_rows:,}, candidates={len(candidates):,}",
        flush=True,
    )
    return candidates, profile


def collect_reviews(
    candidate_ids: set[str],
    *,
    maximum_rows: int,
    pool_limit_per_product: int,
) -> tuple[
    dict[str, list[tuple[dict[str, Any], dict[str, Any]]]],
    dict[str, Any],
]:
    pools: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    counters: Counter[str] = Counter()
    response_headers: dict[str, str] = {}
    with requests.get(REVIEW_URL, stream=True, timeout=(20, 120)) as response:
        response.raise_for_status()
        response_headers = {
            key: response.headers[key]
            for key in ("ETag", "Last-Modified", "Content-Length")
            if key in response.headers
        }
        response.raw.decode_content = False
        with gzip.GzipFile(fileobj=response.raw) as handle:
            for line_number, line in enumerate(handle, start=1):
                if line_number > maximum_rows:
                    break
                counters["rows_scanned"] += 1
                if line_number % 50_000 == 0:
                    print(
                        f"reviews: scanned={line_number:,}, matched={counters['candidate_parent_match']:,}, "
                        f"products={len(pools):,}",
                        flush=True,
                    )
                row = json.loads(line)
                parent_asin = str(row.get("parent_asin") or "")
                if parent_asin not in candidate_ids:
                    continue
                counters["candidate_parent_match"] += 1
                normalized = normalize_review(row)
                if normalized is None:
                    counters["invalid_or_short"] += 1
                    continue
                pool = pools[parent_asin]
                if len(pool) < pool_limit_per_product:
                    pool.append((row, normalized))
                    counters["pooled"] += 1
    profile = {
        **dict(sorted(counters.items())),
        "products_with_review_matches": len(pools),
        "response_headers": response_headers,
    }
    return pools, profile


def load_final_review_checkpoint(
    output_dir: Path,
    candidate_ids: set[str],
    *,
    revision: str,
    arguments: argparse.Namespace,
) -> tuple[
    dict[str, list[tuple[dict[str, Any], dict[str, Any]]]],
    dict[str, Any],
]:
    """Re-normalize the already selected raw reviews without another 8M-row scan."""

    manifest_path = output_dir / "manifest.json"
    source_path = output_dir / ARTIFACT_FILENAMES["source_reviews"]
    if not manifest_path.is_file() or not source_path.is_file():
        raise RuntimeError("재사용할 manifest 또는 source_reviews checkpoint가 없습니다.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_selection = {
        "metadata_rows": arguments.metadata_rows,
        "review_rows": arguments.review_rows,
        "min_rating_number": arguments.min_rating_number,
        "min_reviews_per_product": arguments.min_reviews_per_product,
        "reviews_per_product": arguments.reviews_per_product,
        "review_pool_per_product": arguments.review_pool_per_product,
        "max_products": arguments.max_products,
    }
    if manifest.get("dataset_revision") != revision:
        raise RuntimeError("review checkpoint의 dataset revision이 현재 revision과 다릅니다.")
    if manifest.get("selection") != expected_selection:
        raise RuntimeError("review checkpoint의 selection 조건이 현재 인자와 다릅니다.")

    pools: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    checkpoint_rows = 0
    with gzip.open(source_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            raw = json.loads(line)
            checkpoint_rows += 1
            parent_asin = str(raw.get("parent_asin") or "")
            if parent_asin not in candidate_ids:
                raise RuntimeError(
                    f"review checkpoint가 현행 metadata 후보 밖의 상품을 참조합니다: {parent_asin}"
                )
            normalized = normalize_review(raw)
            if normalized is None:
                raise RuntimeError("기존 final review가 현행 정규화 계약을 통과하지 못했습니다.")
            pools[parent_asin].append((raw, normalized))
    expected_rows = int(manifest.get("output", {}).get("review_count", -1))
    if checkpoint_rows != expected_rows:
        raise RuntimeError("review checkpoint 행 수가 manifest output과 일치하지 않습니다.")
    profile = dict(manifest.get("review_profile", {}))
    profile.update(
        {
            "final_checkpoint_input_count": checkpoint_rows,
            "final_checkpoint_reprocessed": True,
        }
    )
    print(
        "final review checkpoint reused and reprocessed: "
        f"reviews={checkpoint_rows:,}, products={len(pools):,}",
        flush=True,
    )
    return pools, profile


def build_output(
    *,
    output_dir: Path,
    revision: str,
    candidates: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    pools: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]],
    metadata_profile: dict[str, Any],
    review_profile: dict[str, Any],
    arguments: argparse.Namespace,
) -> dict[str, Any]:
    product_rows: list[dict[str, Any]] = []
    review_rows_by_product: dict[str, list[dict[str, Any]]] = {}
    raw_products: dict[str, dict[str, Any]] = {}
    raw_reviews_by_id: dict[str, dict[str, Any]] = {}
    for parent_asin, pool in pools.items():
        selected = select_balanced_reviews(
            (normalized for _, normalized in pool),
            limit=arguments.reviews_per_product,
        )
        if len(selected) < arguments.min_reviews_per_product:
            continue
        review_rows_by_product[parent_asin] = selected
        raw_by_id = {
            normalized["review_id"]: raw
            for raw, normalized in pool
        }
        raw_reviews_by_id.update(
            {review["review_id"]: raw_by_id[review["review_id"]] for review in selected}
        )

    provisional_order = sorted(
        review_rows_by_product,
        key=lambda parent_asin: (
            -len(review_rows_by_product[parent_asin]),
            -candidates[parent_asin][1]["rating_number"],
            parent_asin,
        ),
    )
    review_rows_by_product = deduplicate_review_groups(
        review_rows_by_product,
        ordered_product_ids=provisional_order,
        minimum_count=arguments.min_reviews_per_product,
    )
    ordered_ids = [
        parent_asin
        for parent_asin in provisional_order
        if parent_asin in review_rows_by_product
    ][: arguments.max_products]
    retained = set(ordered_ids)
    reviews: list[dict[str, Any]] = []
    for parent_asin in ordered_ids:
        raw, normalized = candidates[parent_asin]
        raw_products[parent_asin] = raw
        product_rows.append(
            {
                **normalized,
                "selected_review_count": len(review_rows_by_product[parent_asin]),
            }
        )
        reviews.extend(review_rows_by_product[parent_asin])

    if not product_rows or not reviews:
        raise RuntimeError(
            "선택 조건을 만족하는 실제 상품·리뷰가 없습니다. 표본 행 수 또는 최소 리뷰 수를 조정하세요."
        )

    normalization_profile, attribute_scores = compute_quantile_scores(product_rows)
    for product in product_rows:
        product["attribute_scores_json"] = json.dumps(
            attribute_scores[product["parent_asin"]],
            ensure_ascii=False,
            sort_keys=True,
        )

    raw_reviews = [
        raw_reviews_by_id[review["review_id"]]
        for review in reviews
        if review["parent_asin"] in retained
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        name: output_dir / filename for name, filename in ARTIFACT_FILENAMES.items()
    }
    paths["manifest"] = output_dir / "manifest.json"
    _write_gzip_jsonl(paths["source_metadata"], (raw_products[key] for key in ordered_ids))
    _write_gzip_jsonl(paths["source_reviews"], raw_reviews)
    if product_rows:
        pq.write_table(pa.Table.from_pylist(product_rows), paths["products"], compression="zstd")
    if reviews:
        pq.write_table(pa.Table.from_pylist(reviews), paths["reviews"], compression="zstd")
    _write_json(
        paths["normalization"],
        {
            "schema_version": "amazon-tablet-quantiles-v1",
            "population_product_count": len(product_rows),
            "profile": normalization_profile,
            "scores": attribute_scores,
            "missing_values_are_imputed": False,
        },
    )

    sentiment_counts = Counter(review["sentiment_bucket"] for review in reviews)
    manifest = {
        "schema_version": "amazon-tablet-pilot-v2",
        "dataset": REPOSITORY_ID,
        "dataset_revision": revision,
        "metadata_source": {
            "config": METADATA_PREFIX,
            "files": _metadata_urls(revision),
        },
        "review_source": REVIEW_URL,
        "selection": {
            "metadata_rows": arguments.metadata_rows,
            "review_rows": arguments.review_rows,
            "min_rating_number": arguments.min_rating_number,
            "min_reviews_per_product": arguments.min_reviews_per_product,
            "reviews_per_product": arguments.reviews_per_product,
            "review_pool_per_product": arguments.review_pool_per_product,
            "max_products": arguments.max_products,
        },
        "metadata_profile": metadata_profile,
        "review_profile": review_profile,
        "output": {
            "product_count": len(product_rows),
            "review_count": len(reviews),
            "sentiment_counts": dict(sorted(sentiment_counts.items())),
            "verified_purchase_count": sum(
                int(review["verified_purchase"]) for review in reviews
            ),
            "products_missing": {
                field: sum(product.get(field) is None for product in product_rows)
                for field in (
                    "price_usd",
                    "image_url",
                    "storage_gb",
                    "memory_gb",
                    "screen_inches",
                    "weight_grams",
                    "operating_system",
                )
            },
        },
        "normalization": {
            "schema_version": "amazon-tablet-quantiles-v1",
            "profile": normalization_profile,
            "missing_values_are_imputed": False,
        },
        "privacy": {
            "source_rows_local_only": True,
            "processed_user_id_retained": False,
            "review_images_retained": False,
        },
    }
    _write_json(paths["manifest"], manifest)
    return finalize_manifest(output_dir)


def main() -> None:
    arguments = parse_args()
    arguments.output_dir = arguments.output_dir.resolve()
    if arguments.finalize_only:
        manifest = finalize_manifest(arguments.output_dir)
        print(json.dumps(manifest["output"], ensure_ascii=False, indent=2), flush=True)
        print(f"manifest finalized: {arguments.output_dir / 'manifest.json'}", flush=True)
        return
    if arguments.metadata_rows <= 0 or arguments.review_rows <= 0:
        raise SystemExit("metadata/review row limit은 양수여야 합니다.")
    existing_manifest_path = arguments.output_dir / "manifest.json"
    if arguments.reuse_final_review_checkpoint and existing_manifest_path.is_file():
        existing_manifest = json.loads(
            existing_manifest_path.read_text(encoding="utf-8")
        )
        revision = str(existing_manifest.get("dataset_revision") or "")
    else:
        info = dataset_info(REPOSITORY_ID)
        revision = info.sha
    if not revision:
        raise RuntimeError("Hugging Face dataset revision을 확인하지 못했습니다.")
    print(f"dataset revision: {revision}", flush=True)
    if arguments.reuse_metadata_checkpoint:
        candidates, metadata_profile = load_metadata_checkpoint(
            arguments.output_dir,
            revision=revision,
            metadata_rows=arguments.metadata_rows,
            minimum_rating_number=arguments.min_rating_number,
        )
    else:
        candidates, metadata_profile = collect_metadata(
            revision=revision,
            maximum_rows=arguments.metadata_rows,
            minimum_rating_number=arguments.min_rating_number,
        )
    if not candidates:
        raise RuntimeError("metadata 표본에서 태블릿 후보를 찾지 못했습니다.")
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    _write_gzip_jsonl(
        arguments.output_dir / "candidate_metadata.jsonl.gz",
        (
            {"raw": raw, "normalized": normalized}
            for raw, normalized in candidates.values()
        ),
    )
    _write_json(
        arguments.output_dir / "metadata_profile.json",
        {
            "dataset": REPOSITORY_ID,
            "dataset_revision": revision,
            "selection": {
                "metadata_rows": arguments.metadata_rows,
                "min_rating_number": arguments.min_rating_number,
            },
            "profile": metadata_profile,
        },
    )
    if arguments.reuse_final_review_checkpoint:
        pools, review_profile = load_final_review_checkpoint(
            arguments.output_dir,
            set(candidates),
            revision=revision,
            arguments=arguments,
        )
    else:
        pools, review_profile = collect_reviews(
            set(candidates),
            maximum_rows=arguments.review_rows,
            pool_limit_per_product=arguments.review_pool_per_product,
        )
    manifest = build_output(
        output_dir=arguments.output_dir,
        revision=revision,
        candidates=candidates,
        pools=pools,
        metadata_profile=metadata_profile,
        review_profile=review_profile,
        arguments=arguments,
    )
    print(json.dumps(manifest["output"], ensure_ascii=False, indent=2), flush=True)
    print(f"manifest: {arguments.output_dir / 'manifest.json'}", flush=True)


if __name__ == "__main__":
    main()
