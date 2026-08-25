# KDMS SPN + RA-Rec local MVP

영어 자유 입력을 Luxia GPT-4o-mini로 이해하고, 로컬 Amazon Reviews 2023 태블릿 표본 전체에서
상품과 실제 리뷰를 검색·랭킹하는 연구용 데모다. 화면의 기본 경로에는 정해진 대화 시나리오나
상품별 하드코딩 가점이 없다.

```text
Understanding (Luxia LLM)
→ State Manager (deterministic)
→ Policy: clarify/recommend lane (deterministic)
→ Query / DuckDB Browse / pinned Review Retrieval / Rank
→ Clarify or Recommend Response Composer (Luxia LLM)
```

기존 6턴 iPhone fixture는 빠른 회귀 검증용으로만 남아 있으며 실제 데이터 UI와 분리되어 있다.

## 로컬 실행

### 0. Python 환경

고정 의존성은 Python 3.12 기준이다. 새 clone에서는 가상환경을 만들고 설치한다.

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 1. Luxia 설정

`backend/.env`가 없다면 예제를 복사하고 발급받은 키 하나만 채운다.

```powershell
cd backend
Copy-Item .env.example .env
```

```dotenv
LLM_PROVIDER=luxia
LLM_API_KEY=YOUR_LUXIA_PLATFORM_KEY
LLM_BASE_URL=https://bridge.luxiacloud.com/llm/openai/chat/completions/gpt-4o-mini
```

기존 이름인 `LUXIA_API_KEY`도 지원한다. 클라이언트는
`{LLM_BASE_URL}/create`에 `apikey` 헤더로 요청하며 body의 `model`은 Luxia 계약에 따라
`llm`을 사용한다. `.env`, 인증 헤더, 키는 출력하거나 Git에 커밋하지 않는다.

### 2. 실제 데이터 확인

기본 데모에는 아래 로컬 파일이 필요하다. 이 디렉터리는 원문과 Parquet을 포함하므로 Git에서
제외된다. 따라서 새 clone에는 이 파일과 semantic index가 없으며, 아래 검증 전에
[`data/README.md`](data/README.md)의 재현 명령으로 직접 생성해야 한다.

```text
data/amazon_reviews_2023/tablet_catalog_v2/products.parquet
data/amazon_reviews_2023/tablet_catalog_v2/reviews.parquet
```

현재 준비된 산출물은 태블릿 상품 117개와 본문 중복 없는 실제 리뷰 7,552개다. 다음 명령으로
manifest와 파일 내용을 대조할 수 있다.

```powershell
.\.venv\Scripts\python.exe scripts\verify_amazon_pilot.py `
  --pilot-dir data\amazon_reviews_2023\tablet_catalog_v2
.\.venv\Scripts\python.exe scripts\verify_experimental_catalog.py
```

semantic 리뷰 검색을 사용하려면 한 번 인덱스를 만든다. 이 명령만 고정 모델 revision을
다운로드하며, 서버 런타임은 `local_files_only=True`로 로컬 cache와 index만 읽는다.

```powershell
.\.venv\Scripts\python.exe scripts\build_review_semantic_index.py
.\.venv\Scripts\python.exe scripts\verify_review_semantic_retrieval.py
```

`review_embeddings.npy`, review ID metadata, local manifest는 Parquet과 같은 Git 제외 디렉터리에
생긴다. 모델 revision과 산출물 SHA-256은
[`data/manifests/amazon_tablet_semantic_retrieval_v1.json`](data/manifests/amazon_tablet_semantic_retrieval_v1.json)에
남긴다. index/model cache가 없거나 hash가 다르면 요청을 실패시키지 않고 token baseline으로
전환하며 `review_retrieval_fallback_reason`과 node trace에 사유를 기록한다. 강제로 baseline만
쓰려면 `.env`에 `REVIEW_RETRIEVAL_MODE=token`을 설정한다.

산출물을 새로 만드는 방법과 정확한 표본·정규화 규칙은
[`data/README.md`](data/README.md)의 “실제 Amazon Reviews 2023 tablet catalog v2”에 있다.

### 3. 서버 실행

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api:app --reload
```

브라우저에서 `http://127.0.0.1:8000`을 연다. 첫 화면은 영어 자유 입력만 제공하며, 예를 들어
다음처럼 예산·용도·속성 우선순위·이전 카드 참조를 자유롭게 이어갈 수 있다.

```text
I need a tablet under $300 for note taking with at least 64 GB of storage.
Battery reviews and a good display matter more than having the lightest model.
The second result looks too heavy. Remove it.
Show me details for the first one.
```

### 키·실데이터 없는 공개본 smoke

Luxia 키와 Amazon Parquet 없이 서버 계약과 통제된 6턴 회귀 API만 확인하려면 mock provider를
사용한다. 이 모드에서는 실제 태블릿 UI/API가 비활성화되고, `demo_catalog.json`의 합성 iPhone
fixture와 결정론적 Understanding/Response만 실행된다.

```powershell
$env:LLM_PROVIDER = "mock"
.\.venv\Scripts\python.exe -m uvicorn app.api:app --reload
```

다른 터미널에서 다음 요청이 성공하면 공개본의 기본 서버 경로가 준비된 것이다.

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/api/conversations `
  -ContentType "application/json" `
  -Body "{}"
```

`GET /health`에서는 이때 `actual_demo_enabled=false`가 표시된다. 실제 영어 자유 입력 데모는 위의
Luxia 설정과 로컬 catalog 생성 절차를 모두 완료한 뒤 사용한다.

## 실제 데이터 API

- `POST /api/actual/conversations`
- `POST /api/actual/conversations/{conversation_id}/turns`
- `GET /api/actual/conversations/{conversation_id}`
- `DELETE /api/actual/conversations/{conversation_id}`
- `GET /api/experimental/catalog/status`
- `GET /api/experimental/catalog/products`
- `GET /api/experimental/catalog/products/{parent_asin}`
- `GET /api/experimental/catalog/products/{parent_asin}/reviews`

각 실제 대화 턴에는 strict `ActualUnderstandingOutput`, `DialogueState`, `StateDiff`, policy lane,
제한된 browse 요약, 상위 랭킹과 정확히 대응하는 리뷰 근거, 사용자용 응답, 노드 trace가 포함된다.
전체 상품 행이나 전체 리뷰 본문은 턴 응답에 넣지 않는다.

`/api/conversations`와 `/api/catalog`은 통제된 iPhone 6턴 회귀 경로다. 기본 UI에서는 사용하지
않지만 기존 계약의 회귀 비교를 위해 보존한다.

## 실제 데이터 가공과 랭킹

고정 revision `2b6d039ed471f2ba5fd2acb718bf33b0a7e5598e`의
`McAuley-Lab/Amazon-Reviews-2023` Electronics 배포본에서 다음 순서로 만들었다.

1. metadata 500,000행을 10개 shard에서 각 50,000행씩 순회한다.
2. leaf category와 title의 태블릿 본체 신호를 함께 요구하고 액세서리·노트북을 제외한다.
3. title을 우선해 내부 저장공간을 추출하고, 명시적 RAM 문맥만 메모리로 읽는다. 독립적인
   RAM 설명 없이 details의 RAM 값이 저장공간과 같으면 원천 metadata 오류로 보고 결측 처리한다.
4. review stream 8,000,000행 중 후보 `parent_asin`만 모은다.
5. 상품 내 본문 중복을 제거하고 verified purchase·helpful vote·본문 길이·최신순을 우선한다.
6. 평점 극성을 섞어 상품별 최대 100개를 선택하고 전역 본문 중복을 한 번 더 제거한다.
7. 리뷰가 20개 미만인 상품을 제외해 최종 117개 상품·7,552개 리뷰를 만든다.
8. 가격·무게·저장공간·RAM·화면 크기는 이 고정 표본의 empirical percentile 0~100으로
   정규화한다. 결측은 평균이나 중앙값으로 보간하지 않는다.

가공 원문과 Parquet은 Git에서 제외하지만 실행 조건, 결측, revision, 산출물 SHA-256은
[`data/manifests/amazon_tablet_catalog_v2.json`](data/manifests/amazon_tablet_catalog_v2.json)에
추적한다. processed 리뷰에서는 `user_id`와 리뷰 이미지를 제거했다.

추천 턴에서는 확인된 hard filter를 DuckDB SQL에 먼저 적용하고, 조건을 만족하는 표본 전체(현재
최대 117개)를 같은 일반 규칙으로 평가한다. 영어 review query는 고정
`all-MiniLM-L6-v2` bi-encoder로 상품별 5개 후보를 찾고,
`ms-marco-MiniLM-L6-v2` Cross-Encoder가 그 제한된 쌍만 재순위화해 상품별 3개를 반환한다.
표시하는 실제 리뷰와 `evidenceReviewIds`는 동일하다. semantic 점수는 정규화 cosine 35%와
Cross-Encoder logit의 sigmoid 65%를 합친 0~100 값이며, token fallback 점수와 provenance를
별도로 보존한다.

```text
total
= 0.30 × hard_constraint_match
+ 0.30 × metadata_match
+ 0.20 × subjective_need_match
+ 0.15 × review_evidence_score
+ 0.05 × evidence_reliability
```

자유 입력 추천의 hard filter는 USD 가격, 저장공간, RAM, 무게, 화면 크기, 평점, OS를 지원한다.
stylus 언급은 metadata에서 파생한 필기 적합성 soft signal이며, 실험용 catalog 조회 API에서만
직접 필터링할 수 있다. 배터리·오디오·내구성·성능·아동 적합성은 리뷰 기반 soft evidence로만
사용한다. Amazon 원본에 없는 배송일, 재고, 픽업, 가용 색상은 생성하거나 검색·랭킹에 사용하지
않는다.

## 검증

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m compileall -q app scripts
.\.venv\Scripts\python.exe scripts\verify_adapter.py
.\.venv\Scripts\python.exe scripts\verify_understanding_schema.py
.\.venv\Scripts\python.exe scripts\verify_understanding_evaluation.py
.\.venv\Scripts\python.exe scripts\verify_actual_understanding_evaluation.py
.\.venv\Scripts\python.exe scripts\verify_actual_recommendation_evaluation.py
.\.venv\Scripts\python.exe scripts\verify_poster_annotation_packets.py
.\.venv\Scripts\python.exe scripts\verify_poster_evaluation.py
.\.venv\Scripts\python.exe scripts\verify_poster_annotation_sessions.py
.\.venv\Scripts\python.exe scripts\verify_poster_live_replay.py
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_automatic.py
.\.venv\Scripts\python.exe scripts\verify_mvp.py
.\.venv\Scripts\python.exe scripts\verify_amazon_processing.py
.\.venv\Scripts\python.exe scripts\verify_amazon_pilot.py `
  --pilot-dir data\amazon_reviews_2023\tablet_catalog_v2
.\.venv\Scripts\python.exe scripts\verify_experimental_catalog.py
.\.venv\Scripts\python.exe scripts\verify_actual_demo.py
.\.venv\Scripts\python.exe scripts\verify_review_semantic_retrieval.py
node --check app\static\app.js
```

실데이터 `ActualUnderstandingOutput`용으로 기존 28개 dev fixture와 겹치지 않는 영어 30개
holdout도 동결했다. 첫 Luxia 실행 전에 fixture와 prompt v1을 함께 고정했으며, 결과를 본 뒤
v1 prompt를 다시 맞추지 않는다.

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_actual_understanding.py --concurrency 4
```

첫 실행은 schema validation 0.900, canonical ID exact 0.400/micro-F1 0.493, intent exact 0.800,
item-action exact 0.833이었다. 원본 case report는 Git 제외 `reports/`에, 실행 ID·입력/출력 hash와
요약 지표는
[`data/manifests/actual_understanding_holdout_luxia_v1.json`](data/manifests/actual_understanding_holdout_luxia_v1.json)에
남긴다. 이것은 현재 일반화 오류를 드러낸 고정 기준선이지 개선된 성능 주장이 아니다.

실데이터 추천 lane에는 별도의 pooled 관련성 평가가 있다. 상품 질의 4개에서 token/semantic
각 top 10의 합집합 44개를, 단일 상품 리뷰 질의 4개에서 두 방식의 합집합 28개를 원문과
metadata로 0–3 등급화했다. fixture와 판정은 첫 기준선을 보기 전에 동결했으며, 미판정 결과는
0점으로 계산하고 judgment coverage를 함께 보고한다.

```powershell
.\.venv\Scripts\python.exe scripts\verify_actual_recommendation_evaluation.py
.\.venv\Scripts\python.exe scripts\evaluate_actual_recommendation.py
```

| 최초 기준선 | 상품 NDCG@3 | 상품 NDCG@10 | 리뷰 NDCG@3 |
| --- | ---: | ---: | ---: |
| token | 0.821 | 0.921 | 0.733 |
| semantic + Cross-Encoder | 0.699 | 0.863 | 0.661 |

두 방식 모두 top-k judgment coverage 1.000, hard-filter 위반율 0.000, 상품 카드 근거 일치율
1.000이었다. 이 작은 동결셋에서는 semantic 방식이 token보다 낮았으므로 관련성 향상을 주장하지
않으며, 원인을 분석할 때 v1 fixture에 production ranker를 재튜닝하지 않는다. 판정 fixture는
[`data/actual_recommendation_eval_v1.json`](data/actual_recommendation_eval_v1.json), 최초 요약은
[`data/manifests/actual_recommendation_eval_v1.json`](data/manifests/actual_recommendation_eval_v1.json)에
남긴다.

이 pilot과 포스터용 최종 평가의 경계, human annotation·agreement·ablation 계획은
[`docs/poster_evaluation_protocol.md`](docs/poster_evaluation_protocol.md)에 별도로 고정했다.

### 포스터 시나리오 live replay

포스터 packet 생성과 live replay는 서로 다른 실험이다. 기존 packet은 동결된 정답 상태 후보를
State Manager에 직접 넣어 token/semantic 검색·랭킹을 비교한다. 아래 명령은 같은 20개×4턴을
실제 Luxia GPT-4o-mini Understanding과 Response Composer가 포함된 전체 파이프라인으로 재생한다.

```powershell
.\.venv\Scripts\python.exe scripts\verify_poster_live_replay.py
.\.venv\Scripts\python.exe scripts\run_poster_scenarios_live.py
```

원본 턴·trace는 Git 제외 `reports/poster_scenarios_live_v1.json`에 checkpoint되고, 첫 실행의
설정·hash·요약은
[`data/manifests/poster_live_replay_luxia_v1.json`](data/manifests/poster_live_replay_luxia_v1.json)에
동결했다. 결과 해설은
[`docs/poster_live_replay_v1.md`](docs/poster_live_replay_v1.md)에 있다. 첫 실행은
19/20 시나리오·79/80턴을 완주했고 한 시나리오에서 strict schema failure가
발생했다. 전체 20개 기준 recommend-lane reach와 end-to-end hard-filter completion은 각각 0.400,
canonical ID micro-F1은 0.658이었다. 추천까지 간 경우의 conditional hard-filter correctness는
8/8 = 1.000이었고, 추천된 24개에서는 기대 hard-filter 위반 0,
표시 리뷰–점수 근거 일치율 1.000, LLM response 및 semantic retrieval fallback 0이었다.

따라서 현재 gold-state packet을 live end-to-end 성능으로 해석하지 않는다. live 병목은 주로
`category_tablet` 누락에 따른 `supported category` clarify 분기였다. 이 v1 결과를 보고 production
prompt를 같은 holdout에 맞추지 않는다. 개선은 별도 dev 시나리오에서 수행하고 새 untouched
holdout에 한 번 적용한다.

### Tablet-domain v2.3 freeze

v1의 주 병목을 상품 랭킹이 아닌 category extraction과 Policy 경계로 확인한 뒤, 현재 연구 범위를
태블릿 도메인으로 고정했다. v2 초기 상태에는 발화 근거가 아닌 environment provenance의
`category_tablet`이 들어가며, 명시적 laptop/phone 등은 `unsupported_category`로 검색 전에
차단한다. No-memory와 No-review에서도 이 환경 category는 유지된다.

v1 live replay와 분리된 개발용 26건에서 선택한 v2.3은 validation/domain route 1.000,
canonical micro-F1 0.807이었다. 이는 dev 결과이므로 최종 성능으로 사용하지 않는다. prompt/schema,
Luxia 모델 echo, semantic retriever, Cross-Encoder, ranking weight와 source hash는
[`data/manifests/tablet_domain_v2_freeze.json`](data/manifests/tablet_domain_v2_freeze.json)에 동결했다.
설계 해설은 [`docs/tablet_domain_v2_freeze.md`](docs/tablet_domain_v2_freeze.md)를 따른다.

```powershell
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_v2.py
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_dev.py
.\.venv\Scripts\python.exe scripts\verify_experiment_conditions.py
```

세 비교 조건은 `Full = persistent state + review`, `No-memory = current turn only + review`,
`No-review = persistent state - review contribution`이다. 새 untouched holdout에는 turn별 State Diff,
Policy/action, hard constraints만 미리 고정하고 추천 상품·순위는 고정하지 않는다.

v2.3 freeze 이후 새로 작성한 `data/tablet_domain_multiturn_holdout_v1.json`은 20개 시나리오·81턴이며
위 gold만 포함한다. 첫 system run 전 hash는
`data/manifests/tablet_domain_multiturn_holdout_v1.json`에 동결했다.

```powershell
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_holdout.py
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_holdout_freeze.py
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_official.py
```

첫 공식 평가는 세 조건을 중간 집계 없이 Full → No-memory → No-review 순서로 한 batch에서
각 한 번 실행한다. 실행기는 동결 hash, Luxia/GPT-4o-mini, temperature 0, semantic model revision,
clean tracked worktree를 preflight에서 확인하고 기존 공식 output을 덮어쓰지 않는다.

```powershell
.\.venv\Scripts\python.exe scripts\run_tablet_domain_holdout_official.py
```

원본 결과와 LLM trace는 Git 제외 `reports/`, `logs/`에 checkpoint한다. 세 조건이 모두 끝난
뒤에만 State Diff micro-F1, Policy/recommendation reach, hard-constraint completion/violation,
Full–No-memory delta와 Full–No-review ranking 변화를 집계한다. 상세 실행·재시도 계약은
[`docs/tablet_domain_holdout_official_run.md`](docs/tablet_domain_holdout_official_run.md)를 따른다.
상품·리뷰 NDCG는 human relevance label 없이는 산출하지 않는다. 이번 포스터 primary는 아래
process-level automatic benchmark이며 human NDCG는 optional/future로 보존한다.

2026-08-08 첫 공식 batch는 Full 15/20, No-memory 17/20, No-review live 16/20 시나리오를
완주했다. Full–No-memory 공통 완료 14개 시나리오에서 final-state micro-F1은
`0.800 vs 0.524`, hard-constraint completion은 `0.770 vs 0.541`이었다. 반대로 현재-turn
State Diff micro-F1은 `0.630 vs 0.765`로 Full이 낮았다. 이전 상태가 누적 보존에는
도움이 됐지만 LLM의 기존 facet 재추출도 늘렸기 때문에 두 지표를 함께 보고한다.

독립 No-review replay의 Full 대비 upstream identity가 0.721뿐이어서 이를 primary RQ3로
사용하지 않는다. Full의 validated upstream을 고정하고 review contribution만 제거한 결정론적
counterfactual에서는 63개 추천 turn 중 top-1이 55.6% 바뀌었다. 품질 방향은 아직 human grade가
없어 미정이다. 공식 hash·전체 결과는
[`data/manifests/tablet_domain_holdout_official_v1.json`](data/manifests/tablet_domain_holdout_official_v1.json),
해설은 [`docs/tablet_domain_holdout_official_run.md`](docs/tablet_domain_holdout_official_run.md)에 있다.

### Primary automatic benchmark

2026-08-09부터 포스터 primary 평가는 human relevance가 아니라 frozen multi-turn gold와 저장된
pipeline trace를 사용하는 process-level automatic benchmark다. official run은 재실행하지 않는다.

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_tablet_domain_automatic.py
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_automatic.py
```

81개 gold turn 전체를 분모에 포함하므로 schema failure 이후의 미출력 turn도 실패다.

| Condition | Canonical F1 | Final State F1 | State Diff F1 | Policy | Reach | Hard completion |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full | 0.544 | 0.758 | 0.562 | 0.802 | 0.808 | 0.630 |
| No-memory | 0.744 | 0.500 | 0.700 | 0.741 | 0.744 | 0.444 |
| No-review live (secondary) | 0.541 | 0.738 | 0.560 | 0.802 | 0.808 | 0.630 |

Full–No-memory 20-scenario paired bootstrap에서 Final State difference는 +0.258
`[0.172, 0.334]`, State Diff는 -0.138 `[-0.222, -0.056]`, Policy는 +0.062
`[-0.111, 0.222]`, reach는 +0.064 `[-0.103, 0.231]`, hard-filter completion은 +0.185
`[0.000, 0.363]`다. rejection retention은 paired support가 없어 CI를 만들지 않았다.

Fixed-upstream No-review는 candidate identity 1.000에서 top-1 change 0.556, top-3 order change
0.873, mean Jaccard 0.522, mean absolute rank shift@10 2.228이었다. 이는 review score가 ranking을
바꾼다는 결과이며 품질 개선 주장이 아니다. Gold-State oracle은 Policy/reach/hard-filter를 1.0으로
회복했지만 evaluator-side diagnostic upper bound다.

산출물:

- [`data/results/tablet_domain_automatic_benchmark_v1.json`](data/results/tablet_domain_automatic_benchmark_v1.json)
- [`data/results/tablet_domain_automatic_benchmark_v1_scenarios.csv`](data/results/tablet_domain_automatic_benchmark_v1_scenarios.csv)
- [`docs/tablet_domain_automatic_benchmark_v1.md`](docs/tablet_domain_automatic_benchmark_v1.md)
- [`docs/tablet_domain_automatic_metric_definitions.md`](docs/tablet_domain_automatic_metric_definitions.md)
- [`data/manifests/tablet_domain_automatic_benchmark_v1.json`](data/manifests/tablet_domain_automatic_benchmark_v1.json)

Product/Review NDCG@3, candidate provenance, trade-off product direction과 hidden-intent
promotion/restraint/temporal scope는 현재 gold가 지원하지 않으므로 official metric으로 만들지 않는다.

공식 final output의 별도 blind pool도 준비됐다. Full / No-memory / fixed-upstream No-review top-3
합집합이며, 3명 각각 상품 88건·리뷰 239건을 상품 1세션과 리뷰 2세션으로 나눴다.
작업 파일은 Git 제외 `reports/tablet_domain_holdout_annotation_packet_v1/annotator-*/sessions/`,
추적 manifest는
[`data/manifests/tablet_domain_annotation_packet_v1.json`](data/manifests/tablet_domain_annotation_packet_v1.json)이다.

이 packet과 아래 gold-state packet/집계기는 모두 **Optional / Future Human Relevance
Evaluation**이다. 현재 grade 0건 상태와 private provenance를 그대로 보존하며 이번 포스터 primary
readiness에는 포함하지 않는다.

```powershell
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_annotation_packet.py
```

포스터 평가용 v1은 기존 pilot과 분리한 영어 4턴 시나리오 20개를 첫 packet build 전에 동결했다.
아래 명령은 token/semantic top 10 합집합과 scenario당 hard negative 2개를 만들고, 기본 3명의
annotator마다 후보 순서를 독립적으로 섞는다.

```powershell
.\.venv\Scripts\python.exe scripts\verify_poster_annotation_packets.py
.\.venv\Scripts\python.exe scripts\build_poster_annotation_packets.py
```

public packet은 `reports/poster_annotation_v1/annotator-*`에, 원본 ASIN·review ID·시스템 순위는
같은 Git 제외 폴더의 `private_provenance.json`에 분리된다. 기본 packet은 1인당 상품 170건·리뷰
302건이며 모든 grade와 rationale이 비어 있다. 따라서 이 산출물 자체는 human evaluation 결과가
아니다. 동결 dataset과 재현 설정은
[`data/poster_recommendation_scenarios_v1.json`](data/poster_recommendation_scenarios_v1.json),
파일별 SHA-256 요약은
[`data/manifests/poster_annotation_packet_v1.json`](data/manifests/poster_annotation_packet_v1.json)에
추적한다. 판정자에게는 public packet만 전달하고 private provenance는 집계 시점까지 공개하지 않는다.

동결 원본을 직접 수정하지 않는다. 아래 명령은 원본 hash를 확인한 뒤 Git 제외
`reports/poster_annotation_v1_completed/`에 public packet 6개만 복사한다. 내용이 있는 작업 폴더는
덮어쓰지 않으며 private provenance도 복사하지 않는다.

```powershell
.\.venv\Scripts\python.exe scripts\prepare_poster_annotation_collection.py
.\.venv\Scripts\python.exe scripts\prepare_poster_annotation_sessions.py
```

두 번째 명령은 1인당 상품 170건을 2세션, 리뷰 302건을 3세션으로 나누며 각 세션은 120건
이하다. 각 판정자에게 해당 `annotator-NN/sessions/` 폴더만 전달한다. 상세 운영 절차와 연습·휴식
원칙은 [`docs/poster_annotation_guide.md`](docs/poster_annotation_guide.md)를 따른다. 독립 판정이
끝나면 세션을 안전하게 원래 packet으로 병합하고, 작업 폴더의 `collection_manifest.json`을
사실대로 완성한 뒤 집계한다.

```powershell
.\.venv\Scripts\python.exe scripts\merge_poster_annotation_sessions.py
.\.venv\Scripts\python.exe scripts\evaluate_poster_annotations.py
```

향후 human study를 재개하면 집계기는 모든 relevance·rationale, annotator별 동일 후보 집합,
dataset/packet/provenance 연결을
검증한다. 중앙값 grade, ordinal Krippendorff's alpha, 시스템별 상품·리뷰 NDCG@3,
hard-filter·evidence 일치율과 시나리오 단위 paired bootstrap 95% CI를 Git 제외 report에 남긴다.
수집 manifest가 독립 human annotation을 증명하지 않으면 계산 검증은 가능해도 human 결과로
승격하지 않는다. 현재 작업 사본은 전부 빈 grade이므로 의도대로 `not_ready`를 반환한다.

packet v1의 시스템은 token/semantic뿐이다. 향후 결과가 생겨도 module-level human relevance로
분리하고 RQ1의 Full/No-memory automatic State Diff·Final State를 대신하지 않는다. 기존
`poster_readiness`는 optional human study의 완결성 검증용으로 보존한다.

`.env`의 실제 Luxia 키와 로컬 Parquet을 사용한 3턴 smoke probe는 다음과 같다. 인자를 주면
고정 예문 대신 원하는 영어 발화를 순서대로 실행한다. report와 LLM trace는 Git에서 제외된다.

```powershell
.\.venv\Scripts\python.exe scripts\probe_actual_demo_live.py
.\.venv\Scripts\python.exe scripts\probe_actual_demo_live.py `
  "I need an Android tablet under `$250" `
  "At least 64 GB, and battery reviews matter"
```

## 현재 경계

- 대화 상태는 프로세스 메모리에만 저장되며 재시작·다중 worker를 지원하지 않는다.
- 실제 표본은 Electronics 전체의 확률 표본이 아니라 고정 prefix 표본이다.
- semantic retrieval은 로컬 CPU에서 전체 117개 상품 cold-start smoke가 약 14초였다. 최초
  소규모 관련성 기준선도 token보다 낮았으며, 배포 규모와 새 holdout에서 추가 평가가 필요하다.
- PostgreSQL/pgvector, 별도 inventory snapshot, 세션 영속화, 인증, 배포, 결제 연동은 없다.
- LLM 후보는 strict schema로 검증되지만 의미 추출 오류 가능성은 남는다. 상태 변화와 결정론적
  점수는 UI inspector와 trace에서 확인할 수 있다.
