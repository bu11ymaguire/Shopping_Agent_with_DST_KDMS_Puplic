import type { BrowseResult, DialogueState, Product, Review } from "@/lib/types";

function budgetValue(state: DialogueState) {
  const text = state.hardConstraints.budget?.valueText ?? "";
  const match = text.match(/[\d,]+/);
  return match ? Number(match[0].replace(/,/g, "")) : undefined;
}

/** 상한 초과를 수용한다는 선호가 있으면 탐색 단계에서 후보 범위를 조금 넓힌다. */
function priceCeiling(state: DialogueState) {
  const budget = budgetValue(state);
  if (!budget) return undefined;
  return state.softConstraints.budgetFlexibility ? Math.round(budget * 1.35) : budget;
}

export function browseMockCatalog(query: string, state: DialogueState, products: Product[], reviews: Review[]): BrowseResult {
  const ceiling = priceCeiling(state);
  const category = state.category?.valueText;
  const filtered = products.filter((product) => (!category || product.metadata.category === category) && (!ceiling || product.price <= ceiling));
  const visibleProducts = filtered.length ? filtered : products.filter((product) => !category || product.metadata.category === category);
  const productIds = new Set(visibleProducts.map((product) => product.id));
  return {
    products: visibleProducts,
    reviews: reviews.filter((review) => productIds.has(review.productId)),
    images: visibleProducts.map((product) => product.image),
    priceEvidence: visibleProducts.map((product) => `${product.title}: ${product.price.toLocaleString("ko-KR")}원 (mock)`),
    deliveryEvidence: visibleProducts.map((product) => `${product.title}: ${product.shipping} / ${product.stock}`)
  };
}
