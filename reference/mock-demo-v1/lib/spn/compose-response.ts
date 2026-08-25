import { composeQuestion } from "@/lib/spn/compose-question";
import type { BrowseResult, FinalResponse, IntentResult, PolicyDecision, Product, RecommendationResponse } from "@/lib/types";

function won(value: number) { return `${value.toLocaleString("ko-KR")}원`; }
function colors(product: Product) { return product.metadata.availableColors.length ? product.metadata.availableColors.join(", ") : "없음"; }

/**
 * SPN Response Composer — Clarify (RESPOND)
 *
 * 입력  : SPN Policy의 clarify 결정, 현재 RA-Rec 상태
 * 출력  : 추가 질문(에이전트 응답)
 */
export function composeClarifyResponse(policy: PolicyDecision): FinalResponse {
  return {
    message: composeQuestion(policy),
    productCards: [], priceEvidence: [], deliveryEvidence: [], imageEvidence: [], reviewEvidence: [],
    nextActions: ["예산 말하기", "사용 기간 말하기", "수령 기한 말하기"]
  };
}

/**
 * SPN Response Composer — Recommend (RESPOND)
 *
 * 입력  : RA-Rec Recommendation Engine의 순위 및 근거, SPN Browsing Actions의 가격·배송·이미지
 * 출력  : 추천 결과(에이전트 응답)
 */
export function composeRecommendResponse(
  recommendation: RecommendationResponse | null,
  browseResult: BrowseResult | null,
  products: Product[],
  itemAction?: IntentResult["itemAction"]
): FinalResponse {
  const top = recommendation?.rankedProducts[0];
  const product = top ? products.find((item) => item.id === top.productId) : undefined;
  const rejectionId = itemAction?.name === "reject_first" ? itemAction.rejectionReason?.id : undefined;

  const message = !product
    ? "추천 후보를 찾지 못했습니다."
    : rejectionId === "reject-stock-delay"
      ? `수령 기한을 하드 제약으로 반영했어요. 대기가 필요한 모델은 후보에서 내리고, 지금 ${product.metadata.deliveryDays}일 안에 받을 수 있는 ${product.title}을(를) 우선 추천합니다. ${won(product.price)}이고 ${product.stock}입니다.`
      : rejectionId === "reject-audio-battery"
        ? `스피커·배터리 기준을 추가해 다시 계산했어요. 남은 후보 중에서는 ${product.title}이(가) 스피커 ${product.metadata.speakerTier}등급, 영상 재생 ${product.metadata.batteryHours}시간으로 가장 높습니다. 다만 ${won(product.price)}으로 말씀하신 예산 상한을 넘습니다.`
        : itemAction?.name === "inspect_current"
          ? `${product.title} 상세를 볼게요. ${product.metadata.display} 화면, ${product.metadata.chipset}, ${product.metadata.storage}, 무게 ${product.metadata.weightGrams}g이고 ${product.shipping}입니다. 지금 즉시 수령이 가능한 색상은 ${colors(product)}뿐입니다.`
          : itemAction?.name === "purchase_current"
            ? `${product.title}을(를) 구매할 제품으로 기록했어요. 색상은 선택이 아니라 즉시 수령이 가능한 잔여 옵션이었다는 점, 그리고 이번 결정에서 우선한 조건과 양보한 조건도 상태에 함께 남겼습니다.`
            : itemAction?.name === "compare_first_second"
              ? `${product.title}을(를) 우선 추천합니다. 비교할 두 제품을 관심 목록에 담았어요. 가격, 수령 시점, 리뷰 근거를 카드에서 나란히 확인해 보세요.`
              : `${product.title}을(를) 우선 추천합니다. ${won(product.price)}, ${product.shipping} 조건과 점수에 사용된 리뷰 근거를 함께 확인해 보세요.`;

  return {
    message,
    productCards: recommendation?.rankedProducts ?? [],
    priceEvidence: browseResult?.priceEvidence ?? [],
    deliveryEvidence: browseResult?.deliveryEvidence ?? [],
    imageEvidence: browseResult?.images ?? [],
    reviewEvidence: recommendation?.reviewEvidence ?? [],
    nextActions: ["첫 번째 제품 자세히 보기", "수령 기한 조정", "예산 상한 조정"]
  };
}
