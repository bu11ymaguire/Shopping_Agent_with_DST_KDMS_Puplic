import { parseUserInput } from "@/lib/mock/parse-user-input";
import type {
  DialogueState,
  EvidenceOrigin,
  IntentResult,
  ItemActionCandidate,
  SPNFacetName,
  StateUpdateCandidate
} from "@/lib/types";

/**
 * SPN Understanding (PLAN)
 *
 * 입력  : 사용자 발화, 이전 턴 RA-Rec 상태(루프백)
 * 출력  : 의도 분류, SPN 5 Facets, 상태 갱신 후보
 *
 * 이 단계는 상태를 직접 쓰지 않는다. 어떤 슬롯에 어떤 값을 어떤 근거로 넣을지까지만 정하고,
 * 병합과 이력 기록은 RA-Rec State Manager (MEMORY) 로 넘긴다.
 */

function candidate(
  target: StateUpdateCandidate["target"],
  id: string,
  valueText: string,
  origin: EvidenceOrigin = "explicit",
  confidence = 1
): StateUpdateCandidate {
  return { target, id, valueText, origin, confidence };
}

function facetCandidate(facet: SPNFacetName, id: string, valueText: string, origin: EvidenceOrigin = "explicit", confidence = 1) {
  return candidate({ kind: "facet", facet }, id, valueText, origin, confidence);
}

function softCandidate(key: string, id: string, valueText: string, origin: EvidenceOrigin = "explicit", confidence = 1) {
  return candidate({ kind: "constraint", scope: "soft", key }, id, valueText, origin, confidence);
}

function hardCandidate(key: string, id: string, valueText: string) {
  return candidate({ kind: "constraint", scope: "hard", key }, id, valueText, "explicit", 1);
}

export function understandUtterance(utterance: string, previousState: DialogueState): IntentResult {
  const parsed = parseUserInput(utterance);
  const candidates: StateUpdateCandidate[] = [];
  const supersedes: string[] = [];

  if (parsed.category) candidates.push(candidate({ kind: "category" }, "category-smartphone", parsed.category));

  // 교체를 유발한 사건은 event 사실로 남긴다. 얼마나 급한지는 아직 확인되지 않았으므로
  // 긴급도는 inferred / unconfirmed 후보로만 제안한다.
  if (parsed.deviceBroken) {
    candidates.push(facetCandidate("event", "event-device-broken", "기존 기기 고장"));
    candidates.push(facetCandidate("goal_purpose", "goal-replace-device", "고장 난 기기 교체", "implicit", 0.9));
    candidates.push(softCandidate("urgencyPressure", "urgency-pressure", "지금 쓸 기기가 없어 수령 대기에 민감할 가능성", "inferred", 0.64));
  }

  if (parsed.budget) candidates.push(hardCandidate("budget", "budget", `${parsed.budget.toLocaleString("ko-KR")}원 이하`));
  if (parsed.budgetFlexible) candidates.push(softCandidate("budgetFlexibility", "budget-flexibility", "장기 사용 가치가 있으면 예산 상한을 넘겨도 수용"));

  if (parsed.longevityValue) {
    candidates.push(facetCandidate("subjective_property", "subjective-longevity", "오래 쓸 수 있는 제품"));
    candidates.push(softCandidate("longevityValue", "longevity-value", "장기 사용 가치 중시"));
  }

  if (parsed.valuesReviewCount) candidates.push(softCandidate("reviewSignal", "review-signal", "리뷰 수를 중요하게 봄"));

  // 수령 기한이 확인되면 상황적 제약이 하드 제약으로 승격되고, 앞선 긴급도 가설은 대체 대상이 된다.
  if (parsed.deliveryDeadlineDays) {
    candidates.push(hardCandidate("deliveryDeadline", "delivery-deadline", `${parsed.deliveryDeadlineDays}일 이내 수령`));
    supersedes.push("urgencyPressure");
  }

  // 상품이 확인되지 않은 발화라면 카테고리에서 구매 목적을 보충한다.
  const hasGoalPurpose = candidates.some((item) => item.target.kind === "facet" && item.target.facet === "goal_purpose");
  const knownCategory = parsed.category ?? previousState.category?.valueText;
  if (!hasGoalPurpose && !previousState.subjectiveNeeds.goal_purpose && knownCategory) {
    candidates.push(facetCandidate("goal_purpose", "goal-product-category", `${knownCategory} 구매`, "implicit", 0.8));
  }

  let itemAction: ItemActionCandidate | undefined;
  if (parsed.action === "reject_first_delivery") {
    itemAction = { name: "reject_first", rejectionReason: { id: "reject-stock-delay", valueText: "재고 부족으로 수령까지 대기 필요 · 상황적 제약" } };
  } else if (parsed.action === "reject_first_audio_battery") {
    itemAction = { name: "reject_first", rejectionReason: { id: "reject-audio-battery", valueText: "스피커·배터리 성능이 기준에 미달 · 상품 속성" } };
    candidates.push(softCandidate("audioBatteryPriority", "audio-battery-priority", "스피커·배터리 성능 중시"));
  } else if (parsed.action === "compare_first_second") {
    itemAction = { name: "compare_first_second" };
  } else if (parsed.action === "purchase_current") {
    itemAction = { name: "purchase_current" };
  } else if (parsed.action === "inspect_current") {
    itemAction = { name: "inspect_current" };
  }

  const facets = Object.fromEntries(
    candidates
      .filter((item): item is StateUpdateCandidate & { target: { kind: "facet"; facet: SPNFacetName } } => item.target.kind === "facet")
      .map((item) => [item.target.facet, item])
  ) as IntentResult["facets"];

  return {
    utterance,
    intents: parsed.intent,
    facets,
    candidates,
    itemAction,
    supersedes,
    residualColorChoice: Boolean(parsed.residualColorChoice)
  };
}
