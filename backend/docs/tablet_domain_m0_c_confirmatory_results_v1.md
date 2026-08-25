# M0 Full-Memory vs C Semantic No-op Confirmatory 결과

## 판정

사전 동결한 새 20개 episode·80턴 holdout에서 **C: deterministic semantic
no-op suppression을 confirm**했다. 판정 규칙은 첫 실행 전에 고정했으며,
fixed-upstream causal replay와 독립 live replication이 모두 다음 조건을 통과해야
했다.

- episode 단위 paired State Diff F1 차이(C − M0)가 양수
- 10,000회 paired bootstrap 95% CI의 하한이 0보다 큼
- material State Diff false positive 감소
- Final State F1, correction recall, hard-filter completion, turn completion의
  비열등성 gate 통과

두 비교에서 모든 조건이 참이었다. 따라서 탐색 실험에서 선택한 C가 M0의
**이미 알고 있던 상태를 다시 material update로 기록하는 문제**를 완화한다는 결과가
새 untouched holdout에서도 재현됐다.

## 가장 강한 비교: fixed-upstream causal replay

M0 live가 실제로 생성한 동일한 56개 validated Understanding 출력을 C State
Manager에 그대로 재생했다. 이 replay에는 추가 LLM 호출이 없으므로 두 조건의 차이는
State Manager의 semantic no-op 판정뿐이다.

| 지표 | M0 live | C fixed-upstream | 변화 |
| --- | ---: | ---: | ---: |
| Candidate F1 | 0.532 | 0.532 | 0.000 |
| Candidate FP | 46 | 46 | 0 |
| Carryover→update FP | 35 | 35 | 0 |
| State Diff F1 | 0.552 | 0.671 | +0.119 |
| Material State Diff FP | 47 | 13 | **−34 (−72.3%)** |
| Final State F1 | 0.752 | 0.752 | 0.000 |
| Correction recall | 0.500 | 0.500 | 0.000 |
| Hard-filter completion | 0.513 | 0.513 | 0.000 |
| Turn completion | 56/80 | 56/80 | 0 |

Candidate 계층 지표가 완전히 같은데 material FP만 감소했으므로, 이는 LLM 후보 추출
개선이 아니라 **동일 의미의 재방출을 merge 전에 no-op 처리한 결정론적 효과**다.
실제 수정은 억제하지 않았고, Final State와 downstream hard-filter 입력도 손실되지
않았다.

episode 단위 paired mean State Diff F1 차이는 `+0.1002`, 사전 지정한 10,000회
bootstrap 95% CI는 `[+0.0577, +0.1457]`이었다.

## 독립 live replication

M0와 C는 동결된 GPT-4o-mini Understanding을 각각 독립적으로 한 번 실행했다. LLM
출력이 bitwise deterministic하지 않으므로 이 비교만으로 State Manager 인과효과를
말하지 않으며, fixed-upstream 결과의 운영 환경 재현 여부로 사용했다.

| 지표 | M0 live | C live | 변화 |
| --- | ---: | ---: | ---: |
| 완료 episode | 13/20 | 13/20 | 0 |
| 완료 turn | 56/80 | 56/80 | 0 |
| Candidate F1 | 0.532 | 0.568 | +0.036 |
| Candidate FP | 46 | 40 | −6 |
| Carryover→update FP | 35 | 30 | −5 |
| State Diff F1 | 0.552 | 0.688 | **+0.136** |
| Material State Diff FP | 47 | 11 | **−36 (−76.6%)** |
| Final State F1 | 0.752 | 0.752 | 0.000 |
| Correction recall | 0.500 | 0.750 | +0.250 |
| Hard-filter completion | 0.513 | 0.550 | +0.038 |

paired mean State Diff F1 차이는 `+0.1127`, bootstrap 95% CI는
`[+0.0044, +0.2172]`였다. 하한이 0보다 크지만 매우 가깝고 표본이 20개이므로,
효과 크기의 정밀한 추정보다는 **방향 재현과 사전 판정 통과**로 해석한다. Live
Candidate·correction·hard-filter 차이는 서로 다른 LLM 출력의 영향이 섞였으므로 C의
인과효과로 귀속하지 않는다.

## 실행 및 LLM 감사

- 브랜치: `Extended_Experiment`
- 실행 commit: `4d1936cbd6f2d6f562a5647f2f31a837709d89b8`
- C 구현 commit: `cb6bcf2683f622e3c44edc6672b7167ae1b2171a`
- run ID: `m0c-confirmatory-20260817T100554Z-8fb67f98`
- 실행 시각: 2026-08-17 19:05:54–19:27:33 KST
- 데이터: 새 untouched holdout 20 episode·80 turn(`th21`–`th40`)
- 모델: 모든 trace에서 `gpt-4o-mini-2024-07-18`
- prompt: `spn-understanding-tablet-domain-en-v2.3-frozen`
- structured mode: 모든 logical call에서 `json_schema`
- Response Composer: deterministic template
- live logical Understanding call: 126회(M0 63, C 63)
- schema repair: 57회(M0 29, C 28)
- structured-generation HTTP attempt: 183회(M0 92, C 91)
- transport retry와 fallback: 모두 0회
- fixed-upstream replay 추가 LLM 호출: 0회
- trace token: input 394,524 / output 19,705

계획상 최대 live logical call은 `80 × 2 = 160`이었지만, strict structured-output
검증에 실패한 episode는 그 지점에서 종료해 실제 호출은 126회였다. HTTP attempt는
logical call과 그 안의 schema repair를 합한 값이며 transport retry가 아니다.

## 중요 제한

양쪽 live arm 모두 턴 완주율이 70%(56/80), episode 완주율이 65%(13/20)에
그쳤다. 주된 실패는 preference ID의 scope와 facet payload, unsupported-category
mutation에 대한 strict schema 검증이었다. 이번 C는 **출력된 Understanding 후보를
State Manager가 잘못 material update로 세는 문제**를 해결하지만, upstream schema
신뢰성은 해결하지 않는다.

Fixed replay record는 M0가 멈춘 지점까지 정상 재생한 episode를
`completed_to_m0_boundary`로 기록한다. 공통 evaluator가 오직 `completed`만 episode
완료로 세어 replay의 `scenario_completion_rate`가 0으로 표시되는 status-label artifact가
있다. 실제 primary 분모와 retention gate는 `completed_turn_count=56/80` 및 동일 M0
boundary이며, 판정에는 영향을 주지 않는다.

이 실험은 Gold State Diff와 최종 DialogueState에 대한 평가다. Gold 상품 relevance,
인간 추천 품질, 자연어 응답 품질은 평가하지 않았고 E 또는 C+E도 시험하지 않았다.

## 산출물과 무결성

- compact result: `data/results/tablet_domain_m0_c_confirmatory_v1.json`
- raw report(Git 제외): `reports/tablet_domain_m0_c_confirmatory_v1.json`
- live traces(Git 제외):
  `logs/tablet_confirmatory_m0c-confirmatory-20260817T100554Z-8fb67f98_*.jsonl`
- result manifest:
  `data/manifests/tablet_domain_m0_c_confirmatory_result_v1.json`
- result verifier: `scripts/verify_tablet_domain_m0_c_confirmatory_results.py`

Raw SHA-256는
`5288cb5081b74be66c52bcbddab4a8e88f508af7d6cf9ec04d16803ec7e4bafc`다.
Compact, report, trace의 hash와 크기는 result manifest에 고정한다. API key, 인증
header, `.env` 내용은 tracked 산출물에 포함하지 않았다.
