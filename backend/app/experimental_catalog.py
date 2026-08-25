"""로컬 Parquet 표본을 제한된 쿼리로 읽는 experimental catalog adapter."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import duckdb

from app.config import BACKEND_ROOT
from app.models.experimental import (
    ExperimentalCatalogStatus,
    ExperimentalProduct,
    ExperimentalProductDetail,
    ExperimentalProductQuery,
    ExperimentalProductSearchResponse,
    ExperimentalProductSort,
    ExperimentalReview,
    ExperimentalReviewSearchResponse,
    QuantileScores,
    SentimentBucket,
)

DEFAULT_EXPERIMENTAL_CATALOG_DIR = (
    BACKEND_ROOT / "data" / "amazon_reviews_2023" / "tablet_catalog_v2"
)

DISCLOSURE = (
    "Experimental local-data mode. Product metadata and reviews come from the pinned "
    "Amazon Reviews 2023 sample. Prices are recorded USD values, and missing fields "
    "are returned as null without imputation."
)
LIMITATIONS = [
    "Delivery, stock, and available colors are absent from the source and are neither provided nor ranked.",
    "Quantile scores are relative to this pinned sample and are not comparable with mock-catalog scores.",
    "Review retrieval may use a pinned local semantic index and limited Cross-Encoder reranking; the deterministic English token baseline remains the explicit fallback.",
]

_TOKEN_PATTERN = re.compile(r"[\w]+", re.UNICODE)
_OPTIONAL_ATTRIBUTE_FIELDS = (
    "price_usd",
    "storage_gb",
    "memory_gb",
    "screen_inches",
    "weight_grams",
    "operating_system",
)


class ExperimentalCatalogUnavailableError(RuntimeError):
    pass


class ExperimentalProductNotFoundError(KeyError):
    pass


def _sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def _tokens(value: str | None) -> list[str]:
    if not value:
        return []
    # 긴 자유 입력이 LIKE 절을 무한히 늘리지 않도록 중복을 제거하고 상한을 둔다.
    return list(dict.fromkeys(_TOKEN_PATTERN.findall(value.casefold())))[:8]


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("JSON object 열이 객체가 아닙니다.")
    return parsed


class ExperimentalAmazonCatalog:
    """실데이터 파일이 없을 때도 기본 MVP를 깨뜨리지 않는 읽기 전용 store."""

    def __init__(self, catalog_dir: Path | str = DEFAULT_EXPERIMENTAL_CATALOG_DIR):
        self.catalog_dir = Path(catalog_dir).resolve()
        self.products_path = self.catalog_dir / "products.parquet"
        self.reviews_path = self.catalog_dir / "reviews.parquet"
        self.manifest_path = self.catalog_dir / "manifest.json"
        self.manifest: dict[str, Any] | None = None
        self.unavailable_reason: str | None = None
        self._load_manifest()

    def _load_manifest(self) -> None:
        if not self.manifest_path.is_file():
            self.unavailable_reason = "로컬 experimental catalog v2가 설치되지 않았습니다."
            return
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.unavailable_reason = "experimental catalog manifest를 읽거나 해석하지 못했습니다."
            return
        if manifest.get("schema_version") != "amazon-tablet-pilot-v2":
            self.unavailable_reason = "experimental mode에는 catalog schema v2가 필요합니다."
            return
        if not self.products_path.is_file() or not self.reviews_path.is_file():
            self.unavailable_reason = "experimental catalog의 Parquet 산출물이 없습니다."
            return
        self.manifest = manifest

    @property
    def available(self) -> bool:
        return self.manifest is not None

    def status(self) -> ExperimentalCatalogStatus:
        output = self.manifest.get("output", {}) if self.manifest else {}
        return ExperimentalCatalogStatus(
            available=self.available,
            schema_version=self.manifest.get("schema_version") if self.manifest else None,
            dataset_revision=(
                self.manifest.get("dataset_revision") if self.manifest else None
            ),
            product_count=int(output.get("product_count", 0)),
            review_count=int(output.get("review_count", 0)),
            disclosure=DISCLOSURE,
            limitations=LIMITATIONS,
            unavailable_reason=self.unavailable_reason,
        )

    def _require_available(self) -> None:
        if not self.available:
            raise ExperimentalCatalogUnavailableError(
                self.unavailable_reason or "experimental catalog를 사용할 수 없습니다."
            )

    def _connect(self) -> duckdb.DuckDBPyConnection:
        self._require_available()
        return duckdb.connect(":memory:")

    @property
    def _products_relation(self) -> str:
        return f"read_parquet('{_sql_path(self.products_path)}')"

    @property
    def _reviews_relation(self) -> str:
        return f"read_parquet('{_sql_path(self.reviews_path)}')"

    @staticmethod
    def _row_dicts(
        cursor: duckdb.DuckDBPyConnection,
    ) -> list[dict[str, Any]]:
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    @staticmethod
    def _product(row: dict[str, Any]) -> ExperimentalProduct:
        scores = QuantileScores.model_validate(
            _json_object(row.pop("attribute_scores_json", None))
        )
        missing = [field for field in _OPTIONAL_ATTRIBUTE_FIELDS if row.get(field) is None]
        return ExperimentalProduct(
            **row,
            attribute_scores=scores,
            missing_attributes=missing,
        )

    def search_products(
        self,
        *,
        q: str | None = None,
        max_price_usd: float | None = None,
        min_storage_gb: int | None = None,
        min_memory_gb: int | None = None,
        max_weight_grams: int | None = None,
        min_rating: float | None = None,
        min_screen_inches: float | None = None,
        operating_system: str | None = None,
        stylus: bool | None = None,
        sort_by: ExperimentalProductSort = "review_count",
        limit: int = 20,
        offset: int = 0,
    ) -> ExperimentalProductSearchResponse:
        self._require_available()
        conditions: list[str] = []
        parameters: list[Any] = []
        searchable = (
            "lower(concat_ws(' ', title, coalesce(brand, ''), coalesce(store, ''), "
            "array_to_string(features, ' '), array_to_string(description, ' ')))"
        )
        for token in _tokens(q):
            conditions.append(f"{searchable} LIKE ?")
            parameters.append(f"%{token}%")
        for expression, value in (
            ("price_usd <= ?", max_price_usd),
            ("storage_gb >= ?", min_storage_gb),
            ("memory_gb >= ?", min_memory_gb),
            ("weight_grams <= ?", max_weight_grams),
            ("average_rating >= ?", min_rating),
            ("screen_inches >= ?", min_screen_inches),
            ("stylus_mentioned = ?", stylus),
        ):
            if value is not None:
                conditions.append(expression)
                parameters.append(value)
        if operating_system:
            conditions.append("lower(operating_system) LIKE ?")
            parameters.append(f"%{operating_system.casefold()}%")
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        score = (
            "CAST(json_extract(attribute_scores_json, '$.price_value') AS INTEGER)"
        )
        sort_sql = {
            "review_count": "selected_review_count DESC NULLS LAST",
            "rating": "average_rating DESC NULLS LAST",
            "price_low": "price_usd ASC NULLS LAST",
            "price_value": f"{score} DESC NULLS LAST",
            "portability": "CAST(json_extract(attribute_scores_json, '$.portability') AS INTEGER) DESC NULLS LAST",
            "storage": "CAST(json_extract(attribute_scores_json, '$.storage') AS INTEGER) DESC NULLS LAST",
            "memory": "CAST(json_extract(attribute_scores_json, '$.memory') AS INTEGER) DESC NULLS LAST",
            "display": "CAST(json_extract(attribute_scores_json, '$.display') AS INTEGER) DESC NULLS LAST",
            "note_taking": "CAST(json_extract(attribute_scores_json, '$.note_taking') AS INTEGER) DESC NULLS LAST",
        }[sort_by]
        projection = """
            parent_asin, title, brand, store, price_usd, average_rating,
            rating_number, image_url, storage_gb, memory_gb, screen_inches,
            weight_grams, operating_system, stylus_mentioned,
            selected_review_count, attribute_scores_json, source
        """
        aggregate_sql = f"""
            SELECT count(*) AS total,
                   count(price_usd) AS price_usd,
                   count(storage_gb) AS storage_gb,
                   count(memory_gb) AS memory_gb,
                   count(screen_inches) AS screen_inches,
                   count(weight_grams) AS weight_grams,
                   count(operating_system) AS operating_system
            FROM {self._products_relation}
            {where_sql}
        """
        rows_sql = f"""
            SELECT {projection}
            FROM {self._products_relation}
            {where_sql}
            ORDER BY {sort_sql}, average_rating DESC NULLS LAST,
                     selected_review_count DESC, parent_asin
            LIMIT ? OFFSET ?
        """
        connection = self._connect()
        try:
            aggregate = self._row_dicts(
                connection.execute(aggregate_sql, parameters)
            )[0]
            rows = self._row_dicts(
                connection.execute(rows_sql, [*parameters, limit, offset])
            )
        finally:
            connection.close()
        total = int(aggregate.pop("total"))
        return ExperimentalProductSearchResponse(
            status=self.status(),
            query=ExperimentalProductQuery(
                q=q,
                max_price_usd=max_price_usd,
                min_storage_gb=min_storage_gb,
                min_memory_gb=min_memory_gb,
                max_weight_grams=max_weight_grams,
                min_rating=min_rating,
                min_screen_inches=min_screen_inches,
                operating_system=operating_system,
                stylus=stylus,
                sort_by=sort_by,
            ),
            total=total,
            limit=limit,
            offset=offset,
            coverage={key: int(value) for key, value in aggregate.items()},
            products=[self._product(row) for row in rows],
        )

    def get_product(self, parent_asin: str) -> ExperimentalProductDetail:
        projection = """
            parent_asin, title, brand, store, price_usd, average_rating,
            rating_number, image_url, storage_gb, memory_gb, screen_inches,
            weight_grams, operating_system, stylus_mentioned,
            selected_review_count, attribute_scores_json, source,
            categories, features, description, classification_json,
            attribute_evidence_json
        """
        connection = self._connect()
        try:
            rows = self._row_dicts(
                connection.execute(
                    f"SELECT {projection} FROM {self._products_relation} "
                    "WHERE parent_asin = ? LIMIT 1",
                    [parent_asin],
                )
            )
        finally:
            connection.close()
        if not rows:
            raise ExperimentalProductNotFoundError(parent_asin)
        row = rows[0]
        classification = _json_object(row.pop("classification_json", None))
        attribute_evidence = _json_object(row.pop("attribute_evidence_json", None))
        categories = list(row.pop("categories") or [])
        features = list(row.pop("features") or [])
        description = list(row.pop("description") or [])
        product = self._product(row)
        return ExperimentalProductDetail(
            **product.model_dump(),
            categories=categories,
            features=features,
            description=description,
            classification=classification,
            attribute_evidence=attribute_evidence,
        )

    def search_reviews(
        self,
        parent_asin: str,
        *,
        q: str | None = None,
        sentiment: SentimentBucket | None = None,
        verified_only: bool = False,
        limit: int = 10,
    ) -> ExperimentalReviewSearchResponse:
        # 없는 상품과 검색 결과 0건을 구분한다.
        self.get_product(parent_asin)
        conditions = ["parent_asin = ?"]
        parameters: list[Any] = [parent_asin]
        tokens = _tokens(q)
        searchable = "lower(concat_ws(' ', coalesce(title, ''), text))"
        if tokens:
            token_conditions = [f"{searchable} LIKE ?" for _ in tokens]
            conditions.append(f"({' OR '.join(token_conditions)})")
            parameters.extend(f"%{token}%" for token in tokens)
        if sentiment is not None:
            conditions.append("sentiment_bucket = ?")
            parameters.append(sentiment)
        if verified_only:
            conditions.append("verified_purchase")
        relevance_sql = " + ".join(
            f"CASE WHEN {searchable} LIKE ? THEN 1 ELSE 0 END" for _ in tokens
        ) or "0"
        relevance_parameters = [f"%{token}%" for token in tokens]
        sql = f"""
            SELECT review_id, parent_asin, asin, rating, title, text, timestamp,
                   verified_purchase, helpful_vote, sentiment_bucket,
                   ({relevance_sql})::INTEGER AS retrieval_score, source
            FROM {self._reviews_relation}
            WHERE {' AND '.join(conditions)}
            ORDER BY retrieval_score DESC, verified_purchase DESC,
                     helpful_vote DESC, timestamp DESC, review_id
            LIMIT ?
        """
        connection = self._connect()
        try:
            rows = self._row_dicts(
                connection.execute(
                    sql,
                    [*relevance_parameters, *parameters, limit],
                )
            )
        finally:
            connection.close()
        return ExperimentalReviewSearchResponse(
            parent_asin=parent_asin,
            q=q,
            sentiment=sentiment,
            verified_only=verified_only,
            limit=limit,
            reviews=[ExperimentalReview.model_validate(row) for row in rows],
        )

    def search_review_evidence(
        self,
        product_ids: list[str],
        *,
        q: str | None,
        per_product: int = 5,
    ) -> list[ExperimentalReview]:
        """후보 상품 집합에서 한 번의 제한 쿼리로 상품별 review top-k를 가져온다."""

        self._require_available()
        unique_ids = list(dict.fromkeys(product_ids))[:300]
        if not unique_ids:
            return []
        per_product = max(1, min(per_product, 20))
        tokens = _tokens(q)
        searchable = "lower(concat_ws(' ', coalesce(title, ''), text))"
        relevance_sql = " + ".join(
            f"CASE WHEN {searchable} LIKE ? THEN 1 ELSE 0 END" for _ in tokens
        ) or "0"
        relevance_parameters = [f"%{token}%" for token in tokens]
        id_placeholders = ", ".join("?" for _ in unique_ids)
        conditions = [f"parent_asin IN ({id_placeholders})"]
        where_parameters: list[Any] = [*unique_ids]
        if tokens:
            conditions.append(
                "(" + " OR ".join(f"{searchable} LIKE ?" for _ in tokens) + ")"
            )
            where_parameters.extend(f"%{token}%" for token in tokens)
        sql = f"""
            SELECT review_id, parent_asin, asin, rating, title, text, timestamp,
                   verified_purchase, helpful_vote, sentiment_bucket,
                   retrieval_score, source
            FROM (
                SELECT review_id, parent_asin, asin, rating, title, text, timestamp,
                       verified_purchase, helpful_vote, sentiment_bucket,
                       ({relevance_sql})::INTEGER AS retrieval_score, source,
                       row_number() OVER (
                           PARTITION BY parent_asin
                           ORDER BY retrieval_score DESC, verified_purchase DESC,
                                    helpful_vote DESC, timestamp DESC, review_id
                       ) AS evidence_rank
                FROM {self._reviews_relation}
                WHERE {' AND '.join(conditions)}
            ) ranked
            WHERE evidence_rank <= ?
            ORDER BY parent_asin, evidence_rank
        """
        connection = self._connect()
        try:
            rows = self._row_dicts(
                connection.execute(
                    sql,
                    [*relevance_parameters, *where_parameters, per_product],
                )
            )
        finally:
            connection.close()
        return [ExperimentalReview.model_validate(row) for row in rows]

    def get_reviews_by_ids(self, review_ids: list[str]) -> list[ExperimentalReview]:
        """제한된 ID 집합의 원문 리뷰를 원래 요청 순서로 가져온다."""

        self._require_available()
        unique_ids = list(dict.fromkeys(review_ids))[:1500]
        if not unique_ids:
            return []
        placeholders = ", ".join("?" for _ in unique_ids)
        sql = f"""
            SELECT review_id, parent_asin, asin, rating, title, text, timestamp,
                   verified_purchase, helpful_vote, sentiment_bucket,
                   0::INTEGER AS retrieval_score, source
            FROM {self._reviews_relation}
            WHERE review_id IN ({placeholders})
        """
        connection = self._connect()
        try:
            rows = self._row_dicts(connection.execute(sql, unique_ids))
        finally:
            connection.close()
        by_id = {
            row["review_id"]: ExperimentalReview.model_validate(row) for row in rows
        }
        return [by_id[review_id] for review_id in unique_ids if review_id in by_id]
