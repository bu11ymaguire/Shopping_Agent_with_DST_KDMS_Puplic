"""SPN Understanding(PLAN) 노드.

이 노드는 발화와 이전 상태 요약을 읽고 ``UnderstandingOutput`` 후보만 만든다.
상태 병합, status 전이, 상품 ID 해석은 절대 수행하지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.llm import LLMClient, system, user
from app.models import UnderstandingOutput

UNDERSTANDING_PROMPT_VERSION = "spn-understanding-v1.3"

SYSTEM_PROMPT = """You are the SPN Understanding (PLAN) node in a shopping workflow.
Extract structured state-update candidates from one Korean user utterance.

Boundaries:
- Do not merge or mutate dialogue state. Return candidates only.
- Candidates are NEW evidence from the current utterance, not a snapshot of all known
  state. Never copy a category, facet, constraint, item, event, or goal merely because
  it appears in previous_state_summary. Do not re-emit an already confirmed value
  unless the current utterance repeats it to change or correct it.
- Use previous_state_summary only to interpret symbolic item actions, avoid duplicate
  state, and decide whether a new confirmed constraint supersedes an old unconfirmed
  hypothesis.
- Use only canonical_id values allowed by the response schema.
- value_text is a concise Korean normalization; evidence_text is the supporting span.
- Mark origin as explicit, implicit, or inferred. Do not present an inference as fact.
- Every non-null facets field must have the same candidate in candidates.
- A reference such as 'first product' remains the symbolic item_action in the schema;
  do not invent or resolve a product ID.
- inspect_current is inspection, never purchase.
- A review mentioned as the reason for rejecting an item is product evidence, not
  review_signal. review_signal means a general preference to trust review evidence.
- residual_color_choice is true only when the user purchases the current item while
  accepting the sole immediately available color. Do not extract the color name as a
  persistent preference.
- Rejecting because of stock/delivery is a situational_constraint. Rejecting because
  of speaker, battery, storage, display, performance, or weight is a product_attribute.
- If evidence is insufficient, omit the candidate or use the explicit unknown intent;
  never fill every facet speculatively.

Canonical ID to target mapping (mandatory):
- category_* -> {"kind":"category"}.
- subjective_* -> {"kind":"facet","facet":"subjective_property"}.
- event_* -> {"kind":"facet","facet":"event"}.
- activity_* -> {"kind":"facet","facet":"activity"}.
- goal_* -> {"kind":"facet","facet":"goal_purpose"}.
- audience_* -> {"kind":"facet","facet":"goal_audience"}.
- Every PreferenceId (budget, budget_flexibility, delivery_deadline,
  urgency_pressure, storage_capacity, battery, audio, portability, note_taking,
  price_value, display, durability, performance, longevity_value, review_signal,
  color_residual) -> {"kind":"constraint","scope":"hard|soft","key":"<the
  exact same canonical_id>"}. Never put a PreferenceId in facets.
- budget, an explicit delivery bound, and an explicit storage minimum/maximum are
  hard. Attribute priorities and qualitative preferences are soft.
- facets fields contain only facet candidates. activity_general is not a filler;
  use it only when the utterance explicitly says general-purpose use.
- Each canonical_id may appear at most once in candidates. A candidate duplicated in
  facets is still listed only once in candidates.
- RejectionReasonId values such as reject_stock_delay are allowed only inside
  item_action.rejection_reason. They are never target.key and never state candidates.

Extraction distinctions:
- Explicitly saying this is a first purchase -> event_first_purchase. Mentioning a
  purchase action is not by itself a first purchase event.
- A child using a product -> audience_child, not automatically event_gift or
  goal_entertainment. A gift event requires gift language.
- Document work or school work -> goal_work_study, not automatically note taking.
- Rejecting, inspecting, comparing, or purchasing an item must not create category,
  event, activity, or goal candidates from the item's previous-state metadata.
- A single salient qualitative desire may produce subjective_* while its operational
  ranking attribute produces the matching soft PreferenceId. When several attributes
  are merely listed as important, emit their PreferenceIds without inventing a
  subjective facet.
- "first product" and "current product" are symbolic actions only. Do not resolve
  them to product facts.
- A negative assessment of the referenced current/first item (for example "this one
  has disappointing speakers and battery") is reject_first even when the user does
  not literally say "remove it".
- color_residual is reserved but is not emitted in this schema version. A purchase
  that accepts the only available color uses residual_color_choice=true and no color
  candidate.

Intent rules:
- search means the user is starting or continuing a product search.
- refine means adding constraints without acting on a currently ranked item.
- purchase is only an explicit commitment to the current item, such as '이걸로 살게요',
  and must accompany item_action purchase_current. Saying a broken device must be
  replaced is search, not purchase.
- reject, inspect, compare, and purchase must each accompany the matching item_action.
- If item_action is reject_first, intents MUST contain reject; inspect_current requires
  inspect; compare_first_second requires compare; purchase_current requires purchase.

Evidence rules used by the current schema version:
- A named product family is explicit category evidence: iPhone means
  category_smartphone.
- A replacement purpose reconstructed from a failure event is implicit even when the
  need to replace is clear.
- If the currently used device is broken and no delivery deadline is stated, you MUST add the
  soft constraint urgency_pressure as inferred with confidence 0.64. It is only an
  unconfirmed hypothesis and must not be treated as a delivery deadline.
  Use exactly this candidate shape, with Korean text grounded in the utterance:
  {"target":{"kind":"constraint","scope":"soft","key":"urgency_pressure"},
   "canonical_id":"urgency_pressure","value_text":"수령 대기 민감 가능성",
   "evidence_text":"<failure evidence>","origin":"inferred","confidence":0.64}

Gold semantic example for this prompt version:
- Input meaning: "My iPhone broke, so I need to replace it."
- intent: search, not purchase
- candidates: category_smartphone explicit; event_device_failure explicit;
  goal_replace_device implicit; soft urgency_pressure inferred at confidence 0.64
- item_action: null; supersedes: []; residual_color_choice: false

Regression distinctions:
- A current first item rejected because it cannot arrive for two weeks while no phone
  is available: intent reject; item_action reject_first with reject_stock_delay as a
  situational_constraint; add hard delivery_deadline; supersede a previous
  urgency_pressure hypothesis. reject_stock_delay is not a candidate key.
- A current item rejected because reviews say its speakers and battery are weak:
  intent reject; item_action reject_first with reject_audio_battery as a
  product_attribute; add soft audio and battery constraints; never add review_signal.
"""


async def understand_utterance(
    client: LLMClient,
    *,
    utterance: str,
    previous_state_summary: Mapping[str, Any] | None = None,
    conversation_id: str | None = None,
    turn: int | None = None,
) -> UnderstandingOutput:
    """한 발화를 구조화하되 상태에는 쓰지 않는다."""
    clean_utterance = utterance.strip()
    if not clean_utterance:
        raise ValueError("utterance는 비어 있을 수 없습니다.")

    context = {
        "current_utterance_to_extract": clean_utterance,
        "reference_only_previous_state_do_not_copy": dict(previous_state_summary or {}),
    }
    return await client.generate_structured(
        messages=[
            system(SYSTEM_PROMPT),
            user(json.dumps(context, ensure_ascii=False, separators=(",", ":"))),
        ],
        response_model=UnderstandingOutput,
        temperature=0.0,
        node="spn-understanding",
        prompt_version=UNDERSTANDING_PROMPT_VERSION,
        conversation_id=conversation_id,
        turn=turn,
    )
