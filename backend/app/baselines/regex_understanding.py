"""reference 목업의 아이디어를 일반화한 정규식 Understanding 베이스라인.

이 코드는 현재 런타임 파서가 아니라 LLM의 효용을 비교하기 위한 고정 기준이다.
목업의 6턴 하드코딩 action 이름은 현행 ``UnderstandingOutput`` 계약으로 변환하고,
자유 생성 canonical ID는 만들지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.models import (
    FacetCandidates,
    ItemActionCandidate,
    RejectionReason,
    StateUpdateCandidate,
    UnderstandingOutput,
)


def _candidate(
    canonical_id: str,
    utterance: str,
    *,
    kind: str,
    origin: str = "explicit",
    confidence: float = 1.0,
    facet: str | None = None,
    scope: str | None = None,
    value_text: str | None = None,
) -> StateUpdateCandidate:
    if kind == "category":
        target: dict[str, Any] = {"kind": "category"}
    elif kind == "facet":
        target = {"kind": "facet", "facet": facet}
    else:
        target = {
            "kind": "constraint",
            "scope": scope,
            "key": canonical_id,
        }
    return StateUpdateCandidate.model_validate(
        {
            "target": target,
            "canonical_id": canonical_id,
            "value_text": value_text or canonical_id,
            "evidence_text": utterance,
            "origin": origin,
            "confidence": confidence,
        }
    )


def _rejection_action(text: str) -> ItemActionCandidate | None:
    product_ref = re.search(r"첫\s*번째|1번|이\s*제품|이건|그건|추천해\s*주신", text)
    rejection = re.search(r"안\s*돼|빼\s*주|제외|싫|아쉽|부족|별로|너무\s*비싸", text)
    if not product_ref or not rejection:
        return None

    rules = (
        (r"재고|품절|배송|수령|출고|입고", "reject_stock_delay", "situational_constraint"),
        (r"스피커|음질|사운드|소리|배터리", "reject_audio_battery", "product_attribute"),
        (r"저장|용량|\d+\s*gb", "reject_storage", "product_attribute"),
        (r"가격|예산|비싸", "reject_price", "product_attribute"),
        (r"화면|디스플레이", "reject_display", "product_attribute"),
        (r"성능|속도|게임", "reject_performance", "product_attribute"),
        (r"무게|무거|휴대", "reject_portability", "product_attribute"),
    )
    reason_id, reason_type = "reject_other", "product_attribute"
    for pattern, candidate_id, candidate_type in rules:
        if re.search(pattern, text, re.IGNORECASE):
            reason_id, reason_type = candidate_id, candidate_type
            break
    return ItemActionCandidate(
        name="reject_first",
        rejection_reason=RejectionReason(
            canonical_id=reason_id,
            value_text=reason_id,
            evidence_text=text,
            reason_type=reason_type,
        ),
    )


def _item_action(text: str) -> ItemActionCandidate | None:
    rejection = _rejection_action(text)
    if rejection:
        return rejection
    if re.search(r"첫\s*번째.*두\s*번째|1번.*2번", text) and "비교" in text:
        return ItemActionCandidate(name="compare_first_second")
    if re.search(r"이걸로|첫\s*번째(?:\s*제품)?(?:으)?로", text) and re.search(
        r"살게|구매|결제|주문", text
    ):
        return ItemActionCandidate(name="purchase_current")
    if re.search(r"추천해\s*주신|이\s*제품|이건|그럼", text) and re.search(
        r"자세히|상세|살펴", text
    ):
        return ItemActionCandidate(name="inspect_current")
    return None


def regex_understand_utterance(
    utterance: str,
    previous_state_summary: Mapping[str, Any] | None = None,
) -> UnderstandingOutput:
    """정규식과 고정 사전만으로 ``UnderstandingOutput``을 만든다."""
    text = re.sub(r"\s+", " ", utterance).strip()
    if not text:
        raise ValueError("utterance는 비어 있을 수 없습니다.")

    candidates: dict[str, StateUpdateCandidate] = {}

    def add(canonical_id: str, **kwargs: Any) -> None:
        candidates.setdefault(canonical_id, _candidate(canonical_id, text, **kwargs))

    category_rules = (
        (r"아이폰|iphone|스마트폰|휴대폰|핸드폰", "category_smartphone", "스마트폰"),
        (r"태블릿|아이패드|ipad", "category_tablet", "태블릿"),
        (r"노트북|랩톱|laptop", "category_laptop", "노트북"),
        (r"헤드폰|헤드셋|이어폰|에어팟", "category_headphones", "헤드폰"),
    )
    for pattern, canonical_id, value_text in category_rules:
        if re.search(pattern, text, re.IGNORECASE):
            add(canonical_id, kind="category", value_text=value_text)
            break

    device_broken = bool(re.search(r"고장|파손|망가|깨졌|먹통|전원이\s*안", text))
    has_deadline = bool(re.search(r"\d+\s*(?:일|주).*받|이번\s*주|당장|지금\s*쓸.*없", text))
    if device_broken:
        add("event_device_failure", kind="facet", facet="event", value_text="기존 기기 고장")
        add(
            "goal_replace_device",
            kind="facet",
            facet="goal_purpose",
            origin="implicit",
            confidence=0.9,
            value_text="고장 난 기기 교체",
        )
        if not has_deadline:
            add(
                "urgency_pressure",
                kind="constraint",
                scope="soft",
                origin="inferred",
                confidence=0.64,
                value_text="수령 대기 민감 가능성",
            )
    elif re.search(r"처음\s*(?:사|구매)", text):
        add("event_first_purchase", kind="facet", facet="event", value_text="첫 구매")
    elif re.search(r"선물", text):
        add("event_gift", kind="facet", facet="event", value_text="선물 구매")
    elif re.search(r"교체|바꾸", text):
        add("event_replacement", kind="facet", facet="event", value_text="기기 교체")

    if re.search(r"아이(?!폰|패드)|자녀|초등|중학생|고등학생", text):
        add("audience_child", kind="facet", facet="goal_audience")
    if re.search(r"필기|노트\s*정리", text):
        add("activity_note_taking", kind="facet", facet="activity")
        add("note_taking", kind="constraint", scope="soft")
    elif re.search(r"게임", text):
        add("activity_gaming", kind="facet", facet="activity")
    elif re.search(r"영상|동영상|넷플릭스|유튜브", text):
        add("activity_video", kind="facet", facet="activity")
    elif re.search(r"독서|전자책|책\s*읽", text):
        add("activity_reading", kind="facet", facet="activity")

    if re.search(r"업무|일할|공부|학업|학교", text):
        add("goal_work_study", kind="facet", facet="goal_purpose", value_text="업무·학업용")
    elif re.search(r"오래\s*(?:쓸|쓰|사용)|장기\s*사용|몇\s*년", text):
        add("goal_long_term_use", kind="facet", facet="goal_purpose", value_text="장기 사용")

    if re.search(r"오래\s*(?:쓸|쓰|사용)|장기\s*사용|몇\s*년", text):
        add("subjective_longevity", kind="facet", facet="subjective_property", value_text="오래 쓸 수 있는 제품")
        add("longevity_value", kind="constraint", scope="soft", value_text="장기 사용 가치 중시")
    elif re.search(r"가벼|휴대|들고\s*다|무겁|무거운|무거워", text):
        add("subjective_portability", kind="facet", facet="subjective_property")
    elif re.search(r"화면.*(?:선명|좋)|디스플레이.*(?:선명|좋)", text):
        add("subjective_display_quality", kind="facet", facet="subjective_property")
    elif re.search(r"음질.*(?:좋|중요)|소리.*(?:좋|중요)", text):
        add("subjective_audio_quality", kind="facet", facet="subjective_property")
    elif re.search(r"튼튼|내구|잘\s*버티", text):
        add("subjective_durability", kind="facet", facet="subjective_property")
    elif re.search(r"성능.*(?:좋|뛰어나|최우선|가장\s*중요)", text):
        add("subjective_performance", kind="facet", facet="subjective_property")

    budget_matches = list(re.finditer(r"(\d+(?:\.\d+)?)\s*만\s*원?", text))
    if budget_matches:
        budget_won = round(float(budget_matches[-1].group(1)) * 10_000)
        add("budget", kind="constraint", scope="hard", value_text=f"{budget_won:,}원 이하")
    if re.search(r"비싸도|더\s*써도|더\s*줘도|넘겨도|초과해도", text) and re.search(
        r"괜찮|상관\s*없|가능|의향", text
    ):
        add("budget_flexibility", kind="constraint", scope="soft", value_text="장기 사용 가치가 있으면 예산 상한 초과 수용")
    if has_deadline and re.search(r"받|배송|수령|지금\s*쓸.*없", text):
        add(
            "delivery_deadline",
            kind="constraint",
            scope="hard",
            origin="inferred" if "지금" in text else "explicit",
            confidence=0.86 if "지금" in text else 1.0,
            value_text="3일 이내 수령" if "지금" in text else "명시된 기한 내 수령",
        )

    storage_match = re.search(r"\d+\s*(?:gb|기가)|저장\s*공간|용량", text, re.IGNORECASE)
    if storage_match and re.search(r"이상|필요|부족|적어도", text):
        storage_values = re.findall(r"(\d+)\s*(?:gb|기가)", text, re.IGNORECASE)
        storage_text = f"{storage_values[-1]}GB 이상" if storage_values else "필요 저장 공간 이상"
        add("storage_capacity", kind="constraint", scope="hard", value_text=storage_text)
    if re.search(r"배터리", text) and re.search(r"중요|길|하루|아쉽|짧|부족", text):
        add(
            "battery",
            kind="constraint",
            scope="soft",
            origin="implicit" if "후기" in text else "explicit",
            confidence=0.85 if "후기" in text else 1.0,
            value_text="배터리 지속 시간 중시",
        )
    if re.search(r"스피커|음질|사운드|소리", text) and re.search(
        r"중요|좋|아쉽|별로|부족", text
    ):
        add(
            "audio",
            kind="constraint",
            scope="soft",
            origin="implicit" if "후기" in text else "explicit",
            confidence=0.85 if "후기" in text else 1.0,
            value_text="스피커·음질 중시",
        )
    if re.search(r"가벼|휴대|들고\s*다|무겁|무거운|무거워", text):
        add("portability", kind="constraint", scope="soft")
    if re.search(r"화면|디스플레이", text) and re.search(r"중요|선명|좋", text):
        add("display", kind="constraint", scope="soft")
    if re.search(r"튼튼|내구|잘\s*버티", text):
        add("durability", kind="constraint", scope="soft")
    if re.search(r"성능", text) and re.search(r"중요|좋|최우선|부족", text):
        add("performance", kind="constraint", scope="soft")

    action = _item_action(text)
    is_rejection = action is not None and action.name == "reject_first"
    if not is_rejection and re.search(r"후기|리뷰", text) and re.search(
        r"많|믿|신뢰|중요", text
    ):
        add("review_signal", kind="constraint", scope="soft")

    action_to_intent = {
        "inspect_current": "inspect",
        "reject_first": "reject",
        "compare_first_second": "compare",
        "purchase_current": "purchase",
    }
    if action:
        intents = [action_to_intent[action.name]]
    elif device_broken or any(
        candidate_id.startswith("category_") for candidate_id in candidates
    ):
        intents = ["search"]
    elif candidates:
        intents = ["refine"]
    elif re.search(r"처음\s*(?:사|구매)|추천|찾", text):
        intents = ["search"]
    else:
        intents = ["unknown"]

    facet_values: dict[str, StateUpdateCandidate | None] = {
        "subjective_property": None,
        "event": None,
        "activity": None,
        "goal_purpose": None,
        "goal_audience": None,
    }
    for candidate in candidates.values():
        if candidate.target.kind == "facet":
            facet_values[candidate.target.facet] = candidate

    previous = previous_state_summary or {}
    soft_constraints = previous.get("soft_constraints", {})
    supersedes = (
        ["urgency_pressure"]
        if "delivery_deadline" in candidates and "urgency_pressure" in soft_constraints
        else []
    )
    residual_color_choice = bool(
        action
        and action.name == "purchase_current"
        and re.search(r"색상|컬러", text)
        and re.search(r"뿐|밖에|남은|고를\s*수\s*없", text)
    )

    return UnderstandingOutput(
        utterance=text,
        intents=intents,
        facets=FacetCandidates.model_validate(facet_values),
        candidates=list(candidates.values()),
        item_action=action,
        supersedes=supersedes,
        residual_color_choice=residual_color_choice,
    )
