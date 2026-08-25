export type SPNFacetName =
  | "subjective_property"
  | "event"
  | "activity"
  | "goal_purpose"
  | "goal_audience";

export type EvidenceOrigin = "explicit" | "implicit" | "inferred";
export type EvidenceStatus = "confirmed" | "unconfirmed" | "superseded";

export type PreferenceValue = {
  id: string;
  valueText: string;
  origin: EvidenceOrigin;
  confidence: number;
  status: EvidenceStatus;
  evidenceTurnIds: string[];
  updatedAtTurnId: string;
};

export type SPNState = Record<SPNFacetName, PreferenceValue | null>;

export type RejectedItem = { productId: string; reason: PreferenceValue };

/* ------------------------------------------------------------------ *
 * SPN Understanding (PLAN) 의 출력 계약
 *
 * Understanding은 발화와 이전 턴 상태만 보고 "상태 갱신 후보"를 만든다.
 * 후보에는 값(valueText)과 근거 종류(origin, confidence)까지 담기지만,
 * 실제 병합·status 전이·이력 기록은 RA-Rec State Manager (MEMORY) 의 책임이다.
 * ------------------------------------------------------------------ */

/** 어떤 상태 슬롯을 갱신할 후보인지 나타낸다. */
export type CandidateTarget =
  | { kind: "category" }
  | { kind: "facet"; facet: SPNFacetName }
  | { kind: "constraint"; scope: "hard" | "soft"; key: string };

export type StateUpdateCandidate = {
  target: CandidateTarget;
  id: string;
  valueText: string;
  origin: EvidenceOrigin;
  confidence: number;
};

export type ItemActionName =
  | "inspect_current"
  | "reject_first"
  | "compare_first_second"
  | "purchase_current";

export type ItemActionCandidate = {
  name: ItemActionName;
  /** 거절 행동일 때 함께 기록할 이유. 이유의 종류(상황적 제약 / 상품 속성)를 문구에 담는다. */
  rejectionReason?: { id: string; valueText: string };
};

export type IntentResult = {
  utterance: string;
  /** 의도 분류 결과 라벨. */
  intents: string[];
  /** SPN 5 facets 중 이번 발화에서 확인된 값. */
  facets: Partial<Record<SPNFacetName, StateUpdateCandidate>>;
  /** category · hard · soft 슬롯에 대한 상태 갱신 후보. facets 후보도 함께 포함한다. */
  candidates: StateUpdateCandidate[];
  /** 상품 단위 행동(상세 보기 · 거절 · 비교 · 구매). */
  itemAction?: ItemActionCandidate;
  /**
   * 이번 발화로 확정 근거가 확보되어, 기존 inferred / unconfirmed 값을 대체해야 하는 soft 슬롯 키.
   * State Manager가 해당 값을 superseded로 전이시킨다.
   */
  supersedes: string[];
  /**
   * 색상처럼 값이 카탈로그에만 있는 경우. Understanding은 "잔여 옵션을 수용하는 상황"만 인식하고,
   * 실제 색상 이름은 State Manager가 현재 상품의 metadata에서 읽는다.
   */
  residualColorChoice: boolean;
};

export type DialogueState = {
  category?: PreferenceValue;
  hardConstraints: Record<string, PreferenceValue>;
  softConstraints: Record<string, PreferenceValue>;
  subjectiveNeeds: SPNState;
  tradeoffs: PreferenceValue[];
  recommendedItems: string[];
  shortlistedItems: string[];
  inspectedItems: string[];
  rejectedItems: RejectedItem[];
  cartedItems: string[];
  purchasedItems: string[];
  currentItem?: string;
  unresolvedPreferences: Array<{ field: string; reason: string; priority: number }>;
  preferenceHistory: Array<{ turnId: string; changedPaths: string[] }>;
};

export type Product = {
  id: string;
  title: string;
  image: string;
  brand: string;
  price: number;
  rating: number;
  reviewCount: number;
  description: string;
  shipping: string;
  stock: string;
  metadata: {
    category: string;
    display: string;
    chipset: string;
    storage: string;
    /** 그램 단위 무게. 휴대성 점수의 입력값이다. */
    weightGrams: number;
    /** 영상 재생 기준 배터리 지속 시간(시간). */
    batteryHours: number;
    /** 스피커 품질 등급 1~5. */
    speakerTier: number;
    /** 성능 등급 1~5. */
    performanceTier: number;
    /** 예상 소프트웨어 지원 연수. 장기 사용 가치의 입력값이다. */
    longevityYears: number;
    /** 주문 후 수령까지 걸리는 일수. 재고 상황이 반영된 값이다. */
    deliveryDays: number;
    /** 지금 주문하면 즉시 수령 가능한 색상. 비어 있으면 잔여 재고가 없다. */
    availableColors: string[];
    useCases: string[];
  };
};

export type Review = {
  id: string;
  productId: string;
  text: string;
  helpfulness?: number;
  source: "mock";
};

export type RankedReview = Review & {
  similarityScore: number;
  preferenceCoverageScore: number;
  reliabilityScore: number;
  totalScore: number;
  matchedPreferenceIds: string[];
};

export type ScoreBreakdown = {
  hardConstraintMatch: number;
  metadataMatch: number;
  subjectiveNeedMatch: number;
  reviewEvidenceScore: number;
  evidenceReliability: number;
  total: number;
};

export type RankedProduct = {
  productId: string;
  rank: number;
  score: ScoreBreakdown;
  evidenceReviewIds: string[];
  matchedPreferenceIds: string[];
};

export type StateDiff = { changedPaths: string[]; summary: string[] };

export type VaguenessBreakdown = {
  categoryBreadth: number;
  missingRequiredInfo: number;
  unresolvedSPN: number;
  contradictionPenalty: number;
  total: number;
  threshold: number;
  reasons: string[];
};

export type SPNSnapshot = { facets: SPNState; vagueness: VaguenessBreakdown };

export type PolicyLane = "clarify-lane" | "recommend-lane";

export type PolicyDecision = {
  action: "ask_user" | "recommend";
  /** 아키텍처 다이어그램의 분기 레인 식별자. */
  lane: PolicyLane;
  questionTarget?: { field: string; reason: string; priority: number };
  reasons: string[];
  /** Policy가 판단 근거로 계산한 facet 스냅숏과 모호성 점수. */
  snapshot: SPNSnapshot;
};

export type BrowseResult = { products: Product[]; reviews: Review[]; images: string[]; priceEvidence: string[]; deliveryEvidence: string[] };

export type RecommendationResponse = {
  updatedDialogueState: DialogueState;
  rankedProducts: RankedProduct[];
  reviewEvidence: RankedReview[];
  explanation: string;
  unresolvedPreferences: DialogueState["unresolvedPreferences"];
};

export type FinalResponse = {
  message: string;
  productCards: RankedProduct[];
  priceEvidence: string[];
  deliveryEvidence: string[];
  imageEvidence: string[];
  reviewEvidence: RankedReview[];
  nextActions: string[];
};

export type IntentHypothesis = { id: string; text: string; confidence: number; evidence: string[] };

export type ConversationTurn = {
  id: string;
  user: string;
  assistant: string;
  intent: IntentResult;
  state: DialogueState;
  diff: StateDiff;
  policy: PolicyDecision;
  query: string | null;
  browseResult: BrowseResult | null;
  recommendation: RecommendationResponse | null;
  finalResponse: FinalResponse;
  previousRankings: RankedProduct[];
  hiddenIntentHypotheses: IntentHypothesis[];
};
