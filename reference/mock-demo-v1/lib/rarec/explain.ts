import type { DialogueState, Product, RankedProduct } from "@/lib/types";

export function explainRecommendation(state: DialogueState, rankings: RankedProduct[], products: Product[]) {
  const first = rankings[0];
  const product = first ? products.find((item) => item.id === first.productId) : undefined;
  if (!product || !first) return "현재 상태에서는 추천 후보를 계산하지 않았습니다.";
  const criterion = state.hardConstraints.deliveryDeadline?.valueText
    ?? state.subjectiveNeeds.subjective_property?.valueText
    ?? state.hardConstraints.budget?.valueText
    ?? "현재 선호";
  return `${product.title}이(가) ${criterion} 조건과 리뷰 근거를 가장 잘 충족해 ${first.rank}위로 계산되었습니다.`;
}
