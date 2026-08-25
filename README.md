# KDMS SPN + RA-Rec

Luxia GPT-4o-mini가 영어 자유 입력을 이해하고, 결정론적 상태·정책·검색·랭킹 단계가
로컬 Amazon Reviews 2023 태블릿 데이터에서 추천을 만드는 연구용 MVP다.

- 실행·API·현재 결과: [`backend/README.md`](backend/README.md)
- 현재 완료 범위와 다음 작업: [`PROGRESS.md`](PROGRESS.md)
- 포스터용 평가 설계: [`backend/docs/poster_evaluation_protocol.md`](backend/docs/poster_evaluation_protocol.md)
- 포스터 primary automatic 결과: [`backend/docs/tablet_domain_automatic_benchmark_v1.md`](backend/docs/tablet_domain_automatic_benchmark_v1.md)
- automatic metric 정의: [`backend/docs/tablet_domain_automatic_metric_definitions.md`](backend/docs/tablet_domain_automatic_metric_definitions.md)
- v2.3 공식 holdout 실행·결과: [`backend/docs/tablet_domain_holdout_official_run.md`](backend/docs/tablet_domain_holdout_official_run.md)
- 실제 데이터 가공·정규화·재현: [`backend/data/README.md`](backend/data/README.md)
- 연구 계약과 아키텍처 정본: [`flow.md`](flow.md)
- 기술·데이터 선택 근거: [`manual.md`](manual.md)
- 작업 규칙과 현행/reference 경계: [`AGENTS.md`](AGENTS.md)
- 공개본의 데이터·개인정보 경계: [`DATA_AND_PRIVACY.md`](DATA_AND_PRIVACY.md)
- 공개 스냅샷의 원본 ref와 제외 항목: [`PUBLIC_RELEASE_MANIFEST.json`](PUBLIC_RELEASE_MANIFEST.json)
- 기여 규칙: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- 보안 및 비밀정보 보고: [`SECURITY.md`](SECURITY.md)

현행 런타임은 `backend/`에 있다. `reference/`는 과거 목업의 읽기 전용 스냅숏이며
현행 빌드나 데이터 경로에 연결하지 않는다.

빠른 시작은 `backend/README.md`의 “로컬 실행”을 따른다. 실제 원문·Parquet·semantic
index와 `.env`는 Git에 포함되지 않으므로, 새 clone에서는 문서의 데이터 생성 절차와 Luxia
키 설정을 먼저 수행해야 한다.

이 브랜치는 비공개 연구 저장소의 최종 스냅샷을 새 Git 이력으로 옮긴 공개본이다. 실제 리뷰
발췌를 포함한 내부 episode 감사 로그, 원본 LLM trace, 로컬 데이터와 개인 식별 정보가 들어간
과거 포스터 파일은 포함하지 않는다.
