import type { PolicyDecision } from "@/lib/types";

export function composeQuestion(policy: PolicyDecision) {
  if (!policy.questionTarget) return "어떤 기준을 가장 중요하게 보시는지 알려주세요.";
  if (policy.questionTarget.field === "가격 기준") return "교체가 필요한 상황이라면 후보를 빨리 좁히는 게 좋겠어요. 예산은 어느 정도로 생각하고 계신가요?";
  if (policy.questionTarget.field === "사용 기간 기준") return "새 기기를 얼마나 오래 쓸 계획인가요? 오래 쓸 계획이라면 성능과 지원 기간에 더 비중을 둘 수 있어요.";
  return "언제까지 받아야 하는지도 알려주시겠어요? 모델마다 재고 상황이 달라 수령까지 걸리는 시간이 크게 차이 납니다.";
}
