# SEGSE-lite 최초 개발 실행 결과 v1

## 판정

`SEGSE-lite v1`은 False Update 억제에는 성공했지만 schema completion과 authorized recall을
보존하지 못해 **module development gate 실패**로 판정한다. 이 결과는 development fixture의
방법 선택 근거이며 untouched confirmatory evidence가 아니다.

- 실행 commit: `aae36bdce3c4491019d18b22fa7ef3b1f475cd74`
- run ID: `segse-dev-20260818T031459Z-a4cafb11`
- 입력: 12 contrast family, 24 independent one-turn cases
- 호출: B0 24회 후 D4 24회, 총 48 logical Understanding calls
- 모델 echo: `gpt-4o-mini-2024-07-18`
- structured mode: `json_schema`
- catalog/retrieval/ranking/Response Composer 호출: 없음

## 주요 결과

| 지표 | B0: v2.3 + C | D4: SEGSE-lite v1 | 해석 |
| --- | ---: | ---: | --- |
| schema completion | 0.958 | 0.833 | guardrail 실패, -12.5 pp |
| Raw Candidate P/R/F1 | .625/.769/.690 | .846/.846/.846 | raw extraction은 개선 |
| Raw Candidate FP | 6 | 2 | 66.7% 감소 |
| C2U FP | 2 | 1 | 50% 감소 |
| Novel Candidate FP | 4 | 1 | 75% 감소 |
| Authorized Candidate P/R/F1 | N/A | 1.000/.462/.632 | validator 뒤 recall 급락 |
| Material Delta P/R/F1 | .600/.600/.600 | 1.000/.400/.571 | FP 6→0, FN 6→9 |
| Final State P/R/F1 | .680/.773/.723 | .842/.727/.780 | FP 8→3, FN 5→6 |
| correction recall | .750 | .750 | 표본상 유지 |
| retract recall | .000 | .000 | 두 방법 모두 실패 |
| reactivation recall | 1.000 | .000 | D4 회귀 |
| exact evidence rate | .938 | 1.000 | current-turn lexical grounding 개선 |

Development gate에서 C2U 감소, Candidate FP 감소, Candidate recall, correction/retract recall,
Final State F1 조건은 통과했다. 그러나 schema completion 비열등성 조건을 통과하지 못했다.
hard-filter completion은 이 module fixture에서 평가할 수 없어 pending이다.

## 계층별 진단

```text
Raw SEGSE extraction
  Candidate FP는 크게 감소, Candidate recall은 증가
            ↓
Typed act/payload confusion
  Raw typed-event F1 = 0.514
            ↓
Deterministic authorization
  12 events rejected, Candidate precision = 1.0 / recall = 0.462
            ↓
Material state
  FP = 0이지만 FN = 9
            ↓
Final state
  precision은 개선됐지만 필요한 update/retract/reactivate를 놓침
```

즉 v1은 이전 state가 잘못 update로 들어오는 경로를 차단했지만, 작은 LLM이 복잡한 event payload를
정확히 조립하지 못해 유효한 event까지 버렸다. 사용자가 앞서 구분한 표현을 그대로 적용하면,
**“버려야 할 것을 못 버리는 문제”는 줄었고 “얻어야 할 것을 지나치는 문제”가 커졌다.**

## 실패 원인

### 1. Control schema가 state extraction 전체를 실패시킴

B0는 1건, D4는 4건이 terminal `StructuredOutputError`였다. D4의 추가 실패는 `compare`, `reject`
같은 intent를 item action 없이 출력한 top-level cross-field validation에서 발생했다. State event가
유효할 가능성이 있어도 control field 하나 때문에 전체 turn이 빈 예측이 됐다.

### 2. LLM이 state-relative payload까지 동시에 맞춰야 했음

12개 rejection의 주된 원인은 다음 redundant field 조합이었다.

- `refine`인데 `source_ref` 누락
- `retract`인데 `value_source` 또는 state payload 불일치
- `confirm`인데 이전 value/scope를 다시 채움
- 신규 assertion을 `refine`로 출력

`source_ref`, `value_source`, `relation`은 대부분 `act`, `canonical_id`, `scope`와 prior state로
결정론적으로 유도할 수 있다. 이를 LLM에게 모두 재출력시킨 계약이 작은 모델의 schema surface와
operation confusion을 키웠다.

### 3. act 분류 자체가 아직 불안정함

Raw typed-event F1은 0.514였다. 특히 `assert ↔ refine`, `confirm ↔ refine`,
`scope change ↔ retract`가 혼동됐다. Validator는 잘못된 mutation을 막았지만 이를 복구하지 않으므로
Authorized Candidate와 Material Delta recall이 낮아졌다.

### 4. retraction은 아직 해결되지 않음

Gold retract 2건에서 두 arm 모두 recall 0이었다. SEGSE가 retraction event를 일부 제안해도 payload
precondition 불일치 또는 schema failure로 적용되지 않았다. 현재 결과로 belief revision이 개선됐다고
주장할 수 없다.

## 다음 개발 버전의 변경 원칙

v1 결과를 보고 고치는 다음 버전은 `v1.1-development`로 명시하며 confirmatory가 아니다.

1. `source_ref`, `value_source`, `relation`을 LLM 출력에서 제거하고 deterministic하게 유도한다.
2. act별 discriminated event schema로 불필요한 payload를 구조적으로 없앤다.
3. raw item action을 tolerant proposal로 받은 뒤 action만 개별 검증해, control 오류가 state event
   전체를 실패시키지 않게 한다.
4. raw event와 normalized/authorized event를 모두 보존해 deterministic repair가 extraction 개선으로
   오인되지 않게 한다.
5. B0 결과는 최초 실행 결과를 그대로 재사용하고 v1.1 treatment만 새로 24회 실행한다.
6. 같은 development fixture에서 schema completion, typed-event confusion, retract/reactivate를 다시
   확인한 뒤에만 end-to-end development check를 고려한다.

v1의 prompt/schema/validator와 결과는 수정하지 않고 별도 파일과 manifest로 보존한다.
