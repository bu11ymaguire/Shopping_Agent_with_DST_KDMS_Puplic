# SPN Orchestrator / RA-Rec 쇼핑 데모

사용자 발화와 피드백이 **대화 상태 → 숨은 의도 가설 → 다음 행동 → 리뷰 근거 → 점수와 순위 → 최종 응답**으로 이어지는 Next.js 프론트엔드 데모입니다.

실제 LLM, 쇼핑 API, 크롤러, 결제, 서버 데이터베이스는 사용하지 않습니다. 모든 처리는 브라우저에서 실행되는 로컬 mock 데이터와 결정론적 TypeScript 함수로 재현됩니다.

## 배포된 데모

Vercel 프로덕션 배포: [https://agent-iota-five.vercel.app/](https://agent-iota-five.vercel.app/)

별도의 API 키나 서버 환경 변수 없이 동작하는 프론트엔드 데모입니다. GitHub `main` 브랜치에 변경 사항을 push하면 Vercel이 자동으로 새 배포를 만듭니다.

## 의존성

- Node.js LTS와 npm이 필요합니다.
- 런타임은 Next.js 15와 React 19를 사용합니다.
- 개발·검증 도구로 TypeScript, ESLint, Tailwind CSS를 사용합니다.
- 정확한 패키지 버전은 [`package-lock.json`](./package-lock.json)으로 고정되어 있으며, 설치에는 `npm ci`를 사용합니다.

## 실행

```powershell
npm ci        # 최초 1회 또는 node_modules를 새로 설치할 때
npm run dev
```

개발 서버가 표시하는 주소(보통 `http://localhost:3000`)를 엽니다.

## 검증

```powershell
npx tsc --noEmit
npm run lint
npm run build
```

## 화면을 읽는 법

| 영역 | 데모에서 보여주는 것 |
| --- | --- |
| 왼쪽: User conversation | 전송 버튼 하나로 현재 단계에 맞는 다음 데모 발화를 진행합니다. 상세 보기·거절·구매 행동도 다음 추천에 반영됩니다. |
| 가운데: Live decision trace | 아키텍처 다이어그램과 같은 노드 구성을 렌더링합니다. 공통 3노드(PLAN → MEMORY → DECIDE) 아래에 Clarify Path와 Recommendation Path를 나란히 두고, 이번 턴에 실행된 레인만 강조합니다. IntentResult 요약과 모호성 점수도 함께 표시합니다. |
| 오른쪽: User insight | 현재 이해한 요구, 숨은 의도 가설, 탈락 이유와 우선순위, 다음 행동과 이유, State Diff, 리뷰 근거와 상품 점수를 표시합니다. |

**탈락 이유와 우선순위** 카드는 거절된 후보를 이유와 함께 보여 줍니다. 이 데모의 두 거절은 서로 다른 종류입니다. 하나는 재고 때문에 생긴 **상황적 제약**이고, 다른 하나는 리뷰에서 확인된 **상품 속성**입니다. 구매를 확정하면 이 카드에 우선한 조건과 양보한 조건(`tradeoffs`)이 함께 남습니다.

SPN은 에메랄드 계열로, RA-Rec은 블루 계열로 표시합니다.

## 처리 흐름

구조의 정본은 [`episode/spn-ra-rec-architecture-annotated.html`](./episode/spn-ra-rec-architecture-annotated.html)입니다. 그 문서의 `architecture-graph` JSON이 노드 id, owner, role, 입출력, 엣지를 정의하고 코드가 그 대응을 따릅니다.

```text
사용자 발화 (INPUT)
  → SPN Understanding (PLAN)        → IntentResult · 상태 갱신 후보
  → RA-Rec State Manager (MEMORY)   → DialogueState + Diff   (모든 발화 뒤 실행)
  → SPN Policy (DECIDE)             → clarify-lane 또는 recommend-lane
     ├─ clarify-lane (정보 부족)
     │    → SPN Response Composer (RESPOND) → 추가 질문
     └─ recommend-lane (정보 충분)
          → RA-Rec Query Generator (QUERY)        → 추천 질의
          → SPN Browsing Actions (ACT)            → 상품 · 리뷰 · 가격 · 배송
          → RA-Rec Recommendation Engine (RANK)   → RankedProducts + 근거
          → SPN Response Composer (RESPOND)       → 추천 결과
  → 에이전트 응답 (OUTPUT) → 다음 턴 발화로 재입력 (LOOP)
```

| 노드 | role | owner | 구현 |
| --- | --- | --- | --- |
| SPN Understanding | PLAN | SPN | `lib/spn/understand.ts` |
| RA-Rec State Manager | MEMORY | RA-Rec | `lib/rarec/state-manager.ts` |
| SPN Policy | DECIDE | SPN | `lib/spn/select-action.ts` |
| SPN Response Composer (Clarify) | RESPOND | SPN | `composeClarifyResponse` |
| RA-Rec Query Generator | QUERY | RA-Rec | `lib/rarec/generate-query.ts` |
| SPN Browsing Actions | ACT | SPN | `lib/spn/browse.ts` |
| RA-Rec Recommendation Engine | RANK | RA-Rec | `lib/rarec/rank-products.ts` |
| SPN Response Composer (Recommend) | RESPOND | SPN | `composeRecommendResponse` |

단계 경계는 다음과 같습니다. Understanding은 상태를 쓰지 않고 **상태 갱신 후보**만 만듭니다(슬롯·id·valueText·origin·confidence까지 결정). State Manager는 발화를 파싱하지 않고 **병합 의미론**만 담당합니다(turn 근거, status 전이, 행동 이력, Diff). Policy의 입력은 `DialogueState` 하나이며 모호성 점수도 이 단계에서 계산합니다. 가운데 패널의 Live decision trace가 이 노드 구성을 그대로 렌더링하고, 현재 턴에서 실행된 레인만 강조합니다.

### 숨은 의도 가설

화면의 **숨은 의도 가설**은 확정 사실이 아닙니다. 명시한 조건과 대화 행동을 근거로 confidence를 함께 보이는 가설입니다.

예시:

- 기기 고장 → 사양보다 수령 시점이 먼저 걸릴 가능성 (아직 확인되지 않아 랭킹에는 반영하지 않음)
- 재고 때문에 후보를 내림 → 수령 대기 시간이 실질적 결정 변수
- 장기 사용 + 예산 유연성 → 가격 자체보다 사용 기간 대비 비용으로 판단
- 리뷰 기반 거절 → 반복 지적된 단점이 사양표상 우위보다 강한 탈락 기준
- 잔여 색상 구매 → 색상 선호가 아니라 즉시 수령이 가능한 잔여 대안

## 데모 시나리오

이 데모는 [`episode/iPhone.html`](./episode/iPhone.html)에 정리된 iPhone 17 Pro 구매 사례를 대화로 재현합니다. 사례의 요지는 **관찰된 구매 결과만으로 선호를 추론하면 어긋난다**는 것입니다. 최고가 모델과 특정 색상을 구매했지만 실제 결정 변수는 기기 고장에서 온 수령 시점이었고, 색상은 선택이 아니라 잔여 옵션이었습니다.

1. “쓰던 아이폰이 고장 나서 새로 바꿔야 해요.”
   - 고장을 `event`로, 교체를 `goal_purpose`로 기록합니다. 긴급도는 아직 확인되지 않았으므로 `inferred / unconfirmed` 가설로만 남고 랭킹에는 반영되지 않습니다.
   - 모호성 점수(57)가 질문 임계값(45)보다 높아 가격 기준을 먼저 묻습니다.
2. “150만 원 정도 생각하는데, 오래 쓸 거면 조금 더 써도 괜찮아요.”
   - 예산 상한, 상한 초과 수용, 장기 사용 기준을 기록합니다. 상한 초과를 수용하므로 예산은 통과·탈락이 아니라 초과율 감점으로 계산됩니다.
   - 가격과 성능 균형이 좋은 **iPhone 17**이 1위가 됩니다.
3. “첫 번째 제품은 재고가 없어서 2주 뒤에나 받는대요. 지금 쓸 폰이 없어서 그건 안 돼요.”
   - 재고 지연을 이유로 1위를 거절하고, 이 시점에 수령 기한이 **하드 제약으로 승격**됩니다. 1턴의 inferred 긴급도 가설은 `superseded`로 바뀝니다.
   - 대기가 필요한 모델이 하드 제약에서 탈락하고 **iPhone Air**가 새 1위가 됩니다.
4. “이건 스피커랑 배터리가 아쉽다는 후기가 많네요.”
   - 리뷰에서 확인한 상품 속성을 이유로 다시 거절하고 스피커·배터리 기준을 추가합니다.
   - 남은 후보 중 두 속성이 가장 높은 **iPhone 17 Pro**가 1위가 됩니다. 예산 상한을 넘지만 유연성 덕분에 후보로 남습니다.
5. “그럼 추천해주신 제품을 자세히 볼게요.”
   - 17 Pro를 `inspectedItems`, `shortlistedItems`, `currentItem`에 기록합니다. 응답은 즉시 수령이 가능한 색상이 코스믹 오렌지뿐임을 알려 줍니다.
6. “색상은 지금 받을 수 있는 게 코스믹 오렌지뿐이네요. 그럼 이걸로 살게요.”
   - `purchasedItems`에 기록하고, 색상을 선호가 아닌 **잔여 옵션**으로 저장합니다.
   - `tradeoffs`에 “빠른 수령과 장기 사용 가치를 우선하고 가격·색상 선택을 양보”가 남습니다.

두 거절의 종류가 다른 것이 이 시나리오의 핵심입니다. 3턴은 재고에서 온 **상황적 제약**, 4턴은 리뷰에서 확인된 **상품 속성**입니다. 같은 “거절”로 뭉개면 이 차이가 사라집니다.

상세 보기와 구매도 갱신된 상태를 반영한 추천 파이프라인을 실행합니다. 다만 응답은 상품 행동별로 달라집니다.

Vagueness Score의 전체 산식, 첫 턴의 57점 계산, 질문 정책 규칙은 [Interaction.md의 부록](./interaction.md#appendix--vagueness-score--policy-rule)을 참고하세요. 이 점수는 논문 공식을 그대로 재현한 값이 아니라, 상태 변화와 정책 결정을 설명하기 위한 local mock 휴리스틱입니다.

## 구현 범위

이 프로젝트는 프론트엔드 데모입니다. `lib/mock`, `lib/spn`, `lib/rarec`의 코드는 서버 내부 파이프라인이 아니라 화면에 상태 변화와 추천 근거를 재현하기 위한 클라이언트 측 순수 함수입니다.

- `app/page.tsx`: 파이프라인 배선과 3패널 UI 조립
- `lib/types.ts`: `IntentResult` 계약, 상태, 리뷰 근거, 랭킹, 정책 결정, 숨은 의도 가설 타입
- `lib/mock/*`: mock 어휘 계층 — 발화 정규식 파싱과 동의어 그룹
- `lib/spn/*`: Understanding, Policy, 모호성, browsing, 응답 구성(clarify/recommend), 의도 가설
- `lib/rarec/*`: State Manager, 질의 생성, 리뷰·상품 랭킹, 추천 설명
- `data/products.json`, `data/reviews.json`: 로컬 mock 카탈로그와 정규화된 리뷰
- `episode/spn-ra-rec-architecture-annotated.html`: 파이프라인 구조의 정본 다이어그램
- `episode/iPhone.html`: 현재 데모 시나리오의 원자료인 구매 사례 정리 문서

상품 순위는 `lib/rarec/rank-products.ts`의 일반 점수 함수가 계산합니다. 상품 ID별 보너스나 시나리오 전용 가점은 두지 않고, 선호 id를 상품 속성(수령 소요일, 성능 등급, 지원 연수, 배터리 시간, 스피커 등급, 무게, 가격)에 매핑해 mock fixture의 속성 차이로 순위가 바뀌게 했습니다. 확정되지 않은 `inferred / unconfirmed` 선호는 근거로만 표시되고 점수에는 들어가지 않습니다.

`data/scenario.json`, `index.html`, `app.js`, `styles.css`는 이전 프로토타입 참고 파일이며 현재 실행 경로에서는 사용하지 않습니다.
