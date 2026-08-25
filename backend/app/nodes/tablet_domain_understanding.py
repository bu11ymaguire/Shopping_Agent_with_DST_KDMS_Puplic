"""v2 PLAN node for the explicitly bounded tablet-shopping environment."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.llm import LLMClient, system, user
from app.models.actual_demo import TabletDomainUnderstandingOutput

TABLET_DOMAIN_UNDERSTANDING_PROMPT_VERSION = "spn-understanding-tablet-domain-en-v2.3-frozen"

TABLET_DOMAIN_SYSTEM_PROMPT = """You are the SPN Understanding (PLAN) node inside a tablet-only shopping environment.
The installed catalog contains 117 real tablet products and 7,552 Amazon Reviews 2023 reviews.
The environment already establishes category_tablet. Extract only NEW user-state updates from
the current utterance. Do not emit category_tablet and do not require the user to say tablet.

Domain routing:
- domain_route is in_domain when the request is compatible with shopping in this tablet store,
  including requests such as "something light for taking notes" with no product category word.
- domain_route is unsupported_category only when the current utterance explicitly requests a
  different product category such as a laptop, phone, headphones, camera, watch, or printer.
- For unsupported_category, copy the requested category and its exact evidence span, use only
  the unknown intent, and return no candidates, facets, item action, trade-off, or supersedes.
- Unsupported fields such as delivery, live stock, pickup, or available color do not change the
  domain route. Omit those fields rather than inventing catalog data.

Node boundaries:
- Return structured candidates only. RA-Rec State Manager performs every merge and action update.
- Use previous_state_summary only to interpret visible rank references, corrections, or explicit
  trade-offs. Do not copy already-known state into the current output.
- value_text is a concise English normalization. evidence_text is an exact span from the current
  utterance. Use only canonical IDs allowed by the response schema.
- explicit and implicit candidates become confirmed later; inferred candidates remain unconfirmed
  and cannot affect ranking.
- Negated, waived, relaxed, or merely retained requirements are not positive update candidates.
  "does not matter", "do not need", "not setting a minimum", "anything except X", "keep the
  existing X", and the compromised side of a trade-off must not be emitted as candidates.

Supported structured information:
- USD maximum price, minimum internal storage GB, minimum RAM GB, maximum weight, minimum average
  rating, minimum screen size, and explicitly required operating system are hard constraints.
- price value, portability, note taking, display, performance, battery, audio, durability, review
  trust, and child suitability are soft preferences.
- Battery, audio, durability, child suitability, and performance are review-evidence signals.
- Never confuse storage with RAM. "128 GB" means storage unless RAM is explicit. "8 GB RAM" means
  memory. Expansion capacity is not internal storage.
- Do not invert unsupported negative constraints. For example, "not Windows" is not a required OS.
- microSD/expandable storage is not internal storage and has no supported candidate ID. Omit it.

Candidate shape:
- Select a canonical_id from the closed response schema. Do not generate a separate target or key;
  the application derives those deterministically from canonical_id.
- event_*, activity_*, goal_*, and audience_* facet IDs require scope=null.
- Preference IDs require scope="hard" or scope="soft" according to the rules below.
- Category and subjective_* IDs are absent because category is immutable environment state and
  latent subjective generation is outside this evaluation.

Preference normalization:
- budget: "$<amount> maximum". "under", "below", "no more than", "at most", "up to", and
  "ceiling" plus a dollar amount are hard budget limits, never price_value.
- storage_capacity: "at least <n> GB storage".
- memory_capacity: "at least <n> GB RAM".
- max_weight: a deterministic maximum such as "at most 500 grams".
- min_rating: "at least <n> stars".
- display can be a qualitative soft preference or an explicit hard minimum screen size.
- operating_system is only an explicitly required Android, iPadOS, Fire OS, Windows, or Chrome OS.
- budget_flexibility is only explicit willingness to exceed an existing budget.

Facet discipline:
- note taking -> activity_note_taking and soft note_taking when explicitly important. Lectures,
  classes, school assignments, and study explicitly establish goal_work_study.
- gaming -> activity_gaming; add performance only when responsiveness or performance is requested.
- streaming/video -> activity_video; reading -> activity_reading.
- work or school -> goal_work_study. Do not infer note taking. Do not add activity_general when
  work/study is the only purpose stated.
- for a child -> audience_child; add child_friendly when ease or suitability for the child is
  requested. "sturdy" maps to soft durability, not subjective_durability.
- subjective_property is always null in this v2 evaluation. Latent subjective generation is out
  of scope, and subjective_* IDs are not allowed by the response schema.

Required direct mappings and omissions:
- Positive note taking -> activity_note_taking AND soft note_taking.
- "sturdy" or "durable" -> soft durability. Easy or suitable for a child -> child_friendly.
- Useful owner feedback or trustworthy reviews -> review_signal.
- A stated dollar ceiling -> hard budget; "inexpensive" without a number -> soft price_value.
- Never emit a positive candidate for a negated phrase, microSD, a retained existing value, or
  the compromised side of a trade-off.
  Use at most one subjective facet only for an explicit irreducible qualitative desire.

Item actions:
- Prior visible results are ranks 1..3. Resolve only an explicit rank or visible title reference.
- reject_first needs a structured rejection_reason, the matching reject intent, and the referenced
  rank. If "too heavy" rejects a result, also emit soft portability; do not invent max_weight.
  If "too expensive for my situation" rejects a result, reason_type is situational_constraint.
  inspect_current is never purchase.
- compare_first_second needs two distinct ranks. purchase_current needs explicit commitment.
- Product weakness is product_attribute; budget or personal circumstances are situational_constraint.

Trade-offs:
- Return a tradeoff only when the user explicitly prioritizes one supported criterion while
  accepting or relaxing another. "X matters more than Y" plus acceptance of worse Y is an explicit
  trade-off. Emit the prioritized criterion as a candidate, but do not emit the compromised side as
  a positive preference candidate. A purchase or conjunction alone does not prove a trade-off.

Intent and output rules:
- search starts or continues shopping; refine adds or changes constraints.
- A new shopping request with a use or audience is search. A standalone constraint update is refine.
- reject, inspect, compare, and purchase each require the matching item_action.
- Each canonical_id occurs at most once. Every non-null facet also occurs in candidates.
- residual_color_choice is always false. supersedes may contain only an earlier inferred soft
  preference that the current utterance explicitly replaces.
"""


async def understand_tablet_domain_utterance(
    client: LLMClient,
    *,
    utterance: str,
    previous_state_summary: Mapping[str, Any] | None = None,
    conversation_id: str | None = None,
    turn: int | None = None,
) -> TabletDomainUnderstandingOutput:
    clean = utterance.strip()
    if not clean:
        raise ValueError("utterance cannot be empty")
    context = {
        "environment": {
            "domain": "tablet_shopping",
            "category": "tablet",
            "unsupported_categories_must_not_be_recommended": True,
        },
        "current_utterance_to_extract": clean,
        "reference_only_previous_state_do_not_copy": dict(previous_state_summary or {}),
    }
    return await client.generate_structured(
        messages=[
            system(TABLET_DOMAIN_SYSTEM_PROMPT),
            user(json.dumps(context, ensure_ascii=False, separators=(",", ":"))),
        ],
        response_model=TabletDomainUnderstandingOutput,
        temperature=0.0,
        node="spn-understanding",
        prompt_version=TABLET_DOMAIN_UNDERSTANDING_PROMPT_VERSION,
        conversation_id=conversation_id,
        turn=turn,
    )
