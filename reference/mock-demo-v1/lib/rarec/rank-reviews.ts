import { expandKeywords } from "@/lib/mock/synonym-map";
import type { DialogueState, Product, RankedReview, Review } from "@/lib/types";

function tokens(text: string) { return text.toLowerCase().match(/[가-힣a-z0-9]+/g) ?? []; }

function preferenceKeywords(state: DialogueState): string[] {
  const values: Array<string | undefined> = [
    state.category?.valueText,
    ...Object.values(state.hardConstraints).map((value) => value.valueText),
    ...Object.values(state.softConstraints).map((value) => value.valueText),
    ...Object.values(state.subjectiveNeeds).map((value) => value?.valueText)
  ];
  return values.filter((value): value is string => Boolean(value)).flatMap((value) => tokens(value));
}

export function rankReviews(state: DialogueState, products: Product[], reviews: Review[], topK = 3): RankedReview[] {
  const productById = new Map(products.map((product) => [product.id, product]));
  const keywords = expandKeywords(preferenceKeywords(state));
  return reviews.map((review) => {
    const reviewTokens = new Set(tokens(review.text));
    const product = productById.get(review.productId);
    const productTokens: string[] = product ? tokens(`${product.title} ${product.metadata.chipset} ${product.metadata.storage} ${product.metadata.useCases.join(" ")}`) : [];
    const matches = keywords.filter((keyword) => reviewTokens.has(keyword) || productTokens.includes(keyword));
    const similarityScore = Math.min(100, 20 + matches.length * 20);
    const preferenceCoverageScore = Math.min(100, keywords.length ? Math.round((matches.length / Math.max(1, Math.min(keywords.length, 5))) * 100) : 25);
    const reliabilityScore = Math.round((review.helpfulness ?? 0.5) * 100);
    const totalScore = Math.round(0.65 * similarityScore + 0.25 * preferenceCoverageScore + 0.1 * reliabilityScore);
    return { ...review, similarityScore, preferenceCoverageScore, reliabilityScore, totalScore, matchedPreferenceIds: matches };
  }).sort((a, b) => b.totalScore - a.totalScore).slice(0, Math.max(topK, reviews.length));
}
