import { explainRecommendation } from "@/lib/rarec/explain";
import { rankProducts } from "@/lib/rarec/rank-products";
import type { DialogueState, Product, RecommendationResponse, Review } from "@/lib/types";

export function createRecommendationResponse(state: DialogueState, products: Product[], reviews: Review[]): RecommendationResponse {
  const { rankedProducts, rankedReviews } = rankProducts(state, products, reviews);
  return { updatedDialogueState: state, rankedProducts, reviewEvidence: rankedReviews, explanation: explainRecommendation(state, rankedProducts, products), unresolvedPreferences: state.unresolvedPreferences };
}
