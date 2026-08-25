"""Real-data English workflow의 자유 입력 수직 슬라이스를 오프라인 검증한다."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.actual_service import ActualDemoService  # noqa: E402
from app.actual_workflow import ActualCatalogWorkflow  # noqa: E402
from app.api import create_app  # noqa: E402
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.llm.json_utils import to_strict_json_schema  # noqa: E402
from app.models.actual_demo import (  # noqa: E402
    ActualFacetCandidates,
    ActualItemActionCandidate,
    ActualRejectionReason,
    ActualStateUpdateCandidate,
    ActualTradeoffCandidate,
    ActualUnderstandingOutput,
)
from app.models.understanding import (  # noqa: E402
    CategoryTarget,
    ConstraintTarget,
    FacetTarget,
)
from app.nodes.actual_recommendation import (  # noqa: E402
    browse_actual_catalog,
    generate_actual_query,
)
from app.nodes.actual_policy import select_actual_policy  # noqa: E402
from app.nodes.actual_response import ActualTemplateResponseComposer  # noqa: E402
from app.nodes.actual_state_manager import update_actual_dialogue_state  # noqa: E402
from app.nodes.state_manager import create_initial_dialogue_state  # noqa: E402
from app.service import build_demo_test_service  # noqa: E402


def check(label: str, condition: bool, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def category() -> ActualStateUpdateCandidate:
    return ActualStateUpdateCandidate(
        target=CategoryTarget(kind="category"),
        canonical_id="category_tablet",
        value_text="tablet",
        evidence_text="tablet",
        origin="explicit",
        confidence=1,
    )


def constraint(
    canonical_id: str,
    value_text: str,
    *,
    scope: str,
    evidence_text: str,
) -> ActualStateUpdateCandidate:
    return ActualStateUpdateCandidate.model_validate(
        {
            "target": {"kind": "constraint", "scope": scope, "key": canonical_id},
            "canonical_id": canonical_id,
            "value_text": value_text,
            "evidence_text": evidence_text,
            "origin": "explicit",
            "confidence": 1,
        }
    )


def activity_note_taking() -> ActualStateUpdateCandidate:
    return ActualStateUpdateCandidate(
        target=FacetTarget(kind="facet", facet="activity"),
        canonical_id="activity_note_taking",
        value_text="taking class notes",
        evidence_text="note taking",
        origin="explicit",
        confidence=1,
    )


class SequenceUnderstandingProvider:
    def __init__(self, outputs: list[ActualUnderstandingOutput]) -> None:
        self.outputs = outputs
        self.index = 0

    async def __call__(self, **kwargs) -> ActualUnderstandingOutput:
        utterance = kwargs["utterance"]
        output = self.outputs[self.index].model_copy(deep=True)
        self.index += 1
        return output.model_copy(update={"utterance": utterance})


def understanding_sequence() -> list[ActualUnderstandingOutput]:
    activity = activity_note_taking()
    first_candidates = [
        category(),
        activity,
        constraint(
            "budget", "$300 maximum", scope="hard", evidence_text="under $300"
        ),
        constraint(
            "storage_capacity",
            "at least 64 GB storage",
            scope="hard",
            evidence_text="at least 64 GB",
        ),
        constraint(
            "note_taking",
            "note taking is important",
            scope="soft",
            evidence_text="note taking",
        ),
    ]
    return [
        ActualUnderstandingOutput(
            utterance="placeholder",
            intents=["search"],
            facets=ActualFacetCandidates(activity=activity),
            candidates=first_candidates,
            supersedes=[],
            residual_color_choice=False,
        ),
        ActualUnderstandingOutput(
            utterance="placeholder",
            intents=["refine"],
            facets=ActualFacetCandidates(),
            candidates=[
                constraint(
                    "battery",
                    "long battery life matters",
                    scope="soft",
                    evidence_text="Battery life matters",
                ),
                constraint(
                    "display",
                    "better display is important",
                    scope="soft",
                    evidence_text="better screen",
                ),
            ],
            tradeoff=ActualTradeoffCandidate(
                prioritized_ids=["battery", "display"],
                compromised_ids=["portability"],
                value_text="battery and display over lower weight",
                evidence_text="accept a little more weight for a better screen",
                origin="explicit",
                confidence=1,
            ),
            supersedes=[],
            residual_color_choice=False,
        ),
        ActualUnderstandingOutput(
            utterance="placeholder",
            intents=["reject"],
            facets=ActualFacetCandidates(),
            candidates=[
                constraint(
                    "portability",
                    "lighter weight preferred",
                    scope="soft",
                    evidence_text="too heavy",
                )
            ],
            item_action=ActualItemActionCandidate(
                name="reject_first",
                target_rank=2,
                compare_rank=None,
                rejection_reason=ActualRejectionReason(
                    canonical_id="reject_portability",
                    value_text="the referenced tablet is too heavy",
                    evidence_text="second result looks too heavy",
                    reason_type="product_attribute",
                ),
            ),
            supersedes=[],
            residual_color_choice=False,
        ),
        ActualUnderstandingOutput(
            utterance="placeholder",
            intents=["inspect"],
            facets=ActualFacetCandidates(),
            candidates=[],
            item_action=ActualItemActionCandidate(
                name="inspect_current",
                target_rank=1,
                compare_rank=None,
            ),
            supersedes=[],
            residual_color_choice=False,
        ),
        ActualUnderstandingOutput(
            utterance="placeholder",
            intents=["purchase"],
            facets=ActualFacetCandidates(),
            candidates=[],
            item_action=ActualItemActionCandidate(
                name="purchase_current",
                target_rank=None,
                compare_rank=None,
            ),
            supersedes=[],
            residual_color_choice=False,
        ),
    ]


def build_test_service() -> ActualDemoService:
    catalog = ExperimentalAmazonCatalog()
    provider = SequenceUnderstandingProvider(understanding_sequence())
    workflow = ActualCatalogWorkflow(
        catalog=catalog,
        understand=provider,
        response_composer=ActualTemplateResponseComposer(),
    )
    return ActualDemoService(workflow, catalog, llm_provider="test-sequence")


UTTERANCES = [
    "I'm looking for a tablet for note taking under $300 with at least 64 GB.",
    "Battery life matters, but I can accept a little more weight for a better screen.",
    "The second result looks too heavy. Remove it.",
    "Show me details for the first one.",
    "I'll choose this one.",
]


async def verify_workflow() -> None:
    service = build_test_service()
    snapshot = await service.create_conversation()
    turns = []
    for utterance in UTTERANCES:
        turns.append(await service.run_turn(snapshot.conversation_id, utterance))

    check("모든 자유 입력이 recommend lane", all(t.policy.lane == "recommend-lane" for t in turns))
    check("실제 catalog에서 세 장의 카드", all(len(t.final_response.product_cards) == 3 for t in turns))
    check(
        "상품 ID는 실제 parent_asin",
        all(
            card.product.parent_asin == card.ranking.product_id
            for turn in turns
            for card in turn.final_response.product_cards
        ),
    )
    check(
        "표시 리뷰와 evidenceReviewIds 일치",
        all(
            {review.review_id for review in card.evidence_reviews}
            == set(card.ranking.evidence_review_ids)
            for turn in turns
            for card in turn.final_response.product_cards
        ),
    )
    check(
        "실제 리뷰 source만 사용",
        all(
            review.source == "amazon_reviews_2023"
            for turn in turns
            for review in turn.final_response.review_evidence
        ),
    )
    rejected_expected = turns[1].rankings[1].product_id
    check(
        "2위 참조 거절을 현재 순위로 해결",
        turns[2].dialogue_state.rejected_items[-1].product_id == rejected_expected,
        rejected_expected,
    )
    check("명시적 trade-off 저장", bool(turns[1].dialogue_state.tradeoffs))
    check("상세 보기는 구매가 아님", not turns[3].dialogue_state.purchased_items)
    check(
        "명시적 구매만 purchased_items 기록",
        turns[4].dialogue_state.current_item in turns[4].dialogue_state.purchased_items,
    )
    targeted_purchase = ActualUnderstandingOutput(
        utterance="I'll take the second result.",
        intents=["purchase"],
        facets=ActualFacetCandidates(),
        candidates=[],
        item_action=ActualItemActionCandidate(
            name="purchase_current",
            target_rank=2,
            compare_rank=None,
        ),
        supersedes=[],
        residual_color_choice=False,
    )
    targeted_state, _ = update_actual_dialogue_state(
        turns[3].dialogue_state,
        targeted_purchase,
        turns[3].rankings,
        turn_id="targeted-purchase-check",
    )
    check(
        "명시적 구매 rank가 이전 current_item보다 우선",
        targeted_state.current_item == turns[3].rankings[1].product_id,
    )
    check("모든 발화 뒤 State Manager 실행", len(turns[-1].dialogue_state.preference_history) == 5)
    expected_nodes = {
        "spn-understanding",
        "ra-state-manager",
        "spn-policy",
        "ra-query-generator",
        "spn-browsing-actions",
        "ra-recommendation-engine",
        "spn-response-recommend",
    }
    check("explicit workflow node 경계", {node.node_id for node in turns[0].trace} == expected_nodes)
    browse_trace = next(
        node for node in turns[0].trace if node.node_id == "spn-browsing-actions"
    )
    check(
        "review retrieval provenance를 trace에 기록",
        browse_trace.output_summary["review_retrieval_method"] == "token"
        and browse_trace.output_summary["review_retrieval_fallback_reason"] is None,
    )
    await service.aclose()


def verify_api() -> None:
    actual_service = build_test_service()
    app = create_app(
        build_demo_test_service(),
        actual_service.catalog,
        actual_service,
    )
    with TestClient(app) as client:
        created = client.post("/api/actual/conversations")
        check("실데이터 대화 생성 API", created.status_code == 200, created.text[:200])
        payload = created.json()
        check("고정 시나리오 없이 catalog/API 정보 반환", "demo_scenario" not in payload)
        turn = client.post(
            f"/api/actual/conversations/{payload['conversation_id']}/turns",
            json={"utterance": UTTERANCES[0]},
        )
        check("실데이터 자유 입력 turn API", turn.status_code == 200, turn.text[:200])
        body = turn.json()
        check("실제 review retrieval 실행", body["browse_result"]["retrieved_review_count"] > 0)
        check(
            "review retrieval mode를 browse 응답에 기록",
            body["browse_result"]["review_retrieval_method"] == "token"
            and body["browse_result"]["review_retrieval_fallback_reason"] is None,
        )
        check(
            "전체 상품/리뷰를 browse 응답에 넣지 않음",
            "products" not in body["browse_result"]
            and "reviews" not in body["browse_result"],
        )


def verify_catalog_scope() -> None:
    catalog = ExperimentalAmazonCatalog()
    initial_state = create_initial_dialogue_state()
    initial_policy = select_actual_policy(initial_state)
    check(
        "지원 category 확인 전에는 항상 clarify",
        initial_policy.lane == "clarify-lane"
        and initial_policy.question_target is not None
        and initial_policy.question_target.field == "supported category",
    )
    query = generate_actual_query(initial_state)
    products, reviews, summary = browse_actual_catalog(
        query,
        catalog,
        rejected_product_ids=set(),
    )
    status = catalog.status()
    check(
        "hard filter가 없으면 실제 catalog 전체가 후보",
        len(products) == status.product_count == summary.candidate_product_count,
        len(products),
    )
    check(
        "전체 후보에서도 상품별 review top-k 제한",
        len(reviews) <= len(products) * 5,
        len(reviews),
    )


def verify_schema_guards() -> None:
    schema = to_strict_json_schema(ActualUnderstandingOutput)
    schema_text = str(schema)
    check("actual Understanding strict schema", schema["additionalProperties"] is False)
    check(
        "strict schema의 모든 최상위 필드 required",
        set(schema["required"]) == set(schema["properties"]),
    )
    check(
        "actual closed vocabulary가 schema에 포함",
        all(
            token in schema_text
            for token in ("category_tablet", "storage_capacity", "target_rank", "tradeoff")
        ),
    )
    try:
        constraint(
            "delivery_deadline",
            "tomorrow",
            scope="hard",
            evidence_text="tomorrow",
        )
    except Exception:
        pass
    else:
        raise AssertionError("실데이터에 없는 delivery_deadline이 허용됐습니다.")
    check("delivery/stock vocabulary 차단", True)
    try:
        ActualUnderstandingOutput(
            utterance="Buy the orange one",
            intents=["purchase"],
            facets=ActualFacetCandidates(),
            candidates=[],
            item_action=ActualItemActionCandidate(
                name="purchase_current",
                target_rank=1,
                compare_rank=None,
            ),
            supersedes=[],
            residual_color_choice=True,
        )
    except Exception:
        pass
    else:
        raise AssertionError("실데이터의 residual color가 허용됐습니다.")
    check("합성 color 선택 차단", True)


def main() -> None:
    verify_schema_guards()
    verify_catalog_scope()
    asyncio.run(verify_workflow())
    verify_api()
    print("\nActual-data English local demo 검증 통과")


if __name__ == "__main__":
    main()
