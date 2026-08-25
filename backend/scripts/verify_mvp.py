"""LangGraph MVP의 6턴 회귀와 FastAPI 수직 슬라이스를 검증한다."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.api import DEMO_SCENARIO, create_app  # noqa: E402
from app.service import build_demo_test_service  # noqa: E402


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}" + (f" - {detail}" if detail else ""))
    if not condition:
        raise SystemExit(1)


async def verify_six_turns() -> None:
    service = build_demo_test_service()
    snapshot = await service.create_conversation()
    turns = []
    for utterance in DEMO_SCENARIO:
        turn = await service.run_turn(snapshot.conversation_id, utterance)
        turns.append(turn)
        top = turn.rankings[0].product_id if turn.rankings else "clarify"
        print(
            f"  {turn.turn_id}: {turn.policy.lane} / top={top} / "
            f"diff={','.join(turn.state_diff.changed_paths)}"
        )

    lanes = [turn.policy.lane for turn in turns]
    check(
        "1턴만 clarify, 이후 recommend",
        lanes == ["clarify-lane", *("recommend-lane" for _ in range(5))],
        str(lanes),
    )
    check("clarify lane은 query를 만들지 않음", turns[0].query is None)
    check(
        "recommend lane은 Query/Browse/Rank 실행",
        all(
            turn.query is not None
            and turn.browse_result is not None
            and turn.recommendation is not None
            for turn in turns[1:]
        ),
    )
    top_products = [
        turn.rankings[0].product_id if turn.rankings else None for turn in turns
    ]
    check(
        "일반 속성 점수로 6턴 순위 회귀",
        top_products
        == [
            None,
            "iphone-17",
            "iphone-air",
            "iphone-17-pro",
            "iphone-17-pro",
            "iphone-17-pro",
        ],
        str(top_products),
    )

    actions = [
        turn.understanding.item_action.name
        if turn.understanding.item_action
        else None
        for turn in turns
    ]
    check(
        "6턴 행동 계약",
        actions
        == [None, None, "reject_first", "reject_first", "inspect_current", "purchase_current"],
        str(actions),
    )

    state_after_inspect = turns[4].dialogue_state
    check(
        "상세 보기는 구매가 아님",
        bool(state_after_inspect.inspected_items)
        and not state_after_inspect.purchased_items,
    )
    final_state = turns[-1].dialogue_state
    check(
        "구매 확정만 purchased_items 기록",
        final_state.current_item in final_state.purchased_items,
        str(final_state.purchased_items),
    )
    check(
        "잔여 색상은 지속 색상 선호가 아닌 상황으로 저장",
        "color_residual" in final_state.soft_constraints
        and "선호가 아니라" in final_state.soft_constraints["color_residual"].value_text,
    )
    check("구매 trade-off 기록", bool(final_state.tradeoffs))
    check(
        "거절 이유 유형 분리",
        {item.reason_type for item in final_state.rejected_items}
        == {"situational_constraint", "product_attribute"},
    )
    check(
        "모든 사용자 발화 뒤 State Manager 실행",
        len(final_state.preference_history) == 6,
    )

    for turn in turns[1:]:
        for card in turn.final_response.product_cards:
            visible_ids = {review.id for review in card.evidence_reviews}
            check(
                f"{turn.turn_id} {card.product.id} 리뷰 근거 일치",
                visible_ids == set(card.ranking.evidence_review_ids),
            )
        check(
            f"{turn.turn_id} RA explanation과 SPN message 분리",
            turn.recommendation is not None
            and turn.recommendation.explanation != turn.final_response.message,
        )
        check(
            f"{turn.turn_id} unmapped ID 없음",
            turn.recommendation is not None
            and not turn.recommendation.unmapped_preference_ids,
        )

    expected_nodes = {
        "spn-understanding",
        "ra-state-manager",
        "spn-policy",
    }
    check(
        "clarify trace는 clarify RESPOND만 실행",
        {node.node_id for node in turns[0].trace}
        == {*expected_nodes, "spn-response-clarify"},
    )
    check(
        "recommend trace는 검색·랭킹 RESPOND 실행",
        {node.node_id for node in turns[1].trace}
        == {
            *expected_nodes,
            "ra-query-generator",
            "spn-browsing-actions",
            "ra-recommendation-engine",
            "spn-response-recommend",
        },
    )
    await service.aclose()


def verify_api() -> None:
    app = create_app(build_demo_test_service())
    with TestClient(app) as client:
        health = client.get("/health")
        check("FastAPI health", health.status_code == 200)
        created = client.post("/api/conversations")
        check("대화 생성 API", created.status_code == 200)
        payload = created.json()
        conversation_id = payload["conversation_id"]
        turn = client.post(
            f"/api/conversations/{conversation_id}/turns",
            json={"utterance": DEMO_SCENARIO[0]},
        )
        check("턴 실행 API", turn.status_code == 200, turn.text[:200])
        check(
            "API가 clarify lane 반환",
            turn.json()["policy"]["lane"] == "clarify-lane",
        )
        snapshot = client.get(f"/api/conversations/{conversation_id}")
        check(
            "대화 snapshot API",
            snapshot.status_code == 200 and len(snapshot.json()["turns"]) == 1,
        )
        index = client.get("/")
        check(
            "MVP UI 제공",
            index.status_code == 200 and "Decision Memory Lab" in index.text,
        )
        deleted = client.delete(f"/api/conversations/{conversation_id}")
        check("대화 삭제 API", deleted.status_code == 204)
        missing = client.get(f"/api/conversations/{conversation_id}")
        check("삭제한 대화는 조회 불가", missing.status_code == 404)


def main() -> None:
    asyncio.run(verify_six_turns())
    verify_api()
    print("\nLangGraph + FastAPI MVP 검증 통과")


if __name__ == "__main__":
    main()
