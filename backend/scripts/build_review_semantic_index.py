"""Build the pinned local review embedding index used by the actual-data demo."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
from sentence_transformers import CrossEncoder, SentenceTransformer

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_review_retrieval_settings  # noqa: E402
from app.review_retrieval import SEMANTIC_INDEX_MANIFEST  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def build_index(catalog_dir: Path) -> dict[str, object]:
    settings = load_review_retrieval_settings()
    catalog_dir = catalog_dir.resolve()
    reviews_path = catalog_dir / "reviews.parquet"
    catalog_manifest_path = catalog_dir / "manifest.json"
    if not reviews_path.is_file() or not catalog_manifest_path.is_file():
        raise FileNotFoundError("catalog reviews.parquet and manifest.json are required")
    catalog_manifest = json.loads(catalog_manifest_path.read_text(encoding="utf-8"))

    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(
            "SELECT review_id, parent_asin, coalesce(title, ''), text "
            "FROM read_parquet(?) ORDER BY review_id",
            [str(reviews_path)],
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        raise RuntimeError("the catalog contains no reviews")

    review_ids = [str(row[0]) for row in rows]
    parent_asins = [str(row[1]) for row in rows]
    documents = [f"{row[2]}\n{row[3]}".strip() for row in rows]
    if len(review_ids) != len(set(review_ids)):
        raise RuntimeError("review_id must be unique before index construction")

    embedding_model = SentenceTransformer(
        settings.embedding_model,
        revision=settings.embedding_revision,
    )
    # Instantiation downloads and validates the exact reranker revision. Runtime then
    # uses local_files_only=True and never reaches the network.
    CrossEncoder(
        settings.reranker_model,
        revision=settings.reranker_revision,
    )
    embeddings = embedding_model.encode(
        documents,
        batch_size=settings.embedding_batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
    ).astype(np.float32, copy=False)
    if embeddings.shape[0] != len(review_ids):
        raise RuntimeError("the embedding row count does not match review metadata")

    embeddings_path = catalog_dir / "review_embeddings.npy"
    metadata_path = catalog_dir / "review_embedding_metadata.json"
    embeddings_temporary = embeddings_path.with_suffix(".npy.tmp")
    with embeddings_temporary.open("wb") as handle:
        np.save(handle, embeddings, allow_pickle=False)
    os.replace(embeddings_temporary, embeddings_path)
    _write_json_atomic(
        metadata_path,
        {
            "schema_version": "review-embedding-metadata-v1",
            "review_ids": review_ids,
            "parent_asins": parent_asins,
        },
    )

    manifest: dict[str, object] = {
        "schema_version": "review-semantic-index-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalog_schema_version": catalog_manifest.get("schema_version"),
        "dataset_revision": catalog_manifest.get("dataset_revision"),
        "review_count": len(review_ids),
        "embedding_dimension": int(embeddings.shape[1]),
        "embedding_model": settings.embedding_model,
        "embedding_revision": settings.embedding_revision,
        "reranker_model": settings.reranker_model,
        "reranker_revision": settings.reranker_revision,
        "build_parameters": {
            "embedding_batch_size": settings.embedding_batch_size,
            "normalize_embeddings": True,
            "document_template": "{title}\\n{text}",
            "row_order": "review_id ASC",
        },
        "files": {
            "catalog_manifest": {
                "path": catalog_manifest_path.name,
                "sha256": _sha256(catalog_manifest_path),
            },
            "catalog_reviews": {
                "path": reviews_path.name,
                "sha256": _sha256(reviews_path),
            },
            "embeddings": {
                "path": embeddings_path.name,
                "sha256": _sha256(embeddings_path),
                "bytes": embeddings_path.stat().st_size,
            },
            "metadata": {
                "path": metadata_path.name,
                "sha256": _sha256(metadata_path),
                "bytes": metadata_path.stat().st_size,
            },
        },
    }
    _write_json_atomic(catalog_dir / SEMANTIC_INDEX_MANIFEST, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=(
            BACKEND_ROOT / "data" / "amazon_reviews_2023" / "tablet_catalog_v2"
        ),
    )
    args = parser.parse_args()
    manifest = build_index(args.catalog_dir)
    print(
        json.dumps(
            {
                "status": "ok",
                "review_count": manifest["review_count"],
                "embedding_dimension": manifest["embedding_dimension"],
                "embedding_model": manifest["embedding_model"],
                "embedding_revision": manifest["embedding_revision"],
                "reranker_model": manifest["reranker_model"],
                "reranker_revision": manifest["reranker_revision"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
