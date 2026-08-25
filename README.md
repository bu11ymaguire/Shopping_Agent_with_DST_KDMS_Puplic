# KDMS SPN + RA-Rec

여러 대화 턴에 걸쳐 달라지는 사용자 요구를 상태로 추적하고, 그 상태를 검색·랭킹에 반영하는
태블릿 쇼핑 에이전트 연구 프로젝트다. Luxia GPT-4o-mini가 발화를 구조화하고, 상태 병합부터
정책 분기·상품 검색·리뷰 검색·랭킹까지는 결정론적 코드가 담당한다.

- **Summer Intern 프로젝트 기간:** 2026년 6월 23일–8월 26일
- **Vercel 프로덕션 배포:** [agent-iota-five.vercel.app](https://agent-iota-five.vercel.app/)
- **최종 포스터:** [사용자 요구 변화를 추적하는 다중 턴 쇼핑 에이전트 PDF](<presentation/김진욱_사용자 요구 변화를 추적하는 다중 턴 쇼핑 에이전트.pdf>)

## 연구 배경: 왜 상태 기반 쇼핑 에이전트인가

### 기존 쇼핑 에이전트에서 관찰한 문제

개발 초기의 Agent N 사용성 점검에서는 대화 맥락을 유지하고 조건에 따라 후보를 갱신하는 장점이
있었지만, 실제 구매 결정을 돕기에는 다음 한계가 있었다.

- 대화가 진행되어도 후보가 충분히 좁혀지지 않아 사용자가 많은 상품을 직접 비교해야 했다.
- 요구가 모호해도 핵심 조건을 먼저 묻기보다 바로 추천해 정보 과부하와 의사결정 피로를 남겼다.
- 연령·성별 같은 표면적 단서로 상품을 일반화하거나 태블릿 케이스를 완제품으로 제시하는 등
  상품군과 요구를 잘못 연결하는 사례가 있었다.
- 단점, 추가 비용, 호환성, 배송·설치·AS와 조건 사이의 우선순위를 충분히 설명하지 못했다.

즉, 탐색 비용은 줄였지만 **왜 이 후보를 남기고 다른 후보를 제외했는지**, 사용자의 피드백으로
**무엇이 바뀌었는지**를 상태와 근거로 관리하지 못해 의사결정과 검증의 부담이 사용자에게 남았다.

### 전통적인 추천 시스템을 중심에 두지 못한 이유

초기에는 사용자 프로필과 과거 클릭·구매 같은 상호작용으로 다음 아이템을 예측하고 Recall·NDCG를
높이는 전통적 추천 방식을 검토했다. 그러나 이를 이 프로젝트의 핵심 추천 엔진으로 채택하면 연구
질문을 제대로 다룰 수 없었다.

1. **구매 결과와 지속 선호를 구분할 수 없다.** 최고가·특정 색상 상품의 구매가 실제 선호가 아니라
   고장 난 기기를 빨리 교체하기 위한 재고·배송 제약과 조건 양보의 결과일 수 있다. 고정된 로그의
   다음 아이템 예측만으로는 이런 상황, 거절 이유와 턴별 우선순위 변화를 복원하기 어렵다.
2. **필요한 학습·평가 정답이 없었다.** Amazon Reviews 2023은 상품·리뷰 근거를 제공하지만
   질의별 적합 상품, 턴별 Dialogue State·State Diff, 재질문/추천 행동, 거절 이후 기대 순위 변화의
   정답을 제공하지 않는다. ESCI도 검색 모듈의 관련성 평가는 가능하지만 같은 상품·대화 상태와
   연결된 end-to-end 다중 턴 정답은 아니다.
3. **공식 실험의 목적과도 맞지 않았다.** 직접 만든 20개·81턴 시나리오는 memory 유무에 따른 상태
   보존과 hard-filter 전달을 평가하기 위한 동결 holdout이지 추천 모델을 학습할 사용자–아이템
   행동 corpus가 아니다. 인간 relevance label도 없어 학습형 ranker의 품질 향상을 정직하게
   평가할 수 없었다.

따라서 전통 추천 시스템 자체를 부정한 것이 아니라, **데이터 계약과 평가 질문의 불일치 때문에
주 실험의 end-to-end 추천기로 도입하지 않았다.** 대신 LLM은 발화 이해와 응답 작성에 한정하고,
Dialogue State 병합·정책·hard filter·질의 생성·랭킹은 결정론적으로 유지했다. 상품 리뷰는
Bi-encoder와 Cross-Encoder로 검색·재정렬하되, 상태와 후보를 고정한 ablation으로 순위 기여만
분리해 측정했다.

근거 자료: [Agent N 사용 경험 분석](<presentation/Summer_Intern_에이전트N_사용경험_진욱.pptx>),
[중간점검](<presentation/Summer_Intern_중간점검.pptx>),
[중간점검 보완](<presentation/Summer_Intern_중간점검_보완.pptx>),
[최종 포스터](<presentation/김진욱_사용자 요구 변화를 추적하는 다중 턴 쇼핑 에이전트.pdf>),
[`manual.md`](manual.md).

## 핵심 설계

```text
UNDERSTAND → MEMORY → DECIDE → QUERY / BROWSE / RANK → RESPOND
```

- 구매 결과만으로 지속 선호를 추론하지 않고 상황, 상품 속성, 우선순위와 양보를 구분한다.
- LLM은 Understanding과 응답 작성에 사용하고, 상태 병합·정책·검색·랭킹·평가는 코드로 고정한다.
- 현재 연구 범위는 태블릿 도메인이며, 실제 상품·리뷰 데이터는 로컬에서만 사용한다.
- 구현과 API의 상세 계약은 [`flow.md`](flow.md), 실행법은 [`backend/README.md`](backend/README.md)에 있다.

## 저장소 구성

- `backend/`: LangGraph workflow, FastAPI API·UI, 평가기와 동결 fixture
- `presentation/`: 중간 점검부터 최종 발표·포스터까지의 개발 과정 자료(PPTX/PDF)
- [`flow.md`](flow.md): 아키텍처, 상태 계약, 실험 결과의 정본
- [`manual.md`](manual.md): 기술과 데이터 선택 근거
- [`PROGRESS.md`](PROGRESS.md): 구현 현황과 남은 작업
- [`DATA_AND_PRIVACY.md`](DATA_AND_PRIVACY.md): 공개 데이터와 개인정보 경계

## 빠른 시작

PowerShell 기준으로 키와 실데이터 없이 synthetic 6턴 경로를 실행할 수 있다.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:LLM_PROVIDER="mock"
.\.venv\Scripts\python.exe -m uvicorn app.api:app --reload
```

브라우저에서 `http://127.0.0.1:8000`을 연다. Luxia와 실제 태블릿 catalog를 사용하는 경로는
API 키, Amazon Reviews 2023 가공 데이터와 semantic index가 별도로 필요하며 저장소에는 포함되지 않는다.

## 연구 브랜치

| 브랜치 | 질문 | 결론 |
| --- | --- | --- |
| `main` | memory가 multi-turn 상태와 추천에 어떤 영향을 주는가 | v2.3 기준선과 official automatic benchmark |
| `exp/0-intent-extraction` | 상태만으로 숨은 의도 가설을 만들 수 있는가 | 정성 exploratory probe; 정확도 주장이 아님 |
| `exp/1-state-diff-strategies` | 반복·거짓 State Diff를 어떻게 줄이는가 | semantic no-op suppression은 State Diff를 개선했지만 Final State는 동일 |
| `exp/2-segse-final-state` | sparse grounded event가 누적 상태를 고칠 수 있는가 | 개발·재측정에서는 개선됐지만 confirmatory decision rule은 실패 |

전체 실험 흐름은 `exp/2-segse-final-state` 브랜치의 `experiments.md`에서 볼 수 있다.

```powershell
git show exp/2-segse-final-state:experiments.md
```

결과를 해석할 때 개발 세트, post-hoc 분석, official holdout과 confirmatory 결과를 섞지 않는다.
특히 SEGSE v1.4는 confirmatory 실패 이후 수정하지 않으며, 남은 핵심 오류는 semantic dimension
attribution이다.

## 평가와 재현

- primary automatic 결과: [`backend/docs/tablet_domain_automatic_benchmark_v1.md`](backend/docs/tablet_domain_automatic_benchmark_v1.md)
- metric 정의: [`backend/docs/tablet_domain_automatic_metric_definitions.md`](backend/docs/tablet_domain_automatic_metric_definitions.md)
- official 실행 기록: [`backend/docs/tablet_domain_holdout_official_run.md`](backend/docs/tablet_domain_holdout_official_run.md)
- 실제 데이터 가공: [`backend/data/README.md`](backend/data/README.md)

동결된 holdout·manifest·공식 결과는 보고된 수치를 개선하기 위해 사후 수정하지 않는다. 공개본은
원본 연구 저장소의 브랜치 tip을 새 Git 이력으로 옮긴 것이며, 대응 commit과 제외 항목은
[`PUBLIC_RELEASE_MANIFEST.json`](PUBLIC_RELEASE_MANIFEST.json)에 기록되어 있다.

## 공개 범위

원본 리뷰 corpus, LLM trace, episode 감사 로그, 로컬 model/index와 자격 증명은 포함하지 않는다.
`presentation/`에는 개발 과정을 설명하기 위해 선별한 발표 자료만 둔다. 기여와 보안 정책은
각각 [`CONTRIBUTING.md`](CONTRIBUTING.md)와 [`SECURITY.md`](SECURITY.md)를 따른다.
