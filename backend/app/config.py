"""환경 변수 기반 설정.

특정 제공업체에 코드가 결합되지 않도록 provider 선택과 접속 정보를 모두
환경 변수로 뺀다. LangGraph 노드는 이 설정을 직접 읽지 않고
`app.llm.factory.build_client()`가 만든 `LLMClient`만 받는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

BACKEND_ROOT = Path(__file__).resolve().parent.parent

Provider = Literal["luxia", "mock"]
ReviewRetrievalMode = Literal["token", "semantic"]

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
DEFAULT_RERANKER_REVISION = "c5ee24cb16019beea0893ab7796b1df96625c6b8"


def _load_dotenv() -> None:
    """backend/.env를 환경 변수로 읽어들인다.

    python-dotenv가 없어도 동작하도록 수동 파싱 경로를 함께 둔다.
    이미 설정된 환경 변수는 덮어쓰지 않는다. CI나 셸에서 준 값이 우선이다.
    """
    env_path = BACKEND_ROOT / ".env"
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass

    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _env_str(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key}는 실수여야 합니다. 현재 값: {raw!r}") from exc


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key}는 정수여야 합니다. 현재 값: {raw!r}") from exc


@dataclass(frozen=True)
class LLMSettings:
    """LLM 접속 설정.

    Luxia 브리지는 OpenAI와 두 군데가 다르다. 그 차이를 필드로 드러내
    나중에 공식 OpenAI로 갈아탈 때 어디를 바꿔야 하는지 명확히 한다.

    1. 인증이 ``Authorization: Bearer``가 아니라 ``apikey`` 헤더다.
    2. 실제 모델은 request body의 ``model``이 아니라 URL 경로가 결정한다.
       body의 ``model``에는 ``"llm"`` 고정값이 들어간다.
    """

    provider: Provider = "luxia"
    api_key: str = field(default="", repr=False)
    base_url: str = "https://bridge.luxiacloud.com/llm/openai/chat/completions/gpt-4o-mini"

    #: request body의 "model" 필드로 보낼 값. Luxia는 "llm" 고정.
    model_field: str = "llm"
    #: 연구 로그용. 우리가 어떤 모델을 의도했는지 기록한다.
    requested_model: str = "gpt-4o-mini"

    timeout_seconds: float = 60.0
    temperature: float = 0.0
    top_p: float = 0.95
    max_tokens: int = 2048

    max_retries: int = 5
    initial_backoff_seconds: float = 5.0

    log_dir: Path = BACKEND_ROOT / "logs"

    @property
    def completions_url(self) -> str:
        """실제 POST 대상. Luxia는 base URL 뒤에 /create를 붙인다."""
        return f"{self.base_url.rstrip('/')}/create"

    def require_api_key(self) -> str:
        """키가 필요한 경로에서 호출한다. 값은 반환만 하고 로그에 남기지 않는다."""
        if not self.api_key:
            raise RuntimeError(
                "LLM_API_KEY 또는 LUXIA_API_KEY가 비어 있습니다. "
                "backend/.env.example을 .env로 복사한 뒤 키를 채우거나, "
                "LLM_PROVIDER=mock으로 두고 실행하세요."
            )
        return self.api_key


def load_llm_settings() -> LLMSettings:
    """환경 변수에서 LLM 설정을 만든다."""
    _load_dotenv()

    provider_raw = _env_str("LLM_PROVIDER", "luxia").lower()
    if provider_raw not in ("luxia", "mock"):
        raise ValueError(
            f"LLM_PROVIDER는 'luxia' 또는 'mock'이어야 합니다. 현재 값: {provider_raw!r}"
        )

    # 기존 Using_Luxia_API 예제는 LUXIA_API_KEY를 쓴다. 그 이름도 함께 받아
    # 키를 두 곳에 중복 저장하지 않게 한다.
    api_key = _env_str("LLM_API_KEY") or _env_str("LUXIA_API_KEY")

    log_dir_raw = _env_str("LLM_LOG_DIR", "logs")
    log_dir = Path(log_dir_raw)
    if not log_dir.is_absolute():
        log_dir = BACKEND_ROOT / log_dir

    return LLMSettings(
        provider=provider_raw,  # type: ignore[arg-type]
        api_key=api_key,
        base_url=_env_str(
            "LLM_BASE_URL",
            "https://bridge.luxiacloud.com/llm/openai/chat/completions/gpt-4o-mini",
        ),
        model_field=_env_str("LLM_MODEL_FIELD", "llm"),
        requested_model=_env_str("LLM_REQUESTED_MODEL", "gpt-4o-mini"),
        timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 60.0),
        temperature=_env_float("LLM_TEMPERATURE", 0.0),
        top_p=_env_float("LLM_TOP_P", 0.95),
        max_tokens=_env_int("LLM_MAX_TOKENS", 2048),
        max_retries=_env_int("LLM_MAX_RETRIES", 5),
        initial_backoff_seconds=_env_float("LLM_INITIAL_BACKOFF_SECONDS", 5.0),
        log_dir=log_dir,
    )


@dataclass(frozen=True)
class ReviewRetrievalSettings:
    """실리뷰 검색 설정.

    런타임은 로컬에 미리 생성된 인덱스와 모델 cache만 읽는다. 네트워크 다운로드는
    별도 build script의 책임이며, 준비되지 않은 경우 token baseline으로 명시적으로
    fallback한다.
    """

    mode: ReviewRetrievalMode = "semantic"
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_revision: str = DEFAULT_EMBEDDING_REVISION
    reranker_model: str = DEFAULT_RERANKER_MODEL
    reranker_revision: str = DEFAULT_RERANKER_REVISION
    index_dir: Path = (
        BACKEND_ROOT / "data" / "amazon_reviews_2023" / "tablet_catalog_v2"
    )
    bi_encoder_per_product: int = 5
    reranked_per_product: int = 3
    embedding_batch_size: int = 64
    reranker_batch_size: int = 32


def load_review_retrieval_settings() -> ReviewRetrievalSettings:
    _load_dotenv()
    mode_raw = _env_str("REVIEW_RETRIEVAL_MODE", "semantic").lower()
    if mode_raw not in ("token", "semantic"):
        raise ValueError(
            "REVIEW_RETRIEVAL_MODE must be 'token' or 'semantic'; "
            f"received {mode_raw!r}"
        )
    index_dir = Path(
        _env_str(
            "REVIEW_RETRIEVAL_INDEX_DIR",
            "data/amazon_reviews_2023/tablet_catalog_v2",
        )
    )
    if not index_dir.is_absolute():
        index_dir = BACKEND_ROOT / index_dir
    bi_encoder_per_product = _env_int("REVIEW_BI_ENCODER_PER_PRODUCT", 5)
    reranked_per_product = _env_int("REVIEW_RERANKED_PER_PRODUCT", 3)
    embedding_batch_size = _env_int("REVIEW_EMBEDDING_BATCH_SIZE", 64)
    reranker_batch_size = _env_int("REVIEW_RERANKER_BATCH_SIZE", 32)
    for name, value in (
        ("REVIEW_BI_ENCODER_PER_PRODUCT", bi_encoder_per_product),
        ("REVIEW_RERANKED_PER_PRODUCT", reranked_per_product),
        ("REVIEW_EMBEDDING_BATCH_SIZE", embedding_batch_size),
        ("REVIEW_RERANKER_BATCH_SIZE", reranker_batch_size),
    ):
        if value < 1:
            raise ValueError(f"{name} must be at least 1")
    if reranked_per_product > bi_encoder_per_product:
        raise ValueError(
            "REVIEW_RERANKED_PER_PRODUCT cannot exceed "
            "REVIEW_BI_ENCODER_PER_PRODUCT"
        )
    return ReviewRetrievalSettings(
        mode=mode_raw,  # type: ignore[arg-type]
        embedding_model=_env_str(
            "REVIEW_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL
        ),
        embedding_revision=_env_str(
            "REVIEW_EMBEDDING_REVISION", DEFAULT_EMBEDDING_REVISION
        ),
        reranker_model=_env_str("REVIEW_RERANKER_MODEL", DEFAULT_RERANKER_MODEL),
        reranker_revision=_env_str(
            "REVIEW_RERANKER_REVISION", DEFAULT_RERANKER_REVISION
        ),
        index_dir=index_dir.resolve(),
        bi_encoder_per_product=bi_encoder_per_product,
        reranked_per_product=reranked_per_product,
        embedding_batch_size=embedding_batch_size,
        reranker_batch_size=reranker_batch_size,
    )
