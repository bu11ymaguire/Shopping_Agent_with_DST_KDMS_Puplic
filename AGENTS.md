# ShoppingAgent_with_DST_KDMS repository guidance

이 파일은 저장소 전체에 적용된다. 구현 세부사항을 중복 보관하는 문서가 아니라,
후속 작업자가 정본을 올바른 순서로 읽고 목업과 현행 코드를 혼동하지 않게 하는
작업 라우터다.

## 작업 시작 시 반드시 읽을 것

1. `flow.md`
   - 연구 목적, 아키텍처와 데이터 계약, 수식, 회귀 fixture, 알려진 함정의 최우선 정본이다.
2. `manual.md`
   - LangGraph, 데이터셋, 검색·저장 기술을 선택한 근거다.
3. 필요한 경우에만 `reference/README.md`와 `reference/mock-demo-v1/`
   - 이전 Next.js 목업의 읽기 전용 스냅숏이며 현행 런타임 소스가 아니다.

문서나 코드가 충돌하면 다음 우선순위를 적용한다.

```text
현재 사용자 지시
→ 이 AGENTS.md의 작업 규칙
→ flow.md의 계약과 교정 사항
→ manual.md의 기술·데이터 권고
→ reference/mock-demo-v1의 과거 구현
```

`flow.md`와 목업 코드가 다르면 목업을 그대로 복사하지 말고 현행 코드를
`flow.md`에 맞춘다.

## 현재 코드와 reference의 경계

- 현행 Python 구현은 `backend/`다.
- `reference/mock-demo-v1/`는 UI 재구성, fixture 확인, 회귀 비교를 위한 원본 스냅숏이다.
- 명시적인 요청 없이 reference 파일을 수정하거나 현행 빌드에 연결하지 않는다.
- reference의 `data/scenario.json`, `app.js`, `index.html`, `styles.css`는 더 오래된
  태블릿 프로토타입이다. 현재 6턴 iPhone 회귀 시나리오로 사용하지 않는다.
- reference의 `package.json`과 `package-lock.json`은 목업 재현용이다. 루트 또는
  `backend/` 의존성으로 설치하지 않는다.
- reference에서 코드를 가져올 때는 알려진 버그와 시나리오 하드코딩을 먼저 제거한다.

## 연구·상태 모델의 불변식

- 구매 결과만 보고 지속 선호를 추론하지 않는다. 상황적 제약, 상품 속성,
  우선순위, 양보한 조건을 서로 구분해 상태에 남긴다.
- `SPN Understanding (PLAN)`은 발화와 이전 상태를 읽고 상태 갱신 후보만 만든다.
  상태를 직접 병합하지 않는다.
- `RA-Rec State Manager (MEMORY)`는 발화를 다시 파싱하지 않는다. 후보 병합,
  provenance, status 전이, 행동 이력, trade-off, State Diff만 담당한다.
- State Manager는 모든 사용자 발화 뒤 실행되어 부분 정보도 누적한다.
- `SPN Policy (DECIDE)`의 입력은 `DialogueState` 하나다. 분기는
  `clarify-lane` 또는 `recommend-lane`으로 명시한다.
- clarify와 recommend 응답은 서로 다른 RESPOND 노드다.
- RA-Rec은 사용자용 질문 문장을 직접 만들지 않는다. 필요한 필드와 이유를
  구조화하고 SPN이 문장을 만든다.
- `RecommendationResponse.explanation`과 `FinalResponse.message`는 다른 객체와
  다른 문장으로 유지한다.

## LLM과 결정론적 코드의 경계

LLM을 사용하는 단계:

- SPN Understanding
- Latent Hypothesis Candidate Generation
- Response Composer

결정론적으로 유지하는 단계:

- State Manager와 State Diff
- confidence·uncertainty 계산
- Vagueness Score와 Policy 분기
- Query Generator
- browsing, filtering, review retrieval, ranking
- 정답 대비 evaluation

애플리케이션은 초기 단계에서 `single agent + explicit LangGraph workflow`로
구현한다. 자유로운 multi-agent 의사결정으로 노드 경계를 흐리지 않는다.

## 식별자·근거·랭킹 규칙

- 랭킹에 쓰는 `canonical_id`는 versioned closed vocabulary다. LLM이 자유 생성한
  ID를 조용히 무시하지 않는다.
- `target.key`와 `canonical_id` 불일치 및 unmapped ID는 검증하거나 계측한다.
- `explicit`, `implicit`, `inferred`를 구분하고 UI에서도 별도 badge로 표시한다.
- `status !== "confirmed"`인 값은 근거로 표시할 수 있지만 랭킹 점수에는 넣지 않는다.
- 상세 보기는 `inspectedItems`, `shortlistedItems`, `currentItem`만 변경한다.
  구매 확정 전에는 `purchasedItems`에 넣지 않는다.
- 거절 이유의 `situational_constraint`와 `product_attribute`를 구분한다.
- 상품 ID별 보너스나 시나리오 전용 가점을 두지 않는다. 일반 속성 차이로 순위를 만든다.
- 추천 카드에 표시하는 리뷰와 점수 계산에 사용한 `evidenceReviewIds`는 일치해야 한다.
- Amazon Reviews 2023에 없는 `deliveryDays`와 `availableColors`는 통제된 inventory
  snapshot의 합성 운영 데이터라는 사실을 숨기지 않는다.

## reference에서 그대로 가져오면 안 되는 항목

- `rank-reviews.ts`의 `slice(0, Math.max(topK, reviews.length))`는 topK를 자르지 못하는 버그다.
- 전체 리뷰·상품을 응답 객체에 담거나 매 요청마다 전부 스캔하는 목업 경로는
  실데이터에서 DB 쿼리와 제한된 top-k로 교체한다.
- 레거시 태블릿 시나리오의 `accepted_items`는 상세 보기를 구매 수락처럼 기록하므로
  현행 상태 계약에 사용하지 않는다.
- iPhone 6개 fixture 전용 정규화 상수를 실데이터 분포에 그대로 적용하지 않는다.
- 정규식 Understanding과 하드코딩된 숨은 의도 7개는 비교 기준일 뿐 최종 LLM 구현이 아니다.

## Luxia API와 비밀정보

- Luxia 호출은 `{LLM_BASE_URL}/create`에 POST한다.
- 인증은 `Authorization: Bearer`가 아니라 `apikey` 헤더다.
- body의 `model`은 `llm`이고 실제 모델은 URL 경로가 결정한다.
- `backend/.env`의 `LLM_API_KEY` 또는 `LUXIA_API_KEY`를 사용한다.
- `.env`, API 키, 인증 헤더를 출력·로그·보고서·커밋에 포함하지 않는다.
- live probe 결과 원본은 `backend/reports/`, 호출 trace는 `backend/logs/`에 저장하며
  두 폴더는 Git에서 제외한다.
- 2026-08-06 probe에서 Luxia GPT-4o-mini는 case A(strict json_schema),
  json_object, tools, stream, usage, model echo를 지원했다. 상세 결과는 `flow.md` §3을 따른다.

## 현재 구현 상태와 다음 순서

- 교체 가능한 LLM transport/client, trace, mock 검증이 구현되어 있다.
- Luxia capability probe와 실제 복합 Understanding schema probe가 구현되어 있다.
- `UnderstandingOutput`은 versioned closed vocabulary와 교차 필드 검증을 가진다.
- `spn-understanding` 노드는 실제 Luxia strict schema 호출로 1턴 gold seed를 통과했다.
- 부록 C 시드와 확장 유형을 포함한 합성 gold 발화 28개, 정규식 베이스라인,
  regex/Luxia 공통 평가기가 구현되어 있다.
- 28개 dev fixture에서 정규식 v1은 canonical ID exact 0.964/micro-F1 0.990,
  Luxia `spn-understanding-v1.3`은 0.393/0.740이다. Luxia의 validation, intent exact,
  item action exact은 각각 1.000/0.964/1.000이다.
- `v1.3`은 같은 fixture를 보고 개선한 dev-set 결과다. 이 28개에 더 맞추지 말고
  새 holdout 전에는 일반화 우위를 주장하지 않는다.
- explicit LangGraph, State Manager, Policy, Query/Browse/Rank, 두 LLM Response Composer,
  FastAPI 메모리 세션, 자유 입력 3패널 UI의 end-to-end MVP가 구현되어 있다.
- 오프라인 6턴은 기대 lane·순위·행동·trade-off를 재현한다. live Luxia 6턴도 완주하지만
  3턴 배송 문맥을 허용 기한으로 과잉 추출하는 편차가 남아 있다.
- Amazon Reviews 2023 Electronics 가공기를 metadata 500,000행·review 8,000,000행까지
  확장했다. 최종 태블릿 117개·본문 중복 없는 실제 리뷰 7,552개이며 상품당 최소 20개다.
  원문·Parquet은 Git 제외, 실행 조건·revision·해시는 tracked v2 manifest에 남긴다.
- 실제 속성은 표본 empirical percentile로 정규화하며 결측은 보간하지 않는다. RAM은 명시적
  RAM 문맥만 허용하고 storage는 title을 parent variant 설명보다 우선한다.
- read-only catalog adapter가 local DuckDB/Parquet으로 구조화 필터·분위수 정렬·실제 리뷰
  top-k를 제공한다.
- 영어 자유 입력용 `ActualUnderstandingOutput`, State Manager/Policy, 실제 catalog Query/Browse/Rank,
  두 LLM Response Composer, 메모리 세션과 `/api/actual/conversations` API가 구현되어 있다.
- 기본 로컬 UI는 고정 시나리오 버튼 없이 Luxia와 실제 117개 상품·7,552개 리뷰 경로를 사용한다.
  통제 iPhone 경로는 회귀용 `/api/conversations`로만 보존한다.
- 2026-08-07 실제 Luxia 3턴 probe와 브라우저 1턴에서 strict Understanding, 실제 review
  retrieval, LLM Response Composer가 fallback 없이 완주했다.
- 실데이터 hard filter는 USD 가격·저장공간·RAM·무게·평점·화면·OS를 지원한다. 리뷰 기반
  soft signal은 고정 bi-encoder 상품별 top 5 → Cross-Encoder top 3이며, index/model/hash 오류 시
  token baseline과 fallback reason을 남긴다.
- 첫 실행 전에 동결한 실제 schema 영어 holdout 30개에서 Luxia prompt v1은 validation 0.900,
  canonical ID exact 0.400/micro-F1 0.493, intent exact 0.800, item action exact 0.833이다. 이 holdout에
  v1을 다시 맞추지 않는다.
- 7,552개 실제 리뷰의 384차원 normalized local index, 고정 모델 revision, index/hash 검증,
  local-only runtime이 구현되어 있다. 단일 전체-catalog CPU cold-start smoke는 약 14초이며
  실제 Luxia 1턴은 20개 후보·semantic 리뷰 60개·카드 3개를
  fallback 없이 recommend lane과 LLM composer까지 완주했다.
- 상품 4개 질의의 pooled 상품 44개와 리뷰 4개 질의의 pooled 리뷰 28개를 0~3으로 판정한 추천
  관련성 v1을 첫 실행 전에 동결했다. 최초 상품 NDCG@3은 token 0.821/semantic 0.699, 리뷰
  NDCG@3은 token 0.733/semantic 0.661이다. 둘 다 hard-filter 위반 0, 근거 일치율 1.0이다.
- 이 작은 단일-annotator v1에 production ranker를 맞추거나 semantic 우위를 주장하지 않는다.
  포스터 평가 계획은 `backend/docs/poster_evaluation_protocol.md`를 따른다.
- 기존 dev/pilot과 분리한 영어 4턴 시나리오 20개와 3인용 blind annotation packet을 첫 packet
  build 전에 동결했다. 1인당 상품 170건·리뷰 302건이며, public packet과 private provenance를
  분리하고 파일 hash를 tracked manifest에 보존한다. 아직 human grade는 없다.
- gold-state packet의 복수 annotator agreement·NDCG·bootstrap은 optional/future human relevance
  평가로 보존하며 live full-system 결과와 분리한다. 현재 live v1 holdout에는 prompt를 다시 맞추지 않는다.
- 동결 public packet을 보존하는 작업 사본 준비와 guarded 집계기가 구현되어 있다. 빈 label,
  synthetic/미확인 수집, public/private ID 불일치를 거부하거나 readiness=false로 남기며 ordinal
  agreement·NDCG·paired bootstrap을 계산한다. 현재 human grade는 0건이라 `not_ready`다.
- 1인당 상품 2세션·리뷰 3세션, 세션당 최대 120건으로 나누는 판정 세션과 무결성 검증·안전 병합이
  구현되어 있다. 실제 Git 제외 작업 사본에는 3인용 15개 빈 세션만 있으며 아직 판정 결과가 아니다.
- 같은 20개×4턴을 실제 Luxia 전체 workflow로 첫 재생했다. 19/20 시나리오·79/80턴 완주,
  scenario-level schema failure 0.05, recommend-lane reach 8/20, conditional hard-filter correctness
  8/8, canonical ID micro-F1 0.658이었다.
  추천된 24개는 기대 hard-filter 위반 0, evidence 일치 1.0, response/retrieval fallback 0이었다.
- 기존 annotation packet은 frozen gold state를 직접 사용한 retrieval/ranking 평가다. live GPT
  end-to-end 결과로 부르지 않는다. 첫 live 결과를 보고 같은 holdout에 prompt를 맞추지 않는다.
- packet v1은 token/semantic만 포함하고 턴별 State Diff gold가 없다. 이번 포스터 primary는 별도
  multi-turn holdout의 automatic benchmark이며, 이 packet은 optional/future module-level human
  relevance artifact다. No-review가 노출한 새 후보용 packet도 삭제하거나 primary 결과로 승격하지 않는다.
- 연구 범위는 태블릿 도메인으로 고정했다. v2는 `environment.category=tablet`을 모든 조건에서
  유지하고 명시적 타 카테고리만 `unsupported_category`로 차단한다.
- 별도 개발용 26건에서 선택한 `spn-understanding-tablet-domain-en-v2.3-frozen`은 validation과
  domain route exact 1.000, canonical micro-F1 0.807이었다. 이는 dev 결과이며 최종 holdout 성능이
  아니다. prompt/schema/retriever/ranker/source hash는 `tablet_domain_v2_freeze.json`에 동결했다.
- v2 평가에서는 latent `subjective_*` 생성을 비활성화했다. canonical ID에서 target을 결정론적으로
  유도하며 LLM은 ID와 hard/soft scope만 출력한다.
- Full/No-memory/No-review 한 변수 계약과 offline verifier가 구현됐다. 다음은 새 multi-turn
  untouched holdout의 발화·Gold State Diff·Policy/action·hard constraints를 실행 전에 동결하는 것이다.
- v2.3 freeze 이후 작성한 untouched holdout 20개·81턴이 첫 system run 전에 동결됐다. product ID,
  추천 결과, ranking, human relevance는 포함하지 않는다.
- 같은 holdout을 clean commit에서 Full → No-memory → No-review 순서로 중간 집계 없이 각 한 번
  실행했다. Full 15/20, No-memory 17/20, No-review live 16/20 시나리오가 완주했다.
- 기존 공통 완료 14개 시나리오 분석은 보존한다. 포스터 primary automatic benchmark는 모든 81턴을
  분모로 사용해 미출력 turn도 실패로 집계한다. 이 기준에서 Full/No-memory의 Final State F1은
  0.758/0.500, State Diff F1은 0.562/0.700, Policy는 0.802/0.741, recommendation reach는
  0.808/0.744, hard-filter completion은 0.630/0.444다.
- Full-No-memory의 20-scenario paired bootstrap difference(95% CI)는 Final State +0.258
  [0.172, 0.334], State Diff -0.138 [-0.222, -0.056], Policy +0.062 [-0.111, 0.222],
  reach +0.064 [-0.103, 0.231], hard-filter completion +0.185 [0.000, 0.363]다.
  rejection retention은 paired target support가 없어 `insufficient_support`다.
- 독립 No-review는 GPT upstream identity가 0.721이라 primary 한 변수 RQ3로 사용하지 않는다.
  Full upstream을 고정하고 review contribution만 제거한 결정론 결과에서 top-1은 55.6%, top-3
  order는 87.3% 바뀌었고 mean top-3 Jaccard는 0.522다. 이는 causal ranking contribution이지
  human relevance나 품질 향상 근거가 아니다.
- 공식 raw/analysis hash는 `tablet_domain_holdout_official_v1.json` manifest에 동결했다. raw run을
  재실행하지 않고 조건별 trace까지 재집계하는 automatic evaluator, scenario bootstrap,
  fixed-upstream rank shift/Kendall tau, evaluator-side Gold-State oracle을 구현했다.
- end-to-end blind packet도 생성했다. 출력이 있는 17개 시나리오에서 1인당 상품 88건·리뷰
  239건이며, 3명 각각 상품 1세션·리뷰 2세션으로 분리했다. public packet은 system/rank/score와
  원본 ID를 숨기고 private provenance와 분리하며 현재 human grade는 0건이다.
- packet hash는 `tablet_domain_annotation_packet_v1.json`에 동결했다. 3인 판정은 수행하지 않으며
  기존 packet·private provenance·agreement/NDCG 코드는 **Optional / Future Human Relevance
  Evaluation**으로 그대로 보존한다.
- primary automatic 결과는 `tablet_domain_automatic_benchmark_v1.json`과 scenario CSV, poster
  Markdown, metric definitions, tracked manifest에 보존한다. Product/Review NDCG는 계산하지 않는다.
- candidate provenance, trade-off product direction, hidden-intent promotion/restraint/temporal scope는
  현재 gold가 지원하지 않아 `not_evaluable_with_current_holdout`이다. 새 gold를 사후 추가하지 않는다.
- 아직 없는 것은 PostgreSQL/pgvector, 통제 inventory snapshot, 세션 영속화, 인증·배포다.

## 검증과 변경 보고

Python 작업 후 최소 검증:

```powershell
cd backend
python -m compileall -q app scripts
python scripts\verify_adapter.py
python scripts\verify_understanding_schema.py
python scripts\verify_understanding_evaluation.py
python scripts\verify_actual_understanding_evaluation.py
python scripts\verify_actual_recommendation_evaluation.py
python scripts\verify_poster_annotation_packets.py
python scripts\verify_poster_evaluation.py
python scripts\verify_poster_annotation_sessions.py
python scripts\verify_poster_live_replay.py
python scripts\verify_tablet_domain_v2.py
python scripts\verify_tablet_domain_dev.py
python scripts\verify_experiment_conditions.py
python scripts\verify_tablet_domain_holdout.py
python scripts\verify_tablet_domain_holdout_freeze.py
python scripts\verify_tablet_domain_official.py
python scripts\verify_tablet_domain_annotation_packet.py
python scripts\verify_tablet_domain_automatic.py
python scripts\verify_mvp.py
python scripts\verify_amazon_processing.py
python scripts\verify_actual_demo.py
```

로컬 실제 pilot을 생성한 환경에서는 추가로 실행한다.

```powershell
python scripts\verify_amazon_pilot.py
python scripts\verify_amazon_pilot.py --pilot-dir data\amazon_reviews_2023\tablet_catalog_v2
python scripts\verify_experimental_catalog.py
python scripts\verify_review_semantic_retrieval.py
node --check app\static\app.js
```

reference 목업을 의도적으로 수정한 경우에만 해당 폴더에서 다음을 실행한다.

```powershell
npm ci
npx tsc --noEmit
npm run lint
npm run build
```

- 기존 사용자의 관련 없는 변경을 되돌리거나 함께 커밋하지 않는다.
- 변경 후 수정 파일, 핵심 설계 결정, 실행한 검증과 남은 위험을 요약한다.
- 커밋 전 `git diff --check`, Git 상태, 비밀정보 포함 여부를 확인한다.
