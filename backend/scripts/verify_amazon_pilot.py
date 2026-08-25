"""로컬 Amazon Reviews 2023 tablet pilot 산출물의 무결성을 검사한다."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PILOT_DIR = BACKEND_ROOT / "data" / "amazon_reviews_2023" / "tablet_pilot"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-dir", type=Path, default=DEFAULT_PILOT_DIR)
    return parser.parse_args()


def check(label: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    pilot_dir = parse_args().pilot_dir.resolve()
    manifest_path = pilot_dir / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(
            f"pilot manifest가 없습니다: {manifest_path}\n"
            "먼저 scripts/build_amazon_tablet_sample.py를 실행하세요."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    check(
        "pilot schema version",
        manifest["schema_version"] in {"amazon-tablet-pilot-v1", "amazon-tablet-pilot-v2"},
    )
    check("dataset revision 기록", len(manifest["dataset_revision"]) == 40)
    for name, item in manifest["files"].items():
        path = BACKEND_ROOT / Path(item["path"])
        check(f"{name} 파일 존재", path.is_file(), path)
        check(f"{name} byte 크기", path.stat().st_size == item["bytes"], item["bytes"])
        check(f"{name} sha256", sha256(path) == item["sha256"])

    products_path = (pilot_dir / "products.parquet").as_posix().replace("'", "''")
    reviews_path = (pilot_dir / "reviews.parquet").as_posix().replace("'", "''")
    connection = duckdb.connect(":memory:")
    connection.execute(f"CREATE VIEW products AS SELECT * FROM read_parquet('{products_path}')")
    connection.execute(f"CREATE VIEW reviews AS SELECT * FROM read_parquet('{reviews_path}')")
    product_count = connection.execute("SELECT count(*) FROM products").fetchone()[0]
    review_count = connection.execute("SELECT count(*) FROM reviews").fetchone()[0]
    check("상품 수 manifest 일치", product_count == manifest["output"]["product_count"], product_count)
    check("리뷰 수 manifest 일치", review_count == manifest["output"]["review_count"], review_count)
    check(
        "parent_asin 참조 무결성",
        connection.execute(
            "SELECT count(*) FROM reviews r ANTI JOIN products p USING (parent_asin)"
        ).fetchone()[0]
        == 0,
    )
    check(
        "리뷰 ID 중복 없음",
        connection.execute("SELECT count(*) = count(DISTINCT review_id) FROM reviews").fetchone()[0],
    )
    check(
        "리뷰 본문 중복 없음",
        connection.execute("SELECT count(*) = count(DISTINCT lower(trim(text))) FROM reviews").fetchone()[0],
    )
    columns = {
        row[0]
        for row in connection.execute("DESCRIBE reviews").fetchall()
    }
    check("processed review에서 user_id 제외", "user_id" not in columns)
    check(
        "실제 데이터 source만 포함",
        connection.execute(
            "SELECT count(*) FROM reviews WHERE source <> 'amazon_reviews_2023'"
        ).fetchone()[0]
        == 0,
    )
    minimum_reviews = manifest["selection"]["min_reviews_per_product"]
    actual_minimum = connection.execute(
        "SELECT min(review_count) FROM (SELECT count(*) review_count FROM reviews GROUP BY parent_asin)"
    ).fetchone()[0]
    check("상품별 최소 리뷰 수", actual_minimum >= minimum_reviews, actual_minimum)
    sentiments = {
        row[0]
        for row in connection.execute("SELECT DISTINCT sentiment_bucket FROM reviews").fetchall()
    }
    check("긍정·중립·부정 근거 포함", sentiments == {"negative", "neutral", "positive"}, sentiments)
    verified = connection.execute(
        "SELECT count(*) FROM reviews WHERE verified_purchase"
    ).fetchone()[0]
    check("verified purchase 우선 표본", verified / review_count >= 0.8, f"{verified}/{review_count}")
    connection.close()
    print("\nAmazon Reviews 2023 실제 tablet pilot 검증 통과")


if __name__ == "__main__":
    main()
