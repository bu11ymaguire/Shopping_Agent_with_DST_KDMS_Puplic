# Tablet-domain automatic benchmark metric definitions

상태: v1 · 기준일 2026-08-09

이 문서는 frozen 20-scenario/81-turn holdout와 official raw result를 사후 변경하지 않고
재집계하는 automatic benchmark의 분모와 해석을 고정한다. evaluator는 LLM을 호출하지 않으며
새 gold label을 만들지 않는다.

## 1. 공통 집계 원칙

- `Full`, `No-memory`, 독립 실행된 `No-review live`의 end-to-end 지표는 81개 gold turn 전체를
  분모로 쓴다. schema failure로 실행되지 않은 turn은 predicted set이 빈 것으로 처리하며,
  정확도·completion에서는 실패다.
- hard-constraint violation과 evidence consistency처럼 실제 표시 결과가 있어야 정의되는 지표는
  표시된 상품/카드만 조건부 분모로 쓴다.
- Full–No-memory difference는 `Full - No-memory`다.
- paired 95% CI는 20개 scenario ID를 replacement 방식으로 10,000회 재표집한다. 각 표본에서
  turn을 따로 재표집하지 않고 scenario 안의 count를 함께 유지한 채 micro metric을 다시 계산한다.
  고정 seed는 `20260809 + metric offset`이다.
- p-value는 계산하지 않는다. point estimate, paired difference, bootstrap CI를 보고한다.

## 2. Understanding / State

| Metric | 정의 | 분모 |
| --- | --- | --- |
| Canonical ID precision | `TP / (TP + FP)`; turn별 `gold_candidate_ids`와 Understanding candidate ID 비교 | 모든 출력 및 미출력 turn의 predicted ID |
| Canonical ID recall | `TP / (TP + FN)` | 81턴의 gold candidate ID |
| Canonical ID micro-F1 | 위 전체 TP/FP/FN에서 계산 | 81턴 전체 |
| Final State P/R/F1 | scenario의 `final_gold_state_ids`와 실행 종료 시 active state ID 비교 | 20개 scenario의 ID 합계 |
| State Diff P/R/F1 | `gold_state_diff`와 normalized actual State Diff operation 비교 | 81턴 전체 |
| Scope accuracy | gold ID가 실제 추출되고 facet/hard/soft scope까지 같은 비율. 미추출은 오답 | gold candidate ID |
| Rejection target accuracy | gold rejection action의 target rank와 실제 rejection target rank가 같은 비율 | gold rejection turn 2개 |
| Rejection reason ID/type accuracy | rejection reason canonical ID와 reason type 각각 exact | gold rejection turn 2개 |
| Trade-off relation accuracy | prioritized ID set, compromised ID set, origin이 모두 exact | gold trade-off turn |

`gold_candidate_scopes`는 facet/hard/soft target gold이지 evidence provenance나 temporal persistence
scope gold가 아니다. 따라서 candidate-level provenance accuracy와 current-purchase→persistent scope
error에는 사용하지 않는다.

## 3. Policy / Execution

| Metric | 정의 | 분모 |
| --- | --- | --- |
| Policy lane accuracy | actual clarify/recommend가 gold lane과 exact | 81턴 |
| Question target exact | clarify turn의 `question_target.field` exact | gold clarify turn만 |
| Premature recommendation rate | gold clarify인데 actual recommend | gold clarify turn |
| Unnecessary clarification rate | gold recommend인데 actual clarify | gold recommend turn |
| Recommendation reach | gold recommend turn에서 actual lane이 recommend | gold recommend turn |
| Recommendation completion | gold recommend turn에서 query·browse·recommendation이 모두 생성 | gold recommend turn |
| Scenario completion | status가 completed인 scenario | 20개 |
| Turn completion | 저장된 successful pipeline turn | 81개 |
| Strict schema validation | `validation_success=true`인 Understanding call | 실제 시도된 Understanding call |
| Understanding attempt coverage | Understanding call이 실제 시도된 비율 | 81턴 |
| Schema repair call rate | `retry_count > 0`인 Understanding call | 실제 시도된 Understanding call |
| Transport retry call rate | `transport_retry_count > 0`인 Understanding call | 실제 시도된 Understanding call |
| LLM fallback call rate | trace의 `fallback_used=true`인 call | 모든 LLM call |
| Response/retrieval fallback | template 또는 review retrieval fallback | completed turn 또는 recommendation output |

Strict schema validation의 분모는 API call이고 turn completion의 분모는 frozen gold turn이므로 두
값을 혼용하지 않는다.

## 4. Constraint / Feedback

| Metric | 정의 | 분모 |
| --- | --- | --- |
| Hard-filter completion | turn 후 actual hard-filter object가 gold object와 exact | 81턴 |
| Hard-filter field P/R/F1 | non-null `field=value` pair를 set으로 비교 | 81턴의 field pair |
| Hard-constraint violation | 표시 top-3 상품이 그 turn의 gold hard filter를 위반 | 실제 표시·평가된 상품 |
| Rejection retention | 직전 condition-specific ranking에서 gold target rank의 상품을 복원할 수 있고, rejection turn 이후 state의 `rejected_items`에 남은 비율 | target을 복원할 수 있는 gold rejection event |
| Rejected-item reappearance | retained target이 rejection 이후 표시 순위에 다시 등장 | rejection 후 recommendation output이 있는 event |
| Feedback→state reflection | turn 2 이후 새 hard/soft gold candidate ID가 post-turn active state에 존재 | 해당 gold ID |
| Feedback→query reflection | gold recommend turn에서 위 candidate ID가 query `active_preference_ids`에 존재 | 해당 gold ID |

현재 rejection gold는 2개뿐이고 Full–No-memory에서 함께 target을 복원할 수 있는 scenario가 없다.
개별 raw rate는 보존하되 paired rejection retention difference와 CI는 `insufficient_support`다.

Trade-off relation extraction은 평가할 수 있지만, prioritized attribute가 실제 product ranking에서
위로 이동해야 한다는 product-level direction gold는 없다. 따라서 trade-off direction compliance는
`not_evaluable_with_current_holdout`이다.

## 5. Evidence / Ranking behavior

- Evidence-ID consistency: 추천 카드에 표시된 review ID set과 해당 상품 score의
  `evidence_review_ids` set이 같은 비율이다.
- Fixed-upstream No-review는 Full의 validated state, query, product candidate set을 재사용하고
  `review_evidence_score`와 `evidence_reliability`만 0으로 둔다. 추가 LLM 호출은 없다.
- Top-1 change: Full과 fixed No-review의 1위 product ID가 다른 turn 비율.
- Top-3 overlap: 두 top-3 set의 교집합 상품 수 평균.
- Top-3 Jaccard: `|intersection| / |union|`의 turn 평균.
- Mean absolute rank shift@10: 두 top-10 합집합에서 한 목록에 없는 상품을 rank 11로 놓고
  absolute rank difference를 평균한다.
- Kendall tau common@10: 두 top-10에 공통으로 존재하는 상품만 남긴 순서의 pairwise Kendall
  tau를 계산하고, 최소 2개 공통 상품이 있는 turn끼리 평균한다.

Review ablation에서 ranking 변화는 review evidence의 causal contribution을 보여주지만, human
relevance judgment가 없으므로 추천 품질 향상을 의미하지는 않는다. Product NDCG@3와 Review
NDCG@3는 official automatic result로 계산하지 않는다.

## 6. Hidden Intent 평가 가능 범위

| 요청 지표 | 상태 | 이유 |
| --- | --- | --- |
| Situational constraint promotion error | `not_evaluable_with_current_holdout` | gold rejection 2개가 모두 `product_attribute`; situational positive case 없음 |
| Unsupported preference/hypothesis inference | `not_evaluable_with_current_holdout` | v2에서 latent subjective generation 비활성화, hypothesis support gold 없음 |
| Inference restraint | `not_evaluable_with_current_holdout` | candidate/inferred status와 insufficient-evidence opportunity gold 없음 |
| Current→persistent temporal scope error | `not_evaluable_with_current_holdout` | gold scope는 facet/hard/soft만 구분 |

Forbidden candidate violation과 Canonical ID false positive는 기존 gold가 지원하는 범위의
over-extraction 지표로 별도 보고한다. 이를 hidden-intent gold로 재해석하지 않는다.

## 7. Secondary Gold-State oracle

Oracle은 각 turn의 기존 `gold_candidate_ids`, `gold_candidate_scopes`, expected hard filters를
confirmed evaluator-side state로 구성한 뒤 deterministic Policy, Query Generator, catalog filter만
실행한다. LLM, response composer, semantic retrieval은 호출하지 않는다.

Actual Full과 oracle의 Policy accuracy, recommendation reach, hard-filter completion 차이는 upstream
state 오류가 제거될 때 downstream이 회복 가능한 정도를 진단한다. expected hard filter를 사용해
gold value text를 구성하므로 독립적인 end-to-end 점수나 추천 relevance 점수가 아니라 diagnostic
upper bound다.

## 8. Human evaluation 지위

기존 blind packet, private provenance, 0~3 rubric, Krippendorff's alpha, human NDCG 및 guarded
aggregation 코드는 삭제하지 않는다. 모두 **Optional / Future Human Relevance Evaluation**으로
보존하며 이번 포스터 primary result의 readiness 조건에는 포함하지 않는다.
