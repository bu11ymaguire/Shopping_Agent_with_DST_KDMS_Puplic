# SEGSE-lite v1.1 개발 계약

## 지위와 변경 근거

이 버전은 v1 최초 development 결과를 확인한 뒤 작성한 **post-v1 development iteration**이다.
동일 24-case fixture를 재사용하므로 confirmatory evidence가 아니며, v1의 prompt/schema/결과 파일은
수정하지 않는다.

v1은 Raw Candidate FP와 C2U를 줄였지만 schema completion 0.833, Authorized Candidate recall 0.462,
Material recall 0.400으로 gate에 실패했다. 주된 실패는 current semantics가 아니라 LLM이 함께
출력하던 redundant operation payload와 control-field cross validation이었다.

## v1.1 변경

### 1. LLM event payload 축소

LLM은 다음만 출력한다.

```text
canonical_id
act
trigger_evidence_text
origin / confidence
scope_after / value_after (assert 또는 refine에만 존재)
```

`source_ref`, `value_source`, `relation`은 출력에서 제거한다. application이 다음처럼 유도한다.

| act/scope | value source | source ref | relation |
| --- | --- | --- | --- |
| assert | current utterance | null | hard=require, soft=prefer |
| refine | current utterance | canonical ID | soft=prefer |
| confirm/retract | prior-state reference | canonical ID | null |

### 2. act별 discriminated schema

`assert`, `confirm`, `retract`, `refine`은 서로 다른 schema branch다. Confirm/retract branch에는
value/scope field 자체가 없으므로 v1의 “confirm이 prior payload를 다시 채움” 오류를 구조적으로
줄인다.

### 3. control partial-failure containment

Raw item action은 action-specific validation 전의 tolerant proposal로 받는다. 잘못된 compare/reject
control은 action만 reject하고 state event는 계속 평가한다. Intent/action 정합성도 deterministic하게
정리하며 violation을 보존한다. State event와 무관한 control 하나가 전체 turn을 schema failure로
만들지 않는 것이 목적이다.

### 4. 명시적 deterministic normalization

Active prior가 없는데 LLM이 `refine`을 사용한 경우 `assert`로 normalize한다. 이 변환은
`segse_v11_normalizations[]`에 raw act와 normalized act를 모두 기록한다. Raw typed-event metric은
변환 전 act를 사용하므로 후처리 개선을 extraction 개선으로 보고하지 않는다.

### 5. 추가 deterministic type gate

- microSD/removable/expandable storage는 internal `storage_capacity`로 승인하지 않는다.
- numeric hard fact는 현재 evidence/value에 숫자가 있어야 한다.

이는 새 ontology를 추가하지 않고 기존 positive state의 type boundary를 강화한다.

## 실행 계약

- baseline: v1 최초 실행의 B0 case scores와 metrics를 hash로 고정해 재사용
- treatment: v1.1만 같은 24 cases에 새로 24 logical Understanding calls
- arm/case는 독립 prior state에서 시작
- missing output은 빈 예측으로 모든 분모에 유지
- catalog/retrieval/ranking/Response Composer 호출 없음
- 기존 v1 결과와 v1.1 결과 덮어쓰기 금지

Baseline을 다시 호출하지 않으므로 independent LLM variance를 추가하지 않고 비용을 24 calls로 제한한다.
다만 v1.1은 동일 fixture의 v1 오류를 본 뒤 설계됐으므로 결과는 오직 development method-selection에만
사용한다.

## 판정

v1과 같은 module gate를 적용한다.

- C2U FP 50% 이상 감소
- Raw Candidate FP 30% 이상 감소
- Raw Candidate recall 감소 2 pp 이내
- correction/retract recall 감소 각각 2 pp 이내
- Final State F1 감소 0.01 이내
- schema completion 감소 0.5 pp 이내

hard-filter completion은 여전히 module fixture에서 평가 불가다. 나머지 gate가 통과해도 판정은
`module_gate_passed_downstream_pending`이다.

추가 진단으로 다음을 반드시 보고한다.

- raw typed-event F1
- authorized typed-event F1
- normalization count와 family
- event rejection count/reason
- confirmation metadata recall
- retract/reactivation recall

## 금지되는 주장

- v1.1이 untouched data에서 일반화됐다는 주장
- v1.1 component 하나의 causal effect 주장
- Final State F1 상승만으로 belief revision이 해결됐다는 주장
- module 결과로 downstream hard-filter 비열등성을 주장

v1.1이 통과하면 별도 작은 end-to-end development check를 먼저 수행하고, 그 뒤에만 새로운 작성자의
untouched confirmatory holdout과 최종 package 동결을 고려한다.
