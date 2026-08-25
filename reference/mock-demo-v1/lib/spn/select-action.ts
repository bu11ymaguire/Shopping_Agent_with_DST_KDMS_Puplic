import { computeVagueness } from "@/lib/spn/compute-vagueness";
import type { DialogueState, PolicyDecision, SPNSnapshot } from "@/lib/types";

/**
 * SPN Policy (DECIDE)
 *
 * 입력  : RA-Rec Dialogue State
 * 출력  : 분기 결정 — clarify-lane(정보 부족) 또는 recommend-lane(정보 충분)
 *
 * 모호성과 누락 정보 판단은 이 단계의 책임이다. facet 스냅숏도 여기서 만들어
 * 판단 근거로 함께 반환한다.
 */
export function selectAction(state: DialogueState): PolicyDecision {
  const snapshot: SPNSnapshot = { facets: structuredClone(state.subjectiveNeeds), vagueness: computeVagueness(state) };
  const missing = [
    !state.hardConstraints.budget ? { field: "가격 기준", reason: "가격 범위가 후보군을 가장 크게 바꿉니다.", priority: 25 } : null,
    !state.subjectiveNeeds.subjective_property ? { field: "사용 기간 기준", reason: "얼마나 오래 쓸 계획인지에 따라 성능과 지원 기간의 비중이 달라집니다.", priority: 20 } : null,
    !state.hardConstraints.deliveryDeadline ? { field: "수령 시점", reason: "모델별 재고 상황이 달라 수령까지 걸리는 시간이 크게 차이 납니다.", priority: 12 } : null
  ].filter(Boolean) as DialogueState["unresolvedPreferences"];

  if (snapshot.vagueness.total > snapshot.vagueness.threshold && missing.length) {
    const questionTarget = [...missing].sort((a, b) => b.priority - a.priority)[0];
    return { action: "ask_user", lane: "clarify-lane", questionTarget, reasons: snapshot.vagueness.reasons, snapshot };
  }
  return { action: "recommend", lane: "recommend-lane", reasons: ["현재 상태가 모호성 임계값 이하이므로 검색과 추천을 진행합니다."], snapshot };
}
