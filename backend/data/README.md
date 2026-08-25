# 데이터 카드와 가공 규칙

이 디렉터리는 운영 데이터가 아니라 서로 목적이 다른 두 연구용 회귀 fixture를 보관한다.

- `understanding_eval_v1.json`: Understanding 의미 추출을 비교하는 합성 gold 발화 28개
- `demo_catalog.json`: end-to-end MVP가 사용하는 통제 상품 6개와 mock 리뷰 17개
- `results/tablet_domain_automatic_benchmark_v1.json`: frozen official run을 재실행하지 않고
  20개·81턴 전체를 집계한 primary automatic summary
- `results/tablet_domain_automatic_benchmark_v1_scenarios.csv`: paired bootstrap의 scenario-level 입력

Git에는 실제 사용자 대화, Amazon Reviews 2023 원문, 실시간 상품·재고 데이터를 포함하지
않는다. 실제 Amazon pilot은 재생성 스크립트로 로컬 ignored 디렉터리에 만든다.

## Understanding evaluation fixture

`understanding_eval_v1.json`은 `flow.md` 부록 C의 6턴 회귀 시드와 문서가 요구한
확장 유형을 바탕으로 직접 작성한 합성 연구 fixture다. 실제 사용자 대화나 개인정보를
복사한 데이터가 아니다.

gold label은 자연어 표현 자체가 아니라 상태에 영향을 주는 의미 필드만 고정한다.

- intent
- versioned `canonical_id`
- target kind/facet/scope
- evidence origin (`explicit` / `implicit` / `inferred`)
- item action과 거절 이유 유형
- `supersedes`
- 잔여 색상 수용 여부

fixture는 회귀 비교를 위해 버전 관리한다. 실행별 LLM 원본 응답과 평가 report는
`backend/logs/`, `backend/reports/`에 남고 Git에는 포함하지 않는다.

### Subjective facet과 ranking constraint의 구분

- 하나의 두드러진 질적 요구(`오래 쓸`, `가볍고 편한`, `화면 품질 좋은`,
  `튼튼한`, `음질 좋은`, `성능 좋은`)는 `subjective_*` facet과 대응하는 ranking
  constraint를 함께 가질 수 있다.
- 여러 속성을 단순히 중요하다고 나열한 경우에는 해당 constraint만 붙이고 임의로 하나를
  subjective facet으로 승격하지 않는다.
- `activity_general`은 빈 facet을 채우는 기본값이 아니다. 사용자가 범용 목적을 명시했을
  때만 정답이다.
- `previous_state_summary`는 행동 참조와 대체 관계를 해석하기 위한 문맥이다. 현재 발화에
  새 근거가 없는 기존 값을 candidate 정답에 복사하지 않는다.

## MVP 상품·리뷰 fixture

### 1. 데이터 출처와 성격

`demo_catalog.json`의 상품 6개와 리뷰 17개는 `flow.md` 부록 B의 6턴 시나리오를
end-to-end로 재현하기 위해 수작업으로 만든 통제 데이터다.

- 상품명, 가격, 평점, 리뷰 수, 설명, 성능 속성은 회귀 비교가 가능하도록 고정한 값이다.
- 리뷰는 실제 구매 리뷰의 인용·번역본이 아니라 테스트 목적의 한국어 mock 문장이다.
- `delivery_days`, `available_colors`, `shipping`, `stock`은 합성 inventory snapshot이다.
- `source=controlled_inventory`와 `source=controlled_mock_review`로 provenance를 구분한다.
- `catalog_version=iphone-controlled-inventory-v1`로 fixture 버전을 고정한다.

따라서 이 fixture의 점수나 추천 결과를 실제 시장 가격·재고·소비자 평가로 해석하면 안 된다.

### 2. 저장 전 정규화

상품은 다음 형태로 정규화해 JSON에 저장했다.

| 필드 | 형식 | 가공 목적 |
| --- | --- | --- |
| `id` | 안정적인 kebab-case ID | 상태·행동·회귀 assertion 연결 |
| `price` | 원 단위 정수 | 예산 비교에서 문자열 파싱 제거 |
| `rating` | 0~5 실수 | 화면 표시용; 현재 총점에는 직접 미사용 |
| `review_count` | 0 이상의 정수 | 리뷰 근거 신뢰도 보조 신호 |
| `storage_gb` | GB 정수 | 저장 공간 hard constraint 비교 |
| `weight_grams` | g 정수 | 휴대성 0~100 정규화 |
| `battery_hours` | 시간 수치 | 배터리 0~100 정규화 |
| `speaker_tier` | 1~5 통제 등급 | 오디오 0~100 정규화 |
| `performance_tier` | 1~5 통제 등급 | 성능 0~100 정규화 |
| `longevity_years` | 예상 사용 연수 | 장기 사용 0~100 정규화 |
| `delivery_days` | 수령까지의 일수 | 배송기한 hard constraint 비교 |
| `available_colors` | 즉시 수령 가능 색상 배열 | 구매 시점 trade-off 기록 |

리뷰는 안정적인 `id`, 연결할 `product_id`, mock `text`, 0~1 범위의
`helpfulness`, 명시적인 `source`로 저장한다. 로더인 `app/catalog.py`가 UTF-8 JSON을
읽은 뒤 모든 행을 Pydantic `Product`와 `Review` 계약으로 검증하고 불변 tuple로 만든다.
스키마에 맞지 않는 행을 조용히 보정하거나 버리지 않는다.

### 3. 사용자 발화에서 랭킹 입력까지

```text
사용자 발화
→ LLM UnderstandingOutput (closed vocabulary 후보)
→ State Manager (상태·provenance·status 병합)
→ confirmed 값만 Query/Rank 입력
→ category 필터와 거절 상품 제외
→ 상품별 리뷰 top 3
→ 결정론적 상품 점수
```

- LLM이 만든 `canonical_id`는 versioned closed vocabulary로 검증한다.
- `explicit`과 `implicit` 후보는 확정 상태로, `inferred` 후보는 미확정 상태로 병합한다.
- `status=confirmed`인 값만 검색어와 랭킹 점수에 사용한다.
- `color_residual`은 지속 선호가 아니라 구매 당시 양보 조건이므로 랭킹에서 제외한다.
- 거절한 상품은 다음 턴의 후보군에서 제외한다.
- `canonical_id`가 속성 매핑에도 비랭킹 목록에도 없으면 `unmapped_preference_ids`로 계측한다.

### 4. 리뷰 가공과 top-k

현재 MVP는 임베딩 대신 재현 가능한 토큰 기반 비교를 사용한다.

1. 선호값, 리뷰 본문, 상품명, 칩셋, 저장 용량, 사용 사례에서 한글·영문·숫자 토큰을 추출한다.
2. 배송, 장기 사용, 배터리, 오디오, 휴대성 등 사전에 정의한 동의어 그룹을 확장한다.
3. 리뷰마다 일치 선호 수로 `similarity`와 `coverage`를 만들고 `helpfulness`를
   `reliability` 0~100으로 변환한다.
4. 아래 식으로 리뷰 점수를 계산하고 상품별 상위 3개만 남긴다.

```text
review_total
= 0.65 × similarity
+ 0.25 × preference_coverage
+ 0.10 × reliability
```

동점은 리뷰 ID 오름차순으로 고정한다. 상품 카드에 보이는 리뷰는 상품 점수 계산에 사용한
`evidence_review_ids`와 정확히 같은 집합이다.

### 5. 상품 속성과 최종 점수

선호 ID는 `app/nodes/recommendation.py`의 `PREFERENCE_ATTRIBUTES`를 통해 일반 상품
속성으로 변환한다. 특정 상품 ID에만 적용되는 가점은 없다. 주요 속성은 다음처럼 0~100으로
정규화하고 범위를 벗어난 값은 0~100으로 clamp한다.

```text
delivery_speed = 100 - delivery_days × 12
performance    = performance_tier × 20
longevity      = longevity_years / 6 × 100
battery        = (battery_hours - 18) / 20 × 100
audio          = speaker_tier × 20
portability    = (240 - weight_grams) / 80 × 100
price_value    = (2,100,000 - price) / 1,300,000 × 100
storage        = storage_gb / 256 × 100
display        = display_inches / 6.9 × 100
```

예산·배송기한·저장 용량은 hard constraint로 별도 판정한다. 예산 초과는 유연성이 확정된
경우에만 초과 폭에 따라 감점하고, 그 외에는 0점이다. 최종 상품 점수는 다음 고정 가중치다.

```text
total
= 0.30 × hard_constraint_match
+ 0.30 × metadata_match
+ 0.20 × subjective_need_match
+ 0.15 × review_evidence_score
+ 0.05 × evidence_reliability
```

`evidence_reliability`는 선택된 리뷰의 helpfulness 기반 점수 70%와 전체 후보 중 해당 상품의
리뷰 수 로그 정규화 30%를 결합한다. 최종 동점은 상품 ID 오름차순으로 고정한다.

### 6. 검증과 재현

다음 검증은 fixture를 다시 만들지 않고 같은 파일을 읽어 lane, 순위, 행동, 리뷰 근거를
검사한다.

```powershell
cd backend
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe scripts\verify_mvp.py
```

기대 흐름은 `clarify → recommend × 5`, 추천 1위 변화는
`iPhone 17 → iPhone Air → iPhone 17 Pro`다. 상세 보기와 구매를 분리하고, 최종 구매에서
남은 색상 수용을 지속 선호가 아닌 trade-off로 기록하는지도 검증한다.

### 7. 실데이터로 전환할 때

Amazon Reviews 2023을 붙일 때는 이 JSON을 그대로 확장하지 않는다.

1. 원본 행은 별도 raw/staging 계층에 변경 없이 보존한다.
2. 원본 dataset/version, item ID, review ID와 변환 시각을 provenance로 남긴다.
3. 가격·평점·리뷰 수처럼 원본에 있는 필드와 배송일·가용 색상 같은 합성 운영 필드를
   서로 다른 테이블 또는 명시적인 source 필드로 구분한다.
4. 중복, 결측, 비정상 범위, 카테고리 분포를 프로파일링한 뒤 정규화 기준을 확정한다.
5. 리뷰는 전체 스캔 대신 DB 필터와 제한된 top-k 검색으로 교체한다.
6. 현재 fixture 전용 최소·최대값을 실데이터 분포에 재사용하지 않고 train/dev와 독립된
   holdout으로 랭킹 회귀를 다시 측정한다.

데이터 전환 후에도 이 fixture는 빠른 회귀 테스트용으로 유지하되, 운영 품질 지표와는
분리해서 보고한다.

## 실제 Amazon Reviews 2023 tablet pilot

2026-08-06에 McAuley Lab의 공식 Electronics 배포본을 처음으로 실제 처리했다.

- 데이터셋: `McAuley-Lab/Amazon-Reviews-2023`
- 고정 revision: `2b6d039ed471f2ba5fd2acb718bf33b0a7e5598e`
- metadata: 공식 Hugging Face Parquet 10개 shard에서 각 5,000행, 총 50,000행
- reviews: 공식 Electronics gzip stream의 앞 1,000,000행
- 결과: 태블릿 본체 8개, 실제 리뷰 194개
- verified purchase: 179개
- 평점 극성: 긍정 139개, 중립 18개, 부정 37개

공식 원본 필드와 `parent_asin` 연결 방식은 [Amazon Reviews 2023 데이터 페이지](https://amazon-reviews-2023.github.io/)를
따른다. 최신 `datasets 5.x`는 저장소의 legacy remote loading script를 실행하지 않으므로,
metadata는 revision이 고정된 Parquet URL을 직접 streaming한다. 리뷰는 공식 gzip을 한 줄씩
해제해 후보 `parent_asin`만 보관한다.

### 실제 데이터가 드러낸 문제와 교정

mock에서는 보이지 않았던 다음 문제가 첫 pilot에서 발견됐다.

1. `Computers & Tablets`가 태블릿 본체뿐 아니라 노트북·마우스·케이블에도 붙어 있었다.
   leaf category와 title의 본체 신호를 동시에 요구하고 액세서리 표현을 제외했다.
2. 원본 category가 `Tablets`인데 실제로는 모바일 hotspot인 행도 있었다. category만 신뢰하지
   않고 title 기기 신호를 필수로 만들었다.
3. Arrow의 이미지 필드는 list-of-struct가 아니라 struct-of-lists로 반환될 수 있었다.
   `variant=MAIN` 인덱스로 실제 이미지를 복구했다.
4. `64GB ROM, 512GB Expandable`에서 512GB를 내부 저장공간으로 읽었다. `expandable`,
   `microSD`, `up to` 문맥을 제외해 내부 64GB만 남겼다.
5. 화면 크기의 `8"` 표기와 `Product Dimensions` 끝의 ounces 무게를 별도 파싱했다.

교정 후 최종 8개는 Fire HD, Kindle Fire, Galaxy Tab, iPad, Android Tablet, Pixel Slate로
구성되며 이미지와 저장공간은 전부 추출됐다. 다만 가격은 4개, 무게와 OS는 각각 3개 상품에서
결측이다.

### 로컬 산출물 경계

```text
data/amazon_reviews_2023/tablet_pilot/
├─ source_metadata.jsonl.gz  선택 상품의 원본 행
├─ source_reviews.jsonl.gz   선택 리뷰의 원본 행
├─ products.parquet          정규화 상품; 추출 근거 포함
├─ reviews.parquet           user_id·리뷰 이미지를 제거한 서비스용 근거
└─ manifest.json             실행 조건·revision·결측률·파일 해시
```

이 디렉터리는 Git에서 제외한다. 원문을 커밋하지 않고도 결과를 감사할 수 있도록 요약 manifest는
`data/manifests/amazon_tablet_pilot_v1.json`에 별도로 버전 관리한다.

### v1 pilot 결론

태블릿 본체는 category leaf와 title을 결합하면 높은 정밀도로 분리할 수 있다. 그러나 이
pilot은 확률 표본이 아니며 8개 중 상품당 리뷰 20개 이상을 확보한 것은 4개뿐이었다. 이 결과는
분류·파싱 규칙을 교정하는 용도로 남기고, 아래 v2 확장 표본에서 목표 규모와 정규화를 다시
검증했다.

## 실제 Amazon Reviews 2023 tablet catalog v2

### 1. 확대 결과

2026-08-07에 같은 revision을 고정하고 metadata 500,000행과 review 8,000,000행을
순차 처리했다.

| 항목 | 결과 |
| --- | ---: |
| raw metadata 순회 | 500,000행, shard당 50,000행 |
| 현행 규칙 재정규화 후보 | 870개 |
| raw review 순회 | 8,000,000행 |
| 후보 `parent_asin` 일치 | 55,699행 |
| 최종 태블릿 상품 | 117개 |
| 최종 고유 실제 리뷰 | 7,552개 |
| verified purchase | 6,990개 |
| 상품별 리뷰 | 최소 20개, 최대 100개 |

상품 안에서 먼저 중복 본문을 제거하고 verified purchase, helpful vote, 본문 길이, 최신순을
우선한다. 긍정 65%·부정 25%·중립 10% 목표로 평점 극성을 섞은 뒤, 카탈로그 전체에서 다시
본문을 deduplicate한다. 전역 중복 제거 뒤 리뷰가 20개 미만인 상품은 포함하지 않는다.
processed 리뷰에는 `user_id`와 리뷰 이미지가 없으며 `parent_asin`·파생 `review_id`·평점·본문·
timestamp·verified·helpful vote·극성·source만 남는다.

### 2. 속성 파싱 교정

확대 표본의 이상치 감사에서 다음 규칙을 추가했다.

1. `Memory Storage Capacity`는 RAM이 아니다. `RAM`, `installed RAM`, `system memory`처럼
   명시된 표현만 `memory_gb`로 읽고 1~64GB 범위만 허용한다.
   details의 명시적 RAM도 독립적인 서술 근거가 없고 추출된 저장공간과 같은 값이면 원천
   metadata가 저장공간을 RAM으로 중복 기록한 것으로 보고 결측 처리한다.
2. parent metadata의 feature에는 다른 용량 variant가 섞일 수 있다. 내부 저장공간은 상품
   title의 `storage`·`ROM`·`SSD` 표기와 일반 용량을 먼저 읽고, 없을 때만 나머지 필드를 쓴다.
3. `MacBook`·`Chromebook` 또는 제목 선두의 laptop/notebook은 뒤에 iPad 호환 표현이 있어도
   태블릿으로 분류하지 않는다.
4. 150g 미만 또는 2,500g 초과 무게는 단위·metadata 오류 가능성이 커 결측으로 남긴다.
5. 저장한 metadata checkpoint는 normalized 결과 캐시가 아니라 raw 다운로드 캐시다. 재사용할
   때도 반드시 현행 분류·정규화 코드로 다시 계산한다.

교정 후 최종 표본에서 RAM은 1~8GB, 무게는 272~1,397g이고 MacBook 제목은 0건이다.

### 3. 분위수 정규화와 결측

통제 iPhone fixture의 최소·최대 상수를 재사용하지 않는다. 최종 117개 표본의 empirical
percentile rank를 계산해 0~100 정수로 저장한다.

- 낮을수록 좋은 값: `price_usd → price_value`, `weight_grams → portability`
- 높을수록 좋은 값: `storage_gb → storage`, `memory_gb → memory`,
  `screen_inches → display`
- `stylus_mentioned`는 `note_taking` 0 또는 100으로 별도 표시
- 동일 값은 평균 rank를 사용하고 범위 끝은 0·100으로 clamp
- 결측은 점수도 `null`이며 중앙값·평균값으로 보간하지 않음

| 원본 속성 | non-null / 117 | p05 | median | p95 |
| --- | ---: | ---: | ---: | ---: |
| price (USD) | 63 | 46.389 | 164 | 745.595 |
| weight (g) | 67 | 301 | 499 | 956 |
| storage (GB) | 117 | 8 | 32 | 256 |
| RAM (GB) | 73 | 1 | 2 | 6.8 |
| display (inch) | 117 | 7 | 9.7 | 12.22 |

가격 54개, 무게 50개, RAM 44개, OS 49개 상품은 결측이다. API는 검색 결과 전체에서
필드별 non-null 건수를 `coverage`로 함께 반환한다.

### 4. 로컬 계층과 재현

```text
data/amazon_reviews_2023/tablet_catalog_v2/
├─ candidate_metadata.jsonl.gz  raw 후보 + 재계산된 normalized checkpoint
├─ metadata_profile.json        후보 선택 프로파일
├─ source_metadata.jsonl.gz     최종 상품 원본 행
├─ source_reviews.jsonl.gz      최종 리뷰 원본 행
├─ products.parquet             정규화 상품 + 추출 근거 + 분위수 점수
├─ reviews.parquet              서비스용 실제 리뷰 근거
├─ normalization.json           분포·결측·상품별 점수
├─ manifest.json                실행 조건·revision·결측·파일 해시
├─ review_embeddings.npy        실제 리뷰의 정규화 384차원 embedding (Git 제외)
├─ review_embedding_metadata.json  embedding 행의 review_id·parent_asin (Git 제외)
└─ review_semantic_manifest.json   모델 revision·입력/산출물 hash (Git 제외)
```

전체 디렉터리는 Git에서 제외한다. 원문 없이 실행 조건과 결과를 감사하기 위한 요약본은
`data/manifests/amazon_tablet_catalog_v2.json`에 추적한다.

```powershell
$env:HF_HOME = (Join-Path (Get-Location).Path '.cache\huggingface')
.\.venv\Scripts\python.exe scripts\build_amazon_tablet_sample.py `
  --metadata-rows 500000 `
  --review-rows 8000000 `
  --min-reviews-per-product 20 `
  --reviews-per-product 100 `
  --review-pool-per-product 500 `
  --max-products 300 `
  --output-dir data\amazon_reviews_2023\tablet_catalog_v2
.\.venv\Scripts\python.exe scripts\verify_amazon_pilot.py `
  --pilot-dir data\amazon_reviews_2023\tablet_catalog_v2
.\.venv\Scripts\python.exe scripts\verify_experimental_catalog.py
```

metadata 단계가 완료된 동일 revision·선택 조건에서는 `--reuse-metadata-checkpoint`로 raw 후보를
재사용할 수 있다. 최종 `source_reviews.jsonl.gz`와 manifest도 보존되어 있다면
`--reuse-final-review-checkpoint`를 함께 주어 8,000,000행 review stream을 다시 읽지 않고
동일한 최종 원문을 현행 코드로 재정규화할 수 있다. 두 옵션 모두 normalized 값을 다시 계산하고,
manifest의 revision·선택 조건이 다르면 재사용을 거부한다.

### 5. semantic review retrieval

실제 7,552개 리뷰의 `title + "\\n" + text`를 `review_id ASC` 순으로 정렬해
`sentence-transformers/all-MiniLM-L6-v2`의 정규화 384차원 embedding으로 만든다. 모델 이름뿐
아니라 Hub commit SHA `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`도 고정했다. 런타임은
hard filter 뒤 남은 상품의 행만 dot-product cosine으로 비교해 상품별 5개를 고른다.

그 제한된 query-review pair만 `cross-encoder/ms-marco-MiniLM-L6-v2` revision
`c5ee24cb16019beea0893ab7796b1df96625c6b8`에 통과시키고 상품별 상위 3개를 반환한다. 최종
retrieval 점수는 정규화 cosine 35%와 Cross-Encoder logit sigmoid 65%의 결합값이다. 모델은
일반 영어 passage ranking용이므로 태블릿 리뷰 관련성 label에 맞춰 학습된 것은 아니다.

```powershell
.\.venv\Scripts\python.exe scripts\build_review_semantic_index.py
.\.venv\Scripts\python.exe scripts\verify_review_semantic_retrieval.py
```

build script만 네트워크에서 정확한 revision을 내려받는다. 서버는 로컬 파일만 읽고 index schema,
모델 revision, review Parquet/embedding/metadata SHA-256을 처음 사용할 때 검증한다. 파일이 없거나
검증·추론에 실패하면 영어 token baseline으로 전환하고 사유를 browse summary와 node trace에
기록한다. 추적 가능한 요약 manifest는
`data/manifests/amazon_tablet_semantic_retrieval_v1.json`이다.

2026-08-07 로컬 CPU smoke에서 12개 상품/36개 최종 리뷰 cold start는 약 8.1초, 전체 117개
상품/351개 최종 리뷰는 약 14.1초였다. 단일 실행 latency일 뿐 benchmark가 아니다. 이후 동결한
소규모 pooled 관련성 평가에서도 상품 NDCG@3은 token 0.821 대 semantic 0.699, 리뷰 NDCG@3은
token 0.733 대 semantic 0.661이었다. 따라서 현재 semantic 경로의 정확도 우위를 주장하지 않는다.

### 6. local catalog와 자유 입력 추천 mode

`app/experimental_catalog.py`는 v2 Parquet을 DuckDB로 읽는 read-only adapter다.
`app/actual_workflow.py`의 영어 자유 입력 경로는 이 adapter를 QUERY/ACT/RANK 단계에 연결한다.
통제 6턴 회귀와 `/api/catalog`은 `demo_catalog.json`을 계속 사용하지만 기본 웹 UI는
`/api/actual/conversations` 실데이터 경로를 사용한다.

- 상품 검색: 최대 USD 가격, 최소 저장공간·RAM·화면, 최대 무게, 최소 평점, OS, stylus 유무
- 후보 범위: 확인된 hard filter를 만족하는 로컬 표본 전체; 현재 최대 117개
- 정렬 입력: 표본 분위수 기반 가격 가치·휴대성·저장공간·RAM·화면·필기 점수
- 상세: 원본 feature/description, 분류 provenance, 속성 추출 근거
- 리뷰 검색: 기본은 고정 semantic index의 상품별 bi-encoder top 5 → Cross-Encoder top 3;
  index/model 부재 시 token 일치 → verified → helpful vote → 최신순 baseline
- 리뷰 근거: 결정론적 리뷰 점수 상위 3개의 ID를 상품 점수와 화면 카드에서 동일하게 사용
- 응답 경계: 내부 상위 상품 10개, 화면 카드 3개만 반환하며 전체 상품·리뷰 행을 대화 응답에
  포함하지 않음
- 파일 부재: catalog status는 `available=false`; 실제 대화 서비스 시작은 실패하고 데이터 조회는
  503 반환

정해진 발화 시나리오는 실제 경로에 없다. LLM은 현재 영어 발화에서 versioned closed-vocabulary
상태 갱신 후보를 만들고, State Manager·Policy·Query·Browse·Rank는 결정론적으로 실행된다.
현재 7,552개 규모는 메모리 매핑 NumPy index로 처리한다. 데이터 규모나 동시 사용자·배포 요구가
생기면 PostgreSQL/pgvector로 옮긴다. 별도의 통제 inventory snapshot은 다음 단계다.
배송일·재고·가용 색상은 Amazon 원본에 없으므로 현재 실데이터 응답에 합성하지 않는다.

### 7. 포스터 추천 평가 입력물

`poster_recommendation_scenarios_v1.json`은 기존 4질의 개발용 관련성 pilot과 분리한 영어 4턴
시나리오 20개다. 각 시나리오는 최종 요청 요약, 턴별 상태 갱신 후보, 기대 hard filter를 담고
있으며 첫 annotation packet build 전에 동결했다. 이 상태 후보는 사람 관련성 grade가 아니라
동일한 최종 검색 상태를 재현하기 위한 deterministic 입력이다.

`scripts/build_poster_annotation_packets.py`는 각 시나리오에서 token/semantic top 10 합집합과
hard negative를 만든다. public packet에는 무작위 blind ID와 동일 catalog/review evidence만 넣고,
원본 ASIN·review ID·시스템 rank·score는 Git 제외 private provenance로 분리한다. 기본 설정은
annotator 3명, hard negative 2개이며 annotator별 표시 순서가 독립적으로 섞인다.

```powershell
.\.venv\Scripts\python.exe scripts\verify_poster_annotation_packets.py
.\.venv\Scripts\python.exe scripts\build_poster_annotation_packets.py
```

생성된 `reports/poster_annotation_v1/`은 Git에서 제외하고, 재현에 필요한 dataset/catalog/index
hash와 파일별 SHA-256만 `manifests/poster_annotation_packet_v1.json`에 추적한다. public packet의
grade와 rationale은 모두 비어 있으므로 human annotation 전에는 평가 결과로 해석하지 않는다.

동결 원본을 직접 편집하지 않는다. `scripts/prepare_poster_annotation_collection.py`는 원본 hash를
검증한 뒤 `reports/poster_annotation_v1_completed/`에 public packet만 복사하고 private provenance는
제외한다. `poster_annotation_collection_manifest.template.json`도 작업 폴더의
`collection_manifest.json`으로 복사된다. 판정 완료 후 수집 방식과 독립성을 사실대로 갱신하되
개인식별정보는 tracked 문서나 report에 넣지 않는다.

`scripts/prepare_poster_annotation_sessions.py`는 각 작업 packet을 시나리오 경계를 유지한 채
세션당 최대 120건으로 나눈다. 기본 3인 설정에서 상품 2세션·리뷰 3세션씩 총 15개이며,
`session_manifest.json`에는 `annotation`을 제외한 불변 payload hash와 정확한 coverage가 기록된다.
세션과 manifest는 작업 사본 아래에만 생기며 Git에서 제외된다. 완료 후
`scripts/merge_poster_annotation_sessions.py`가 모든 grade·rationale, hash, 누락·중복을 검증하고
label만 원래 public packet에 병합한다.

`scripts/evaluate_poster_annotations.py`는 모든 relevance·rationale과 blind ID 무결성을 검사한 뒤
중앙값 grade, ordinal agreement, 상품·리뷰 NDCG@3과 paired bootstrap CI를 계산한다. 현재 packet
v1은 token/semantic만 포함하고 턴별 State Diff gold가 없으므로, report는 추천 관련성 결과와 전체
포스터 primary claim readiness를 분리한다.

### 8. 포스터 시나리오 live replay

`scripts/run_poster_scenarios_live.py`는 위 20개 시나리오의 사용자 발화 80개를 실제 Luxia 기반
workflow에 시나리오별 새 세션으로 입력한다. 원본 턴·상태·trace·응답은 Git 제외 `reports/`에
checkpoint한다. `scripts/summarize_poster_scenarios_live.py`는 compact metrics를 다시 계산하고,
첫 실행의 dataset·prompt·runner·raw report hash와 결과는
`manifests/poster_live_replay_luxia_v1.json`에 동결한다.

이 manifest는 사람 대화 로그가 아니라 연구자 작성 시나리오의 live replay다. 또한 기존
annotation packet은 정답 상태를 직접 사용했으므로 live replay와 동일한 출력으로 해석하지 않는다.

### 9. Tablet-domain v2 개발셋과 동결 manifest

`tablet_domain_understanding_dev_v1.json`은 v1 live replay와 독립된 영어 **개발용** 26건이다.
암묵적 태블릿 매장 요청, 명시적 타 카테고리, RAM/storage, 부정, 거절, 조건 변경, trade-off,
모순을 포함한다. `split=dev`가 schema에서 강제되며 untouched holdout이나 최종 성능으로 사용할 수
없다.

```powershell
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_dev.py
.\.venv\Scripts\python.exe scripts\evaluate_tablet_domain_dev.py --concurrency 4
```

선택한 v2.3 dev run은 validation/domain route 1.000, canonical micro-F1 0.807이었다. 이 결과를
본 뒤 같은 26건에 더 맞추지 않는다. `manifests/tablet_domain_v2_freeze.json`은 새 holdout 작성
전에 환경 범위, prompt/schema/model, semantic retriever/Cross-Encoder revision, ranking weights와
관련 source SHA-256을 동결한다. 원본 dev LLM report는 `reports/`에만 있으며 Git에 포함하지 않는다.

untouched holdout에는 발화·Gold State Diff·Policy/action·hard constraints만 미리 고정했고 추천
상품이나 순위는 넣지 않았다. Full/No-memory/No-review official run과 automatic benchmark까지
완료했다. 기존 top-k blind packet은 optional/future human relevance artifact로 보존하며 이번
포스터 primary result에는 Product/Review NDCG를 포함하지 않는다.
