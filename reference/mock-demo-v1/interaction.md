# 실제 데모 상호작용 시나리오

이 문서는 현재 실행 경로인 `app/page.tsx`와 `lib/*`의 동작을 기준으로 작성한다. `data/scenario.json`, `app.js` 등 이전 프로토타입 파일은 현재 화면의 대화 진행에 사용되지 않는다.

시나리오의 원자료는 `episode/iPhone.html`이다. 그 문서의 요지는 **관찰된 구매 결과만으로는 지속적 선호와 구매 시점의 상황적 제약을 구분할 수 없다**는 것이다. 이 데모는 그 구분이 상태에 어떻게 남는지를 보여 준다.

## 한눈에 보는 실제 흐름

전송 버튼은 사용자가 직접 문장을 입력받는 UI가 아니라, 현재 상태에 맞는 다음 고정 데모 발화를 `processInput`에 전달한다. 이 데모의 실제 순서는 다음과 같다.

1. **기기 고장 알림** → 정보 부족으로 가격 기준 질문
2. **예산과 사용 기간 답변** → 즉시 첫 추천 (iPhone 17)
3. **재고 지연을 이유로 1위 거절** → 수령 기한이 하드 제약으로 승격, 재추천 (iPhone Air)
4. **리뷰의 스피커·배터리 단점을 이유로 1위 거절** → 상품 속성 기준 추가, 재추천 (iPhone 17 Pro)
5. **추천 제품 상세 보기** → 관심/검토 상태 기록
6. **잔여 색상 수용과 구매** → 구매·색상·trade-off 기록

여섯 번의 사용자 발화가 여섯 개의 턴이다. 1턴만 `ask_user`이고 나머지는 모두 `recommend`다. 상세 보기와 구매도 랭킹 파이프라인을 유지하지만, 응답 생성 단계는 상품 행동에 맞는 대사를 선택한다.

| 턴 | 사용자 발화 | lane | 상품 행동 | State Diff의 핵심 | 화면상 결과 |
| --- | --- | --- | --- | --- | --- |
| 1 | “쓰던 아이폰이 고장 나서 새로 바꿔야 해요.” | clarify | 없음 | `category`, `subjectiveNeeds.event`, `subjectiveNeeds.goal_purpose`, `softConstraints.urgencyPressure` | 가격 기준을 묻는 질문 |
| 2 | “150만 원 정도 생각하는데, 오래 쓸 거면 조금 더 써도 괜찮아요.” | recommend | 없음 | `hardConstraints.budget`, `softConstraints.budgetFlexibility`, `subjectiveNeeds.subjective_property`, `softConstraints.longevityValue` | iPhone 17 256GB 우선 추천 (96점) |
| 3 | “첫 번째 제품은 재고가 없어서 2주 뒤에나 받는대요. 지금 쓸 폰이 없어서 그건 안 돼요.” | recommend | `reject_first` (상황적 제약) | `hardConstraints.deliveryDeadline`, `softConstraints.urgencyPressure` → `superseded`, `rejectedItems` | iPhone Air 256GB 우선 추천 (92점) |
| 4 | “이건 스피커랑 배터리가 아쉽다는 후기가 많네요.” | recommend | `reject_first` (상품 속성) | `softConstraints.audioBatteryPriority`, `rejectedItems` | iPhone 17 Pro 256GB 우선 추천 (89점) |
| 5 | “그럼 추천해주신 제품을 자세히 볼게요.” | recommend | `inspect_current` | `inspectedItems`, `shortlistedItems`, `currentItem` | 17 Pro의 사양과 즉시 수령 가능 색상 안내 |
| 6 | “색상은 지금 받을 수 있는 게 코스믹 오렌지뿐이네요. 그럼 이걸로 살게요.” | recommend | `purchase_current` | `softConstraints.colorChoice`, `purchasedItems`, `tradeoffs` | 구매 기록과 우선·양보 조건 안내 |

`reject_first`가 두 번 나오지만 `rejectionReason`이 다르다. 3턴은 `reject-stock-delay`, 4턴은 `reject-audio-battery`이고, 이 id가 응답 문구와 화면의 탈락 이유 카드를 나눈다.

## 공통 처리 순서

노드 구성의 정본은 `episode/spn-ra-rec-architecture-annotated.html`이다. 각 턴은 그 그래프의 엣지 순서대로 처리된다.

```text
사용자 발화 (INPUT)
  → 이전 상태로 기존 순위 계산 ("첫 번째 제품" 지시 해석용 참조)
  → SPN Understanding (PLAN)       : understandUtterance → IntentResult
  → RA-Rec State Manager (MEMORY)  : updateDialogueState → DialogueState + Diff
  → SPN Policy (DECIDE)            : selectAction → lane 결정 (모호성 계산 포함)
  ├─ clarify-lane
  │    → SPN Response Composer (RESPOND) : composeClarifyResponse
  └─ recommend-lane
       → RA-Rec Query Generator (QUERY)      : generateNaturalLanguageQuery
       → SPN Browsing Actions (ACT)          : browseMockCatalog
       → RA-Rec Recommendation Engine (RANK) : createRecommendationResponse
       → recommendedItems 갱신
       → SPN Response Composer (RESPOND)     : composeRecommendResponse
  → 에이전트 응답 (OUTPUT) → 다음 턴 발화로 재입력 (LOOP)
```

단계 경계가 코드에서 지켜지는 방식은 다음과 같다.

- **Understanding은 상태를 쓰지 않는다.** `IntentResult`에 상태 갱신 후보만 담는다. 각 후보는 목표 슬롯(`category` / `facet` / `hard` / `soft`), id, valueText, origin, confidence를 갖는다. 선호 어휘를 정하는 것이 이 단계의 책임이다.
- **State Manager는 발화를 파싱하지 않는다.** 후보 병합, turn 근거 부착, status 전이, 행동 이력, Diff만 담당한다. 색상처럼 카탈로그에만 있는 값은 Understanding이 "잔여 옵션 수용" 상황만 표시하고 State Manager가 `metadata.availableColors`에서 읽는다.
- **Policy의 입력은 `DialogueState` 하나다.** 모호성 점수와 facet 스냅숏을 이 단계에서 계산해 `PolicyDecision.snapshot`에 담고, 분기 식별자(`clarify-lane` / `recommend-lane`)를 함께 반환한다.
- **RESPOND는 두 함수로 분리되어 있다.** 한 경로에서 다른 경로의 노드는 호출되지 않는다. 1턴에서 `query`가 `null`인 것으로 확인할 수 있다.

중요한 기준은 다음과 같다.

- `첫 번째 제품`은 고정된 상품명이 아니라 **해당 턴 직전 순위의 1위**다. 그래서 3턴에서는 iPhone 17이, 4턴에서는 재추천된 iPhone Air가 대상이 된다.
- `State Diff`는 `updateDialogueState`가 인식한 변경 경로다. 추천 뒤에 채워지는 `recommendedItems`는 화면의 Diff에는 포함되지 않는다.
- 추천은 `browseMockCatalog`의 가격·카테고리 결과를 바탕으로 한다. 최종 상품 랭킹은 `rankProducts`가 다시 계산하며, `rejectedItems`에 있는 상품은 여기서 제외된다.
- 탐색 증거(`priceEvidence`, `deliveryEvidence`)는 거절 상품을 별도로 제외하지 않는다. 그 목록에는 이전에 거절한 상품이 남을 수 있지만, 추천 카드의 순위 후보에는 남지 않는다.
- **확정되지 않은 선호는 점수에 들어가지 않는다.** `rankProducts`는 `status === "confirmed"`인 선호만 상품 속성에 매핑한다.

## 점수 계산 요약

상품 점수는 다섯 항목의 가중합이다.

```text
total = 0.30 × hardConstraintMatch
      + 0.30 × metadataMatch
      + 0.20 × subjectiveNeedMatch
      + 0.15 × reviewEvidenceScore
      + 0.05 × evidenceReliability
```

- `hardConstraintMatch`: 예산과 수령 기한의 충족도 평균. 제약이 없으면 70이다.
- `metadataMatch`: hard/soft constraint가 참조하는 상품 속성 점수의 평균. 활성 속성이 없으면 65다.
- `subjectiveNeedMatch`: SPN facet이 참조하는 상품 속성 점수의 평균. 활성 속성이 없으면 55다.

선호 id는 아래처럼 상품 속성에 매핑된다. 상품 ID별 보너스는 없다.

| 선호 id | 참조하는 상품 속성 |
| --- | --- |
| `delivery-deadline` | `deliveryDays` |
| `longevity-value` | `longevityYears` |
| `audio-battery-priority` | `speakerTier`, `batteryHours` |
| `subjective-longevity` | `longevityYears`, `performanceTier` |
| `budget-flexibility`, `urgency-pressure`, `color-residual` | 없음 (상태·근거로만 남는다) |

예산은 기본적으로 통과·탈락이지만, `budget-flexibility`가 확정되면 초과율에 비례한 감점으로 바뀐다. 그래서 1,790,000원인 17 Pro가 1,500,000원 상한에서도 후보로 남는다.

## 1턴 — 고장 사실과 확인되지 않은 긴급도

### 입력

> 쓰던 아이폰이 고장 나서 새로 바꿔야 해요.

### 상태 변화

`understandUtterance`가 스마트폰 카테고리와 기기 고장을 인식해 후보 4건을 만들고, `updateDialogueState`가 다음을 저장한다.

- `category`: 스마트폰
- `subjectiveNeeds.event`: 기존 기기 고장 (explicit / confirmed)
- `subjectiveNeeds.goal_purpose`: 고장 난 기기 교체 (implicit / confirmed, confidence 0.9)
- `softConstraints.urgencyPressure`: 지금 쓸 기기가 없어 수령 대기에 민감할 가능성 (**inferred / unconfirmed**, confidence 0.64)

여기가 이 시나리오의 출발점이다. 고장은 사실로 확인됐지만 **얼마나 급한지는 확인되지 않았다.** 그래서 긴급도는 가설로만 남고, 랭킹에는 전혀 반영되지 않는다. 화면의 숨은 의도 가설에도 “아직 확인되지 않아 랭킹에는 반영하지 않았습니다”라고 표시된다.

### 왜 추천이 아니라 질문인가

가격 기준(+25), 사용 기간 기준(+20), 수령 시점(+12)이 모두 비어 있어 모호성 점수는 **57점**이다. 질문 임계값 45를 넘으므로 `selectAction`은 `ask_user`를 선택하고, 우선순위가 가장 높은 가격 기준을 묻는다.

## 2턴 — 예산과 사용 기간, 그리고 첫 추천

### 입력

> 150만 원 정도 생각하는데, 오래 쓸 거면 조금 더 써도 괜찮아요.

### 상태 변화

- `hardConstraints.budget`: 1,500,000원 이하
- `softConstraints.budgetFlexibility`: 장기 사용 가치가 있으면 예산 상한을 넘겨도 수용
- `subjectiveNeeds.subjective_property`: 오래 쓸 수 있는 제품
- `softConstraints.longevityValue`: 장기 사용 가치 중시

수령 시점만 비어 있어 모호성은 12점이다. 임계값 이하이므로 정책은 `recommend`다.

### 순위 결과

| 순위 | 상품 | total | hard | meta | subj | review |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | iPhone 17 256GB | 96 | 100 | 100 | 90 | 91 |
| 2 | iPhone Air 256GB | 92 | 85 | 100 | 90 | 91 |
| 3 | iPhone 16 128GB | 86 | 100 | 83 | 72 | 83 |
| 4 | iPhone 17 Pro 256GB | 83 | 52 | 100 | 100 | 85 |

iPhone 17이 1위인 이유는 예산 안에 들어오면서 지원 연수와 성능이 높기 때문이다. **수령까지 14일이 걸린다는 사실은 아직 어떤 제약에도 연결되지 않았으므로 점수를 깎지 않는다.** 사례 문서가 말하는 “가격과 충분한 성능 사이의 균형”이 그대로 재현된다.

## 3턴 — 재고 지연 거절과 하드 제약 승격

### 입력

> 첫 번째 제품은 재고가 없어서 2주 뒤에나 받는대요. 지금 쓸 폰이 없어서 그건 안 돼요.

### 상태 변화

이 발화의 `첫 번째 제품`은 2턴 순위 1위였던 `iphone-17`이다.

- `rejectedItems`: `iphone-17`, 이유 “재고 부족으로 수령까지 대기 필요 · 상황적 제약”
- `hardConstraints.deliveryDeadline`: 3일 이내 수령
- `softConstraints.urgencyPressure`: status가 `unconfirmed` → **`superseded`**

“지금 쓸 폰이 없어서”가 1턴의 inferred 가설을 확인해 준다. 그래서 긴급도는 가설에서 벗어나 explicit 하드 제약이 되고, 이 시점부터 수령 소요일이 실제로 점수를 움직인다.

이 전이는 두 단계로 나뉘어 일어난다. Understanding이 `supersedes: ["urgencyPressure"]`를 후보와 함께 내보내고, State Manager가 해당 슬롯의 값이 실제로 `inferred / unconfirmed`인지 확인한 뒤 status만 바꾼다. 값과 근거 turn은 지우지 않고 보존한다.

### 순위 결과

| 순위 | 상품 | total | hard | meta | subj | review |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | iPhone Air 256GB | 92 | 93 | 94 | 90 | 91 |
| 2 | iPhone 17 Pro 256GB | 88 | 76 | 94 | 100 | 85 |
| 3 | iPhone 16 128GB | 87 | 100 | 86 | 72 | 83 |
| 5 | iPhone 17 Pro Max 256GB | 55 | 9 | 50 | 100 | 89 |

수령까지 12일이 걸리는 Pro Max는 하드 제약에서 탈락해 9점으로 내려간다. Air는 예산 초과폭이 작아 hard 93을 받고 1위가 된다. 17 Pro는 성능과 지원 연수에서 앞서지만 예산 초과 감점 때문에 2위다.

## 4턴 — 리뷰 기반 거절과 상품 속성 기준

### 입력

> 이건 스피커랑 배터리가 아쉽다는 후기가 많네요.

### 상태 변화

- `rejectedItems`: `iphone-air` 추가, 이유 “스피커·배터리 성능이 기준에 미달 · 상품 속성”
- `softConstraints.audioBatteryPriority`: 스피커·배터리 성능 중시

3턴과 4턴의 거절은 `itemAction.name`이 둘 다 `reject_first`지만 `rejectionReason.id`가 다르다. 3턴은 재고에서 온 상황적 제약(`reject-stock-delay`)이고 4턴은 상품 속성(`reject-audio-battery`)이다. 화면의 “탈락 이유와 우선순위” 카드가 이 둘을 구분해 보여 주고, 응답 문구도 이 id로 갈린다.

이 발화에도 “후기가 많네요”가 있지만 리뷰 신뢰 선호는 켜지지 않는다. 거절로 해석된 발화에서는 리뷰 언급을 신뢰 선호가 아니라 탈락 근거로 다룬다.

### 순위 결과

| 순위 | 상품 | total | hard | meta | subj | review |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | iPhone 17 Pro 256GB | 89 | 76 | 91 | 100 | 97 |
| 2 | iPhone 16 128GB | 84 | 100 | 68 | 72 | 98 |
| 3 | iPhone 16e 128GB | 79 | 100 | 64 | 54 | 99 |
| 4 | iPhone 17 Pro Max 256GB | 64 | 9 | 74 | 100 | 97 |

스피커 5등급·영상 재생 33시간인 17 Pro가 `metadataMatch` 91로 올라 1위가 된다. 16과 16e는 예산을 충족하지만 지원 연수·성능·배터리에서 밀린다. 예산 상한을 넘는 상품이 1위가 된 것은 `budgetFlexibility`가 확정되어 있기 때문이다.

## 5턴 — 상세 보기와 잔여 색상 노출

### 입력

> 그럼 추천해주신 제품을 자세히 볼게요.

### 상태 변화

이 시점의 1위가 17 Pro이므로 `inspect_current`는 17 Pro를 대상으로 동작한다.

- `inspectedItems`, `shortlistedItems`에 17 Pro 추가
- `currentItem`을 17 Pro로 설정

응답은 사양과 함께 **지금 즉시 수령이 가능한 색상은 코스믹 오렌지뿐**임을 알려 준다. 이 값은 `metadata.availableColors`에서 온다.

## 6턴 — 잔여 색상 수용과 구매

### 입력

> 색상은 지금 받을 수 있는 게 코스믹 오렌지뿐이네요. 그럼 이걸로 살게요.

### 상태 변화

- `softConstraints.colorChoice`: “코스믹 오렌지는 선호가 아니라 즉시 수령 가능한 잔여 색상” (implicit, confidence 0.95)
- `purchasedItems`: `iphone-17-pro`
- `tradeoffs`: “빠른 수령과 장기 사용 가치를 우선하고 가격·색상 선택을 양보”

색상 이름은 정규식으로 뽑지 않는다. Understanding은 `residualColorChoice: true`로 “잔여 옵션을 수용하는 상황”만 표시하고, 실제 색상은 State Manager가 `currentItem`의 `metadata.availableColors[0]`에서 읽는다. 단계 경계를 지키기 위한 분담이다.

이 턴에서 숨은 의도 가설 두 개가 추가된다.

- 구매한 색상은 색상 선호가 아니라 즉시 수령이 가능한 잔여 대안일 가능성 (0.92)
- 최고가 모델 구매가 가격 민감도가 낮다는 뜻이 아니라 수령 시점을 우선한 결과일 가능성 (0.90)

두 가설이 사례 문서의 결론에 대응한다. **최종 구매 상품의 속성과 실제 사용자의 우선순위는 일치하지 않을 수 있다.**

## 주변 상태와 화면의 관계

| 상태 | 갱신 턴 | 다음 추천에 미치는 영향 | 화면에서 확인할 위치 |
| --- | --- | --- | --- |
| 교체 사유 (event) | 1 | 점수에는 영향 없음. 긴급도 가설의 근거 | 사용자 요구, 숨은 의도, JSON |
| 긴급도 (inferred) | 1 → 3에서 superseded | `unconfirmed`이므로 랭킹에 반영되지 않음 | 숨은 의도, JSON |
| 예산 | 2 | 탐색 범위와 `hardConstraintMatch`를 결정 | 사용자 요구, State Diff, JSON |
| 예산 유연성 | 2 | 예산을 통과·탈락에서 초과율 감점으로 전환 | 사용자 요구, 점수 상세 |
| 사용 기간 기준 | 2 | 지원 연수·성능 적합도를 높임 | 사용자 요구, 점수 상세 |
| 수령 기한 | 3 | 대기가 필요한 모델을 하드 제약에서 탈락시킴 | 사용자 요구, State Diff, 점수 상세 |
| 거절 상품 | 3, 4 | 최종 랭킹 후보에서 제외 | 탈락 이유와 우선순위, JSON |
| 스피커·배터리 기준 | 4 | 스피커 등급·배터리 시간 적합도를 높임 | 사용자 요구, 점수 상세 |
| 색상 (잔여 옵션) | 6 | 점수에는 영향 없음. 선호가 아님을 명시 | 사용자 요구, 숨은 의도, JSON |
| Trade-off | 6 | 점수에는 영향 없음. 우선·양보 관계를 보존 | 탈락 이유와 우선순위, JSON |

## Appendix — Vagueness Score & Policy Rule

### 휴리스틱 점수 기준

아래 점수와 임계값은 원 SPN 논문의 공식을 그대로 재현한 값이 아니다. 이 local mock 데모에서 **추가 질문과 즉시 추천 중 무엇을 할지 결정론적으로 설명하기 위해 정의한 휴리스틱**이다.

| 구분 | 코드상 조건 | 점수 |
| --- | --- | ---: |
| 상품 카테고리 미확인 | `!state.category` | +18 |
| 가격 기준 미확인 | `!state.hardConstraints.budget` | +25 |
| 사용 기간 기준 미확인 | `!facets.subjective_property` | +20 |
| 수령 시점 미확인 | `!state.hardConstraints.deliveryDeadline` | +12 |
| 리뷰 선호 확인 | `state.softConstraints.reviewSignal` 존재 | -8 |
| 거절 이유가 제약으로 정리되지 않음 | 거절이 발생한 턴에 제약 갱신이 없음 | 건당 +12 |

계산식은 다음 네 묶음의 합이며, 최종값은 `0~100` 범위로 제한한다.

```text
Vagueness Score
= categoryBreadth
+ missingRequiredInfo
+ unresolvedSPN
+ contradictionPenalty
```

마지막 항목은 상품 ID나 특정 이유 문구에 의존하지 않는다. 거절 이유의 `evidenceTurnIds`가 어떤 hard/soft constraint의 `evidenceTurnIds`에도 나타나지 않으면 “정리되지 않은 거절”로 센다. 이 데모의 3·4턴은 거절과 제약 갱신이 같은 턴에서 일어나므로 penalty가 0이다.

### 1턴의 57점 계산

첫 발화는 카테고리와 교체 사유를 채우지만, 가격·사용 기간·수령 시점은 채우지 않는다.

```text
상품 카테고리 확인됨       0
가격 기준 미확인         +25
사용 기간 기준 미확인    +20
수령 시점 미확인         +12
리뷰 선호 미확인           0
거절 이력 없음             0
────────────────────────────
Vagueness Score           57
Ask-User Threshold        45
```

교체 사유와 inferred 긴급도는 상태와 숨은 의도 가설의 근거로 저장되지만 Vagueness Score에는 직접 가점·감점되지 않는다.

### 정책 선택 규칙

추가 질문은 아래 두 조건을 모두 만족할 때만 선택한다.

```text
Vagueness Score > Ask-User Threshold
AND
질문할 수 있는 미확인 항목이 존재
```

1턴은 `57 > 45`가 참이고 미확인 항목이 셋 있으므로 `ask_user`를 선택한다. 우선순위는 가격 기준(25) > 사용 기간 기준(20) > 수령 시점(12)이다. 2턴 이후는 12점 또는 0점이므로 계속 `recommend`다. 비교 연산자가 `>=`가 아닌 엄격한 `>`이므로, 점수가 정확히 45여도 추천 경로를 선택한다.

수령 시점이 세 항목 중 우선순위가 가장 낮다는 점이 이 사례의 핵심이다. 일반적인 쇼핑 상담의 사전 순위에서는 가격과 용도가 먼저다. 그런데 실제 결정 변수는 수령 시점이었고, 그 사실은 사용자가 재고를 이유로 후보를 거절한 뒤에야 드러난다.

## 발표용 요약

이 데모는 구매 결과가 아니라 결과에 이르는 과정을 상태로 남긴다. 1턴은 고장을 사실로 기록하되 긴급도는 확인되지 않은 가설로만 둔다. 2턴은 가격과 사용 기간만으로 iPhone 17을 1위로 만든다. 3턴의 재고 거절이 긴급도를 하드 제약으로 승격시켜 Air를 1위로 바꾸고, 4턴의 리뷰 기반 거절이 17 Pro를 1위로 만든다. 6턴에서 코스믹 오렌지는 선호가 아니라 잔여 옵션으로 기록되고, 우선한 조건과 양보한 조건이 `tradeoffs`에 남는다. 최종 구매 결과만 보면 “Pro 라인업 선호, 특정 색상 선호”로 읽히지만 상태에는 그렇게 적혀 있지 않다.
