"""Limited real-review retrieval with an explicit deterministic fallback."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config import ReviewRetrievalSettings
from app.experimental_catalog import ExperimentalAmazonCatalog
from app.models.experimental import ExperimentalReview, ReviewRetrievalMethod

SEMANTIC_INDEX_MANIFEST = "review_semantic_manifest.json"


class SemanticReviewRetrievalUnavailable(RuntimeError):
    """The pinned local semantic index or model cache cannot be used."""


@dataclass(frozen=True)
class ReviewRetrievalResult:
    reviews: list[ExperimentalReview]
    method: ReviewRetrievalMethod
    fallback_reason: str | None = None


class ReviewEvidenceRetriever(Protocol):
    def retrieve(
        self,
        catalog: ExperimentalAmazonCatalog,
        product_ids: list[str],
        query: str,
    ) -> ReviewRetrievalResult: ...


class TokenReviewRetriever:
    def __init__(self, *, per_product: int = 5) -> None:
        self.per_product = per_product

    def retrieve(
        self,
        catalog: ExperimentalAmazonCatalog,
        product_ids: list[str],
        query: str,
    ) -> ReviewRetrievalResult:
        reviews = catalog.search_review_evidence(
            product_ids,
            q=query or None,
            per_product=self.per_product,
        )
        return ReviewRetrievalResult(reviews=reviews, method="token")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


class SentenceTransformersReviewRetriever:
    """Bi-encoder per-product top-k followed by a limited Cross-Encoder rerank."""

    def __init__(self, settings: ReviewRetrievalSettings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._loaded = False
        self._embeddings = None
        self._review_ids: list[str] = []
        self._parent_ids: list[str] = []
        self._indices_by_parent: dict[str, list[int]] = {}
        self._embedding_model = None
        self._reranker = None

    def _load(self, catalog: ExperimentalAmazonCatalog) -> None:
        if self._loaded:
            return
        manifest_path = self.settings.index_dir / SEMANTIC_INDEX_MANIFEST
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SemanticReviewRetrievalUnavailable("semantic_index_missing") from exc
        if manifest.get("schema_version") != "review-semantic-index-v1":
            raise SemanticReviewRetrievalUnavailable("semantic_index_schema_mismatch")
        expected = {
            "embedding_model": self.settings.embedding_model,
            "embedding_revision": self.settings.embedding_revision,
            "reranker_model": self.settings.reranker_model,
            "reranker_revision": self.settings.reranker_revision,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise SemanticReviewRetrievalUnavailable("semantic_model_revision_mismatch")
        if catalog.catalog_dir != self.settings.index_dir:
            raise SemanticReviewRetrievalUnavailable("semantic_catalog_path_mismatch")

        files = manifest.get("files", {})
        embeddings_path = self.settings.index_dir / str(
            files.get("embeddings", {}).get("path", "")
        )
        metadata_path = self.settings.index_dir / str(
            files.get("metadata", {}).get("path", "")
        )
        for path, entry_name in (
            (embeddings_path, "embeddings"),
            (metadata_path, "metadata"),
            (catalog.reviews_path, "catalog_reviews"),
        ):
            expected_hash = files.get(entry_name, {}).get("sha256")
            if not path.is_file() or not expected_hash or _sha256(path) != expected_hash:
                raise SemanticReviewRetrievalUnavailable(
                    f"semantic_{entry_name}_hash_mismatch"
                )
        try:
            import numpy as np
            from sentence_transformers import CrossEncoder, SentenceTransformer

            embeddings = np.load(embeddings_path, mmap_mode="r")
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            review_ids = metadata["review_ids"]
            parent_ids = metadata["parent_asins"]
            if (
                embeddings.ndim != 2
                or embeddings.shape[0] != len(review_ids)
                or len(review_ids) != len(parent_ids)
                or embeddings.shape[0] != manifest.get("review_count")
            ):
                raise ValueError("index row counts do not agree")
            embedding_model = SentenceTransformer(
                self.settings.embedding_model,
                revision=self.settings.embedding_revision,
                local_files_only=True,
            )
            reranker = CrossEncoder(
                self.settings.reranker_model,
                revision=self.settings.reranker_revision,
                local_files_only=True,
            )
        except Exception as exc:
            raise SemanticReviewRetrievalUnavailable(
                "semantic_local_model_or_index_unavailable"
            ) from exc

        indices_by_parent: dict[str, list[int]] = defaultdict(list)
        for index, parent_id in enumerate(parent_ids):
            indices_by_parent[str(parent_id)].append(index)
        self._embeddings = embeddings
        self._review_ids = [str(item) for item in review_ids]
        self._parent_ids = [str(item) for item in parent_ids]
        self._indices_by_parent = dict(indices_by_parent)
        self._embedding_model = embedding_model
        self._reranker = reranker
        self._loaded = True

    def retrieve(
        self,
        catalog: ExperimentalAmazonCatalog,
        product_ids: list[str],
        query: str,
    ) -> ReviewRetrievalResult:
        if not product_ids:
            return ReviewRetrievalResult(
                reviews=[],
                method="semantic_cross_encoder",
            )
        clean_query = query.strip()
        if not clean_query:
            raise SemanticReviewRetrievalUnavailable("semantic_query_empty")
        with self._lock:
            self._load(catalog)
            try:
                import numpy as np

                query_vector = self._embedding_model.encode_query(  # type: ignore[union-attr]
                    clean_query,
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                candidates: list[tuple[int, float]] = []
                for parent_id in list(dict.fromkeys(product_ids))[:300]:
                    indices = self._indices_by_parent.get(parent_id, [])
                    if not indices:
                        continue
                    scores = self._embeddings[indices] @ query_vector  # type: ignore[index,operator]
                    top_count = min(self.settings.bi_encoder_per_product, len(indices))
                    local_top = np.argsort(-scores, kind="stable")[:top_count]
                    candidates.extend(
                        (indices[int(local_index)], float(scores[int(local_index)]))
                        for local_index in local_top
                    )
                selected_ids = [self._review_ids[index] for index, _ in candidates]
                selected_reviews = catalog.get_reviews_by_ids(selected_ids)
                reviews_by_id = {review.review_id: review for review in selected_reviews}
                usable = [
                    (index, cosine, reviews_by_id[self._review_ids[index]])
                    for index, cosine in candidates
                    if self._review_ids[index] in reviews_by_id
                ]
                pairs = [
                    (clean_query, f"{review.title or ''}\n{review.text}")
                    for _, _, review in usable
                ]
                logits = self._reranker.predict(  # type: ignore[union-attr]
                    pairs,
                    batch_size=self.settings.reranker_batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )
            except SemanticReviewRetrievalUnavailable:
                raise
            except Exception as exc:
                raise SemanticReviewRetrievalUnavailable(
                    "semantic_inference_failed"
                ) from exc

        by_parent: dict[str, list[ExperimentalReview]] = defaultdict(list)
        for (_, cosine, review), raw_logit in zip(usable, logits, strict=True):
            logit = float(np.asarray(raw_logit).reshape(-1)[0])
            cosine_score = max(0.0, min(1.0, (cosine + 1.0) / 2.0))
            reranker_score = _sigmoid(logit)
            combined = round(100 * (0.35 * cosine_score + 0.65 * reranker_score))
            by_parent[review.parent_asin].append(
                review.model_copy(
                    update={
                        "retrieval_score": max(0, min(100, combined)),
                        "retrieval_method": "semantic_cross_encoder",
                    }
                )
            )
        output: list[ExperimentalReview] = []
        for parent_id in list(dict.fromkeys(product_ids)):
            ordered = sorted(
                by_parent.get(parent_id, []),
                key=lambda review: (
                    -review.retrieval_score,
                    -int(review.verified_purchase),
                    -review.helpful_vote,
                    review.review_id,
                ),
            )
            output.extend(ordered[: self.settings.reranked_per_product])
        return ReviewRetrievalResult(
            reviews=output,
            method="semantic_cross_encoder",
        )


class FallbackReviewRetriever:
    def __init__(
        self,
        primary: ReviewEvidenceRetriever,
        fallback: ReviewEvidenceRetriever,
    ) -> None:
        self.primary = primary
        self.fallback = fallback

    def retrieve(
        self,
        catalog: ExperimentalAmazonCatalog,
        product_ids: list[str],
        query: str,
    ) -> ReviewRetrievalResult:
        try:
            return self.primary.retrieve(catalog, product_ids, query)
        except SemanticReviewRetrievalUnavailable as exc:
            fallback = self.fallback.retrieve(catalog, product_ids, query)
            return ReviewRetrievalResult(
                reviews=fallback.reviews,
                method=fallback.method,
                fallback_reason=str(exc),
            )


def build_review_retriever(
    settings: ReviewRetrievalSettings,
) -> ReviewEvidenceRetriever:
    token = TokenReviewRetriever(per_product=settings.bi_encoder_per_product)
    if settings.mode == "token":
        return token
    return FallbackReviewRetriever(
        SentenceTransformersReviewRetriever(settings),
        token,
    )
