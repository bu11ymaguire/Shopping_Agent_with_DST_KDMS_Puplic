# Tablet-domain v2.3 official holdout run

이 문서는 `tablet-domain-v2.3-freeze` 시스템과
`tablet-domain-holdout-v1-freeze` 입력을 대상으로 하는 첫 공식 실행 계약이다.
결과를 본 뒤 같은 holdout에 prompt·schema·retriever·ranker를 맞추지 않는다.

## Batch 계약

한 프로세스가 다음 순서를 중간 집계 없이 한 번에 실행한다.

```text
Full (20 scenarios / 81 turns)
→ No-memory (20 / 81)
→ No-review (20 / 81)
→ 세 조건 완료 후 최초 집계
```

모든 조건은 `environment.category=tablet`, 같은 v2.3 Understanding
prompt/schema/model, 같은 catalog와 candidate limit를 사용한다.

| 조건 | Persistent state | Review contribution |
| --- | --- | --- |
| Full | O | O |
| No-memory | X — 매 턴 environment state만 입력 | O |
| No-review | O | X — review score와 reliability를 0으로 고정 |

`No-review`에서도 상품 후보 조회는 Full과 동일하게 수행한다. 단, GPT API가
bitwise deterministic하다고 보장할 수 없으므로 사후에 Full과 No-review의
Understanding·Policy·hard-filter identity rate를 별도 보고한다.

## 재실행 규칙

허용되는 재시도는 timeout, HTTP 429/5xx, 연결 단절처럼 결과 의미와 무관한
infrastructure failure뿐이다. LLM의 schema-valid 오해, strict schema validation failure,
낮은 추천 점수는 재실행 사유가 아니다. Transport와 outer turn retry 횟수는 trace와
공식 report에 남긴다.

공식 output 파일이 이미 있으면 실행기는 overwrite나 새 primary run을 거부한다.
반복 실행이 필요하면 primary 결과가 아니라 별도의 robustness experiment로 설계한다.

## 실행 전 gate와 실행 명령

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_v2.py
.\.venv\Scripts\python.exe scripts\verify_experiment_conditions.py
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_holdout_freeze.py
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_official.py
.\.venv\Scripts\python.exe scripts\run_tablet_domain_holdout_official.py
```

실행기는 다음을 확인한 뒤에만 API를 호출한다.

- 동결 dataset, v2 manifest, frozen source SHA-256
- Luxia / GPT-4o-mini / temperature 0
- semantic embedding·Cross-Encoder revision과 top-k 설정
- tracked worktree가 clean인지
- 공식 raw/summary output과 고유 trace가 아직 없는지

원본 턴 결과는 Git 제외
`reports/tablet_domain_holdout_official_v1.json`, 요약은
`reports/tablet_domain_holdout_official_v1_summary.json`, LLM trace는 `logs/`에 남긴다.
API key와 인증 header는 어떤 산출물에도 포함하지 않는다.

## Official runner가 최초 산출한 지표

- scenario/turn completion과 Understanding schema validation
- canonical ID와 State Diff micro-F1
- Policy lane 및 question-target accuracy
- recommendation-lane reach와 pipeline completion
- hard-filter completion 및 top-3 hard-constraint violation
- evidence consistency와 fallback
- turn 및 node-phase latency
- Full–No-memory delta
- Full–No-review top-3 변화와 upstream identity

현재 workflow trace에서는 catalog/review retrieval과 Cross-Encoder가 같은 browse node 안에
있으므로 latency를 `retrieval_and_cross_encoder`로 합쳐 보고한다. Product/Review NDCG는 human
relevance label이 없어 이번 포스터 official automatic result로 계산하지 않는다.

## 첫 공식 실행 결과 — 2026-08-08

`7542872` clean commit에서 세 조건을 중간 열람 없이 연속 실행했다. 원본과 결정론적 분석 hash는
`data/manifests/tablet_domain_holdout_official_v1.json`에 동결했다.

| 지표 | Full | No-memory | No-review live |
| --- | ---: | ---: | ---: |
| Scenario completion | 15/20 | 17/20 | 16/20 |
| Turn completion | 65/81 | 68/81 | 65/81 |
| Canonical ID micro-F1 | 0.592 | 0.803 | 0.586 |
| State Diff micro-F1 | 0.619 | 0.764 | 0.610 |
| Final-state micro-F1 | 0.758 | 0.500 | 0.738 |
| Policy lane accuracy | 1.000 | 0.882 | 1.000 |
| Recommendation-lane reach | 1.000 | 0.879 | 1.000 |
| Hard-constraint completion | 0.785 | 0.529 | 0.785 |
| Top-3 hard violation | 25/185 | 51/174 | 21/181 |
| Evidence consistency | 1.000 | 1.000 | 1.000 |
| Median turn latency | 9.93 s | 4.16 s | 8.23 s |

Full과 No-memory가 모두 완료한 61개 turn, 14개 scenario만 짝지으면 다음과 같다.

- Final-state micro-F1: `0.800 vs 0.524`, Full delta `+0.276`
- Policy lane accuracy delta: `+0.115`
- Recommendation-lane reach delta: `+0.119`
- Hard-constraint completion delta: `+0.230`
- State Diff micro-F1: `0.630 vs 0.765`, Full delta `-0.135`

즉 persistent state는 누적 상태와 hard constraint, 다음 행동 보존에는 기여했지만, 이전 상태가
Understanding context에 들어가면서 현재 turn에 이미 알려진 facet을 다시 추출하는 오류도 늘었다.
따라서 RQ1은 State Diff 하나만으로 결론내리지 않고 **현재 turn update precision**과
**누적 final-state retention**을 함께 보고해야 한다.

Full의 5개 실패는 모두 `StructuredOutputError`였고, 4개는 facet candidate에 잘못된 scope를
부여한 오류, 1개는 duplicate canonical ID였다. Full에서 budget이 5개 turn 누락되면서 총
14개 hard-filter mismatch turn과 표시된 top-3 185개 중 25개 위반으로 전파됐다. 반면
Policy lane, 표시 review–scoring evidence 일치, response/retrieval fallback은 성공 turn에서
각각 1.000, 1.000, 0이었다.

### No-review 해석 교정

독립 live No-review는 코드상 review만 제거했지만, GPT를 새로 호출했기 때문에 Full과 같은
61개 completed turn에서 Understanding output identity가 0.721에 그쳤다. 이 결과의 ranking
차이를 review 효과라고 단독 해석할 수 없다.

그래서 추가 LLM 호출 없이 Full의 validated state/query/candidate set을 그대로 재사용하고
review score와 reliability만 0으로 만든 fixed-upstream counterfactual을 결정론적으로 산출했다.
Candidate count identity는 1.000이었고, 63개 recommendation turn에서 top-1이 55.6% 바뀌고
top-3 순서가 완전히 같은 경우는 12.7%뿐이었다. 이는 review가 ranking을 **변화시킨다**는
근거이지만, 좋아졌는지는 human relevance 없이는 판단하지 않는다.

## Primary automatic 재집계 — 2026-08-09

위 첫 표는 successful output에 조건부인 original runner metric을 포함한다. 포스터 primary
automatic benchmark는 official run을 재실행하지 않고 raw result와 LLM trace를 읽어 81개 gold
turn 전체를 분모로 다시 집계한다. schema failure 이후 미출력 turn도 end-to-end failure다.

| 지표 | Full | No-memory | No-review live (secondary) |
| --- | ---: | ---: | ---: |
| Canonical ID micro-F1 | 0.544 | 0.744 | 0.541 |
| State Diff micro-F1 | 0.562 | 0.700 | 0.560 |
| Final State micro-F1 | 0.758 | 0.500 | 0.738 |
| Policy accuracy | 0.802 | 0.741 | 0.802 |
| Recommendation reach | 0.808 | 0.744 | 0.808 |
| Hard-filter completion | 0.630 | 0.444 | 0.630 |

Full–No-memory의 20-scenario paired difference와 bootstrap 95% CI:

- Final State F1: `+0.258 [0.172, 0.334]`
- State Diff F1: `-0.138 [-0.222, -0.056]`
- Policy: `+0.062 [-0.111, 0.222]`
- Recommendation reach: `+0.064 [-0.103, 0.231]`
- Hard-filter completion: `+0.185 [0.000, 0.363]`
- Rejection retention: paired target support가 없어 `insufficient_support`

Fixed-upstream No-review의 top-1 change는 0.556, top-3 order change는 0.873, mean top-3
Jaccard는 0.522, mean absolute rank shift@10은 2.228이다. ranking change는 review evidence의
causal contribution을 보여주지만 추천 품질 향상을 의미하지 않는다.

Gold-State oracle은 actual Full 대비 Policy `0.802→1.000`, reach `0.808→1.000`, hard-filter
completion `0.630→1.000`으로 회복했다. 이는 evaluator-side diagnostic upper bound다.

정본 표와 분모는 `tablet_domain_automatic_benchmark_v1.md`와
`tablet_domain_automatic_metric_definitions.md`를 따른다.

## Optional / Future Human Relevance Evaluation packet

Final Full / No-memory / fixed-upstream No-review top-3 합집합을 시나리오별로 중복 제거했다.
세 조건 모두 final 출력이 없는 `th08`, `th11`, `th20`은 relevance pool에서 제외하되,
end-to-end completion 결과에서는 실패로 계속 센다.

- 판정 가능한 시나리오: 17개
- 1인당 상품: 88건
- 1인당 리뷰: 239건
- 1인당 합계: 327건
- 판정자: 3명, 같은 pool에 독립 순서
- 세션: 상품 88건 1개 + 리뷰 120/119건 2개

작업 사본은 Git 제외
`reports/tablet_domain_holdout_annotation_packet_v1/annotator-*/sessions/`에 있다.
판정자에게는 자기 `sessions/`의 세 파일만 전달한다. `private_provenance.json`, 다른 판정자 폴더,
packet/session manifest는 전달하지 않는다. Public 파일에는 시스템 이름, 원래 rank·score,
원본 product/review ID가 없고 모든 grade는 비어 있다. 추적 hash는
`data/manifests/tablet_domain_annotation_packet_v1.json`에 동결했다.

이 packet은 삭제하지 않지만 이번 포스터 primary readiness에는 사용하지 않는다. human grade는
0건이며 private provenance와 빈 세션을 그대로 보존한다.
