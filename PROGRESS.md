# ShoppingAgent_with_DST_KDMS 진행상황

기준일: 2026-08-16 (primary automatic benchmark는 2026-08-09에 동결)

이 문서는 현재 검증된 구현과 포스터에서 주장 가능한 평가 범위를 구분한다. 세부 평가 계약은
`backend/docs/poster_evaluation_protocol.md`, 지표 분모는
`backend/docs/tablet_domain_automatic_metric_definitions.md`가 정본이다.

## 현재 도달점

### 시스템과 데이터

- Luxia GPT-4o-mini strict structured output, retry·trace와 explicit LangGraph workflow
- Understanding → State Manager → Policy → Query/Browse/Rank → 분리된 Response Composer
- 영어 자유 입력 FastAPI/로컬 UI와 통제 iPhone 회귀 경로
- Amazon Reviews 2023 Electronics 고정 가공
  - 태블릿 117개, 본문 중복 없는 실제 리뷰 7,552개
  - DuckDB/Parquet hard filter, 384차원 local semantic index, Cross-Encoder reranking
  - 원문·Parquet·trace·API key는 Git 제외, revision/hash는 manifest로 추적
- tablet-domain v2.3 freeze
  - `environment.category=tablet`, explicit unsupported-category routing
  - latent `subjective_*` generation 비활성
  - prompt/schema/retriever/ranker/source hash 동결

### Frozen evaluation input과 official run

- v2.3 freeze 이후 작성하고 첫 실행 전에 동결한 untouched holdout 20개·81턴
- Gold candidate ID/scope, State Diff, Final State, Policy/action, question target, hard filters 포함
- product ID, 추천 순위, human relevance gold는 포함하지 않음
- Full → No-memory → No-review를 clean execution commit에서 중간 집계 없이 각 1회 실행
- official run ID: `tablet-holdout-20260808T134819Z-9d786cf8`
- raw report, 조건별 LLM trace, deterministic analysis SHA-256 보존
- frozen system/prompt/holdout과 official result는 재실행·재튜닝하지 않음

### Primary automatic benchmark

기존 human relevance 평가를 primary에서 제외했다. frozen 81턴 전체를 분모로 하며 schema failure
이후 미출력 turn도 end-to-end failure로 집계한다.

| Condition | Scenario | Turn | Canonical F1 | Final State F1 | State Diff F1 | Policy | Reach | Hard completion |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Full | 15/20 | 65/81 | 0.544 | 0.758 | 0.562 | 0.802 | 0.808 | 0.630 |
| No-memory | 17/20 | 68/81 | 0.744 | 0.500 | 0.700 | 0.741 | 0.744 | 0.444 |
| No-review live (secondary) | 16/20 | 65/81 | 0.541 | 0.738 | 0.560 | 0.802 | 0.808 | 0.630 |

표시 결과에만 정의되는 Full hard-constraint violation은 25/185 = 0.135이고 evidence-ID
consistency는 1.000이다. Full strict schema validation은 65/70 = 0.929, schema repair가 필요한
Understanding call은 30/70 = 0.429였다. transport retry와 LLM fallback은 0이다.

### Full vs No-memory paired scenario bootstrap

Difference는 `Full - No-memory`다. 20개 scenario를 10,000회 paired bootstrap했다.

| Metric | Full | No-memory | Difference | 95% CI |
| --- | ---: | ---: | ---: | ---: |
| Final State micro-F1 | 0.758 | 0.500 | +0.258 | [0.172, 0.334] |
| State Diff micro-F1 | 0.562 | 0.700 | -0.138 | [-0.222, -0.056] |
| Policy accuracy | 0.802 | 0.741 | +0.062 | [-0.111, 0.222] |
| Recommendation reach | 0.808 | 0.744 | +0.064 | [-0.103, 0.231] |
| Hard-filter completion | 0.630 | 0.444 | +0.185 | [0.000, 0.363] |
| Rejection retention | 0.000 | N/A | N/A | insufficient support |

Persistent state는 accumulated Final State와 hard-filter completion을 개선했다. 반대로 current-turn
State Diff는 이전 상태 문맥의 과잉 재추출 때문에 No-memory보다 낮았다. 두 결과를 함께 보고한다.
Rejection gold는 2개뿐이고 두 조건에서 함께 target을 복원할 수 있는 paired scenario가 없어 CI를
만들지 않았다.

### Fixed-upstream No-review

독립 live No-review의 GPT Understanding은 Full과 동일하지 않아 causal RQ3 비교로 쓰지 않는다.
저장된 Full state/query/candidate set을 고정하고 review score/reliability만 제거한 결과다.

| Metric | Value | 95% scenario-bootstrap CI |
| --- | ---: | ---: |
| Candidate-set identity | 1.000 | — |
| Top-1 change rate | 0.556 | [0.403, 0.712] |
| Top-3 order change rate | 0.873 | [0.754, 0.968] |
| Mean top-3 overlap | 1.794/3 | [1.411, 2.148] |
| Mean top-3 Jaccard | 0.522 | [0.388, 0.661] |
| Mean absolute rank shift@10 | 2.228 | [1.697, 2.836] |
| Mean Kendall tau common@10 | 0.521 | [0.417, 0.619] |

Review contribution이 ranking을 materially 바꾼다는 것은 측정했지만, human relevance가 없으므로
품질 개선이라고 해석하지 않는다.

### Secondary Gold-State oracle

기존 gold로 evaluator-side state를 구성하고 deterministic Policy/query/catalog filter만 실행했다.
추가 LLM 호출은 없다.

| Metric | Actual Full | Gold-State oracle | Recovery |
| --- | ---: | ---: | ---: |
| Policy accuracy | 0.802 | 1.000 | +0.198 |
| Recommendation reach | 0.808 | 1.000 | +0.192 |
| Hard-filter completion | 0.630 | 1.000 | +0.370 |

Oracle candidate availability는 0.974, candidate hard violation은 0이다. 이는 upstream state error를
제거했을 때의 deterministic diagnostic upper bound이며 추천 relevance 결과가 아니다.

### Post-hoc Gold-State oracle ranking diagnostic

포스터 제출 뒤인 2026-08-16에 위 oracle을 에피소드 종료 추천 리스트까지 확장해 별도로 실행했다.
official raw와 primary 결과는 재실행하거나 수정하지 않았다.

- 최종 Gold-State를 동결된 Query → catalog filter → semantic review retrieval → Cross-Encoder →
  Rank에만 주입한다. Understanding과 Response Composer는 실행하지 않으므로 추가 LLM 호출은 0회다.
- 20개 중 19개에서 추천이 생성됐고 Oracle Top-3 54개의 Gold hard-filter 위반과 review fallback은
  모두 0건이다. th17은 `$380 이하 + RAM 8GB 이상`을 함께 만족하는 후보가 없어 빈 리스트다.
- Oracle 대비 비교 가능 에피소드는 Full 15개·No-memory 13개이고, Top-1 일치율은 0.333/0.077,
  exact Top-3 order는 0.333/0.000, mean Top-3 Jaccard는 0.540/0.133이다.

이는 state-to-ranking 전달 충실도 진단이며 Gold 추천 상품이나 human relevance 정답이 아니다.

## 계산하지 않은 지표

- Product NDCG@3 / Review NDCG@3: human relevance label이 없어 official automatic result에서 제외
- candidate provenance accuracy: frozen holdout에 provenance gold 없음
- trade-off direction compliance: relation gold는 있지만 expected product ranking direction 없음
- situational→persistent promotion error: situational rejection positive case 없음
- unsupported hypothesis / restraint / temporal scope error: hypothesis status와 temporal scope gold 없음

없는 gold를 사후 작성하거나 관측값을 gold로 승격하지 않았다.

## Human evaluation의 현재 지위

기존 artifact와 코드는 모두 **Optional / Future Human Relevance Evaluation**으로 보존한다.

- gold-state token/semantic packet: 3명 × (상품 170 + 리뷰 302)
- end-to-end Full/No-memory/fixed No-review packet: 3명 × (상품 88 + 리뷰 239)
- public/private provenance 분리, session hash, guarded merge, Krippendorff alpha, NDCG 코드 보존
- 현재 human grade 0건

이번 포스터의 primary readiness에는 human annotation이 필요하지 않다. 기존 packet은 삭제하거나
재생성하지 않는다.

## 결과 산출물

- `backend/data/results/tablet_domain_automatic_benchmark_v1.json`
- `backend/data/results/tablet_domain_automatic_benchmark_v1_scenarios.csv`
- `backend/docs/tablet_domain_automatic_benchmark_v1.md`
- `backend/docs/tablet_domain_automatic_metric_definitions.md`
- `backend/data/manifests/tablet_domain_automatic_benchmark_v1.json`
- `backend/data/results/tablet_domain_gold_state_oracle_rankings_posthoc_v1.json`
- `backend/docs/tablet_domain_gold_state_oracle_rankings_posthoc_v1.md`
- `backend/data/manifests/tablet_domain_gold_state_oracle_rankings_posthoc_v1.json`

## 아직 하지 않은 것

- Optional future human relevance annotation과 NDCG/agreement
- 통제 inventory snapshot과 배송·재고 상황 실험
- PostgreSQL/pgvector 전환, 세션 영속화, 인증·배포
- 대규모 동시성·지연시간 benchmark와 사용자 연구

## 다음 작업 순서

1. automatic benchmark 표와 error analysis를 포스터 narrative/RQ별 figure로 정리한다.
2. Full의 schema failure, budget 누락, previous-state over-extraction을 limitation과 개선 과제로 분리한다.
3. 이 holdout에는 production prompt/ranker를 맞추지 않는다. 개선이 필요하면 새 dev와 새 holdout으로
   별도 버전을 설계한다.
4. human annotation은 일정이 확보될 때 frozen optional packet으로만 재개한다.

## 재개할 때 확인할 것

```powershell
cd backend
.\.venv\Scripts\python.exe -m compileall -q app scripts
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_official.py
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_annotation_packet.py
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_automatic.py
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_gold_oracle_rankings.py
.\.venv\Scripts\python.exe scripts\verify_actual_demo.py
```

실데이터·semantic index·`.env`는 Git에 포함되지 않는다. `.env`의 키와 `backend/reports/`,
`backend/logs/`의 원본 trace는 문서·커밋에 포함하지 않는다.
