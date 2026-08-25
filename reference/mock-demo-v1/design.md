# SPN / RA-Rec 상태 기반 설계

## 정본

파이프라인 구조의 정본은 [`episode/spn-ra-rec-architecture-annotated.html`](./episode/spn-ra-rec-architecture-annotated.html)이다. 그 문서의 `architecture-graph` JSON이 노드 id, owner, role, 입출력, 엣지를 정의한다. 아래 표는 그 노드와 구현 모듈의 대응이며, 코드가 이 대응을 벗어나면 코드를 고친다.

| 노드 id | role | owner | 구현 | 입력 → 출력 |
| --- | --- | --- | --- | --- |
| `user-utterance` | INPUT | user | `Home.processInput` | 발화 원문 |
| `spn-understanding` | PLAN | SPN | `lib/spn/understand.ts` | 발화 + 이전 상태 → `IntentResult` |
| `ra-state-manager` | MEMORY | RA-Rec | `lib/rarec/state-manager.ts` | `IntentResult` → `DialogueState` + `StateDiff` |
| `spn-policy` | DECIDE | SPN | `lib/spn/select-action.ts` | `DialogueState` → `PolicyDecision`(lane) |
| `spn-response-clarify` | RESPOND | SPN | `composeClarifyResponse` | clarify 결정 → 추가 질문 |
| `ra-query-generator` | QUERY | RA-Rec | `lib/rarec/generate-query.ts` | recommend 결정 + 상태 → 질의 |
| `spn-browsing-actions` | ACT | SPN | `lib/spn/browse.ts` | 질의 → 상품·리뷰·가격·배송 |
| `ra-recommendation-engine` | RANK | RA-Rec | `lib/rarec/rank-products.ts`, `rank-reviews.ts` | 상품·리뷰 → `RankedProduct[]` + 근거 |
| `spn-response-recommend` | RESPOND | SPN | `composeRecommendResponse` | 순위 + 근거 → 추천 응답 |
| `feedback-loop` | LOOP | — | 전송 버튼이 갱신된 상태로 다음 발화를 만든다 | 응답 → 다음 턴 발화 |

## 단계 경계

**Understanding(PLAN)은 상태를 쓰지 않는다.** 발화와 이전 상태만 보고 `IntentResult`를 만든다. 여기에는 의도 라벨, SPN 5 facets, 상태 갱신 후보(`StateUpdateCandidate[]`), 상품 행동 후보, superseded 대상 키가 담긴다. 각 후보는 목표 슬롯(`category` / `facet` / `hard` / `soft`), id, valueText, origin, confidence까지 정한다. 즉 어휘는 Understanding의 책임이다.

**State Manager(MEMORY)는 발화를 파싱하지 않는다.** 후보를 병합하고 turn 근거를 붙이며, status 전이(`unconfirmed` → `superseded`), 관심·거절·비교·구매 이력, trade-off, 변경 경로를 관리한다. 즉 병합 의미론은 State Manager의 책임이다. 정보가 충분한 경우에만 호출되는 모듈이 아니라 모든 발화 뒤 실행되며 부분 정보도 누적한다.

**Policy(DECIDE)의 입력은 `DialogueState` 하나다.** 모호성 점수와 facet 스냅숏은 이 단계에서 계산해 `PolicyDecision.snapshot`으로 반환한다. 어떤 누락 정보를 질문할지와 우선순위도 Policy의 책임이다. 출력에는 다이어그램의 분기 식별자(`clarify-lane` / `recommend-lane`)를 그대로 담는다.

**RESPOND는 두 노드다.** `composeClarifyResponse`와 `composeRecommendResponse`가 각각 대응하며, 한쪽 경로에서는 다른 쪽이 호출되지 않는다.

## 상태와 근거

`DialogueState`는 hard/soft constraint, 5-facet subjective needs, tradeoffs, rejected/inspected/shortlisted/purchased item, 변경 이력, unresolved preference를 보관한다. `PreferenceValue`는 `explicit | implicit | inferred`, confidence, `confirmed | unconfirmed | superseded`, turn evidence를 갖는다.

상세 보기는 inspected/shortlisted/currentItem만 변경한다. 구매 확정만 purchasedItems에 기록한다.

거절 이유는 종류를 구분해 저장한다. 재고처럼 구매 시점의 상황에서 온 제약과, 리뷰에서 확인된 상품 속성은 서로 다른 근거다. 같은 "거절"로 뭉개면 이 차이가 사라진다.

## 랭킹

개별 리뷰 점수는 intent similarity, preference coverage, helpfulness로 계산한다. `reviewCount`는 log-scaled 상품 단위 evidence reliability에만 반영한다.

상품 점수는 hard constraint, metadata, subjective need, top-k review evidence, evidence reliability를 0~100으로 정규화해 가중합한다. 상품 ID 보너스나 시나리오 전용 규칙은 두지 않는다. 선호 id를 상품 속성에 매핑하는 표 하나만 두고, mock fixture의 속성 차이로 순위가 바뀌게 한다.

**확정되지 않은 선호는 점수에 들어가지 않는다.** `status !== "confirmed"`인 값은 근거로만 표시된다. 그래서 inferred 가설은 화면에 보이면서도 순위를 흔들지 않는다.

## 서비스 계약

RA-Rec의 `RecommendationResponse`는 업데이트된 상태, RankedProduct, 실제 evidenceReviewIds, 내부 판단 설명을 반환한다. SPN의 `FinalResponse`는 이를 가격·배송·이미지·후속 행동과 결합한 별도 사용자용 문장이다.
