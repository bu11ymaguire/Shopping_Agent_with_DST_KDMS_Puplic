import { rankReviews } from "@/lib/rarec/rank-reviews";
import type { DialogueState, PreferenceValue, Product, RankedProduct, Review, ScoreBreakdown } from "@/lib/types";

function numberFrom(text: string) { const digits = text.replace(/[^\d]/g, ""); return digits ? Number(digits) : 0; }
function clamp(value: number) { return Math.max(0, Math.min(100, Math.round(value))); }
function average(values: number[]) { return values.reduce((sum, value) => sum + value, 0) / values.length; }

type AttributeName = "deliverySpeed" | "performance" | "longevity" | "battery" | "audio" | "portability" | "priceValue";

/**
 * 상품 속성을 0~100으로 정규화한다. 후보군이 바뀌어도 같은 상품의 속성 점수가 흔들리지 않도록
 * 후보 상대 정규화 대신 고정 기준 구간을 쓴다.
 */
const attributeScore: Record<AttributeName, (product: Product) => number> = {
  deliverySpeed: (product) => clamp(100 - product.metadata.deliveryDays * 12),
  performance: (product) => clamp(product.metadata.performanceTier * 20),
  longevity: (product) => clamp((product.metadata.longevityYears / 6) * 100),
  battery: (product) => clamp(((product.metadata.batteryHours - 18) / 20) * 100),
  audio: (product) => clamp(product.metadata.speakerTier * 20),
  portability: (product) => clamp(((240 - product.metadata.weightGrams) / 80) * 100),
  priceValue: (product) => clamp(((2_100_000 - product.price) / 1_300_000) * 100)
};

/**
 * 선호 id가 실제로 참조하는 상품 속성. 상품 ID별 보너스나 시나리오 전용 가점은 두지 않는다.
 * 여기에 없거나 빈 배열인 선호는 점수를 움직이지 않고 상태와 근거로만 남는다.
 */
const preferenceAttributes: Record<string, AttributeName[]> = {
  "delivery-deadline": ["deliverySpeed"],
  "delivery-speed": ["deliverySpeed"],
  "longevity-value": ["longevity"],
  "audio-battery-priority": ["audio", "battery"],
  "portability-priority": ["portability"],
  "price-sensitivity": ["priceValue"],
  "subjective-longevity": ["longevity", "performance"],
  "subjective-performance": ["performance"],
  "subjective-portability": ["portability"]
};

/** 확정된 선호만 랭킹에 반영한다. inferred / unconfirmed 가설은 근거로만 표시한다. */
function activeAttributes(values: Array<PreferenceValue | null | undefined>) {
  const attributes: AttributeName[] = [];
  const preferenceIds: string[] = [];
  values.forEach((value) => {
    if (!value || value.status !== "confirmed") return;
    const mapped = preferenceAttributes[value.id];
    if (!mapped?.length) return;
    attributes.push(...mapped);
    preferenceIds.push(value.id);
  });
  return { attributes: [...new Set(attributes)], preferenceIds };
}

function budgetValue(state: DialogueState) {
  const budget = state.hardConstraints.budget;
  return budget ? numberFrom(budget.valueText) : undefined;
}

function deadlineDays(state: DialogueState) {
  const deadline = state.hardConstraints.deliveryDeadline;
  return deadline ? numberFrom(deadline.valueText) : undefined;
}

export function rankProducts(state: DialogueState, products: Product[], reviews: Review[]): { rankedProducts: RankedProduct[]; rankedReviews: ReturnType<typeof rankReviews> } {
  const rejected = new Set(state.rejectedItems.map((item) => item.productId));
  const candidates = products.filter((product) => product.metadata.category === (state.category?.valueText ?? product.metadata.category) && !rejected.has(product.id));
  const rankedReviews = rankReviews(state, candidates, reviews.filter((review) => candidates.some((product) => product.id === review.productId)));
  const maximumReviewCount = Math.max(1, ...candidates.map((product) => product.reviewCount));

  const budget = budgetValue(state);
  const deadline = deadlineDays(state);
  // 예산 상한을 넘겨도 수용한다는 선호가 있으면 예산은 통과·탈락이 아니라 초과율 감점으로 다룬다.
  const budgetFlexible = state.softConstraints.budgetFlexibility?.status === "confirmed";
  const constraintPreferences = [...Object.values(state.hardConstraints), ...Object.values(state.softConstraints)];
  const constraintSignals = activeAttributes(constraintPreferences);
  const needSignals = activeAttributes(Object.values(state.subjectiveNeeds));

  const results = candidates.map((product) => {
    const hardChecks: number[] = [];
    if (budget) hardChecks.push(product.price <= budget ? 100 : budgetFlexible ? clamp(100 - ((product.price - budget) / budget) * 250) : 0);
    if (deadline) hardChecks.push(product.metadata.deliveryDays <= deadline ? 100 : 0);
    const hardConstraintMatch = hardChecks.length ? clamp(average(hardChecks)) : 70;

    const metadataMatch = constraintSignals.attributes.length
      ? clamp(average(constraintSignals.attributes.map((name) => attributeScore[name](product))))
      : 65;
    const subjectiveNeedMatch = needSignals.attributes.length
      ? clamp(average(needSignals.attributes.map((name) => attributeScore[name](product))))
      : 55;

    const reviewsForProduct = rankedReviews.filter((review) => review.productId === product.id).sort((a, b) => b.totalScore - a.totalScore).slice(0, 3);
    const reviewEvidenceScore = reviewsForProduct.length ? average(reviewsForProduct.map((review) => review.totalScore)) : 0;
    const helpfulness = reviewsForProduct.length ? average(reviewsForProduct.map((review) => review.reliabilityScore)) : 0;
    const countReliability = Math.log1p(product.reviewCount) / Math.log1p(maximumReviewCount) * 100;
    const evidenceReliability = clamp(0.7 * helpfulness + 0.3 * countReliability);

    const total = clamp(0.3 * hardConstraintMatch + 0.3 * metadataMatch + 0.2 * subjectiveNeedMatch + 0.15 * reviewEvidenceScore + 0.05 * evidenceReliability);
    const score: ScoreBreakdown = { hardConstraintMatch, metadataMatch, subjectiveNeedMatch, reviewEvidenceScore: clamp(reviewEvidenceScore), evidenceReliability, total };
    const matchedPreferenceIds = [
      state.hardConstraints.budget?.id,
      state.hardConstraints.deliveryDeadline?.id,
      ...constraintSignals.preferenceIds,
      ...needSignals.preferenceIds
    ].filter(Boolean) as string[];
    return { productId: product.id, score, evidenceReviewIds: reviewsForProduct.map((review) => review.id), matchedPreferenceIds: [...new Set(matchedPreferenceIds)] };
  }).sort((a, b) => b.score.total - a.score.total || a.productId.localeCompare(b.productId));

  return { rankedProducts: results.map((result, index) => ({ ...result, rank: index + 1 })), rankedReviews };
}
