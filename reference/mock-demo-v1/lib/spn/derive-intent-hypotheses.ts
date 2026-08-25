import type { DialogueState, IntentHypothesis } from "@/lib/types";

export function deriveHiddenIntentHypotheses(state: DialogueState): IntentHypothesis[] {
  const hypotheses: IntentHypothesis[] = [];
  const brokenDevice = state.subjectiveNeeds.event;
  const deadline = state.hardConstraints.deliveryDeadline;
  const longevity = state.softConstraints.longevityValue;
  const flexibility = state.softConstraints.budgetFlexibility;
  const audioBattery = state.softConstraints.audioBatteryPriority;
  const residualColor = state.softConstraints.colorChoice;
  const reviews = state.softConstraints.reviewSignal;

  if (brokenDevice && !deadline) hypotheses.push({ id: "urgency-unconfirmed", text: "기기 고장으로 교체가 필요한 상황이므로 사양보다 수령 시점이 먼저 걸릴 가능성이 있습니다. 아직 확인되지 않아 랭킹에는 반영하지 않았습니다.", confidence: 0.64, evidence: [brokenDevice.valueText, "event facet"] });
  if (deadline) hypotheses.push({ id: "delivery-is-decisive", text: "수령 대기 시간이 사양이나 가격보다 강한 실질적 결정 변수로 작동할 가능성이 높습니다.", confidence: 0.88, evidence: [deadline.valueText, "거절 이유"] });
  if (longevity) hypotheses.push({ id: "cost-per-year", text: "가격 자체보다 사용 기간 대비 비용을 기준으로 판단할 가능성이 높습니다.", confidence: 0.79, evidence: [longevity.valueText, flexibility?.valueText ?? "사용 기간 기준"].filter(Boolean) });
  if (audioBattery) hypotheses.push({ id: "review-driven-rejection", text: "리뷰에서 반복 지적된 단점은 사양표상 우위보다 강한 탈락 기준으로 작동할 가능성이 높습니다.", confidence: 0.85, evidence: [audioBattery.valueText, "거절 행동"] });
  if (residualColor) hypotheses.push({ id: "color-not-preference", text: "구매한 색상은 색상 선호가 아니라 즉시 수령이 가능한 잔여 대안일 가능성이 높습니다. 구매 결과만 보면 색상 선호로 잘못 읽힐 수 있습니다.", confidence: 0.92, evidence: [residualColor.valueText, "잔여 재고"] });
  if (state.tradeoffs.length) hypotheses.push({ id: "outcome-hides-priority", text: "최고가 모델을 구매했지만 이는 가격 민감도가 낮다는 뜻이 아니라 수령 시점을 우선한 결과일 가능성이 높습니다.", confidence: 0.9, evidence: state.tradeoffs.map((item) => item.valueText) });
  if (reviews) hypotheses.push({ id: "social-proof", text: "리뷰가 많은 제품을 선호하므로 사회적 증거를 중요하게 생각합니다.", confidence: 0.9, evidence: [reviews.valueText] });
  return hypotheses;
}
