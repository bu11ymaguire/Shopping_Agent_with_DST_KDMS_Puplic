"""FastAPI entrypoint for the KDMS MVP."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.actual_service import ActualDemoService, build_actual_runtime_service
from app.config import load_llm_settings
from app.models import ConversationSnapshot, DialogueState, PipelineTurn, Product
from app.experimental_catalog import (
    ExperimentalAmazonCatalog,
    ExperimentalCatalogUnavailableError,
    ExperimentalProductNotFoundError,
)
from app.models.experimental import (
    ExperimentalCatalogStatus,
    ExperimentalProductDetail,
    ExperimentalProductSearchResponse,
    ExperimentalProductSort,
    ExperimentalReviewSearchResponse,
    SentimentBucket,
)
from app.models.actual_demo import ActualConversationSnapshot, ActualPipelineTurn
from app.service import (
    ConversationNotFoundError,
    MVPService,
    build_runtime_service,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

DEMO_SCENARIO = [
    "쓰던 아이폰이 고장 나서 새로 바꿔야 해요.",
    "150만 원 정도 생각하는데, 오래 쓸 거면 조금 더 써도 괜찮아요.",
    "첫 번째 제품은 재고가 없어서 2주 뒤에나 받는대요. 지금 쓸 폰이 없어서 그건 안 돼요.",
    "이건 스피커랑 배터리가 아쉽다는 후기가 많네요.",
    "그럼 추천해주신 제품을 자세히 볼게요.",
    "색상은 지금 받을 수 있는 게 코스믹 오렌지뿐이네요. 그럼 이걸로 살게요.",
]


class APIContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnRequest(APIContract):
    utterance: str = Field(min_length=1, max_length=2000)


class ConversationCreated(APIContract):
    conversation_id: str
    dialogue_state: DialogueState
    demo_scenario: list[str]


class CatalogResponse(APIContract):
    version: str
    disclosure: str
    products: list[Product]


class ActualConversationCreated(APIContract):
    conversation_id: str
    dialogue_state: DialogueState
    llm_provider: str
    catalog: ExperimentalCatalogStatus


def create_app(
    injected_service: MVPService | None = None,
    injected_experimental_catalog: ExperimentalAmazonCatalog | None = None,
    injected_actual_service: ActualDemoService | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = injected_service or build_runtime_service()
        experimental_catalog = (
            injected_experimental_catalog or ExperimentalAmazonCatalog()
        )
        actual_service = injected_actual_service
        owns_actual_service = False
        if (
            actual_service is None
            and injected_service is None
            and experimental_catalog.available
            and load_llm_settings().provider == "luxia"
        ):
            actual_service = build_actual_runtime_service(experimental_catalog)
            owns_actual_service = True
        app.state.mvp_service = service
        app.state.experimental_catalog = experimental_catalog
        app.state.actual_service = actual_service
        try:
            yield
        finally:
            if injected_service is None:
                await service.aclose()
            if owns_actual_service and actual_service is not None:
                await actual_service.aclose()

    app = FastAPI(
        title="KDMS SPN + RA-Rec MVP",
        version="0.1.0",
        lifespan=lifespan,
    )

    def service_from(request: Request) -> MVPService:
        return request.app.state.mvp_service

    def experimental_from(request: Request) -> ExperimentalAmazonCatalog:
        return request.app.state.experimental_catalog

    def actual_from(request: Request) -> ActualDemoService:
        service = request.app.state.actual_service
        if service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The live actual-data service is not enabled in this test app.",
            )
        return service

    def experimental_error(exc: Exception) -> HTTPException:
        if isinstance(exc, ExperimentalProductNotFoundError):
            return HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"실데이터 상품을 찾을 수 없습니다: {exc.args[0]}",
            )
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    def not_found(exc: ConversationNotFoundError) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"대화를 찾을 수 없습니다: {exc.args[0]}",
        )

    @app.get("/health")
    async def health(request: Request) -> dict[str, str | bool]:
        return {
            "status": "ok",
            "app": "kdms-mvp",
            "actual_catalog_available": experimental_from(request).available,
            "actual_demo_enabled": request.app.state.actual_service is not None,
        }

    @app.post(
        "/api/actual/conversations",
        response_model=ActualConversationCreated,
    )
    async def create_actual_conversation(request: Request) -> ActualConversationCreated:
        service = actual_from(request)
        snapshot = await service.create_conversation()
        return ActualConversationCreated(
            conversation_id=snapshot.conversation_id,
            dialogue_state=snapshot.dialogue_state,
            llm_provider=service.llm_provider,
            catalog=service.catalog.status(),
        )

    @app.get(
        "/api/actual/conversations/{conversation_id}",
        response_model=ActualConversationSnapshot,
    )
    async def get_actual_conversation(
        conversation_id: str, request: Request
    ) -> ActualConversationSnapshot:
        try:
            return await actual_from(request).get_conversation(conversation_id)
        except ConversationNotFoundError as exc:
            raise not_found(exc) from exc

    @app.post(
        "/api/actual/conversations/{conversation_id}/turns",
        response_model=ActualPipelineTurn,
    )
    async def run_actual_turn(
        conversation_id: str,
        payload: TurnRequest,
        request: Request,
    ) -> ActualPipelineTurn:
        try:
            return await actual_from(request).run_turn(
                conversation_id, payload.utterance
            )
        except ConversationNotFoundError as exc:
            raise not_found(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    @app.delete(
        "/api/actual/conversations/{conversation_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_actual_conversation(
        conversation_id: str, request: Request
    ) -> None:
        try:
            await actual_from(request).delete_conversation(conversation_id)
        except ConversationNotFoundError as exc:
            raise not_found(exc) from exc

    @app.post("/api/conversations", response_model=ConversationCreated)
    async def create_conversation(request: Request) -> ConversationCreated:
        snapshot = await service_from(request).create_conversation()
        return ConversationCreated(
            conversation_id=snapshot.conversation_id,
            dialogue_state=snapshot.dialogue_state,
            demo_scenario=DEMO_SCENARIO,
        )

    @app.get(
        "/api/conversations/{conversation_id}",
        response_model=ConversationSnapshot,
    )
    async def get_conversation(
        conversation_id: str, request: Request
    ) -> ConversationSnapshot:
        try:
            return await service_from(request).get_conversation(conversation_id)
        except ConversationNotFoundError as exc:
            raise not_found(exc) from exc

    @app.post(
        "/api/conversations/{conversation_id}/turns",
        response_model=PipelineTurn,
    )
    async def run_turn(
        conversation_id: str,
        payload: TurnRequest,
        request: Request,
    ) -> PipelineTurn:
        try:
            return await service_from(request).run_turn(
                conversation_id, payload.utterance
            )
        except ConversationNotFoundError as exc:
            raise not_found(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

    @app.delete(
        "/api/conversations/{conversation_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_conversation(conversation_id: str, request: Request) -> None:
        try:
            await service_from(request).delete_conversation(conversation_id)
        except ConversationNotFoundError as exc:
            raise not_found(exc) from exc

    @app.get("/api/catalog", response_model=CatalogResponse)
    async def catalog(request: Request) -> CatalogResponse:
        current = service_from(request).catalog
        return CatalogResponse(
            version=current.version,
            disclosure=current.disclosure,
            products=list(current.products),
        )

    @app.get(
        "/api/experimental/catalog/status",
        response_model=ExperimentalCatalogStatus,
    )
    def experimental_catalog_status(request: Request) -> ExperimentalCatalogStatus:
        return experimental_from(request).status()

    @app.get(
        "/api/experimental/catalog/products",
        response_model=ExperimentalProductSearchResponse,
    )
    def experimental_products(
        request: Request,
        q: str | None = Query(default=None, max_length=200),
        max_price_usd: float | None = Query(default=None, gt=0),
        min_storage_gb: int | None = Query(default=None, ge=0),
        min_memory_gb: int | None = Query(default=None, ge=0),
        max_weight_grams: int | None = Query(default=None, gt=0),
        min_rating: float | None = Query(default=None, ge=0, le=5),
        min_screen_inches: float | None = Query(default=None, gt=0),
        operating_system: str | None = Query(default=None, max_length=40),
        stylus: bool | None = None,
        sort_by: ExperimentalProductSort = "review_count",
        limit: int = Query(default=20, ge=1, le=50),
        offset: int = Query(default=0, ge=0, le=10000),
    ) -> ExperimentalProductSearchResponse:
        try:
            return experimental_from(request).search_products(
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
                limit=limit,
                offset=offset,
            )
        except ExperimentalCatalogUnavailableError as exc:
            raise experimental_error(exc) from exc

    @app.get(
        "/api/experimental/catalog/products/{parent_asin}",
        response_model=ExperimentalProductDetail,
    )
    def experimental_product(
        parent_asin: str,
        request: Request,
    ) -> ExperimentalProductDetail:
        try:
            return experimental_from(request).get_product(parent_asin)
        except (
            ExperimentalCatalogUnavailableError,
            ExperimentalProductNotFoundError,
        ) as exc:
            raise experimental_error(exc) from exc

    @app.get(
        "/api/experimental/catalog/products/{parent_asin}/reviews",
        response_model=ExperimentalReviewSearchResponse,
    )
    def experimental_reviews(
        parent_asin: str,
        request: Request,
        q: str | None = Query(default=None, max_length=200),
        sentiment: SentimentBucket | None = None,
        verified_only: bool = False,
        limit: int = Query(default=10, ge=1, le=50),
    ) -> ExperimentalReviewSearchResponse:
        try:
            return experimental_from(request).search_reviews(
                parent_asin,
                q=q,
                sentiment=sentiment,
                verified_only=verified_only,
                limit=limit,
            )
        except (
            ExperimentalCatalogUnavailableError,
            ExperimentalProductNotFoundError,
        ) as exc:
            raise experimental_error(exc) from exc

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
