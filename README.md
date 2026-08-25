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

빠른 시작은 `backend/README.md`의 "로컬 실행"을 따른다. 실제 원문·Parquet·semantic
index와 `.env`는 Git에 포함되지 않으므로, 새 clone에서는 문서의 데이터 생성 절차와 Luxia
키 설정을 먼저 수행해야 한다.

---

## 브랜치 구성

`main` 이후의 작업은 서로 다른 연구 질문을 가진 실험 브랜치로 갈라져 있다. 브랜치 이름의
번호는 시간 순서다.

| 브랜치 | 연구 질문 | 결과 |
| --- | --- | --- |
| `main` | 동결된 v2.3 시스템의 기준선 | 기준선. 다른 브랜치가 여기서 분기한다 |
| `exp/0-intent-extraction` | turn 단위로 숨은 의도를 뽑을 수 있는가 | exploratory probe |
| `exp/1-state-diff-strategies` | full-memory의 false state update를 무엇이 줄이는가 | C 선택 후 confirmatory 통과 |
| `exp/2-segse-final-state` | sparse grounded event가 accumulated state를 고치는가 | v1.4 동결, confirmatory 실패 |

전체 흐름과 모든 수치는 `exp/2-segse-final-state` 브랜치의 `experiments.md`에
한 번에 정리해 두었다. 브랜치를 받은 뒤 `git show
exp/2-segse-final-state:experiments.md`로도 확인할 수 있다.

이 공개본은 비공개 연구 저장소의 최종 브랜치 스냅샷만 새 Git 이력으로 옮긴 것이다. 실제 리뷰
발췌를 포함한 내부 episode 감사 로그, 원본 LLM trace, 로컬 데이터와 개인 식별 정보가 들어간
과거 포스터 파일은 포함하지 않는다.

### `main`

Luxia GPT-4o-mini 기반 v2.3 Understanding을 동결하고, untouched 20-scenario / 81-turn official
holdout을 Full / No-memory / No-review 세 조건으로 한 번 실행한 기준선이다. poster primary
automatic benchmark, metric 정의, Gold-State oracle 사후 진단이 여기 있다.

이 기준선에서 다음 문제가 관측되어 뒤의 두 실험이 시작됐다.

```text
Full-Memory   State Diff micro F1  0.562     Final State micro F1  0.758
No-Memory     State Diff micro F1  0.700     Final State micro F1  0.500
```

memory를 넣으면 per-turn State Diff가 오히려 떨어졌다.

관련 태그: `tablet-domain-v2.3-freeze`, `tablet-domain-holdout-v1-freeze`,
`tablet-domain-official-v1`.

### `exp/0-intent-extraction`

turn 단위 hidden-intent probe. 발화에서 명시되지 않은 의도를 뽑아낼 수 있는지 보는 exploratory
실험이며, 아직 confirmatory 설계나 동결된 holdout이 없다.

정본: `backend/docs/tablet_domain_intent_probe_turn_v1.md`.

### `exp/1-state-diff-strategies`

`main`의 State Diff 역설을 겨냥해 여섯 전략을 비교했다.

```text
m0_baseline   a_som_operation   b_sparse_delta
c_semantic_noop   d_full_state_diff   e_compact_context
```

A·B·D는 정확도와 turn completion이 함께 무너졌다. 선택된 것은 **C, State Manager의 결정론적
semantic no-op suppression**이다. canonical ID·scope·정규화 값·status가 이미 모두 같으면 merge를
기록하지 않는다. 이 선택 자체는 post-hoc exploratory다.

C는 자체 untouched 20-scenario / 80-turn holdout에서 사전 지정된 estimand로 검증해 통과했다.

```text
paired mean scenario State Diff F1 (C - M0), fixed-upstream replay
  +0.100,  95% CI [0.058, 0.146],  20 scenarios,  10,000 resamples
```

다만 **Final State F1은 M0와 C가 0.752로 완전히 동일**했다. C는 per-turn 오기록만 막았고
accumulated state는 전혀 움직이지 않았다. 이 사실이 `exp/2`를 촉발했다.

정본: `backend/docs/tablet_domain_extended_experiment_protocol_v1.md`,
`backend/docs/tablet_domain_extended_experiment_results_posthoc_v1.md`,
`backend/docs/tablet_domain_m0_c_confirmatory_protocol_v1.md`,
`backend/docs/tablet_domain_m0_c_confirmatory_results_v1.md`.

### `exp/2-segse-final-state`

SEGSE = Sparse Evidence-Grounded State Events. persistent state를 read-only reference memory로만
쓰고, 상태 변경 권한을 **현재 발화가 실제로 촉발한 sparse event**에만 준다. 부재는 carryover를
뜻하고, C는 하위 계층 방어로 그대로 둔다.

v1 → v1.4까지 6-episode / 24-turn development fixture에서 반복했다. v1.3은 FP를 크게 줄였지만
correction recall이 0으로 붕괴해 폐기했고, v1.4는 prompt와 schema를 그대로 둔 채 결정론적 해석
세 곳만 고쳐 fixture에서 correction을 복구했다. v1.4를 hash로 동결한 뒤 세 번 측정했다.

**원래 결함 데이터 재측정** — official 20-episode / 81-turn holdout, 같은 집계기·같은 분모:

| Condition | State Diff F1 | Final State F1 |
| --- | -: | -: |
| Full-Memory (v2.3) | 0.562 | 0.758 |
| No-Memory (v2.3) | 0.700 | 0.500 |
| SEGSE v1.4 | **0.816** | **0.866** |

State Diff false positive가 57에서 4로 줄고 Full < No-memory 역설이 뒤집혔다. official Full이
5개 episode를 완주하지 못한 completion confound를 제거해 공통 15개로만 보면 0.620 → 0.809,
0.803 → 0.853이다.

**untouched confirmatory** — 두 동결 이후 작성한 새 20-episode / 80-turn holdout에서
**사전 등록된 decision rule 실패**다. accumulated state 개선은 재현되지만(13 improved / 4
unchanged / 3 worsened, mean Jaccard +0.237, 95% CI [0.137, 0.333]), value+scope correction
recall이 v1.3과 8/14로 완전히 동일해 개선의 원인이 correction repair가 아니라 facet
null-value repair임이 드러났다. dimension-confusion negative 6개가 전부 오염되어, 남은 지배적
오류는 **semantic dimension attribution**으로 확인됐다.

v1.4는 더 수정하지 않는다. 이 holdout은 이제 노출됐으므로 여기에 맞춰 patch하거나 재실행하지
않는다.

관련 태그: `segse-v1.4-freeze`, `segse-confirmatory-v1-protocol-freeze`,
`segse-confirmatory-holdout-v1-freeze`.

정본: `experiments.md`, `backend/docs/tablet_domain_segse_e2e_dev_result_v14.md`,
`backend/docs/segse_confirmatory_v1_result.md`,
`backend/docs/segse_confirmatory_v1_tables.md`.

### 동결 규칙

모든 freeze는 문서 문장이 아니라 verifier가 hash를 재계산해 확인한다. 과거 freeze manifest는
사후에 수정하지 않는다. v1.2 manifest와 현재 v1.2 소스는 의도적으로 불일치하며 그 사실을
`FAIL_AS_HISTORICAL_MISMATCH`로 기록해 둔다. 사후에 다시 쓸 수 있는 freeze는 freeze가 아니다.
