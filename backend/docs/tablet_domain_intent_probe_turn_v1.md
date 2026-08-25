# Turn-level hidden-intent probe (exploratory)

상태: **정성 탐색 관찰 완료**

동결된 20-scenario / 81-turn holdout의 **매 턴** 누적 대화 상태를 두 갈래로 만들어,
발화 원문 없이 상태만 보고 GPT-4o-mini가 상황과 숨은 의도 가설을 추론하게 했다.

- `gold_dst`: 사전 동결된 turn 주석을 1턴부터 누적한 Gold DST
- `full_dst`: official raw run의 Full 조건이 해당 턴을 처리한 뒤의 DialogueState

> 이 문서는 숨은 의도 정확도가 아니다. 설계 상황을 서술한 gold label이 동결 holdout에
> 없으므로 점수를 만들지 않았다. official run 재실행, 동결 prompt/schema 수정,
> 새 gold label 추가는 없다. 생성된 가설은 분석 artifact이며 랭킹에 들어가지 않는다.
> `hidden_intent_analysis`의 `not_evaluable_with_current_holdout` 네 metric을 해소하지 않는다.

## 실행 규모

- 에피소드 20개 / 턴 81개
- LLM 호출 146회, 조건당 턴당 temperature 0.0 단일 표본

| 조건 | 상태 확보 | 추론 성공 | 평균 상태 ID | 턴당 평균 가설 | 스냅숏에 없는 근거 ID | 근거 ID 없는 가설 | 발화 문면 그대로인 value_text |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gold_dst` | 81/81 | 81/81 | 3.74 | 1.91 | 0 | 9 | 0 (0 턴) |
| `full_dst` | 65/81 | 65/81 | 3.8 | 1.91 | 1 | 19 | 53 (41 턴) |

`full_dst`의 미확보 턴은 official Full run이 그 턴에 도달하지 못한 경우다.
Gold 스냅숏의 `value_text`는 canonical ID에서 기계적으로 만들어지고 provenance가
균일하게 explicit/confirmed다. Full 스냅숏은 LLM이 실제 발화에서 쓴 문면이라 발화
조각이 그대로 실려 올 수 있다. 위 표의 마지막 열이 그 실측치이며, 두 조건의 차이는
상태 내용 차이와 문면 출처 차이가 섞인 결과다.

## 턴 단위 상태 차이

| 항목 | 값 |
| --- | ---: |
| 양쪽 상태가 모두 있는 턴 | 65 |
| 상태가 동일한 턴 | 17 |
| 상태가 갈린 턴 | 48 |
| 갈린 턴의 평균 차이 크기 | 1.37 |
| 한 번이라도 갈린 에피소드 | 15/20 |
| 갈린 뒤 마지막 비교 턴에서 다시 일치한 에피소드 | 0 |
| 갈린 에피소드 중 차이가 한 번도 줄지 않은 것 | 14/15 |

처음 갈라진 턴의 분포:

| 처음 갈라진 턴 | 에피소드 수 |
| ---: | ---: |
| 1 | 10 |
| 2 | 2 |
| 3 | 2 |
| 4 | 1 |

## 에피소드별 갈라짐 궤적

`턴별 차이 크기`는 비교 가능한 턴의 canonical ID 차이 크기를 순서대로 나열한 것이다.

| 에피소드 | 설계 상황 | 비교 가능 턴 | 처음 갈라진 턴 | 턴별 차이 크기 | 차이가 줄지 않음 |
| --- | --- | ---: | ---: | --- | :---: |
| `th01` | Commuter PDF reader with battery trade-off | 4 | 1 | 1 → 1 → 1 → 3 | yes |
| `th02` | Middle-school handwriting device | 4 | 3 | 0 → 0 → 1 → 2 | yes |
| `th03` | Gaming memory correction | 4 | - | 0 → 0 → 0 → 0 | - |
| `th04` | Compact streaming setup | 4 | 4 | 0 → 0 → 0 → 2 | yes |
| `th05` | Android document workflow | 4 | 1 | 2 → 2 → 2 → 2 | yes |
| `th06` | Highly rated kitchen and call device | 4 | 1 | 3 → 3 → 3 → 4 | yes |
| `th07` | Unsupported phone request recovered in tablet store | 4 | 3 | 0 → 0 → 1 → 1 | yes |
| `th08` | Temporary laptop diversion | 0 | - | - | - |
| `th09` | Windows negation with portability | 4 | 2 | 0 → 1 → 1 → 3 | yes |
| `th10` | Shared family device with microSD omission | 4 | 1 | 2 → 2 → 3 → 3 | yes |
| `th11` | Reject bulky third result | 0 | - | - | - |
| `th12` | Compare then inspect ranked choices | 4 | - | 0 → 0 → 0 → 0 | - |
| `th13` | Flexible budget for performance | 4 | 1 | 1 → 2 → 2 → 2 | yes |
| `th14` | Durable gift for a young user | 4 | 1 | 1 → 2 → 2 → 2 | yes |
| `th15` | Long-lived replacement with large storage | 4 | 1 | 1 → 1 → 1 → 2 | yes |
| `th16` | Low-cost portable reader | 4 | 1 | 2 → 1 → 1 → 2 | no |
| `th17` | Same-turn RAM correction | 2 | 1 | 1 → 1 | yes |
| `th18` | Operating-system revision | 0 | - | - | - |
| `th19` | Inspect before explicit purchase | 4 | 1 | 2 → 3 → 3 → 3 | yes |
| `th20` | Compare and reject weak display | 3 | 2 | 0 → 1 → 1 | yes |

## 차이가 가장 크게 벌어진 턴

직전 비교 턴보다 차이가 가장 많이 커진 턴을 궤적에서 계산해 고른 것이다.
사람이 선별한 사례가 아니므로 재생성하면 같은 목록이 나온다.

### `th06` turn 1 — Highly rated kitchen and call device

> It is for video calls, recipes, and ordinary web use.

- Gold State Diff: `upsert:activity_general`
- 상태 차이: 놓침 `activity_general` / 추가 `activity_video`, `goal_entertainment` (직전 대비 +3, 누적 3)
- `gold_dst` 추론: The shopper is exploring options in the tablet category, likely looking for a device that can support general activities. They have not yet made any purchases or inspected specific items, indicating they are in the early stages of their shopping process.
- `full_dst` 추론: The shopper is looking for a tablet primarily for video calls and ordinary web use. They have not yet made any purchases or inspected specific items, indicating they are still in the early stages of their shopping process.

### `th01` turn 4 — Commuter PDF reader with battery trade-off

> Battery life is worth a bit more weight to me.

- Gold State Diff: `upsert:battery`, `tradeoff:battery>portability`
- 상태 차이: 놓침 `battery` / 추가 `goal_entertainment`, `max_weight` (직전 대비 +2, 누적 3)
- `gold_dst` 추론: The shopper is looking for a tablet specifically for reading activities. They have a budget of $275 and require a storage capacity of 128 GB, with a preference for good battery life.
- `full_dst` 추론: The shopper is looking for a tablet primarily for reading and entertainment purposes. They have a budget of $275 and require at least 128 GB of storage, while being open to a tablet that is a bit heavier than average.

### `th09` turn 4 — Windows negation with portability

> Being easy to carry is important.

- Gold State Diff: `upsert:portability`
- 상태 차이: 놓침 `activity_video`, `budget` / 추가 `activity_general` (직전 대비 +2, 누적 3)
- `gold_dst` 추론: The shopper is looking for a tablet with a budget of $370, specifically for activities related to video. They have confirmed a preference for portability, indicating they may need a device that is easy to carry around.
- `full_dst` 추론: The shopper is looking for a tablet that is easy to carry, indicating a focus on portability. They have not yet made any purchases or inspected specific items, suggesting they are in the early stages of their shopping process.

### `th04` turn 4 — Compact streaming setup

> Use speaker experience in the reviews as a tie breaker.

- Gold State Diff: `upsert:audio`
- 상태 차이: 놓침 `audio` / 추가 `review_signal` (직전 대비 +2, 누적 2)
- `gold_dst` 추론: The shopper is looking for a tablet specifically for video activities, with a confirmed budget of $195 and a preference for a 10.1 inch display. They also have a soft preference for good audio quality, indicating that sound is important for their intended use.
- `full_dst` 추론: The shopper is looking for a tablet specifically for video streaming, with a budget of $195 and a minimum display size of 10.1 inches. They are also interested in using speaker experience from reviews as a deciding factor if there are multiple options that meet their criteria.

### `th05` turn 1 — Android document workflow

> I need something for editing reports and spreadsheets away from my desk.

- Gold State Diff: `upsert:goal_work_study`
- 상태 차이: 놓침 - / 추가 `activity_note_taking`, `audience_self` (직전 대비 +2, 누적 2)
- `gold_dst` 추론: The shopper is looking for a tablet specifically for work or study purposes. They have not yet inspected or purchased any items, indicating they are still in the early stages of their shopping process.
- `full_dst` 추론: The shopper is looking for a tablet specifically for note-taking purposes, likely to assist with work or study tasks. They are shopping for themselves and have not yet made any purchases or inspected any items.

## Artifact

| 종류 | 경로 | Git |
| --- | --- | --- |
| 원본 (렌더된 상태 포함) | `reports/tablet_domain_intent_probe_turn_v1.json` | 제외 |
| 전체 턴 상세 문서 | `reports/tablet_domain_intent_probe_turn_v1.md` | 제외 |
| LLM 호출 trace | `logs/tablet_domain_intent_probe_turn_v1_llm_trace.jsonl` | 제외 |
| 압축 결과 | `data/results/tablet_domain_intent_probe_turn_v1.json` | 추적 |
| 이 문서 | `docs/tablet_domain_intent_probe_turn_v1.md` | 추적 |
| manifest | `data/manifests/tablet_domain_intent_probe_turn_v1.json` | 추적 |

재생성:

```powershell
cd backend
python scripts\run_tablet_domain_intent_probe.py --overwrite   # LLM 호출 발생
python scripts\run_tablet_domain_intent_probe.py --reaggregate-only  # LLM 0회
python scripts\verify_tablet_domain_intent_probe.py
```
