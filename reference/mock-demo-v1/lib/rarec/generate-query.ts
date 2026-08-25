import type { DialogueState } from "@/lib/types";

export function generateNaturalLanguageQuery(state: DialogueState) {
  const parts = [state.category?.valueText ?? "상품"];
  if (state.subjectiveNeeds.subjective_property) parts.push(state.subjectiveNeeds.subjective_property.valueText);
  if (state.subjectiveNeeds.goal_purpose) parts.push(state.subjectiveNeeds.goal_purpose.valueText);
  if (state.hardConstraints.budget) parts.push(state.hardConstraints.budget.valueText);
  if (state.softConstraints.budgetFlexibility) parts.push("상한 초과 수용 가능");
  if (state.hardConstraints.deliveryDeadline) parts.push(state.hardConstraints.deliveryDeadline.valueText);
  if (state.softConstraints.audioBatteryPriority) parts.push(state.softConstraints.audioBatteryPriority.valueText);
  if (state.softConstraints.reviewSignal) parts.push("리뷰가 많은 제품");
  return parts.join(" ");
}
