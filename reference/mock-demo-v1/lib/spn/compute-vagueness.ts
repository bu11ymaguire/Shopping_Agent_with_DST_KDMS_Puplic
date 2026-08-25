import type { DialogueState, VaguenessBreakdown } from "@/lib/types";

/** 거절 이유가 발생한 턴에서 제약이 하나도 갱신되지 않았다면 그 거절은 정리되지 않은 것으로 본다. */
function unresolvedRejectionCount(state: DialogueState) {
  const constraintTurnIds = new Set(
    [...Object.values(state.hardConstraints), ...Object.values(state.softConstraints)].flatMap((value) => value.evidenceTurnIds)
  );
  return state.rejectedItems.filter((item) => !item.reason.evidenceTurnIds.some((turnId) => constraintTurnIds.has(turnId))).length;
}

/** SPN Policy가 쓰는 모호성 휴리스틱. facet은 DialogueState의 subjectiveNeeds에서 직접 읽는다. */
export function computeVagueness(state: DialogueState): VaguenessBreakdown {
  const facets = state.subjectiveNeeds;
  const reasons: string[] = [];
  let categoryBreadth = 0, missingRequiredInfo = 0, unresolvedSPN = 0, contradictionPenalty = 0;
  if (!state.category) { categoryBreadth = 18; reasons.push("상품 범위가 확인되지 않음: +18"); }
  if (!state.hardConstraints.budget) { missingRequiredInfo += 25; reasons.push("가격 기준이 확인되지 않음: +25"); }
  if (!facets.subjective_property) { missingRequiredInfo += 20; reasons.push("사용 기간 기준이 확인되지 않음: +20"); }
  if (!state.hardConstraints.deliveryDeadline) { unresolvedSPN += 12; reasons.push("수령 시점이 확인되지 않음: +12"); }
  if (state.softConstraints.reviewSignal) { unresolvedSPN -= 8; reasons.push("리뷰 선호가 확인됨: -8"); }
  const unresolvedRejections = unresolvedRejectionCount(state);
  if (unresolvedRejections) { contradictionPenalty = 12 * unresolvedRejections; reasons.push(`거절 이유가 제약으로 정리되지 않음: +${contradictionPenalty}`); }
  const total = Math.max(0, Math.min(100, categoryBreadth + missingRequiredInfo + unresolvedSPN + contradictionPenalty));
  return { categoryBreadth, missingRequiredInfo, unresolvedSPN, contradictionPenalty, total, threshold: 45, reasons };
}
