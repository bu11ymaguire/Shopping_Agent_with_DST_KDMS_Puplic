# SPN Orchestrator / RA-Rec — 실데이터 + LLM 전환 인수인계

> **이 문서의 성격**
> 원래 이 저장소에는 `agent-main/`(Next.js mock 데모 일체)과 `Using_Luxia_API-main/`(제3자 API 가이드)이
> 있었고, 사용자 지시로 삭제되었다. 개발 중에는 목업 원본을 읽기 전용 스냅숏으로 잠시 보존했지만,
> 공개본에서는 다시 제거했다. 목업에서 필요한 계약과 교정 사항은 이 문서에 자립적으로 남아 있다.
> 이 문서는 "진행상황 메모"이면서, 과거 목업의 알려진 버그와 레거시를 교정한
> **현행 계약·수식·픽스처의 최우선 정본**이다.
>
> 뒤이어 작업하는 에이전트는 §1~§3으로 목적과 현재 상태를 파악하고, 구현할 때 §4~§7을 계약으로 삼고,
> §8의 함정을 먼저 확인한 뒤, §9의 순서로 진행하면 된다. 부록 A·B는 재현·회귀 검증용 원본 데이터다.

---

## 1. 연구 목적

한 문장으로: **관찰된 구매 결과만으로는 지속적 선호와 구매 시점의 상황적 제약을 구분할 수 없다.**

이걸 보여주는 실제 사례가 있다. 어떤 사용자가 iPhone 17 Pro(최고가)를 코스믹 오렌지 색상으로 구매했다.
구매 로그만 보면 "Pro 라인업 선호 + 오렌지 색상 선호"로 읽힌다. 실제로는 둘 다 틀렸다.

| 구매 결과만으로 도출되는 추론 | 실제로 확인된 사실 |
| --- | --- |
| iPhone 재구매 → Apple 선호 | 기존 기기가 고장 나 교체가 필요했다 |
| Pro 라인업 선호 → 성능 중시 | 17 Pro는 검토 후보 중 **즉시 수령이 가능한** 제품이었다 |
| Cosmic Orange 구매 → 색상 선호 | 즉시 수령이 가능한 **잔여** 색상이었다 |

시스템의 목표는 구매 결과가 아니라 **결과에 이르는 과정을 상태로 남기는 것**이다.
"오렌지를 좋아함"이 아니라 "수령 기한이 하드 제약이었고, 색상은 양보했음"이 상태에 적혀야 한다.

### 사례의 후보 탈락 구조 (핵심)

세 후보가 탈락했는데 **탈락 이유의 종류가 서로 다르다.** 이걸 같은 "거절"로 뭉개면 연구 가치가 사라진다.

| 단계 | 결정을 만든 요인 | 유형 |
| --- | --- | --- |
| iPhone 17 탈락 | 재고 부족과 배송 지연 | **상황적 제약** |
| iPhone Air 탈락 | 스피커·배터리 성능 | **상품 속성** |
| iPhone 17 Pro 선택 | 즉시 수령 가능성 + 장기 사용 가치 | 우선순위 |
| Cosmic Orange | 즉시 수령이 가능한 잔여 대안 | 대안 선택 |

### Trade-off (독립 선호가 아니라 하나의 순서 관계)

```
빠른 수령 · 장기 사용 가치   >   가격 · 색상 옵션
```

우선한 것: 빠른 수령 가능성, 장기 사용 가치
양보한 것: 가격(최고가 수용), 색상(선택하지 않고 수용)

### 사례의 한계 (원문에 명시된 범위 한정)

확보된 근거는 사용자 발화 / 후보 탐색 행동 / 비선택 이유 / 최종 행동 네 가지다.
**반사실적 질문("17을 즉시 받을 수 있었다면 선택이 달라졌는가")은 확보되지 않았다.**
단일 자기보고 사례이고 네 근거가 같은 화자의 회상에서 나왔으므로 서로 독립적이지 않다.
따라서 이 사례는 일반적 결론의 근거가 아니라 **정의를 도출하기 위한 예시**로만 쓴다.

출처: Hanyang HCC Lab, Summer Intern week5 사례 정리.

---

## 2. 현재 저장소 상태

```
ShoppingAgent_with_DST_KDMS/
  README.md            저장소 진입점·문서 라우터
  AGENTS.md            문서 우선순위·작업 규칙
  flow.md              ← 이 문서
  manual.md            기술 스택 조사 문서 (유지됨, 읽을 것)
  backend/             Python LLM 계층 + Understanding 스키마·노드 (§6)
  presentation/        개발 과정의 발표 자료 (PPTX/PDF, 런타임과 분리)
```

- 배포 위치에 따라 git remote가 달라질 수 있다. 현재 상태와 원격은 `git log`, `git status`,
  `git remote -v`로 확인한다.
- `manual.md`는 남아 있다. 기술 스택 선택 근거(LangGraph vs LangChain, Amazon Reviews 2023, ESCI, pgvector 등)가
  거기 있으므로 반드시 함께 읽어야 한다.
- `presentation/`은 개발 당시 설명을 보존하는 자료다. 현행 계약·결과와 충돌하면 이 문서와
  동결된 fixture·manifest를 우선한다.
- 개발 환경: Windows / PowerShell, Python 3.12, Node v22.17.0, npm 10.9.2.
  설치 확인됨: httpx 0.28.1, pydantic 2.12.5, python-dotenv 1.2.1, fastapi 0.128.0,
  uvicorn 0.40.0, langgraph 1.0.5, sentence-transformers 5.6.0, torch 2.13.0 CPU.

### 현재 다음 과제

Luxia capability probe부터 실제 117개 상품·7,552개 리뷰의 end-to-end MVP, fixed semantic
retrieval, frozen tablet-domain v2.3과 untouched multi-turn holdout 20개·81턴까지 완료했다.
2026-08-08 clean commit에서 동일 holdout을 Full → No-memory → No-review 순서로 각 한 번 실행했고,
raw report와 조건별 LLM trace를 동결했다. 이 official run은 다시 실행하지 않는다.

2026-08-09 평가 프로토콜을 human relevance primary에서 **process-level automatic benchmark
primary**로 전환했다. evaluator는 저장된 raw result/trace만 읽고 모든 81턴을 분모에 넣는다.
schema failure 이후 미출력 turn도 실패로 집계한다. Full/No-memory의 Final State F1은
0.758/0.500, State Diff F1은 0.562/0.700, Policy는 0.802/0.741, recommendation reach는
0.808/0.744, hard-filter completion은 0.630/0.444다. 20-scenario paired bootstrap에서 Final
State difference는 +0.258 [0.172, 0.334], State Diff는 -0.138 [-0.222, -0.056]이다. persistent
memory의 누적 보존 이점과 previous-state over-extraction을 함께 보고한다.

독립 No-review는 GPT upstream identity가 0.721이라 causal comparison으로 사용하지 않는다.
Full의 state/query/candidate set을 고정하고 review score/reliability만 제거한 결과 top-1은 55.6%,
top-3 order는 87.3% 바뀌고 mean top-3 Jaccard는 0.522였다. 이 변화는 review contribution을
보여주지만 human relevance가 없으므로 품질 향상이라고 주장하지 않는다. evaluator-side Gold-State
oracle은 Policy/reach/hard-filter를 각각 1.0으로 회복했지만 diagnostic upper bound다.

포스터 제출 뒤인 2026-08-16에는 이 Gold-State oracle을 에피소드 종료 추천 리스트까지 확장한
**post-hoc secondary diagnostic**을 별도로 실행했다. `final_gold_state_ids`와 턴별 Gold scope,
`final_expected_hard_filters`로 최종 상태를 재구성한 뒤 기존 Query, 실제 catalog filter, 고정
semantic review retrieval/Cross-Encoder, 결정론적 Rank만 실행했으며 추가 LLM 호출은 0회다.
20개 중 19개에서 추천이 생성됐고 Oracle Top-3 54개의 Gold hard-filter 위반은 0건, review
fallback은 0건이었다. Oracle 대비 Full/No-memory의 비교 가능 에피소드는 15/13개, Top-1
일치율은 0.333/0.077, exact Top-3 order는 0.333/0.000, mean Jaccard는 0.540/0.133이다.
이는 state-to-ranking 전달 충실도이지 Gold 상품이나 인간 relevance 정답이 아니다. 기존 official
raw와 primary 결과는 수정하거나 재실행하지 않았으며 별도 post-hoc report/manifest에 보존한다.

기존 두 종류의 3인용 blind packet, private provenance, agreement/NDCG 코드는 삭제하지 않고
**Optional / Future Human Relevance Evaluation**으로 보존한다. Product/Review NDCG는 이번
포스터 official result로 계산하지 않는다. 다음 과제는 automatic 결과를 포스터 narrative/figure로
정리하고 limitation을 명확히 쓰는 것이다. 규모·배포 요구가 생기면 PostgreSQL/pgvector로 옮긴다.
inventory snapshot, 세션 영속화, 인증은 아직 없다.

2026-08-08에는 이 20개×4턴을 frozen state 주입 없이 실제 Luxia 전체 workflow로 처음 재생했다.
19/20 시나리오·79/80턴이 완주했고 recommend-lane reach는 8/20, conditional hard-filter
correctness는 8/8, canonical ID micro-F1은 0.658이었다.
완료됐지만 추천하지 못한 11개는 모두 `supported category` clarify였고 1개 시나리오는 strict schema
검증에 실패했다. 반면 추천된 24개는 기대 hard-filter 위반 0, evidence 일치 1.0, LLM response와
review retrieval fallback 0이었다. 기존 packet은 gold-state retrieval/ranking 평가이며 이 live
end-to-end 결과와 같지 않다. 첫 실행 hash와 요약은
`backend/data/manifests/poster_live_replay_luxia_v1.json`에 동결했으며 같은 holdout에 prompt를
재튜닝하지 않는다.

같은 날 v1과 분리한 26건 dev에서만 v2를 개발했다. 연구 범위는 태블릿 쇼핑 환경으로 고정하며
`category_tablet`은 발화 추출값이 아니라 모든 비교 조건의 environment state다. 명시적 타 상품군은
`unsupported_category`로 추천 전에 차단한다. latent `subjective_*` 생성은 이번 v2 평가에서
비활성화하고, LLM의 canonical ID에서 target을 결정론적으로 유도한다. 선택한 v2.3 dev 결과는
validation/domain route 1.000, canonical micro-F1 0.807이지만 최종 성능이 아니다. 동결 계약은
`backend/data/manifests/tablet_domain_v2_freeze.json`과
`backend/docs/tablet_domain_v2_freeze.md`가 정본이다.

---

## 3. 제3자 LLM API — Luxia Cloud (삭제된 폴더 보존)

Luxia Cloud는 OpenAI GPT-4o-mini를 **브리지** 형태로 제공하는 플랫폼이다.

| 항목 | 값 |
| --- | --- |
| Base URL | `https://bridge.luxiacloud.com/llm/openai/chat/completions/gpt-4o-mini` |
| 실제 요청 URL | `{Base URL}/create` |
| 인증 | 헤더 `apikey: <KEY>` — **Bearer 아님** |
| body의 `model` | `"llm"` **고정값**. 실제 모델은 URL 경로가 결정한다 |
| 다른 모델 | Base URL 마지막 경로를 변경 (`/gpt-4o` 등, Luxia가 지원하는 경우) |
| 응답 | OpenAI 호환 `choices[0].message.content` |
| OpenAI SDK | **직접 호환 안 됨.** 헤더와 URL 구조가 달라 `openai` 패키지를 쓸 수 없다 → `httpx`/`requests` 직접 호출 |

### 요청 예시

```
POST https://bridge.luxiacloud.com/llm/openai/chat/completions/gpt-4o-mini/create
apikey: <KEY>
Content-Type: application/json

{
  "model": "llm",
  "temperature": 0.2,
  "top_p": 0.95,
  "max_tokens": 2048,
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ]
}
```

### 에러 코드

| 코드 | 의미 | 대응 |
| --- | --- | --- |
| 400 | 파라미터 오류 | payload 확인 |
| 401 | 인증 실패 | 키 확인 |
| 429 | Rate limit | 5초 후 재시도, 배수 증가 |
| 500+ | 서버 에러 | 잠시 후 재시도 |

동시 요청은 8~12 워커가 안전(경험적). 비용은 호출당 약 $0.0002~0.0003.
gpt-4o-mini 실단가는 입력 $0.15 / 출력 $0.60 per 1M tokens.

### 2026-08-06 실제 capability probe 결과

`backend/scripts/probe_capabilities.py`로 정상 요청과 스키마-프롬프트 충돌 대조군을 함께 실측했다.
200 응답 여부만 보지 않고 enum 위반을 지시한 대조군에서도 스키마가 유지되는지 확인했다.

| 항목 | 실측 결과 |
| --- | --- |
| `json_schema` + `strict: true` | **지원, 실제 강제됨** |
| `response_format={"type":"json_object"}` | 지원 |
| `tools` / 강제 function calling | 지원 |
| `stream: true` | 지원, SSE 관측 |
| `usage` | 반환됨 |
| `model` 에코 | `gpt-4o-mini-2024-07-18` |
| timeout | `LLMTimeoutError`로 구분됨 |
| rate limit | 서비스를 압박해 429를 인위적으로 만들지 않음 |

판정은 **경우 A**다. 원본 보고서는 `backend/reports/luxia_capability.json`에 저장되며 Git에서 제외된다.
Luxia의 기본 structured 경로는 `json_schema` 하나로 고정했고, 범용 A/B/C fallback 구현은
mock 및 다른 provider를 위한 방어 경로로만 남겨 두었다.

새 endpoint나 모델 경로로 바꾸면 이 결과를 재사용하지 말고 probe를 다시 실행한다.

---

## 4. 파이프라인 아키텍처 계약

아래 대응은 과거 목업의 아키텍처에서 시작해 현행 Python 구현과 fixture를 기준으로 교정한 값이다.
노드 id / role / owner는 이 문서가 정본이며, 코드가 이 대응을 벗어나면 코드를 고친다.

```
사용자 발화 (INPUT)
  → SPN Understanding (PLAN)        → IntentResult · 상태 갱신 후보
  → RA-Rec State Manager (MEMORY)   → DialogueState + Diff   ★ 모든 발화 뒤 실행
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

| 노드 id | role | owner | 입력 → 출력 |
| --- | --- | --- | --- |
| `user-utterance` | INPUT | user | 발화 원문 |
| `spn-understanding` | PLAN | SPN | 발화 + 이전 상태 → `IntentResult` |
| `ra-state-manager` | MEMORY | RA-Rec | `IntentResult` → `DialogueState` + `StateDiff` |
| `spn-policy` | DECIDE | SPN | `DialogueState` → `PolicyDecision`(lane) |
| `spn-response-clarify` | RESPOND | SPN | clarify 결정 → 추가 질문 |
| `ra-query-generator` | QUERY | RA-Rec | recommend 결정 + 상태 → 질의 |
| `spn-browsing-actions` | ACT | SPN | 질의 → 상품·리뷰·가격·배송 |
| `ra-recommendation-engine` | RANK | RA-Rec | 상품·리뷰 → `RankedProduct[]` + 근거 |
| `spn-response-recommend` | RESPOND | SPN | 순위 + 근거 → 추천 응답 |
| `feedback-loop` | LOOP | — | 응답 → 다음 턴 발화 |

owner 색상 체계: **SPN = 에메랄드, RA-Rec = 블루.** (UI를 다시 만들 때 유지)

### 단계 경계 — 반드시 지킬 것

이 분담이 이 프로젝트의 핵심 설계다. 무너지면 평가가 불가능해진다.

- **Understanding(PLAN)은 상태를 쓰지 않는다.** 발화와 이전 상태만 보고 `IntentResult`를 만든다.
  상태 갱신 **후보**만 만들고(슬롯·id·valueText·origin·confidence까지 결정), 실제 병합은 하지 않는다.
  즉 **어휘를 정하는 것이 Understanding의 책임**이다.
- **State Manager(MEMORY)는 발화를 파싱하지 않는다.** 후보 병합, turn 근거 부착, status 전이
  (`unconfirmed` → `superseded`), 관심·거절·비교·구매 이력, trade-off, 변경 경로만 담당한다.
  즉 **병합 의미론이 State Manager의 책임**이다.
  정보가 충분할 때만 호출되는 게 아니라 **모든 발화 뒤 실행**되며 부분 정보도 누적한다.
- **Policy(DECIDE)의 입력은 `DialogueState` 하나다.** 모호성 점수와 facet 스냅숏도 이 단계에서 계산해
  `PolicyDecision.snapshot`으로 반환한다. 출력에 분기 식별자(`clarify-lane`/`recommend-lane`)를 그대로 담는다.
- **RESPOND는 두 노드다.** clarify와 recommend가 각각 대응하며, 한쪽 경로에서 다른 쪽은 호출되지 않는다.
  (1턴에서 `query`가 `null`인 것으로 확인할 수 있다.)
- **RA-Rec은 사용자에게 직접 질문 문장을 만들지 않는다.** 필요한 정보와 이유를 구조화해 반환하고,
  SPN이 자연스러운 질문으로 표현한다.
- **RA-Rec explanation과 SPN 최종 응답은 다른 객체·다른 문장이다.** 같은 문자열을 재사용하지 않는다.
  `RecommendationResponse.explanation`은 추천 판단용 설명이고, `FinalResponse.message`는
  가격·배송·이미지·후속 행동을 결합한 사용자용 문장이다.

### LLM을 쓸 구간 / 결정론적으로 유지할 구간

이 분리가 무너지면 "어느 규칙 때문에 분기가 생겼는지" 평가할 수 없다.
초기에는 **single agent + explicit workflow**로 구현하고 multi-agent는 쓰지 않는다.

| LLM 사용 | 결정론적 유지 |
| --- | --- |
| SPN Understanding (발화 → 조건·거절 이유·행동 추출) | RA-Rec State Manager (병합, provenance, Diff, status 전이) |
| Latent Hypothesis Generator (관찰 → 숨은 의도 후보) | Vagueness / Confidence (수식) |
| Response Composer (구조화 결과 → 자연어) | SPN Policy (threshold 분기) |
| | Query Generator (hard constraint → 검색 조건) |
| | Browsing / Ranking (필터링, 리뷰 검색, 점수 계산) |
| | Evaluation (정답 대비 출력 비교) |

---

## 5. 데이터 모델 계약 (삭제된 `lib/types.ts` 보존)

```ts
type SPNFacetName =
  | "subjective_property" | "event" | "activity" | "goal_purpose" | "goal_audience";

type EvidenceOrigin = "explicit" | "implicit" | "inferred";
type EvidenceStatus = "confirmed" | "unconfirmed" | "superseded";

type PreferenceValue = {
  id: string;
  valueText: string;          // 자연어 값
  origin: EvidenceOrigin;
  confidence: number;         // 0~1
  status: EvidenceStatus;
  evidenceTurnIds: string[];
  updatedAtTurnId: string;
};

type SPNState = Record<SPNFacetName, PreferenceValue | null>;  // 미확인이면 null 유지, 억지로 채우지 않음

// ── Understanding(PLAN)의 출력 ──────────────────────────────
type CandidateTarget =
  | { kind: "category" }
  | { kind: "facet"; facet: SPNFacetName }
  | { kind: "constraint"; scope: "hard" | "soft"; key: string };

type StateUpdateCandidate = {
  target: CandidateTarget;
  id: string;                 // ★ 랭킹이 이 값으로 룩업한다 (§8-1 참조)
  valueText: string;
  origin: EvidenceOrigin;
  confidence: number;
};

type ItemActionName =
  | "inspect_current" | "reject_first" | "compare_first_second" | "purchase_current";

type ItemActionCandidate = {
  name: ItemActionName;
  // 거절 이유. 종류(상황적 제약 / 상품 속성)를 문구에 담는다.
  rejectionReason?: { id: string; valueText: string };
};

type IntentResult = {
  utterance: string;
  intents: string[];
  facets: Partial<Record<SPNFacetName, StateUpdateCandidate>>;
  candidates: StateUpdateCandidate[];
  itemAction?: ItemActionCandidate;
  supersedes: string[];        // 기존 inferred/unconfirmed 값을 대체할 soft 슬롯 키
  residualColorChoice: boolean; // 색상값은 카탈로그에만 있으므로 "잔여 옵션 수용" 상황만 표시
};

// ── State Manager(MEMORY)가 관리하는 상태 ────────────────────
type DialogueState = {
  category?: PreferenceValue;
  hardConstraints: Record<string, PreferenceValue>;
  softConstraints: Record<string, PreferenceValue>;
  subjectiveNeeds: SPNState;
  tradeoffs: PreferenceValue[];
  recommendedItems: string[];
  shortlistedItems: string[];
  inspectedItems: string[];
  rejectedItems: Array<{ productId: string; reason: PreferenceValue }>;
  cartedItems: string[];
  purchasedItems: string[];
  currentItem?: string;
  unresolvedPreferences: Array<{ field: string; reason: string; priority: number }>;
  preferenceHistory: Array<{ turnId: string; changedPaths: string[] }>;
};

type Product = {
  id: string; title: string; image: string; brand: string;
  price: number; rating: number; reviewCount: number;
  description: string; shipping: string; stock: string;
  metadata: {
    category: string; display: string; chipset: string; storage: string;
    weightGrams: number;      // 휴대성 점수 입력
    batteryHours: number;     // 영상 재생 기준
    speakerTier: number;      // 1~5
    performanceTier: number;  // 1~5
    longevityYears: number;   // 예상 SW 지원 연수
    deliveryDays: number;     // 재고가 반영된 수령 소요일
    availableColors: string[];// 즉시 수령 가능 색상. 비면 잔여 재고 없음
    useCases: string[];
  };
};

type Review = { id: string; productId: string; text: string; helpfulness?: number; source: "mock" };

type RankedReview = Review & {
  similarityScore: number; preferenceCoverageScore: number; reliabilityScore: number;
  totalScore: number; matchedPreferenceIds: string[];
};

type ScoreBreakdown = {
  hardConstraintMatch: number; metadataMatch: number; subjectiveNeedMatch: number;
  reviewEvidenceScore: number; evidenceReliability: number; total: number;
};

type RankedProduct = {
  productId: string; rank: number; score: ScoreBreakdown;
  evidenceReviewIds: string[];    // ★ 화면 표시 리뷰 = 점수에 쓰인 리뷰 (동일해야 함)
  matchedPreferenceIds: string[];
};

type VaguenessBreakdown = {
  categoryBreadth: number; missingRequiredInfo: number; unresolvedSPN: number;
  contradictionPenalty: number; total: number; threshold: number; reasons: string[];
};

type PolicyLane = "clarify-lane" | "recommend-lane";

type PolicyDecision = {
  action: "ask_user" | "recommend";
  lane: PolicyLane;
  questionTarget?: { field: string; reason: string; priority: number };
  reasons: string[];
  snapshot: { facets: SPNState; vagueness: VaguenessBreakdown };
};

type RecommendationResponse = {   // RA-Rec 내부 계약
  updatedDialogueState: DialogueState; rankedProducts: RankedProduct[];
  reviewEvidence: RankedReview[]; explanation: string;
  unresolvedPreferences: DialogueState["unresolvedPreferences"];
};

type FinalResponse = {            // SPN 사용자용 계약
  message: string; productCards: RankedProduct[];
  priceEvidence: string[]; deliveryEvidence: string[]; imageEvidence: string[];
  reviewEvidence: RankedReview[]; nextActions: string[];
};
```

### 상태 기록 규칙

- 상세 보기는 `inspectedItems` / `shortlistedItems` / `currentItem`만 변경한다.
  **`acceptedItems`나 `purchasedItems`에 넣지 않는다.** 구매 확정만 `purchasedItems`에 기록한다.
- 거절 이유는 **종류를 구분해** 저장한다. 재고처럼 구매 시점 상황에서 온 제약과 리뷰에서 확인된
  상품 속성은 서로 다른 근거다.
- **확정되지 않은 선호는 점수에 들어가지 않는다.** `status !== "confirmed"`인 값은 근거로만 표시된다.
  그래서 inferred 가설은 화면에 보이면서도 순위를 흔들지 않는다.
- 추론값을 확정 사실처럼 hard/soft constraint에 바로 넣지 않는다. UI에서 explicit/implicit/inferred를
  서로 다른 badge로 구분 표시한다.

---

## 6. 내가 만든 것 — `backend/` (Python)

`manual.md`의 권고(FastAPI + LangGraph 백엔드 분리)에 따라 저장소 루트의 독립 Python 백엔드로 만들었다.
현재 LLM 접속 계층과 evaluation 위에 **실행 가능한 end-to-end MVP**를 완성했다.
explicit LangGraph, State Manager, Policy, Query/Browse/Rank, 두 Response Composer,
FastAPI 메모리 세션, 자유 입력 3패널 UI가 연결되어 있다. 기본 UI는 영어 자유 입력을 Luxia로
구조화하고 local DuckDB/Parquet의 실제 상품 전체와 제한된 리뷰 top-k를 검색·랭킹한다.
통제 fixture는 별도 6턴 회귀 API로 보존한다. 실제 리뷰는 local NumPy semantic index와 제한
Cross-Encoder로 검색하며 검증 실패 시 사유를 남기고 token baseline으로 fallback한다.
PostgreSQL/pgvector는 아직 없다.

```
backend/
  requirements.txt          버전 고정 (설치본과 일치)
  .env.example              LLM_PROVIDER / LLM_API_KEY / LLM_BASE_URL / ...
  .env                      로컬 Luxia 키 설정됨 (.gitignore됨, 절대 커밋하지 않음)
  .gitignore                .env, logs/, reports/, __pycache__
  app/
    __init__.py
    config.py               LLMSettings (dataclass, frozen)
    llm/
      base.py               Protocol, 예외, 응답 파서
      trace.py              연구 로그 (JSONL)
      luxia.py              Luxia transport
      mock.py               키 없이 쓰는 transport
      json_utils.py         JSON 추출, strict schema 변환
      structured.py         3단 fallback 강등 + repair
      factory.py            provider 선택
      __init__.py
    models/
      understanding.py      closed vocabulary + UnderstandingOutput 계약
      pipeline.py           DialogueState·Policy·상품·추천·응답 계약
      __init__.py
    baselines/
      regex_understanding.py  정규식 Understanding 비교 기준
    evaluation/
      understanding.py      gold 계약 + 공통 의미 단위 채점기
    nodes/
      understanding.py      SPN Understanding(PLAN) LLM 노드
      state_manager.py      RA-Rec State Manager(MEMORY)
      policy.py             Vagueness + 명시적 lane(DECIDE)
      recommendation.py     Query/Browse/Review/Rank 결정론 경로
      response.py           clarify/recommend LLM RESPOND + fallback
      __init__.py
    catalog.py              통제된 inventory/review fixture loader
    workflow.py             single-agent explicit LangGraph
    service.py              in-memory 대화 세션 직렬화
    api.py                  FastAPI entrypoint
    static/                 자유 입력 3패널 MVP UI
  data/
    README.md                 fixture 출처·정규화·점수식 데이터 카드
    understanding_eval_v1.json  합성 gold 발화 28개
    demo_catalog.json       상품 6개 + 리뷰 17개 통제 fixture
  scripts/
    probe_capabilities.py   Luxia A/B/C·tools·stream·usage 실측
    probe_understanding.py  실제 복합 스키마 1턴 probe
    evaluate_understanding.py  regex/Luxia 공통 평가 러너
    verify_adapter.py       adapter 회귀 검증
    verify_understanding_schema.py  스키마·노드 결정론적 검증
    verify_understanding_evaluation.py  fixture·scorer·baseline 검증
    verify_mvp.py           LangGraph 6턴 + FastAPI 스모크
    probe_mvp_live.py       실제 Luxia 6턴 수직 슬라이스
```

### 설계: 2계층 분리

```
LangGraph Node
  → LLMClient (제공업체 무관)          ← 노드는 이것만 본다
     └─ StructuredLLMClient            강등·검증·로깅
        → LLMTransport (제공업체별)
           ├─ LuxiaTransport
           └─ MockTransport
```

판단 기준은 "LangChain `ChatOpenAI`가 동작하는가"가 아니라
**"어떤 제공업체를 쓰더라도 같은 Pydantic 객체가 나오는가"**다.

### 주요 동작

- `LuxiaTransport`: `apikey` 헤더, body `model`은 `model_field` 고정, `{base}/create`로 POST.
  재시도 대상은 `{429, 500, 502, 503, 504}`만. **그 밖의 4xx는 즉시 `LLMHTTPError`로 올리고 응답 body를 담는다**
  (probe가 거부 이유를 읽어야 하므로). `retries=0`을 주면 재시도 없이 첫 응답을 판단할 수 있다.
- `StructuredLLMClient`의 강등 규칙:
  - **4xx(429 제외) = 제공업체의 파라미터 거부** → 그 모드를 `_unsupported_modes`에 영구 기록하고 다음 모드로 내려간다.
    매 호출마다 실패할 모드를 재시도하면 왕복 비용을 계속 낸다.
  - **스키마 검증 실패 = 요청별 사건** → 모드를 죽이지 않고, 같은 모드에서 오류 내용을 포함해 **repair 1회** 재요청.
  - **5xx / timeout** → 모드 문제가 아니므로 강등하지 않고 예외를 그대로 올린다.
  - 성공한 모드는 `_preferred_mode`로 캐싱해 다음 호출에서 먼저 시도한다.
- `factory.build_client(unsupported_modes=...)`로 probe 결과를 미리 주입하면 첫 호출의 왕복 낭비를 없앨 수 있다.
- `trace.py`의 `LLMTraceRecord`는 다음을 **분리해서** 저장한다. 이 구분이 없으면 나중에
  "Understanding 성능이 낮은 이유가 모델 때문인지 스키마 매핑 때문인지"를 나눌 수 없다.
  `raw_response` / `validated_output` / `validation_errors` / `attempts[]` /
  `structured_mode` / `fallback_used` / `retry_count` / `reported_model` /
  `prompt_version` / `schema_version` / `latency_ms` / `input_tokens` / `output_tokens`.
  LangSmith에 로그를 의존하지 않는다. 비용·계정 문제 없이 재분석할 수 있어야 한다.

### 검증 상태 — 여기가 중요하다

```powershell
cd backend
python -m compileall -q app scripts
python scripts\verify_adapter.py
python scripts\verify_understanding_schema.py
python scripts\verify_understanding_evaluation.py
python scripts\verify_mvp.py
```

`MockTransport`로 강등·repair·모드 캐싱·예외 전파를 결정론적으로 확인했다.
실제 Luxia API에서도 capability probe와 복합 `UnderstandingOutput` 호출을 검증했다.
첫 gold 발화는 `search`, `category_smartphone`, `event_device_failure`(explicit),
`goal_replace_device`(implicit), `urgency_pressure`(inferred, 0.64)를 fallback·repair 없이 반환했다.

`verify_mvp.py`의 오프라인 6턴은 lane `clarify → recommend × 5`, 순위
`iPhone 17 → iPhone Air → iPhone 17 Pro`, 상세 보기/구매 분리, 두 거절 유형,
잔여 색상 trade-off, 리뷰 evidence ID 일치를 재현한다. 실제 Luxia 6턴도 모든 노드와
두 LLM 단계를 fallback 없이 완주했고 최종 구매는 `iphone-17-pro`로 기록됐다.

live에서는 3턴의 “첫 상품은 2주 뒤 수령”을 사용자의 허용 기한 2주로도 추출해
Air 대신 iPhone 16이 잠시 1위가 되는 편차가 관찰됐다. MVP는 이 차이를 trace와 State Diff로
노출한다. 새 holdout 없이 같은 시나리오에 프롬프트를 더 맞추지는 않는다.

### probe 이후 구조화 계층 결정

`structured.py`의 범용 3단 사다리는 mock과 향후 provider 교체 검증을 위해 유지한다.
다만 `factory.build_client()`는 Luxia에서 실측된 `("json_schema",)`만 기본 사용하므로
정상 요청이 불필요한 json_object/prompt-only 경로로 강등되지 않는다.

### 함정 하나

Pydantic 2에서 `schema_version`은 반드시 `ClassVar[str]`로 선언해야 한다.
그냥 `schema_version = "v1"`이라고 쓰면 `PydanticUserError`로 **클래스 정의 자체가 실패**한다.

```python
class UnderstandingOutput(BaseModel):
    schema_version: ClassVar[str] = "understanding-v1"   # 필드가 아니라 메타데이터
```

---

## 7. 목업에서 보존한 알고리즘 (교정 사항을 반영해 재현할 것)

### 7-1. Vagueness Score (SPN Policy의 휴리스틱)

> ⚠ 원 SPN 논문 공식이 아니다. 추가 질문과 즉시 추천 중 무엇을 할지 **결정론적으로 설명하기 위해
> 정의한 local 휴리스틱**이다. 실데이터로 옮길 때 이 값들은 재조정 대상이다.

| 조건 | 점수 |
| --- | ---: |
| `!state.category` | +18 |
| `!state.hardConstraints.budget` | +25 |
| `!facets.subjective_property` | +20 |
| `!state.hardConstraints.deliveryDeadline` | +12 |
| `state.softConstraints.reviewSignal` 존재 | −8 |
| 거절 이유가 제약으로 정리되지 않음 | 건당 +12 |

```
total = clamp(categoryBreadth + missingRequiredInfo + unresolvedSPN + contradictionPenalty, 0, 100)
threshold = 45
```

마지막 항목은 상품 ID나 특정 문구에 의존하지 않는다. 거절 이유의 `evidenceTurnIds`가 어떤
hard/soft constraint의 `evidenceTurnIds`에도 나타나지 않으면 "정리되지 않은 거절"로 센다.

**정책 선택 규칙** — 두 조건을 **모두** 만족할 때만 추가 질문을 선택한다.

```
Vagueness Score > Ask-User Threshold      (엄격한 >. 정확히 45면 추천 경로)
AND  질문할 수 있는 미확인 항목이 존재
```

질문 우선순위: 가격 기준(25) > 사용 기간 기준(20) > 수령 시점(12).

> **이 우선순위가 사례의 핵심이다.** 일반적 상담에서는 가격과 용도가 먼저다. 그런데 실제 결정 변수는
> 수령 시점이었고, 그 사실은 사용자가 재고를 이유로 후보를 거절한 **뒤에야** 드러난다.

### 7-2. 리뷰 점수

```
reviewTotal = round(0.65 × intentSimilarity
                  + 0.25 × preferenceCoverage
                  + 0.10 × reliability)
```

- 통제 fixture/token fallback: `similarityScore = min(100, 20 + matchCount × 20)`
- 실데이터 semantic: `round(100 × (0.35 × normalizedCosine + 0.65 × sigmoid(crossEncoderLogit)))`
- `preferenceCoverageScore = round(matchCount / max(1, min(keywordCount, 5)) × 100)`
- `reliabilityScore = round((helpfulness ?? 0.5) × 100)`
- **`reviewCount`는 개별 리뷰 점수에 넣지 않는다.** 상품 단위 `evidenceReliability`에만 반영한다.
- mock 단계의 similarity는 한국어 토큰 overlap + 동의어 사전(§7-5)이다. 실데이터는 고정된
  Sentence Transformers embedding으로 상품별 5개 후보를 만든 뒤 Cross-Encoder로 상품별 3개를
  반환한다. index/model 부재 시 token baseline과 fallback reason을 함께 반환한다.

### 7-3. 상품 점수

```
total = clamp(0.30 × hardConstraintMatch
            + 0.30 × metadataMatch
            + 0.20 × subjectiveNeedMatch
            + 0.15 × reviewEvidenceScore
            + 0.05 × evidenceReliability, 0, 100)
```

- `hardConstraintMatch`: 예산과 수령 기한 충족도 평균. 제약이 없으면 **70**.
  - 예산: `price <= budget ? 100 : (유연성 확정 ? clamp(100 − (초과분/budget) × 250) : 0)`
  - 기한: `deliveryDays <= deadline ? 100 : 0`
- `metadataMatch`: hard/soft constraint가 참조하는 속성 점수 평균. 활성 속성 없으면 **65**.
- `subjectiveNeedMatch`: SPN facet이 참조하는 속성 점수 평균. 활성 속성 없으면 **55**.
- `reviewEvidenceScore`: 상품별 top-3 리뷰 `totalScore` 평균.
- `evidenceReliability = clamp(0.7 × helpfulness평균 + 0.3 × countReliability)`
  여기서 `countReliability = log1p(reviewCount) / log1p(maxReviewCount) × 100`.
- 정렬 tie-break는 `productId.localeCompare`.
- `rejectedItems`의 상품은 후보에서 **제외**된다.

> **`feedback.txt`가 제안한 가중치는 `0.30/0.20/0.25/0.20/0.05`였지만 실제 구현은
> `0.30/0.30/0.20/0.15/0.05`다.** 문서화된 점수표(부록 B)는 구현값 기준이다.

### 7-4. 선호 → 상품 속성 매핑 (★ 최대 난점)

**상품 ID별 보너스나 시나리오 전용 가점을 절대 두지 않는다.** 선호 id를 상품 속성에 매핑하는 표 하나만
두고, fixture의 속성 차이로 순위가 바뀌게 한다.

속성 점수 (고정 기준 구간. 후보군이 바뀌어도 같은 상품의 점수가 흔들리지 않게 하려는 의도):

```ts
deliverySpeed: clamp(100 - deliveryDays * 12)
performance:   clamp(performanceTier * 20)
longevity:     clamp((longevityYears / 6) * 100)
battery:       clamp(((batteryHours - 18) / 20) * 100)
audio:         clamp(speakerTier * 20)
portability:   clamp(((240 - weightGrams) / 80) * 100)
priceValue:    clamp(((2_100_000 - price) / 1_300_000) * 100)
```

선호 id → 속성:

| 선호 id | 참조 속성 |
| --- | --- |
| `delivery-deadline`, `delivery-speed` | `deliverySpeed` |
| `longevity-value` | `longevity` |
| `audio-battery-priority` | `audio`, `battery` |
| `portability-priority` | `portability` |
| `price-sensitivity` | `priceValue` |
| `subjective-longevity` | `longevity`, `performance` |
| `subjective-performance` | `performance` |
| `subjective-portability` | `portability` |
| `budget-flexibility`, `urgency-pressure`, `color-residual`, `review-signal` | **없음** (상태·근거로만 남는다) |

### 7-5. 동의어 그룹 (mock similarity용, 실데이터에서는 embedding으로 대체)

```
[수령 배송 재고 출고 입고 대기 품절]
[장기 오래 지속 수명 내구 지원]
[성능 속도 칩 프로세서 a19 a18]
[배터리 사용시간 충전 지속시간]
[스피커 사운드 음질 소리]
[무게 가벼운 가벼워 휴대 그립]
[가격 예산 비용 부담 고가]
[색상 컬러 오렌지 블랙 화이트]
[후기 리뷰 사용기]
[고장 파손 교체 수리]
```

---

## 8. 알려진 함정 — 구현 전에 반드시 읽을 것

### 8-1. ★ 선호 id가 closed vocabulary라는 점 (가장 큰 문제)

랭킹이 선호를 점수에 반영하는 경로는 `preferenceAttributes[value.id]` 룩업 **하나뿐**이었고,
등록된 id는 §7-4의 9개뿐이었다. 삭제된 코드에는 이렇게 되어 있었다.

```ts
const mapped = preferenceAttributes[value.id];
if (!mapped?.length) return;      // ← 조용히 무시된다
```

LLM이 Understanding을 담당하면 `id`가 자유롭게 생성된다. `large-storage-for-long-term-use` 같은 값이
나오면 룩업이 실패하고 **선호가 조용히 사라진다.** 결과는 "LLM은 제대로 이해했는데 순위는 안 바뀌는"
상태이고, 원인이 화면에도 로그에도 드러나지 않는다.

**대응(반드시 할 것):**
1. LLM이 `canonical_id`를 자유 생성하지 못하게 `Literal` enum으로 제한한다.
   자연어 근거(`value_text`, `evidence_text`)는 자유롭게 쓰게 하되, **랭킹에 쓰이는 id는 제한된 enum에서 고르게** 한다.
2. 룩업 실패를 **조용히 넘기지 말고 계측**한다. `unmapped_preference_ids`를 로그와 화면에 남긴다.
3. 카테고리가 아직 미확정(§8-6)이므로 vocabulary도 확정하지 말고 `schema_version`으로 버전을 붙인다.

제안된 enum(미확정, 태블릿 전제가 섞여 있음):

```python
PreferenceId = Literal[
    "delivery_deadline", "storage_capacity", "battery", "audio",
    "portability", "note_taking", "price_value", "display", "durability",
]
```

기존 TS는 kebab-case(`delivery-deadline`)였고 위 제안은 snake_case다.
**하나로 통일할지 매핑 계층을 둘지 먼저 결정해야 한다.** 두 표기가 섞이면 8-1의 조용한 실패가 재발한다.

### 8-2. 속성 점수 구간이 아이폰 6개 fixture 전용 상수다

§7-4의 `2_100_000` / `1_300_000` / `240g` / `80g` / `18h` / `20h`는 부록 A의 6개 상품에 맞춰진 값이다.
태블릿 300개로 바꾸면 대부분 0 또는 100으로 포화된다. 부록 B의 점수표도 전부 무효가 된다.
**실데이터의 분포를 보고 재설계해야 한다.** (분위수 기반 정규화 등)

### 8-3. 시나리오의 두 축이 Amazon Reviews 2023에 없는 필드다

`deliveryDays`와 `availableColors`가 3·5·6턴을 전부 지탱한다. Amazon Reviews 2023에는 없다.
`manual.md` §1.3이 자체 inventory snapshot을 권하는 이유가 이것이다. **이 부분은 실데이터로 가도 합성이다.**

### 8-4. `supersedes`가 soft constraint만 처리했다

삭제된 State Manager는 `state.softConstraints[key]`만 확인했다. hard constraint는 승격/대체 대상이 아니었다.
hard 제약이 뒤집히는 시나리오를 넣으려면 확장이 필요하다.

### 8-5. 성능 관련 두 가지

- `rankReviews`의 `slice(0, Math.max(topK, reviews.length))`는 **사실상 아무것도 자르지 않았다.**
  `topK` 파라미터가 무력이었다. 리뷰 17개일 때는 무해하지만 5,000~20,000개면 문제다.
- `RecommendationResponse.reviewEvidence`가 **전체** 리뷰를 담았다. `browseMockCatalog`도 전체 상품과
  그 리뷰 전부를 스캔해 `priceEvidence`/`deliveryEvidence` 배열을 만들었다. 실데이터에서는 DB 쿼리로 대체할 것.

### 8-6. 미결정 사항 두 개 (사용자 답변 대기 중)

1. **품목을 태블릿으로 바꿀지.** `manual.md`는 태블릿을 권한다(저장공간·무게·화면·배터리·펜 등 구조화할
   사양이 많고 트레이드오프를 만들기 쉬움). 그러나 현재 시나리오와 문서 전체가 아이폰 기준이다.
   태블릿으로 가면 §1의 사례와 부록 A·B를 다시 만들어야 한다.
   `manual.md`의 권고 순서는 "Amazon Reviews 2023 Electronics 메타데이터를 일부만 읽어
   **태블릿 상품이 실제로 얼마나 깔끔하게 분리되는지 프로파일링**한 뒤 확정"이다.
   참고로 삭제된 레거시 `data/scenario.json`은 태블릿 시나리오였다
   (`android-tablet-10`, `alldocube-iplay-50`, `doogee-t20`, `lectrus-tablet-10`,
   기준은 "너무 비싸지 않게 + 리뷰 수 많은 것", `risk_attitude`를 inferred로 분리).
2. **재고·배송을 자체 snapshot으로 합성할지, Best Buy API를 붙일지.**
   `manual.md`는 정량 평가는 자체 snapshot, 실환경 데모는 Best Buy API로 나누라고 권한다.

### 8-7. 프론트엔드 관련 (다시 만들 경우)

- 삭제된 UI는 3패널이었다. 왼쪽 대화 / 가운데 Live decision trace(§4의 노드 구성을 그대로 렌더링,
  실행된 레인만 강조) / 오른쪽 Pipeline Inspector(요구·가설·탈락이유·정책·Diff·JSON·점수).
- **자유 입력 UI가 없었다.** 전송 버튼이 현재 상태에 맞는 다음 고정 발화를 진행하는 방식이었다.
  `feedback.txt` P0-2가 자유 입력을 요구했는데 미충족 상태였다. LLM 전환의 핵심 가치가 자유 입력이므로 반드시 추가할 것.
- `next.config.mjs`의 `images.remotePatterns`에 `images.unsplash.com`만 있었다.
  실제 상품 이미지를 쓰려면 해당 호스트를 추가해야 한다.
- `tsconfig.json`은 `strict: true`, path alias `@/* → ./*`.
- 기술 스택: Next.js 15 + React 19 + Tailwind 3.4 + shadcn 스타일 UI 프리미티브(badge/button/card/switch) + lucide-react.
- 검증 명령은 `npx tsc --noEmit`, `npm run lint`, `npm run build`였다.

---

## 9. 다음 단계 (권장 순서와 진행 상태)

### 0단계 — Luxia 키 설정 ✅ 완료

`backend/.env`의 `LUXIA_API_KEY`를 `config.py`가 읽는다. `.env`와 키는 Git에서 제외된다.

### 1단계 — Luxia capability probe ✅ 완료

`backend/scripts/probe_capabilities.py`를 구현해 실측했다.
`LuxiaTransport.complete(retries=0, response_format=..., extra_body=...)`로 각 파라미터를 하나씩 보내고
status와 raw body를 기록한다. 확인할 항목:

1. `response_format={"type":"json_schema", "json_schema":{..., "strict":true}}` 수용/거부
2. `strict: true` 실제 강제 여부
3. `response_format={"type":"json_object"}` 수용/거부
4. `tools` / function calling
5. `stream: true`
6. 응답에 `usage`가 오는가
7. 응답이 `model`을 에코하는가 (alias가 고정되는지)
8. timeout / rate limit 응답 형태

> **대조 실험을 반드시 넣을 것.** 200이 곧 지원을 뜻하지 않는다. 브리지가 미지 파라미터를 무시할 수 있다.
> 스키마를 위반할 수밖에 없는 프롬프트(예: enum에 없는 값을 쓰라고 지시)를 함께 보내
> 스키마가 실제로 강제되는지 확인한다.

결과를 `backend/reports/luxia_capability.json`으로 저장하고, 판정된 경우(A/B/C)에 따라
Luxia factory 기본 경로를 case A의 `json_schema` 하나로 제한했다.

### 2단계 — `UnderstandingOutput` 스키마와 첫 노드 ✅ 완료

§5의 `IntentResult`를 Pydantic으로 옮기고 `canonical_id`를 versioned `Literal`로 제한했다.
`schema_version: ClassVar[str] = "understanding-v1-generic-electronics"`를 사용한다.
constraint key 불일치, 자유 생성 ID, 행동-intent 불일치, 구매 전 잔여 색상 확정을 검증한다.
실제 Luxia strict schema에서 부록 C의 첫 발화를 검증했다.

### 3단계 — 스키마 준수율 측정 (20~30개 발화) ✅ 완료

부록 C의 6개 발화를 시드로 예산·목적·거절·비교·상세·구매·모호·모순 발화까지
28개 합성 gold fixture(`understanding-eval-v1.1`)를 만들었다. 문구가 아니라 intent,
`canonical_id`, target/scope, provenance, item action, supersedes, 잔여 색상 수용을 채점한다.

`evaluate_understanding.py`는 정규식과 Luxia를 같은 채점기로 실행하고
`validation_success`, fallback 분포, canonical ID exact/micro-F1, unmapped, latency를 저장한다.
사용자 동의를 받은 뒤 2026-08-06에 같은 28개를 Luxia GPT-4o-mini로 실행했다.
최종 `spn-understanding-v1.3`과 정규식 v1의 결과는 다음과 같다.

| 지표 | regex v1 | Luxia GPT-4o-mini v1.3 |
| --- | ---: | ---: |
| validation success | 1.000 | 1.000 |
| canonical ID exact | **0.964** | 0.393 |
| canonical ID micro precision | **0.981** | 0.627 |
| canonical ID micro recall | **1.000** | 0.904 |
| canonical ID micro-F1 | **0.990** | 0.740 |
| intent exact | 0.964 | 0.964 |
| item action exact | 1.000 | 1.000 |
| matched target accuracy | 1.000 | 1.000 |
| matched origin accuracy | **1.000** | 0.936 |
| mean latency | 약 0.3 ms | 약 4,427 ms |
| p95 latency | 약 0.6 ms | 약 7,552 ms |

Luxia 28건은 전부 `json_schema`, fallback 0건, repair 0건, unmapped 0건이었고
응답 model은 모두 `gpt-4o-mini-2024-07-18`이었다. `canonical_id` 중복도 계약에서
거부하도록 보강했다.

결론: **이 통제된 fixture에서는 정규식이 canonical ID 추출에서 명확히 우세하다.**
Luxia는 recall(0.904)보다 precision(0.627)이 낮아 기존 상태나 관련 facet을 과잉 후보로
만드는 경향이 남았다. 반면 intent와 상품 행동은 정규식과 같은 수준까지 올라왔다.

초기 `v1.1` 결과를 보고 target 매핑과 문맥 경계를 보강해 `v1.2`, `v1.3`을 같은
fixture에서 개발했으므로 최종 Luxia 수치는 **독립 test 성능이 아니라 dev-set 회귀값**이다.
두 구현을 이 데이터에 더 맞추지 말고 현재 버전을 동결한 뒤, 새로운 paraphrase·부정·생략
표현을 직접 주석한 holdout에서 다시 비교해야 일반화 성능을 주장할 수 있다.

#### Actual English holdout 첫 실행 (prompt v1 동결)

기존 28개 dev 문구와 겹치지 않는 영어 30개를 별도로 주석했다. 숫자 단위 정규화, RAM/storage
negative control, soft review signal, 순위·상품명 참조, 거절 유형, 명시적 trade-off,
unsupported category/field/currency를 포함한다. fixture와
`spn-understanding-amazon-tablet-en-v1` prompt를 첫 실행 전에 동결했으며 dataset/prompt SHA-256을
결과에 남겼다.

2026-08-07 첫 Luxia 실행 결과는 validation 0.900, canonical ID exact 0.400/micro-F1 0.493,
intent exact 0.800, item action exact 0.833, action rank exact 0.867, trade-off exact 0.833이다.
30건 모두 strict `json_schema`를 사용했고 transport fallback은 0건, schema repair는 4건이었다.
이 결과는 실제 schema의 과잉·누락을 드러낸 고정 기준선이며, **v1을 이 holdout에 맞춰 다시
수정하지 않는다.** fixture는 `backend/data/actual_understanding_holdout_v1.json`, 추적 요약은
`backend/data/manifests/actual_understanding_holdout_luxia_v1.json`에 있다.

### 4단계 — 데이터 프로파일링·확장 카탈로그 ✅ 완료

2026-08-06의 50,000 metadata/1,000,000 review pilot에서 분류·파싱 규칙을 교정한 뒤,
2026-08-07에 같은 Hub revision `2b6d039ed471f2ba5fd2acb718bf33b0a7e5598e`로 metadata
500,000행과 review 8,000,000행을 처리했다. 최종 결과는 태블릿 117개, 본문 중복 없는 실제
리뷰 7,552개이며 verified purchase는 6,990개다. 모든 상품은 리뷰를 최소 20개 가진다.
processed 리뷰에는 user ID나 리뷰 이미지를 남기지 않았다.

실데이터 정규화는 선택 표본의 empirical percentile을 사용한다. 가격·무게는 낮을수록,
저장공간·RAM·화면은 높을수록 0~100점이며 동일 값은 평균 rank를 쓴다. 결측은 보간하지 않고
점수도 null이다. 확대 감사에서 `Memory Storage Capacity`의 RAM 오인, 독립 RAM 서술 없이 details의
RAM과 저장공간이 같은 원천 중복값, parent variant 설명의 저장공간 덮어쓰기, iPad 문구가 포함된
MacBook 오분류, 110g 무게 이상치를 추가로 교정했다. 교정 후 RAM 결측은 44개다.
상세 실행 조건·결측·해시는 `backend/data/manifests/amazon_tablet_catalog_v2.json`에 있다.

read-only catalog adapter가 local DuckDB로 Parquet을 제한 조회한다. 실제 상품의 구조화 필터·
분위수 정렬·분류/속성 provenance·상품별 리뷰 top-k를 제공한다. 실제 대화 경로에서는 파일이
없으면 서버 시작 단계에서 명시적으로 실패한다. Amazon 원본에 없는 배송일·재고·가용 색상은
합성하지 않으며 검색·점수에도 사용하지 않는다.

7,552개 실제 리뷰는 `all-MiniLM-L6-v2` revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`로 384차원 normalized index를 만들었다. hard filter 뒤
상품별 bi-encoder top 5만 `ms-marco-MiniLM-L6-v2` revision
`c5ee24cb16019beea0893ab7796b1df96625c6b8` Cross-Encoder로 재순위화해 top 3을 반환한다. runtime은
local-only이며 index/model/hash 오류 시 token baseline과 사유를 trace에 남긴다. 산출물은 Git
제외, 모델 revision과 SHA-256은 `backend/data/manifests/amazon_tablet_semantic_retrieval_v1.json`에
추적한다. 전체 117개 local CPU cold-start smoke는 약 14초다.
실제 Luxia 1턴 통합 smoke에서는 hard filter 뒤 20개 상품, semantic 리뷰 60개, 카드 3개를
fallback 없이 recommend lane과 LLM composer까지 완주했다.

#### Actual recommendation 관련성 첫 기준선 (v1 동결)

상품 질의 4개의 token/semantic top 10 합집합 44개와 단일 상품 리뷰 질의 4개의 두 방식 출력
합집합 28개를 원문·metadata로 0–3 등급화했다. fixture와 판정은 첫 결과를 보기 전에 동결했다.
미판정 결과는 0점으로 계산하고 judgment coverage를 보고하며, 등급 2 이상을 relevant로 본다.

2026-08-07 최초 결과는 다음과 같다.

| 방식 | 상품 NDCG@3 | 상품 NDCG@10 | 리뷰 NDCG@3 |
| --- | ---: | ---: | ---: |
| token | 0.821 | 0.921 | 0.733 |
| semantic + Cross-Encoder | 0.699 | 0.863 | 0.661 |

두 방식 모두 top-k judgment coverage 1.000, hard-filter 위반율 0.000, 상품 카드 근거 일치율
1.000이다. 작은 단일-annotator pooled set이므로 일반화 성능이나 token의 우위를 확정하는 결과가
아니다. 다만 현재 semantic 경로의 정확도 향상은 주장할 수 없다. v1에 production ranker를 맞추지
말고 새 질의·독립 판정으로 확인한다. fixture는
`backend/data/actual_recommendation_eval_v1.json`, 요약은
`backend/data/manifests/actual_recommendation_eval_v1.json`에 있다.

#### 포스터용 recommendation holdout 입력물 (v1 동결)

기존 dev/pilot과 문구·상품 조건이 겹치지 않는 영어 4턴 시나리오 20개를 첫 packet build 전에
동결했다. token/semantic 각 top 10의 합집합과 시나리오당 hard negative 2개로 pool을 만들고,
annotator 3명마다 후보 순서를 독립적으로 섞었다. 1인당 판정 대상은 상품 170건·리뷰 302건이다.
public packet에는 blind ID와 동일 evidence bundle만 노출하며, 원본 ASIN·review ID·시스템별
rank·score는 private provenance로 분리한다. dataset과 packet 계약은 verifier가 검사하고,
파일별 SHA-256은 `backend/data/manifests/poster_annotation_packet_v1.json`에 남긴다.

모든 grade·rationale은 빈 값이다. 따라서 이 packet은 평가 입력물이지 성능 결과나 gold truth가
아니다. 2026-08-09 이후 이 packet과 human agreement/NDCG는 optional/future 평가로 보존한다.
이번 포스터 primary automatic result의 완료 조건에는 human annotation을 포함하지 않는다.

이 packet은 연구자가 동결한 정답 상태를 검색·랭킹에 직접 넣어 생성했다. 실제 Luxia
Understanding이 4턴을 해석해 만든 full-system 출력은 아니다. 두 결과를 섞지 않는다.

2026-08-08에는 동결 public packet hash를 확인해 별도 Git 제외 작업 사본을 만드는 준비 명령과,
완료된 packet을 private provenance에 결합하는 guarded 집계기를 추가했다. 집계기는 중앙값 grade,
ordinal Krippendorff's alpha, 상품·리뷰 NDCG@3, hard-filter·evidence 불변식, 시나리오 단위 paired
bootstrap 95% CI를 계산한다. synthetic/미확인 라벨은 human 결과로 승격하지 않고 빈 라벨은
거부한다. 현재 실제 작업 사본은 상품 첫 packet의 170개부터 비어 있어 `not_ready`다.

같은 날 472건을 한 파일에서 처리하지 않도록 각 판정자의 상품을 2세션, 리뷰를 3세션으로 나누는
Git 제외 작업 파일 15개와 manifest를 만들었다. 세션당 최대 120건이며 병합기는 label 밖의 변경,
누락·중복·부분 완료·다른 완료 label 덮어쓰기를 거부한다. 이 세션도 모두 빈 판정 입력물이다.

packet v1은 token/semantic module-level human relevance용이어서 live state 기억의 기여를 분리하지
못한다. RQ1은 별도 20개·81턴 holdout의 Full/No-memory automatic State Diff와 Final State로
평가한다. RQ3는 fixed-upstream No-review의 ranking change까지만 automatic으로 평가하며 품질 방향은
주장하지 않는다. 기존 packet과 readiness gate는 future human study를 위해 수정 없이 보존한다.

### 5단계 — end-to-end MVP ✅ 완료 (사용자 지시로 우선 실행)

§4의 노드 id와 두 lane을 LangGraph로 연결하고 §7 알고리즘을 snake_case vocabulary에 맞춰
이식했다. FastAPI 세션 API와 자유 입력 3패널 UI까지 한 프로세스에서 실행된다.
Response Composer는 LLM이며, 장애 시 template fallback 여부가 node trace에 표시된다.

2026-08-07에는 실데이터용 `ActualUnderstandingOutput`과 영어 prompt, State Manager/Policy,
DuckDB Query/Browse, 실제 리뷰 retrieval/ranking, 두 LLM Response Composer를 별도 explicit
LangGraph로 연결했다. 기본 UI는 고정 시나리오 버튼 없이 이 경로를 사용한다. 조건이 없으면
117개 전체를 같은 규칙으로 평가하고, 확인된 hard filter가 있으면 SQL에서 먼저 제한한다.
상품별 리뷰는 bi-encoder 후보 최대 5개, Cross-Encoder 결과 최대 3개로 제한하고 점수에 사용한
리뷰 ID를 카드 근거와 일치시킨다.
전체 상품·리뷰 행은 턴 응답에 포함하지 않는다.
2026-08-07 `.env`의 실제 Luxia 키로 3턴 probe와 브라우저 자유 입력 1턴을 실행했으며 strict
Understanding, 실제 review retrieval, LLM Response Composer가 template fallback 없이 완주했다.

### 6단계 이후

동결 v2.3용 untouched holdout 20개·81턴 작성 → 동일 입력의 Full/No-memory/No-review 1회 실행 →
모든 81턴 분모의 process-level automatic benchmark → scenario paired bootstrap → fixed-upstream
No-review ranking behavior → Gold-State oracle까지 완료했다. 다음은 결과를 포스터 narrative와
figure로 정리하고, schema failure·budget 누락·previous-state over-extraction을 limitation으로
분리하는 단계다. 기존 human packet과 agreement/NDCG는 optional/future artifact다. 필요 시
PostgreSQL/pgvector → 통제 inventory snapshot → 세션 영속화 → 새 dev/holdout에서 UI·정책 보정
순으로 진행한다. 현재 Understanding/official holdout과 추천 v1 pooled set을 재튜닝에 사용하지
않는다. 데모가 영어 단일 언어인 동안 다국어는 선행 조건이 아니다.

---

## 부록 A — 삭제된 mock 상품 fixture (6개)

부록 B의 점수표를 재현·검증하려면 이 값이 필요하다. `metadata.category`는 전부 `"스마트폰"`,
`brand`는 전부 `"Apple"`.

| id | title | price | rating | reviewCount | weight(g) | battery(h) | speaker | perf | longevity(y) | delivery(d) | availableColors |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `iphone-16e` | iPhone 16e 128GB | 999,000 | 4.2 | 1,340 | 167 | 26 | 3 | 2 | 4 | 1 | 화이트, 블랙 |
| `iphone-16` | iPhone 16 128GB | 1,250,000 | 4.4 | 5,820 | 170 | 22 | 4 | 3 | 5 | 1 | 블랙, 틸, 울트라마린 |
| `iphone-17` | iPhone 17 256GB | 1,290,000 | 4.5 | 2,410 | 177 | 30 | 4 | 4 | 6 | **14** | **(없음)** |
| `iphone-air` | iPhone Air 256GB | 1,590,000 | 4.1 | 1,120 | 165 | 21 | **2** | 4 | 6 | 1 | 스페이스 블랙, 클라우드 화이트, 라이트 골드 |
| `iphone-17-pro` | iPhone 17 Pro 256GB | 1,790,000 | 4.6 | 3,260 | 206 | 33 | **5** | 5 | 6 | 1 | **코스믹 오렌지** (1개뿐) |
| `iphone-17-pro-max` | iPhone 17 Pro Max 256GB | 1,990,000 | 4.6 | 1,980 | 231 | 37 | 5 | 5 | 6 | **12** | **(없음)** |

기타 필드: `display`(6.1/6.1/6.3/6.5/6.3/6.9인치), `chipset`(A18/A18/A19/A19 Pro/A19 Pro/A19 Pro),
`storage`, `shipping`, `stock`, `description`, `useCases`, `image`(로컬 SVG).

리뷰 17개(`helpfulness` 0.75~0.92). 요지만 남긴다.

- `iphone-16e`: 가격 부담 적고 재고 여유 / 지원 기간 짧아 오래 쓸 제품으론 아쉬움
- `iphone-16`: 리뷰 수 많아 믿고 고름 / 스피커는 괜찮지만 배터리가 짧음 / 이전 세대라 남은 지원 기간 짧음
- `iphone-17`: 가격 대비 성능 균형 좋음 / 배터리 길어 오래 씀 / **재고 부족으로 수령 대기 2주**
- `iphone-air`: 재고 여유로 다음날 수령 / 가벼워 부담 없고 지원 기간 길다 / **스피커 얇고 배터리 짧아 아쉬움**
- `iphone-17-pro`: 성능 여유 크고 배터리 길다 / **스피커 크고 음질 선명** / 가격 부담 크지만 지원 기간 생각하면 납득
- `iphone-17-pro-max`: 배터리 가장 길다 / 무거워 한 손 사용 부담 / 재고 부족으로 수령 대기 열흘 넘김

---

## 부록 B — 삭제된 6턴 시나리오와 기대 결과 (회귀 검증용)

여섯 번의 발화가 여섯 턴이다. **1턴만 `ask_user`이고 나머지는 모두 `recommend`다.**
`첫 번째 제품`은 고정된 상품명이 아니라 **해당 턴 직전 순위의 1위**다.

| 턴 | 발화 | lane | itemAction | State Diff 핵심 | 결과 |
| --- | --- | --- | --- | --- | --- |
| 1 | 쓰던 아이폰이 고장 나서 새로 바꿔야 해요. | clarify | — | `category`, `subjectiveNeeds.event`, `subjectiveNeeds.goal_purpose`, `softConstraints.urgencyPressure` | 가격 기준 질문 (모호성 **57** > 45) |
| 2 | 150만 원 정도 생각하는데, 오래 쓸 거면 조금 더 써도 괜찮아요. | recommend | — | `hardConstraints.budget`, `softConstraints.budgetFlexibility`, `subjectiveNeeds.subjective_property`, `softConstraints.longevityValue` | **iPhone 17** 1위 (96점), 모호성 12 |
| 3 | 첫 번째 제품은 재고가 없어서 2주 뒤에나 받는대요. 지금 쓸 폰이 없어서 그건 안 돼요. | recommend | `reject_first` / `reject-stock-delay` (**상황적 제약**) | `hardConstraints.deliveryDeadline`, `urgencyPressure` → **`superseded`**, `rejectedItems` | **iPhone Air** 1위 (92점) |
| 4 | 이건 스피커랑 배터리가 아쉽다는 후기가 많네요. | recommend | `reject_first` / `reject-audio-battery` (**상품 속성**) | `softConstraints.audioBatteryPriority`, `rejectedItems` | **iPhone 17 Pro** 1위 (89점) |
| 5 | 그럼 추천해주신 제품을 자세히 볼게요. | recommend | `inspect_current` | `inspectedItems`, `shortlistedItems`, `currentItem` | 사양 + 즉시 수령 가능 색상 안내 |
| 6 | 색상은 지금 받을 수 있는 게 코스믹 오렌지뿐이네요. 그럼 이걸로 살게요. | recommend | `purchase_current` | `softConstraints.colorChoice`, `purchasedItems`, `tradeoffs` | 구매 기록 + 우선·양보 조건 |

### 1턴 모호성 57점 계산

```
카테고리 확인됨            0
가격 기준 미확인          +25
사용 기간 기준 미확인     +20
수령 시점 미확인          +12
리뷰 선호 미확인            0
거절 이력 없음              0
─────────────────────────────
Vagueness Score            57      Threshold 45  →  ask_user
```

교체 사유와 inferred 긴급도는 상태·가설의 근거로 저장되지만 점수에는 가감되지 않는다.

### 턴별 순위 (부록 A fixture + §7-3 수식으로 재현 가능해야 함)

**2턴** — 예산 150만, 유연성 확정, 장기 사용. 수령 기한은 아직 제약이 아니라 14일이 감점되지 않는다.

| 순위 | 상품 | total | hard | meta | subj | review |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | iPhone 17 | 96 | 100 | 100 | 90 | 91 |
| 2 | iPhone Air | 92 | 85 | 100 | 90 | 91 |
| 3 | iPhone 16 | 86 | 100 | 83 | 72 | 83 |
| 4 | iPhone 17 Pro | 83 | 52 | 100 | 100 | 85 |

**3턴** — 수령 기한 3일 하드 제약 추가, `iphone-17` 거절됨.

| 순위 | 상품 | total | hard | meta | subj | review |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | iPhone Air | 92 | 93 | 94 | 90 | 91 |
| 2 | iPhone 17 Pro | 88 | 76 | 94 | 100 | 85 |
| 3 | iPhone 16 | 87 | 100 | 86 | 72 | 83 |
| 5 | iPhone 17 Pro Max | 55 | **9** | 50 | 100 | 89 |

**4턴** — 스피커·배터리 기준 추가, `iphone-air`도 거절됨.

| 순위 | 상품 | total | hard | meta | subj | review |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | iPhone 17 Pro | 89 | 76 | 91 | 100 | 97 |
| 2 | iPhone 16 | 84 | 100 | 68 | 72 | 98 |
| 3 | iPhone 16e | 79 | 100 | 64 | 54 | 99 |
| 4 | iPhone 17 Pro Max | 64 | **9** | 74 | 100 | 97 |

예산 상한(150만)을 넘는 179만 원 상품이 1위가 되는 이유는 2턴의 `budgetFlexibility`가 확정되어
예산이 통과·탈락에서 초과율 감점으로 바뀌었기 때문이다.

### 시연에서 강조할 지점

- 1턴의 긴급도 가설은 **화면에 보이지만 점수에는 없다.** `status === "confirmed"`만 속성에 매핑된다.
- 2턴 1위인 iPhone 17은 수령까지 14일이다. **사양과 가격만 보면 최적인 후보가 실제 상황에서는 성립하지 않는다.**
- 3·4턴의 순위 변화는 상품 ID 보너스가 아니라 `deliveryDays`/`speakerTier`/`batteryHours` 차이에서 나온다.
- 3·4턴 둘 다 `reject_first`지만 `rejectionReason.id`가 다르다.
- 6턴 이후 상태에 "Pro 라인업 선호"나 "오렌지 색상 선호"라고 적힌 항목이 **없다.**
  대신 수령 기한, 예산 유연성, 잔여 색상 수용, trade-off가 적혀 있다.

### 숨은 의도 가설 (삭제된 `derive-intent-hypotheses.ts` — 하드코딩된 7개, LLM으로 교체 대상)

| 트리거 조건 | 가설 | confidence |
| --- | --- | ---: |
| `event` 있고 `deliveryDeadline` 없음 | 사양보다 수령 시점이 먼저 걸릴 가능성. **아직 확인되지 않아 랭킹 미반영** | 0.64 |
| `deliveryDeadline` 있음 | 수령 대기가 사양·가격보다 강한 실질적 결정 변수 | 0.88 |
| `longevityValue` | 가격 자체보다 사용 기간 대비 비용으로 판단 | 0.79 |
| `audioBatteryPriority` | 리뷰에서 반복 지적된 단점이 사양표상 우위보다 강한 탈락 기준 | 0.85 |
| `colorChoice` | 구매 색상은 선호가 아니라 즉시 수령 가능한 잔여 대안 | 0.92 |
| `tradeoffs` 있음 | 최고가 구매가 가격 민감도 낮음이 아니라 수령 시점 우선의 결과 | 0.90 |
| `reviewSignal` | 사회적 증거를 중요하게 생각함 | 0.90 |

LLM으로 옮길 때는 문장 하나만 반환하게 하지 말고 구조화한다. LLM은 `hypothesis_text`,
`hypothesis_type`(situational_context / tradeoff / motive_hypothesis), `related_attributes`,
`evidence_ids`, `suggested_scope`(current_purchase / category / persistent),
`alternative_explanations`, `abstain_from_ranking`까지 생성하고,
**confidence·uncertainty·ranking impact·confirmation priority·evidence status는 코드가 계산한다.**

---

## 부록 C — 스키마 준수율 측정 시드 (3단계용)

부록 B의 6개 발화를 gold label과 함께 시드로 쓴다. 기대값 예시:

```
"쓰던 아이폰이 고장 나서 새로 바꿔야 해요."
  intent: search
  candidates: category=스마트폰(explicit)
              facet.event=기존 기기 고장(explicit, 1.0)
              facet.goal_purpose=고장 난 기기 교체(implicit, 0.9)
              soft.urgencyPressure=수령 대기 민감 가능성(inferred, 0.64)
  itemAction: 없음        supersedes: []

"150만 원 정도 생각하는데, 오래 쓸 거면 조금 더 써도 괜찮아요."
  hard.budget=1,500,000원 이하(explicit)
  soft.budgetFlexibility(explicit)
  facet.subjective_property=오래 쓸 수 있는 제품(explicit)
  soft.longevityValue(explicit)

"첫 번째 제품은 재고가 없어서 2주 뒤에나 받는대요. 지금 쓸 폰이 없어서 그건 안 돼요."
  itemAction: reject_first / reject-stock-delay (상황적 제약)
  hard.deliveryDeadline=3일 이내 수령
  supersedes: ["urgencyPressure"]

"이건 스피커랑 배터리가 아쉽다는 후기가 많네요."
  itemAction: reject_first / reject-audio-battery (상품 속성)
  soft.audioBatteryPriority
  ★ "후기가 많네요"가 있지만 reviewSignal을 켜면 안 된다.
    거절로 해석된 발화에서 리뷰 언급은 신뢰 선호가 아니라 탈락 근거다.

"그럼 추천해주신 제품을 자세히 볼게요."   itemAction: inspect_current  (purchase 아님)
"색상은 지금 받을 수 있는 게 코스믹 오렌지뿐이네요. 그럼 이걸로 살게요."
  itemAction: purchase_current, residualColorChoice: true
  ★ 색상 이름을 LLM이 추출하지 않는다. "잔여 옵션 수용" 상황만 표시하고
    실제 색상은 State Manager가 currentItem의 metadata.availableColors[0]에서 읽는다.
```

확장할 발화 유형: 예산 변경, 사용 목적(필기·게임·영상), 저장 공간 거절, 비교 요청,
리뷰 신뢰 선호, 모호한 첫 요청("처음 사는데 뭘 봐야 할지 모르겠어요"), 모순 발화(이전 조건과 충돌).

---

## 부록 D — `feedback.txt`의 완료 조건 (미충족 항목 확인용)

삭제된 `feedback.txt`에 있던 체크리스트다. mock 회귀와 실데이터·LLM 경로 모두에서 계속
유효하며, 자유 입력 상태 변경도 actual workflow에서 충족했다.

```
[x] explicit / implicit / inferred가 상태와 UI에서 구분된다.
[x] "자세히 보기"가 accepted/purchased로 잘못 기록되지 않는다.
[x] 거절 상품과 거절 이유가 상태에 저장된다.
[x] 거절 이유를 반영한 재순위화가 실제로 발생한다.
[x] 추천 후보와 점수가 product ID 하드코딩 없이 계산된다.
[x] 모든 RankedProduct가 ScoreBreakdown과 실제 evidenceReviewIds를 가진다.
[x] 상품 카드의 리뷰 근거와 점수 계산에 사용된 리뷰가 일치한다.
[x] 모호성 점수에 breakdown과 reasons가 존재한다.
[x] RA-Rec explanation과 SPN Final Response가 서로 다른 객체·문장으로 관리된다.
[x] 중앙 UI에서 State Manager → Browsing → Recommendation Engine 흐름이 보인다.
[x] 사용자의 자유 입력이 DialogueState를 실제로 변경한다.
```
