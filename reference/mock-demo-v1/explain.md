# SPN Orchestrator / RA-Rec 시연 가이드

이 데모는 고정된 대화 턴을 재생하지 않습니다. 입력한 문장이 로컬 상태, 리뷰 근거, 상품 점수와 순위를 바꾸는 과정을 보여줍니다. 시나리오의 원자료는 `episode/iPhone.html`의 iPhone 17 Pro 구매 사례이고, 보여 주려는 것은 **구매 결과만으로는 선호와 상황적 제약을 구분할 수 없다**는 점입니다.

1. “쓰던 아이폰이 고장 나서…”를 입력합니다. Understanding이 상태 갱신 후보 4건을 만들고, State Manager가 고장을 `event`로, 교체를 `goal_purpose`로 기록합니다. 긴급도는 `inferred / unconfirmed` 가설로만 남고 랭킹에는 들어가지 않습니다. Policy는 모호성 57점을 근거로 `clarify-lane`을 선택해 가격 기준을 먼저 묻습니다. 이 턴에는 Query Generator 이후 노드가 실행되지 않아 검색 질의가 비어 있습니다.
2. “150만 원 정도… 오래 쓸 거면 조금 더 써도 괜찮아요”를 입력합니다. 예산 상한과 상한 초과 수용이 구분되어 저장되고, 예산이 통과·탈락에서 초과율 감점으로 바뀝니다. 모호성이 12점으로 떨어져 Policy가 검색 경로를 선택하고 iPhone 17이 1위가 됩니다. 이 시점에는 수령까지 14일이 걸린다는 사실이 어떤 제약에도 연결되지 않았으므로 점수를 깎지 않습니다.
3. “첫 번째 제품은 재고가 없어서…”를 입력합니다. 거절 이유가 기록되는 동시에 수령 기한이 하드 제약으로 승격되고, 1턴의 긴급도 가설이 `superseded`로 바뀝니다. Understanding이 `supersedes`로 대상을 지목하고 State Manager가 status만 전이시키는 분담입니다. 대기가 필요한 모델이 탈락하며 iPhone Air가 새 1위가 됩니다. 여기서 상태가 “선호가 바뀐 것”이 아니라 “제약이 드러난 것”임을 확인하세요.
4. “이건 스피커랑 배터리가 아쉽다는 후기가 많네요”를 입력합니다. 두 번째 거절은 이유의 종류가 다릅니다. 상품 속성 기준이 추가되면서 스피커 5등급·영상 재생 33시간인 iPhone 17 Pro가 1위가 됩니다. 예산 상한을 넘는 제품이 1위가 되는 이유는 2턴의 예산 유연성 때문입니다.
5. “그럼 추천해주신 제품을 자세히 볼게요”와 “색상은… 코스믹 오렌지뿐이네요. 그럼 이걸로 살게요”를 차례로 입력합니다. inspected/shortlisted와 purchased 상태가 다름을 확인하고, 색상이 `colorChoice`에 **선호가 아니라 잔여 옵션**으로 기록되는 것과 `tradeoffs`에 우선·양보 관계가 남는 것을 확인하세요.

## 중앙 패널 읽는 법

중앙 패널은 [`episode/spn-ra-rec-architecture-annotated.html`](./episode/spn-ra-rec-architecture-annotated.html)의 노드 구성을 그대로 렌더링합니다. 위쪽에 공통 3노드가 있고, 아래에 두 경로가 나란히 놓입니다.

```text
사용자 발화 → SPN Understanding (PLAN) → RA-Rec State Manager (MEMORY) → SPN Policy (DECIDE)
                         ├─ 정보 부족 → CLARIFY PATH        : SPN Response Composer (RESPOND)
                         └─ 정보 충분 → RECOMMENDATION PATH : RA-Rec Query Generator (QUERY)
                                                            → SPN Browsing Actions (ACT)
                                                            → RA-Rec Recommendation Engine (RANK)
                                                            → SPN Response Composer (RESPOND)
```

각 노드 카드에는 role 배지(PLAN/MEMORY/DECIDE/QUERY/ACT/RANK/RESPOND)와 그 노드가 만드는 산출물이 적혀 있습니다. 소유자는 색으로 구분합니다. SPN은 에메랄드, RA-Rec은 블루입니다. 이번 턴에 실행된 레인만 진하게 보이고 실행되지 않은 레인은 흐려집니다. 1턴에서는 Clarify Path만, 2턴부터는 Recommendation Path만 켜집니다. 맨 아래 줄이 응답이 다음 턴 발화로 되돌아가는 피드백 루프입니다.

우측 패널은 현재 이해한 사용자 요구, 숨은 의도 가설, 탈락 이유와 우선순위, 다음 행동과 판단 근거, State Diff 순서로 읽습니다. 숨은 의도는 명시적 조건을 확정 사실로 바꾸지 않고, 근거와 confidence를 가진 가설로 보여줍니다. 탈락 이유 카드는 재고에서 온 상황적 제약과 리뷰에서 확인된 상품 속성을 구분해 표시합니다. 모든 수치와 상품 데이터는 mock입니다.

## 시연 중 강조할 지점

- 1턴의 긴급도 가설은 화면에 보이지만 점수에는 없습니다. `rankProducts`가 `status === "confirmed"`인 선호만 상품 속성에 매핑합니다.
- 2턴에서 1위인 iPhone 17은 수령까지 14일이 걸립니다. 사양과 가격만 보면 최적인 후보가 실제 상황에서는 성립하지 않는다는 것을 보여 주는 지점입니다.
- 3턴과 4턴의 순위 변화는 상품 ID 보너스가 아니라 `deliveryDays`, `speakerTier`, `batteryHours` 같은 fixture 속성 차이에서 나옵니다.
- 3턴과 4턴은 둘 다 `reject_first`지만 `rejectionReason.id`가 다릅니다. 거절을 한 종류로 뭉개지 않는다는 점을 짚어 주세요.
- 6턴 이후 상태를 열어 보면 “Pro 라인업 선호”나 “오렌지 색상 선호”라고 적힌 항목이 없습니다. 대신 수령 기한, 예산 유연성, 잔여 색상 수용, trade-off가 적혀 있습니다.
