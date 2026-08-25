"""SPN의 서로 다른 clarify/recommend RESPOND 노드."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from app.llm import LLMClient, system, user
from app.models import (
    BrowseResult,
    DialogueState,
    FinalResponse,
    PolicyDecision,
    Product,
    ProductCard,
    RecommendationResponse,
    ResponseDraft,
    UnderstandingOutput,
)

CLARIFY_PROMPT_VERSION = "spn-response-clarify-v1"
RECOMMEND_PROMPT_VERSION = "spn-response-recommend-v1"


@dataclass(frozen=True)
class ComposeResult:
    draft: ResponseDraft
    source: str
    fallback_error: str | None = None


class ResponseComposer(Protocol):
    async def clarify(
        self,
        policy: PolicyDecision,
        state: DialogueState,
        *,
        conversation_id: str,
        turn: int,
    ) -> ComposeResult: ...

    async def recommend(
        self,
        recommendation: RecommendationResponse,
        browse_result: BrowseResult,
        understanding: UnderstandingOutput,
        products: list[Product],
        *,
        conversation_id: str,
        turn: int,
    ) -> ComposeResult: ...


class TemplateResponseComposer:
    """LLM 장애 fallback 및 결정론적 회귀 테스트용 문장 생성기."""

    async def clarify(
        self,
        policy: PolicyDecision,
        state: DialogueState,
        *,
        conversation_id: str,
        turn: int,
    ) -> ComposeResult:
        del state, conversation_id, turn
        target = policy.question_target.field if policy.question_target else "선호 기준"
        messages = {
            "가격 기준": "교체가 필요한 상황이라면 후보를 빨리 좁히는 게 좋겠어요. 예산은 어느 정도로 생각하고 계신가요?",
            "사용 기간 기준": "새 기기를 얼마나 오래 쓸 계획인가요? 오래 쓸 계획이면 성능과 지원 기간에 더 비중을 둘 수 있어요.",
            "수령 시점": "언제까지 받아야 하는지도 알려주시겠어요? 모델마다 재고 상황이 달라 수령 시간이 크게 차이 납니다.",
        }
        return ComposeResult(
            draft=ResponseDraft(
                message=messages.get(target, "어떤 기준을 가장 중요하게 보시는지 알려주세요."),
                next_actions=["예산 말하기", "사용 기간 말하기", "수령 기한 말하기"],
            ),
            source="template",
        )

    async def recommend(
        self,
        recommendation: RecommendationResponse,
        browse_result: BrowseResult,
        understanding: UnderstandingOutput,
        products: list[Product],
        *,
        conversation_id: str,
        turn: int,
    ) -> ComposeResult:
        del browse_result, conversation_id, turn
        top = recommendation.ranked_products[0] if recommendation.ranked_products else None
        product = next(
            (item for item in products if top and item.id == top.product_id),
            None,
        )
        action = understanding.item_action.name if understanding.item_action else None
        if product is None:
            message = "현재 조건으로 추천할 후보를 찾지 못했습니다. 조건을 조금 조정해 주세요."
        elif action == "inspect_current":
            colors = ", ".join(product.metadata.available_colors) or "현재 없음"
            message = (
                f"{product.title} 상세입니다. {product.metadata.display}, "
                f"{product.metadata.chipset}, {product.metadata.storage}, "
                f"무게 {product.metadata.weight_grams}g이며 즉시 수령 색상은 {colors}입니다."
            )
        elif action == "purchase_current":
            message = (
                f"{product.title}을 구매 제품으로 기록했습니다. 이번 선택의 수령 조건과 "
                "양보한 색상 조건도 선호 상태와 분리해 남겼습니다."
            )
        elif action == "reject_first":
            message = (
                f"거절 이유를 반영해 다시 계산했습니다. 현재는 {product.title}이 "
                f"{top.score.total}점으로 가장 적합합니다."
            )
        elif action == "compare_first_second":
            message = (
                f"상위 두 후보를 비교 목록에 담았습니다. 현재 1위는 {product.title}이며 "
                "가격·배송·리뷰 근거를 카드에서 비교할 수 있습니다."
            )
        else:
            message = (
                f"현재 조건에서는 {product.title}을 우선 추천합니다. "
                f"총점은 {top.score.total}점이며 가격은 {product.price:,}원입니다."
            )
        return ComposeResult(
            draft=ResponseDraft(
                message=message,
                next_actions=["첫 번째 제품 자세히 보기", "상위 두 제품 비교", "조건 조정"],
            ),
            source="template",
        )


class LLMResponseComposer:
    """Luxia strict output을 사용하고 장애 시 계측 가능한 template로 복구한다."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self.fallback = TemplateResponseComposer()

    async def clarify(
        self,
        policy: PolicyDecision,
        state: DialogueState,
        *,
        conversation_id: str,
        turn: int,
    ) -> ComposeResult:
        payload = {
            "question_target": policy.question_target.model_dump(mode="json")
            if policy.question_target
            else None,
            "vagueness": policy.snapshot.vagueness.model_dump(mode="json"),
            "known_category": state.category.value_text if state.category else None,
        }
        try:
            draft = await self.client.generate_structured(
                messages=[
                    system(
                        "You are the SPN clarify response composer. Write one concise, "
                        "empathetic Korean follow-up question for the given target. Do not "
                        "recommend products and do not invent facts. next_actions must be "
                        "three short user reply suggestions."
                    ),
                    user(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
                ],
                response_model=ResponseDraft,
                temperature=0.2,
                node="spn-response-clarify",
                prompt_version=CLARIFY_PROMPT_VERSION,
                conversation_id=conversation_id,
                turn=turn,
            )
            return ComposeResult(draft=draft, source="llm")
        except Exception as exc:
            fallback = await self.fallback.clarify(
                policy,
                state,
                conversation_id=conversation_id,
                turn=turn,
            )
            return ComposeResult(
                draft=fallback.draft,
                source="template-fallback",
                fallback_error=type(exc).__name__,
            )

    async def recommend(
        self,
        recommendation: RecommendationResponse,
        browse_result: BrowseResult,
        understanding: UnderstandingOutput,
        products: list[Product],
        *,
        conversation_id: str,
        turn: int,
    ) -> ComposeResult:
        product_by_id = {product.id: product for product in products}
        top_products = []
        for ranking in recommendation.ranked_products[:3]:
            product = product_by_id.get(ranking.product_id)
            if product:
                top_products.append(
                    {
                        "title": product.title,
                        "price": product.price,
                        "shipping": product.shipping,
                        "stock": product.stock,
                        "available_colors": product.metadata.available_colors,
                        "score": ranking.score.model_dump(mode="json"),
                    }
                )
        payload = {
            "item_action": understanding.item_action.model_dump(mode="json")
            if understanding.item_action
            else None,
            "top_products": top_products,
            "ra_rec_explanation": recommendation.explanation,
            "inventory_disclosure": browse_result.inventory_disclosure,
        }
        try:
            draft = await self.client.generate_structured(
                messages=[
                    system(
                        "You are the SPN recommendation response composer. Write two or "
                        "three concise Korean sentences grounded only in the supplied facts. "
                        "Explain the current result and reflect the item action. Do not copy "
                        "ra_rec_explanation verbatim, invent specs, or claim synthetic "
                        "inventory is live commerce data. next_actions are short UI actions."
                    ),
                    user(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
                ],
                response_model=ResponseDraft,
                temperature=0.2,
                node="spn-response-recommend",
                prompt_version=RECOMMEND_PROMPT_VERSION,
                conversation_id=conversation_id,
                turn=turn,
            )
            return ComposeResult(draft=draft, source="llm")
        except Exception as exc:
            fallback = await self.fallback.recommend(
                recommendation,
                browse_result,
                understanding,
                products,
                conversation_id=conversation_id,
                turn=turn,
            )
            return ComposeResult(
                draft=fallback.draft,
                source="template-fallback",
                fallback_error=type(exc).__name__,
            )


def build_clarify_final_response(result: ComposeResult) -> FinalResponse:
    return FinalResponse(
        message=result.draft.message,
        product_cards=[],
        price_evidence=[],
        delivery_evidence=[],
        image_evidence=[],
        review_evidence=[],
        next_actions=result.draft.next_actions,
    )


def build_recommend_final_response(
    result: ComposeResult,
    recommendation: RecommendationResponse,
    browse_result: BrowseResult,
) -> FinalResponse:
    product_by_id = {product.id: product for product in browse_result.products}
    review_by_id = {review.id: review for review in recommendation.review_evidence}
    cards: list[ProductCard] = []
    for ranking in recommendation.ranked_products[:3]:
        product = product_by_id.get(ranking.product_id)
        if product is None:
            continue
        card_reviews = [
            review_by_id[review_id]
            for review_id in ranking.evidence_review_ids
            if review_id in review_by_id
        ]
        cards.append(
            ProductCard(
                product=product,
                ranking=ranking,
                evidence_reviews=card_reviews,
            )
        )
    used_review_ids = {
        review.id for card in cards for review in card.evidence_reviews
    }
    visible_reviews = [
        review
        for review in recommendation.review_evidence
        if review.id in used_review_ids
    ]
    message = result.draft.message
    if message.strip() == recommendation.explanation.strip():
        message = f"추천 결과를 정리하면, {message}"
    return FinalResponse(
        message=message,
        product_cards=cards,
        price_evidence=browse_result.price_evidence,
        delivery_evidence=browse_result.delivery_evidence,
        image_evidence=browse_result.images,
        review_evidence=visible_reviews,
        next_actions=result.draft.next_actions,
    )
