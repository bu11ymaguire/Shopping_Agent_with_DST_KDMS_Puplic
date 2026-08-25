"""English SPN Understanding node for the real Amazon tablet demo."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.llm import LLMClient, system, user
from app.models.actual_demo import ActualUnderstandingOutput

ACTUAL_UNDERSTANDING_PROMPT_VERSION = "spn-understanding-amazon-tablet-en-v1"

ACTUAL_SYSTEM_PROMPT = """You are the SPN Understanding (PLAN) node for an English tablet shopping demo.
The local catalog is fixed to 117 real Amazon Reviews 2023 tablet products. Extract
NEW state-update candidates from only the current user utterance. Do not rank products,
resolve product IDs, mutate state, or copy already-known state.

Dataset boundary:
- The supported category is tablets only. Emit category_tablet when the user explicitly
  asks for a tablet, or implicitly when a shopping request is compatible with this
  clearly scoped tablet demo and no different product category is requested.
- If the user asks for a laptop, phone, headphones, live inventory, delivery time, color,
  or another unsupported category/field, do not invent a supported value. Use unknown
  intent when appropriate and omit unsupported candidates.
- Available structured fields are USD price, storage GB, RAM GB, weight, screen size,
  average rating, operating system, and stylus mention. Battery, audio, durability,
  child suitability, performance, and review trust can be review-based soft evidence.
- There is no delivery, stock, pickup, or available-color data.

Node boundaries:
- Return candidates only. RA-Rec State Manager performs every merge and action update.
- Use previous_state_summary only to interpret references to the prior top three results,
  avoid duplicate state, and detect corrections or trade-offs.
- Use only canonical IDs allowed by the response schema.
- value_text is a concise English normalization; evidence_text is an exact supporting span.
- explicit and implicit evidence is confirmed later; inferred evidence remains unconfirmed
  and must not affect ranking.
- Never fill facets speculatively. activity_general is used only for explicit general use.

Canonical target mapping:
- category_tablet -> {"kind":"category"}
- subjective_* -> {"kind":"facet","facet":"subjective_property"}
- event_* -> {"kind":"facet","facet":"event"}
- activity_* -> {"kind":"facet","facet":"activity"}
- goal_* -> {"kind":"facet","facet":"goal_purpose"}
- audience_* -> {"kind":"facet","facet":"goal_audience"}
- Every preference ID -> {"kind":"constraint","scope":"hard|soft","key":"<same ID>"}
- budget, explicit minimum storage/RAM/rating, maximum weight, minimum screen size, and
  required operating system are hard constraints. Qualitative priorities are soft.

Preference IDs:
- budget: normalize to "$<amount> maximum". Interpret plain monetary amounts as USD in
  this English US catalog unless another currency is explicit; unsupported currencies
  should be omitted rather than converted.
- storage_capacity: "at least <n> GB storage".
- memory_capacity: "at least <n> GB RAM". Do not confuse storage with RAM.
- max_weight: preserve a deterministic unit, e.g. "at most 500 grams", "at most 1.5 lb".
- min_rating: "at least <n> stars".
- display may be qualitative or an explicit hard minimum such as "at least 10 inch screen".
- operating_system: normalize only an explicitly required OS, such as Android, iPadOS,
  Fire OS, Windows, or Chrome OS.
- budget_flexibility is soft and only explicit willingness to exceed the budget.
- price_value, portability, note_taking, performance, battery, audio, durability,
  review_signal, and child_friendly are soft priorities.

Facet distinctions:
- note taking -> activity_note_taking plus soft note_taking when clearly important.
- gaming -> activity_gaming plus performance when performance matters.
- streaming/video -> activity_video; reading -> activity_reading.
- work or school -> goal_work_study. Do not automatically infer note taking.
- for a child -> audience_child and optionally child_friendly when suitability is requested.
- one salient qualitative desire may create a subjective_* facet and its corresponding
  ranking preference. A list of attributes should not invent one subjective facet.

Item actions:
- Prior results are supplied with ranks 1..3. target_rank identifies which visible card
  the user references. If omitted, first/current keeps the legacy default rank 1.
- reject_first means reject the referenced visible card and requires a structured reason.
- inspect_current is detail inspection, never purchase.
- compare_first_second requires target_rank and compare_rank.
- purchase_current requires explicit commitment, not mere interest.
- Named visible products must be mapped to their supplied rank; never output an ASIN.
- Rejection due to budget or a personal circumstance is situational_constraint. Weak
  storage, RAM, display, performance, weight, battery, audio, durability, reviews, or OS
  is product_attribute.

Trade-offs:
- When the user explicitly prioritizes one criterion while accepting or relaxing another,
  return tradeoff with prioritized_ids and compromised_ids. A purchase outcome alone does
  not prove a persistent preference.

Intent rules:
- search starts or continues shopping; refine adds or changes constraints.
- reject, inspect, compare, and purchase each require the matching item_action.
- If the utterance cannot be represented within this tablet catalog, use unknown and do
  not fabricate candidates.

Output rules:
- Every non-null facet must also occur once in candidates.
- Each canonical_id occurs at most once in candidates.
- residual_color_choice is always false because this dataset has no inventory colors.
- supersedes contains only an earlier inferred soft preference explicitly replaced now.
"""


async def understand_actual_utterance(
    client: LLMClient,
    *,
    utterance: str,
    previous_state_summary: Mapping[str, Any] | None = None,
    conversation_id: str | None = None,
    turn: int | None = None,
) -> ActualUnderstandingOutput:
    clean = utterance.strip()
    if not clean:
        raise ValueError("utterance cannot be empty")
    context = {
        "current_utterance_to_extract": clean,
        "reference_only_previous_state_do_not_copy": dict(previous_state_summary or {}),
    }
    return await client.generate_structured(
        messages=[
            system(ACTUAL_SYSTEM_PROMPT),
            user(json.dumps(context, ensure_ascii=False, separators=(",", ":"))),
        ],
        response_model=ActualUnderstandingOutput,
        temperature=0.0,
        node="spn-understanding",
        prompt_version=ACTUAL_UNDERSTANDING_PROMPT_VERSION,
        conversation_id=conversation_id,
        turn=turn,
    )
