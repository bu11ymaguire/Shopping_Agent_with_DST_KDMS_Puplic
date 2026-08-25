"""통제된 MVP inventory snapshot과 mock review loader."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.config import BACKEND_ROOT
from app.models import Product, Review

DEFAULT_CATALOG_PATH = BACKEND_ROOT / "data" / "demo_catalog.json"


@dataclass(frozen=True)
class DemoCatalog:
    version: str
    disclosure: str
    products: tuple[Product, ...]
    reviews: tuple[Review, ...]


def load_demo_catalog(path: Path | str = DEFAULT_CATALOG_PATH) -> DemoCatalog:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return DemoCatalog(
        version=str(payload["catalog_version"]),
        disclosure=str(payload["disclosure"]),
        products=tuple(Product.model_validate(item) for item in payload["products"]),
        reviews=tuple(Review.model_validate(item) for item in payload["reviews"]),
    )
