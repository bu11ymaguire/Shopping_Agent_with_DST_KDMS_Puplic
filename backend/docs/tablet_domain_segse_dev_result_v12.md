# SEGSE-lite v1.2 개발 결과 및 evaluator erratum

## 결론

Provider-compatible flat schema의 `SEGSE-lite v1.2`는 24/24 case를 schema repair 없이 완료했고,
case-local correction을 적용한 module gate를 통과했다. 다만 hard-filter completion을 평가하지 않은
동일 개발 fixture 결과이므로 판정은 **`module_gate_passed_downstream_pending`**이며 confirmatory로
진입한 것이 아니다.

- 실행 commit: `b67e7bef39624cdd2543aa65641e1882fe5c57d7`
- run ID: `segse-dev-v12-20260818T034024Z-d87b621e`
- v1 B0 result 재사용: 추가 호출 0
- v1.2 live calls: 24
- completion: 24/24
- schema repair / transport retry / fallback: 0 / 0 / 0
- model echo: `gpt-4o-mini-2024-07-18`
- catalog/retrieval/ranking/Response Composer: 호출하지 않음

## Corrected 주요 결과

| 지표 | B0: v2.3 + C | SEGSE v1.2 | 변화 |
| --- | ---: | ---: | ---: |
| schema completion | .958 | 1.000 | +4.2 pp |
| Raw Candidate P/R/F1 | .625/.769/.690 | .909/.769/.833 | precision 개선, recall 유지 |
| Raw Candidate FP | 6 | 1 | -83.3% |
| C2U FP | 2 | 0 | -100% |
| Novel Candidate FP | 4 | 1 | -75% |
| Authorized Candidate P/R/F1 | N/A | 1.000/.615/.762 | validator 이후 recall 손실은 남음 |
| Material Delta ID P/R/F1 | .600/.600/.600 | .923/.800/.857 | ID 단위 개선 |
| Material Operation P/R/F1 | .600/.600/.600 | .769/.667/.714 | 잘못된 act가 남음 |
| Final State P/R/F1 | .680/.773/.723 | 1.000/.727/.842 | FP 8→0, FN 5→6 |
| correction recall (case-local) | .500 | .500 | 유지 |
| retract recall (case-local) | .000 | 1.000 | 개선 |
| reactivation recall (case-local) | 1.000 | 1.000 | 유지 |
| confirmation metadata recall | N/A | 1.000 | SEGSE 전용 |
| Raw / Authorized typed-event F1 | N/A | .714 / .765 | act confusion이 남음 |

## 무엇이 해결됐는가

1. `oneOf` 제거로 provider schema incompatibility가 사라졌다.
2. Item-action/control 오류를 state event와 분리해 v1의 terminal schema failure 4건이 0건이 됐다.
3. C2U는 2→0, Raw Candidate FP는 6→1, Final State FP는 8→0으로 감소했다.
4. exact current-evidence rate는 1.0을 유지했다.
5. retract와 reactivation fixture는 모두 material operation까지 적용됐다.

이는 persistent memory를 없애지 않고 read-only memory와 current-turn write authority를 분리한 방향이
적어도 개발 fixture에서 유효하다는 근거다.

## 아직 해결되지 않은 것

### 1. False retraction과 operation confusion

`sg04`의 실제 budget correction과 `sg19`의 hard→soft scope change를 LLM이 `retract`로 출력했다.
`sg06`에서는 사용자가 이전 inferred battery guess를 확인하지 않았다는 발화를 battery 철회로 해석했다.
따라서 Material ID metric은 canonical ID가 움직였다는 이유로 맞게 보일 수 있지만, operation-exact와
Final State에서 오류가 드러난다.

### 2. 필요한 신규 정보 누락

`sg13` battery, `sg14` battery, `sg23` portability가 authorization 이후 적용되지 않았다. v1.2의
Final State precision 1.0은 인상적이지만 recall은 B0 .773에서 .727로 낮다. FP=0만 보고 방법을
선택하면 지나치게 보수적인 extractor를 채택할 위험이 있다.

### 3. Validator rejection은 여전히 8건

일부 rejection은 바람직했다.

- negative OS를 prior 없는 retract로 만든 event 차단
- microSD를 internal storage로 만든 event 차단
- unsupported category에서 facet mutation 차단
- evidence substring 불일치 차단

그러나 유효한 battery/portability event도 scope/evidence 문제로 제거됐다. Authorized Candidate recall
.615는 새 confirmatory 설계 전에 더 살펴봐야 한다.

### 4. Downstream은 미평가

이 fixture는 카탈로그를 호출하지 않았으므로 hard-filter completion, recommend-lane reach, 실제
correction propagation은 아직 모른다. Module gate 통과를 full workflow 채택으로 해석하면 안 된다.

## Evaluator erratum

최초 evaluator의 correction/retract/reactivation guardrail은 operation pair를 전체 case에서 합친 뒤
매칭했다. 그 결과 다른 case의 동일 canonical operation이 target을 대신 만족할 수 있었다.

- 잘못 보고된 B0 correction recall: .750 → corrected .500
- 잘못 보고된 v1.2 correction recall: .750 → corrected .500
- retract/reactivation 값과 gate의 상대 결론은 변하지 않음

Candidate, typed-event, Material ID, Material Operation PRF, Final State, FP/FN, schema completion 지표는
원래도 case-local이어서 영향을 받지 않았다. Frozen 원본 result는 수정하지 않고 corrected result를
별도 artifact로 보존한다.

## 다음 단계

새 prompt를 이 24개에 더 맞추지 않는다. 다음 안전한 단계는 v1.2를 고정한 작은 end-to-end
development check다.

1. 기존 개발 자료에서 literal 문구가 겹치지 않는 소수 multi-turn scenario 사용
2. Full workflow에서 correction/retract/reactivation과 hard-filter completion 확인
3. false retraction을 operation-exact와 final-state origin으로 별도 집계
4. downstream guardrail을 통과하면 package를 freeze
5. 그 뒤 새로운 작성자의 untouched confirmatory holdout을 생성

현재 v1.2는 **후보 package**이지 최종 selected/confirmed package가 아니다.
