import type {
  DialogueState,
  IntentResult,
  PreferenceValue,
  Product,
  RankedProduct,
  SPNFacetName,
  StateDiff,
  StateUpdateCandidate
} from "@/lib/types";

/**
 * RA-Rec State Manager (MEMORY)
 *
 * 입력  : SPN Understanding의 상태 갱신 후보 (IntentResult)
 * 출력  : Dialogue State JSON, State Diff + Provenance
 *
 * 이 단계는 발화를 파싱하지 않는다. 후보를 병합하고 origin·status·turn 근거를 관리하며,
 * 관심 · 거절 · 구매 이력과 우선순위·모순을 처리한 뒤 변경 경로를 남긴다.
 */

const facetNames: SPNFacetName[] = ["subjective_property", "event", "activity", "goal_purpose", "goal_audience"];

export function createInitialDialogueState(): DialogueState {
  return {
    hardConstraints: {}, softConstraints: {},
    subjectiveNeeds: Object.fromEntries(facetNames.map((name) => [name, null])) as DialogueState["subjectiveNeeds"],
    tradeoffs: [], recommendedItems: [], shortlistedItems: [], inspectedItems: [], rejectedItems: [], cartedItems: [], purchasedItems: [],
    unresolvedPreferences: [], preferenceHistory: []
  };
}

/** 후보를 turn 근거가 붙은 PreferenceValue로 승격한다. status는 origin에서 파생된다. */
function toPreference(candidate: Pick<StateUpdateCandidate, "id" | "valueText" | "origin" | "confidence">, turnId: string): PreferenceValue {
  return {
    id: candidate.id,
    valueText: candidate.valueText,
    origin: candidate.origin,
    confidence: candidate.confidence,
    status: candidate.origin === "inferred" ? "unconfirmed" : "confirmed",
    evidenceTurnIds: [turnId],
    updatedAtTurnId: turnId
  };
}

function changedFrom(previous: PreferenceValue | null | undefined, next: PreferenceValue) {
  return !previous || previous.valueText !== next.valueText || previous.status !== next.status;
}

/** 마지막 음절의 종성 유무로 주제 조사를 고른다. 한글이 아니면 두 형태를 함께 쓴다. */
function topicParticle(word: string) {
  const last = word.trim().at(-1) ?? "";
  const code = last.charCodeAt(0);
  if (code >= 0xac00 && code <= 0xd7a3) return (code - 0xac00) % 28 === 0 ? "는" : "은";
  return "은(는)";
}

function productAt(rankings: RankedProduct[], index: number) { return rankings.find((item) => item.rank === index)?.productId; }

export function updateDialogueState(
  previous: DialogueState,
  intent: IntentResult,
  rankings: RankedProduct[],
  turnId: string,
  products: Product[] = []
): { state: DialogueState; diff: StateDiff } {
  const changed: string[] = [];
  const state: DialogueState = structuredClone(previous);

  const applyCandidate = (candidate: StateUpdateCandidate) => {
    const value = toPreference(candidate, turnId);
    const { target } = candidate;
    if (target.kind === "category") {
      if (changedFrom(state.category, value)) changed.push("category");
      state.category = value;
      return;
    }
    if (target.kind === "facet") {
      if (changedFrom(state.subjectiveNeeds[target.facet], value)) changed.push(`subjectiveNeeds.${target.facet}`);
      state.subjectiveNeeds[target.facet] = value;
      return;
    }
    const record = target.scope === "hard" ? state.hardConstraints : state.softConstraints;
    const path = target.scope === "hard" ? "hardConstraints" : "softConstraints";
    if (changedFrom(record[target.key], value)) changed.push(`${path}.${target.key}`);
    record[target.key] = value;
  };

  intent.candidates.forEach(applyCandidate);

  // 확정 근거가 확보된 슬롯의 기존 inferred / unconfirmed 값을 superseded로 전이시킨다.
  intent.supersedes.forEach((key) => {
    const existing = state.softConstraints[key];
    if (!existing || existing.origin !== "inferred" || existing.status !== "unconfirmed") return;
    state.softConstraints[key] = { ...existing, status: "superseded", updatedAtTurnId: turnId, evidenceTurnIds: [...existing.evidenceTurnIds, turnId] };
    changed.push(`softConstraints.${key}`);
  });

  const first = productAt(rankings, 1);
  const second = productAt(rankings, 2);
  const action = intent.itemAction;

  if (action?.name === "reject_first" && first && action.rejectionReason) {
    const reason = toPreference({ ...action.rejectionReason, origin: "explicit", confidence: 1 }, turnId);
    state.rejectedItems = [...state.rejectedItems.filter((item) => item.productId !== first), { productId: first, reason }];
    changed.push("rejectedItems");
  }

  if (action?.name === "compare_first_second" && first && second) {
    state.shortlistedItems = [...new Set([...state.shortlistedItems, first, second])];
    changed.push("shortlistedItems");
  }

  if (action?.name === "inspect_current" && first) {
    state.inspectedItems = [...new Set([...state.inspectedItems, first])];
    state.shortlistedItems = [...new Set([...state.shortlistedItems, first])];
    state.currentItem = first;
    changed.push("inspectedItems", "shortlistedItems", "currentItem");
  }

  // 색상 이름은 Understanding이 아니라 현재 상품의 metadata에서 읽는다.
  if (intent.residualColorChoice) {
    const target = products.find((product) => product.id === (state.currentItem ?? first));
    const color = target?.metadata.availableColors[0];
    const value = toPreference({
      id: "color-residual",
      valueText: color ? `${color}${topicParticle(color)} 선호가 아니라 즉시 수령 가능한 잔여 색상` : "즉시 수령 가능한 잔여 색상 수용",
      origin: "implicit",
      confidence: 0.95
    }, turnId);
    if (changedFrom(state.softConstraints.colorChoice, value)) changed.push("softConstraints.colorChoice");
    state.softConstraints.colorChoice = value;
  }

  if (action?.name === "purchase_current" && state.currentItem) {
    state.purchasedItems = [...new Set([...state.purchasedItems, state.currentItem])];
    changed.push("purchasedItems");
    const tradeoff = toPreference({
      id: "tradeoff-delivery-over-price",
      valueText: "빠른 수령과 장기 사용 가치를 우선하고 가격·색상 선택을 양보",
      origin: "explicit",
      confidence: 1
    }, turnId);
    if (!state.tradeoffs.some((item) => item.id === tradeoff.id)) {
      state.tradeoffs = [...state.tradeoffs, tradeoff];
      changed.push("tradeoffs");
    }
  }

  const uniqueChanges = [...new Set(changed)];
  state.preferenceHistory = [...state.preferenceHistory, { turnId, changedPaths: uniqueChanges }];
  return { state, diff: { changedPaths: uniqueChanges, summary: uniqueChanges.length ? uniqueChanges.map((path) => `${path} 갱신`) : ["인식 가능한 상태 변경 없음"] } };
}
