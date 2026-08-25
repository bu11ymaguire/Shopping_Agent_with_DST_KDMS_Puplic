# Tablet-domain automatic benchmark v1

상태: **포스터 primary automatic benchmark 완료**

정본 실행: `tablet-holdout-20260808T134819Z-9d786cf8` · frozen holdout 20개 시나리오/81턴 · official run 재실행 없음

이번 평가는 추천 상품의 주관적 만족도나 인간 relevance를 직접 측정하지 않는다. 대신 사전에 동결된 multi-turn gold annotation을 기준으로 사용자 조건과 피드백이 Dialogue State에 보존되고, Policy·검색 조건·추천 순위에 일관되게 전달되는지를 process-level automatic evaluation으로 측정한다.

## End-to-end automatic overview

모든 81턴을 분모에 포함한다. schema failure 이후 미출력 turn은 end-to-end 실패로 처리한다.

| Condition | Scenario completion | Turn completion | Canonical F1 | Final State F1 | State Diff F1 | Strict schema validation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full | 15/20 | 65/81 | 0.544 | 0.758 | 0.562 | 0.929 |
| No-memory | 17/20 | 68/81 | 0.744 | 0.500 | 0.700 | 0.958 |
| No-review live (secondary) | 16/20 | 65/81 | 0.541 | 0.738 | 0.560 | 0.942 |

## Full vs No-memory: paired scenario bootstrap

Difference는 `Full - No-memory`이며 20개 시나리오를 10,000회 paired bootstrap했다.

| Metric | Full | No-memory | Difference | 95% CI |
| --- | ---: | ---: | ---: | ---: |
| Final State micro-F1 | 0.758 | 0.500 | 0.258 | [0.172, 0.334] |
| State Diff micro-F1 | 0.562 | 0.700 | -0.138 | [-0.222, -0.056] |
| Policy accuracy | 0.802 | 0.741 | 0.062 | [-0.111, 0.222] |
| Recommendation reach | 0.808 | 0.744 | 0.064 | [-0.103, 0.231] |
| Hard-filter completion | 0.630 | 0.444 | 0.185 | [0.000, 0.363] |
| Rejection retention | 0.000 | N/A | N/A | N/A |

Rejection retention은 두 조건에서 함께 target을 복원할 수 있는 paired scenario가 없어 `insufficient_support`다. 현재 holdout에서 CI를 만들지 않는다.

## Fixed-upstream No-review ranking behavior

Full의 validated state·query·candidate set을 그대로 두고 review score와 reliability만 제거했다. 추가 LLM 호출은 0회이고 candidate-set identity는 1.000이다.

| Metric | Value | Scenario-bootstrap 95% CI |
| --- | ---: | ---: |
| Top-1 change rate | 0.556 | [0.403, 0.712] |
| Top-3 order change rate | 0.873 | [0.754, 0.968] |
| Mean top-3 overlap count | 1.794 | [1.411, 2.148] |
| Mean top-3 Jaccard | 0.522 | [0.388, 0.661] |
| Mean absolute rank shift@10 | 2.228 | [1.697, 2.836] |
| Mean Kendall tau on common@10 | 0.521 | [0.417, 0.619] |

- Full evidence-ID consistency: 1.000
- Fixed No-review hard-constraint violation rate: 0.168

Review ablation에서 ranking 변화는 review evidence의 causal contribution을 보여주지만, human relevance judgment가 없으므로 추천 품질 향상을 의미하지는 않는다.

## Secondary Gold-State oracle diagnostic

| Metric | Actual Full | Gold-State oracle | Recovery |
| --- | ---: | ---: | ---: |
| Policy accuracy | 0.802 | 1.000 | 0.198 |
| Recommendation reach | 0.808 | 1.000 | 0.192 |
| Hard-filter completion | 0.630 | 1.000 | 0.370 |

Oracle candidate availability는 0.974, candidate hard-violation rate는 0.000다. 이는 evaluator-side deterministic upper-bound 진단이며 추천 relevance 점수가 아니다.

## 제외·보류 지표

- Product NDCG@3 / Review NDCG@3: human relevance label이 없어 official result에서 제외
- provenance accuracy: frozen holdout에 candidate-level provenance gold가 없음
- trade-off direction compliance: relation gold는 있으나 expected product ranking direction이 없음
- situational→persistent promotion error: situational rejection positive case가 없음
- inference restraint와 temporal scope error: hypothesis/status 및 temporal-scope gold가 없음

기존 3인용 blind packet과 agreement/NDCG 코드는 삭제하지 않고 **Optional / Future Human Relevance Evaluation** artifact로 보존한다.
