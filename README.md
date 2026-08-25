# KDMS SPN + RA-Rec

여러 대화 턴에 걸쳐 달라지는 사용자 요구를 상태로 추적하고, 그 상태를 검색·랭킹에 반영하는
태블릿 쇼핑 에이전트 연구 프로젝트다. Luxia GPT-4o-mini가 발화를 구조화하고, 상태 병합부터
정책 분기·상품 검색·리뷰 검색·랭킹까지는 결정론적 코드가 담당한다.

- **Summer Intern 프로젝트 기간:** 2026년 6월 23일–8월 26일
- **Vercel 프로덕션 배포:** [agent-iota-five.vercel.app](https://agent-iota-five.vercel.app/)
- **최종 포스터:** [사용자 요구 변화를 추적하는 다중 턴 쇼핑 에이전트 PDF](<presentation/김진욱_사용자 요구 변화를 추적하는 다중 턴 쇼핑 에이전트.pdf>)

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
