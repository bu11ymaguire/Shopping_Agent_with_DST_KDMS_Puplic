# 포스터 평가 프로토콜

상태: **primary automatic benchmark 완료 · human relevance는 optional/future**

기준일: 2026-08-09

## 1. 평가 목적과 주장 범위

이번 평가는 추천 상품의 주관적 만족도나 인간 relevance를 직접 측정하지 않는다. 대신 사전에
동결된 multi-turn gold annotation을 기준으로 사용자 조건과 피드백이 Dialogue State에 보존되고,
Policy·검색 조건·추천 순위에 일관되게 전달되는지를 process-level automatic evaluation으로
측정한다.

평가 경계는 다음과 같다.

```text
발화 이해
→ State Diff와 누적 Final State
→ Policy lane / question target
→ hard-filter와 feedback propagation
→ evidence consistency / ranking behavior
```

Product NDCG@3와 Review NDCG@3는 human relevance label이 없으므로 이번 포스터의 official
automatic result로 계산하지 않는다. “추천 정확도”나 “리뷰가 추천 품질을 개선했다”는 주장도
하지 않는다.

## 2. Frozen input과 연구 무결성

Primary benchmark 입력은 다음 artifact로 고정한다.

- `data/tablet_domain_multiturn_holdout_v1.json`: 20개 untouched scenario, 81턴
- system freeze: `tablet-domain-v2.3-freeze`
- holdout freeze: `tablet-domain-holdout-v1-freeze`
- official run: `tablet-holdout-20260808T134819Z-9d786cf8`
- official result freeze: `tablet-domain-official-v1`
- 조건: Full / No-memory / 독립 No-review live

다음 규칙은 변경하지 않는다.

1. frozen system, prompt, schema, retriever, ranker, holdout을 수정하지 않는다.
2. official run을 새 metric 때문에 다시 실행하지 않는다.
3. raw result와 LLM JSONL trace에서 결정론적으로 재집계한다.
4. 새로운 metric을 위해 gold label을 사후 추가하지 않는다.
5. 현재 gold가 지원하지 않는 metric은 `not_evaluable_with_current_holdout`으로 남긴다.
6. 기존 human packet과 private provenance를 보존한다.

## 3. 연구 질문

- RQ1. Persistent multi-turn state가 single-turn state보다 사용자 조건과 피드백의 누적 보존을
  개선하는가?
- RQ2. Gold와 비교한 state가 deterministic Policy와 hard-filter에 일관되게 전달되는가?
- RQ3. Review evidence contribution을 제거하면 동일 upstream/candidate에서 순위가 얼마나
  달라지는가?

RQ3는 ranking change를 측정한다. human relevance가 없으므로 ranking change의 방향이 더 나은지는
평가하지 않는다.

## 4. Primary automatic benchmark

### Understanding / State

- Canonical ID precision / recall / micro-F1
- Final State precision / recall / micro-F1
- State Diff precision / recall / micro-F1
- facet/hard/soft scope accuracy
- rejection target, rejection reason ID/type accuracy
- trade-off relation accuracy
- forbidden candidate turn violation

Candidate-level provenance gold는 없으므로 provenance accuracy는 계산하지 않는다.

### Policy / Execution

- Policy lane accuracy
- Question target exact accuracy
- Premature recommendation rate
- Unnecessary clarification rate
- Recommendation reach/completion
- Scenario/turn completion
- Strict schema validation과 Understanding attempt coverage
- schema repair, transport retry, LLM/response/retrieval fallback

### Constraint / Feedback

- Hard-filter exact completion과 field-level P/R/F1
- 표시 상품의 hard-constraint violation rate
- Rejection retention과 rejected-item reappearance
- turn 2 이후 새 hard/soft constraint의 state/query reflection

Rejection retention은 frozen gold의 positive event가 매우 적다. Full–No-memory에서 함께 target을
복원할 수 있는 paired scenario가 없으므로 paired difference와 CI를 만들지 않고
`insufficient_support`로 보고한다.

### Evidence / Ranking behavior

- displayed review ID와 scoring evidence ID consistency
- fixed-upstream No-review top-1 change
- top-3 overlap/Jaccard와 order change
- top-10 mean absolute rank shift
- 공통 top-10의 Kendall tau

상세 수식과 분모는 `tablet_domain_automatic_metric_definitions.md`를 따른다.

## 5. End-to-end 분모

Canonical ID, State Diff, Policy, hard-filter completion 같은 primary process metric은 81개 gold turn
전체를 분모로 사용한다. schema failure 이후 실행되지 않은 turn도 end-to-end failure로 포함한다.
미출력 turn의 predicted candidate와 State Diff는 빈 set이며, policy/hard-filter exact는 false다.

반대로 displayed-product hard violation과 evidence consistency는 실제 출력이 있어야 정의되므로
표시된 상품/카드만 조건부 분모로 사용한다. 두 종류의 분모를 하나의 “accuracy”로 섞지 않는다.

## 6. Ablation 계약

### Full

```text
environment.category = tablet
+ persistent dialogue state
+ review evidence contribution
```

### No-memory

```text
environment.category = tablet
+ current-turn state only
+ review evidence contribution
```

Full–No-memory는 동일 20개 scenario의 count를 pair로 유지한다. 다음 지표에 대해 Full score,
No-memory score, `Full - No-memory`, scenario-level paired bootstrap 95% CI를 출력한다.

- Final State micro-F1
- State Diff micro-F1
- Policy accuracy
- Recommendation reach
- Hard-filter completion
- Rejection retention(현재 `insufficient_support`)

### Fixed-upstream No-review

독립 live No-review는 frozen contract대로 실행됐지만 GPT Understanding identity가 Full과 1.0이
아니므로 one-variable causal comparison으로 사용하지 않는다. primary RQ3 분석은 저장된 Full의
validated state, query, candidate set을 그대로 재사용하고 다음 항만 제거한다.

```text
review_evidence_score = 0
evidence_reliability = 0
```

가중치는 재분배하지 않고 추가 LLM 호출도 하지 않는다. 독립 live No-review 결과는 operational
robustness 관찰로만 보존한다.

Review ablation에서 ranking 변화는 review evidence의 causal contribution을 보여주지만, human
relevance judgment가 없으므로 추천 품질 향상을 의미하지는 않는다.

## 7. 통계

- bootstrap unit: scenario
- paired sample: 동일한 20개 scenario ID
- resamples: 10,000
- fixed seed: metric별 `20260809 + offset`
- 보고 형식: Full, Ablation, paired difference, 95% bootstrap CI
- p-value는 계산하거나 primary 해석에 사용하지 않는다.

한 scenario를 뽑으면 그 안의 모든 turn count를 함께 뽑고 micro metric을 다시 계산한다. turn을
독립 표본처럼 재표집하지 않는다.

## 8. Hidden Intent automatic evaluation

현재 frozen gold가 직접 지원하는 label만 사용한다.

| Metric | Status | Reason |
| --- | --- | --- |
| Situational constraint promotion error | `not_evaluable_with_current_holdout` | rejection gold 2개가 모두 product attribute |
| Unsupported hypothesis/preference inference | `not_evaluable_with_current_holdout` | hypothesis support gold 없음; v2 latent generation 비활성화 |
| Inference restraint | `not_evaluable_with_current_holdout` | candidate/inferred status와 opportunity gold 없음 |
| Current-purchase→persistent scope error | `not_evaluable_with_current_holdout` | gold scope는 facet/hard/soft만 구분 |

Canonical false positive와 forbidden candidate violation을 over-extraction으로 보고할 수는 있지만,
이를 별도의 hidden-intent gold인 것처럼 재명명하지 않는다.

## 9. Secondary Gold-State oracle

Evaluator가 기존 Gold State Diff, candidate ID/scope, expected hard filter를 confirmed state로 구성한
뒤 deterministic Policy, Query Generator, catalog filter만 실행한다. LLM, response composer,
semantic retrieval은 호출하지 않는다.

Actual Full과 Gold-State oracle의 Policy accuracy, recommendation reach, hard-filter completion을
비교해 upstream state 오류를 제거했을 때 downstream이 회복되는 범위를 본다. 이 분석은
diagnostic upper bound이며 independent recommendation relevance score가 아니다.

## 10. 결과 artifact

- machine JSON: `data/results/tablet_domain_automatic_benchmark_v1.json`
- scenario CSV: `data/results/tablet_domain_automatic_benchmark_v1_scenarios.csv`
- poster table: `docs/tablet_domain_automatic_benchmark_v1.md`
- definitions: `docs/tablet_domain_automatic_metric_definitions.md`
- tracked manifest: `data/manifests/tablet_domain_automatic_benchmark_v1.json`

## 11. Optional / Future Human Relevance Evaluation

기존 human artifact는 삭제하거나 내용을 바꾸지 않는다.

### Gold-state retrieval/ranking packet

- 3 annotators
- 1인당 상품 170건 + 리뷰 302건
- token/semantic module-level retrieval/ranking 평가
- guarded merge, ordinal Krippendorff alpha, human NDCG, bootstrap 코드 보존

### Official end-to-end output packet

- Full / No-memory / fixed-upstream No-review top-k union
- 출력이 존재한 17 scenario
- 3 annotators, 1인당 상품 88건 + 리뷰 239건
- public packet과 private provenance 분리 상태 보존

두 packet 모두 현재 human grade는 0건이다. 이번 포스터의 primary readiness나 결과 완성 조건에
human annotation을 요구하지 않는다. 추후 인력과 일정이 확보되면 동일 frozen packet을 사용해
human relevance, agreement, NDCG를 별도 보고할 수 있다.

## 12. 포스터에 넣을 수 있는 주장과 없는 주장

넣을 수 있는 주장:

- frozen 20-scenario/81-turn benchmark에서 state update, accumulated state, Policy, hard-filter,
  feedback propagation을 process-level로 자동 평가했다.
- persistent memory는 accumulated Final State를 개선했지만 current-turn State Diff에서는
  over-extraction trade-off가 있었다.
- fixed upstream에서 review contribution 제거가 ranking을 materially 변경했다.
- Gold-State oracle은 upstream error 제거 시 Policy/constraint handling의 회복 가능성을 보였다.

넣으면 안 되는 주장:

- “추천 상품이 사람에게 더 관련 있다”
- “review evidence가 추천 품질을 개선했다”
- Product/Review NDCG@3를 이번 automatic benchmark 결과로 제시
- not-evaluable hidden-intent metric을 0 error로 해석
- oracle을 실제 end-to-end 성능으로 해석

## 13. 실행 체크포인트

- [x] v2.3 system/prompt/schema/retriever/ranker freeze
- [x] untouched 20개·81턴과 gold state/action/hard filter freeze
- [x] Full / No-memory / No-review official batch 1회 실행
- [x] raw report와 조건별 LLM trace hash 보존
- [x] 모든 81턴 분모의 automatic benchmark 재집계
- [x] scenario-level paired bootstrap CI
- [x] fixed-upstream No-review ranking behavior 분석
- [x] evaluator-side Gold-State oracle diagnostic
- [x] JSON / CSV / poster Markdown / metric definitions 생성
- [x] 기존 human packet과 private provenance 보존
- [ ] Optional future human relevance annotation
