# Extended Experiment v1 결과: Full-Memory False Update

## 판정

사전 선택 규칙이 고른 탐색적 방법은 **C: deterministic semantic no-op
suppression**이다. C는 M0 Full-Memory 대비 material State Diff false positive를
`50 → 17`로 줄이고 State Diff micro-F1을 `0.601 → 0.739`로 높였다. 동시에
Final State micro-F1(`0.763 → 0.796`), correction recall(`1.000 → 1.000`),
hard-filter completion(`0.679 → 0.741`), turn completion(`69/81 → 71/81`)의
사전 보존 gate를 모두 통과했다.

이 판정은 **사후 탐색 결과**다. 20개·81턴 holdout은 기존 공식 실험에서 이미
관찰됐으므로 C의 우위를 확증 결과로 주장하지 않는다. C와 M0를 새 untouched
holdout에 실행하기 전까지 결과 라벨은 `exploratory_selected_strategy`다.

## 실행 계약

- 브랜치: `Extended_Experiment`
- 실행 commit: `cb6bcf2683f622e3c44edc6672b7167ae1b2171a`
- run ID: `extended-20260817T072917Z-fa9cd2af`
- 데이터: 20 episode, 81 turn, 기존 관찰 holdout의 사후 재사용
- 모델: 모든 trace에서 `gpt-4o-mini-2024-07-18`
- structured mode: 모든 logical call에서 `json_schema`
- Response Composer: deterministic template
- 공통 downstream: DialogueState → Policy → Query → catalog filter → semantic
  review retrieval → Cross-Encoder → Rank
- logical Understanding call: 356회
- schema repair: 133회
- 실제 structured-generation HTTP attempt: 489회
- transport retry와 fallback: 모두 0회

계획상 최대 logical call은 `81 × 6 = 486`이었지만, schema 실패가 발생한 episode는
그 지점에서 종료했기 때문에 실제 logical call은 356회다. 489 HTTP attempt는 logical
call과 그 안의 schema repair attempt를 합한 값이며 transport retry가 아니다.

## Primary automatic 결과

모든 F1과 completion의 분모에는 출력되지 않은 턴도 실패로 포함했다.

| Arm | 완료 episode | 완료 turn | Candidate F1 | Candidate FP | C2U FP | State Diff F1 | Material Diff FP | Final State F1 | Correction recall | Hard-filter completion | Schema valid |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| M0 baseline | 17/20 | 69/81 | 0.579 | 50 | 31 | 0.601 | 50 | 0.763 | 1.000 | 0.679 | 0.958 |
| A SOM operation | 4/20 | 31/81 | 0.508 | 9 | 0 | 0.479 | 9 | 0.638 | 0.333 | 0.383 | 0.660 |
| B sparse delta | 6/20 | 31/81 | 0.448 | 12 | 0 | 0.455 | 13 | 0.584 | 0.000 | 0.383 | 0.689 |
| **C semantic no-op** | **16/20** | **71/81** | 0.593 | 55 | 38 | **0.739** | **17** | **0.796** | **1.000** | **0.741** | 0.947 |
| D full-state diff | 5/20 | 25/81 | 0.424 | 8 | 0 | 0.361 | 28 | 0.444 | 0.333 | 0.210 | 0.625 |
| E compact context | 18/20 | 75/81 | **0.697** | **31** | **4** | 0.713 | 31 | 0.785 | 0.667 | **0.827** | **0.974** |

### C가 해결한 것과 해결하지 않은 것

C의 효과는 **Understanding 후보 추출 개선이 아니라 State Manager의 material-change
계측 개선**이다. 독립 live call의 stochastic variation 때문에 C run의 Candidate FP와
C2U FP는 오히려 M0보다 많았다(`55 vs 50`, `38 vs 31`). 그러나 현재 state와
정규화된 의미가 같은 후보를 merge 전에 제거해 material Diff FP를 17로 낮췄다.
따라서 이번 결과가 지지하는 문장은 다음과 같다.

> 의미상 동일한 재방출을 결정론적으로 no-op 처리하면, 실제 상태 변화가 아닌
> provenance/changed-path 갱신을 막으면서 누적 Final State와 교정을 보존할 수 있었다.

“LLM이 과거 slot을 현재 update로 다시 추출하는 문제가 해결됐다”라고 말할 수는 없다.
그 후보 계층 문제는 C에 그대로 남는다.

### E의 의미

E는 extractor 계층에서 가장 유망했다. M0 대비 Candidate FP는 `50 → 31`, C2U FP는
`31 → 4`, Candidate F1은 `0.579 → 0.697`로 개선됐다. 또한 가장 높은 turn completion,
Final State F1, hard-filter completion을 기록했다. 하지만 사전 지정한 교정 3턴 중
`th18:3` 출력을 얻지 못해 correction recall이 `0.667`이 되었고, M0 대비 허용 하락폭
0.05를 넘어서 탈락했다.

따라서 E를 현재 winner로 바꾸거나 C와 즉시 결합하지 않는다. 새 개발 fixture에서
교정/unsupported-category schema failure를 먼저 분석한 후, 별도 frozen replication의
후보로 두는 것이 맞다.

### A/B/D의 낮은 FP를 해석하면 안 되는 이유

A/B/D의 FP 절대 수가 작아 보이는 주된 이유는 각각 50, 50, 56개의 턴 출력이
없었기 때문이다. 누락 turn은 Gold update의 FN으로 집계돼 recall, Final State,
hard-filter completion이 크게 하락했다. 현 strict-schema 번역에서는 preference
`scope`, carryover/delete payload, unsupported-category mutation 계약을 GPT-4o-mini가
자주 위반했다.

이는 SOM-DST, IC-DST, Diable 또는 full-state tracking 일반이 열등하다는 결론이 아니다.
이번 zero/few-shot GPT structured-output 어댑터 구현이 현재 운영 계약에서 불안정했다는
결과다.

## 사전 보존 gate

M0 대비 허용 margin은 Final State F1 0.02, correction recall 0.05,
hard-filter completion 0.02, turn completion 0.02였다.

| Arm | Final State | Correction | Hard filter | Turn completion | 전체 통과 |
| --- | ---: | ---: | ---: | ---: | ---: |
| M0 | 통과 | 통과 | 통과 | 통과 | 통과 |
| A | 실패 | 실패 | 실패 | 실패 | 실패 |
| B | 실패 | 실패 | 실패 | 실패 | 실패 |
| C | 통과 | 통과 | 통과 | 통과 | **통과** |
| D | 실패 | 실패 | 실패 | 실패 | 실패 |
| E | 통과 | **실패** | 통과 | 통과 | 실패 |

통과 arm을 State Diff F1 내림차순으로 정렬한 결과는 `C → M0`였다.

## Gold-State Oracle 추천 충실도

Oracle은 Gold DialogueState를 현재 결정론적 recommender에 넣은 결과이며 Gold 상품이나
인간 relevance 정답이 아니다. 또한 arm별 비교 가능 episode 수가 달라 secondary
diagnostic으로만 본다.

| Arm | 비교 가능 episode | Top-1 일치 | Ordered Top-3 | Top-3 set | 평균 overlap | 평균 Jaccard |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| M0 | 16 | 0.313 | 0.188 | 0.438 | 1.563 | 0.538 |
| A | 4 | 0.500 | 0.500 | 0.500 | 1.250 | 0.625 |
| B | 6 | 0.500 | 0.167 | 0.333 | 1.667 | 0.533 |
| C | 15 | **0.533** | **0.333** | **0.467** | **1.667** | **0.580** |
| D | 5 | 0.000 | 0.000 | 0.000 | 0.600 | 0.167 |
| E | 17 | 0.412 | 0.176 | 0.176 | 1.059 | 0.341 |

C는 현재 run에서 M0보다 Oracle-conditioned 추천에 가까웠지만, 이 차이는 서로 다른
비교 가능 episode 집합과 작은 표본 위의 사후 결과다. 방법 선택 gate에는 사용하지
않았다.

## 다음 실험 결정

1. C와 M0만 새 untouched multi-turn holdout 실행 전에 동결한다.
2. primary endpoint는 81턴 방식과 동일하게 missing output을 실패로 센 State Diff F1로
   유지하고, material FP와 Final State/교정 보존을 함께 보고한다.
3. C의 confirmatory 결과가 재현된 뒤에만 기본 Full-Memory State Manager 채택을 논의한다.
4. E는 별도 개발·replication arm으로 보존한다. C+E 결합은 두 독립 효과가 확인되기 전에는
   만들지 않는다.

## 산출물과 무결성

- compact result: `data/results/tablet_domain_extended_experiment_posthoc_v1.json`
- raw report(Git 제외): `reports/tablet_domain_extended_experiment_posthoc_v1.json`
- trace(Git 제외): `logs/tablet_extended_extended-20260817T072917Z-fa9cd2af_*.jsonl`
- result manifest: `data/manifests/tablet_domain_extended_experiment_result_v1.json`

Raw SHA-256는
`c2e147a7dfc9785a71ff427f167174bbcbed3d1704ef1b33125acf031e77f371`이다.
Compact와 trace hash는 result manifest에 고정한다. API key, 인증 header, `.env` 내용은
산출물에 포함하지 않았다.
