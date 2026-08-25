# SEGSE-lite 개발 계약 및 실험 프로토콜 v1

## 지위

이 문서는 `Extended_Experiment`의 확증 결과 뒤에 시작한 별도 개발 단계의 계약이다.
기존 official 20개·81턴 및 M0/C confirmatory 20개·80턴은 방법 선택 근거로만 보존하며,
새 prompt/schema를 그 출력에 맞추지 않는다.

- 브랜치: `codex/segse-lite-experiment`
- 기준 브랜치: `Extended_Experiment` (`efee0fa`)
- 단계: development, confirmatory 아님
- LLM live 결과: 아직 없음
- production/default workflow 변경: 없음

## 연구 질문

Full-Memory와 C semantic no-op suppression을 유지하면서, 이전 state를 read-only reference로만
사용하고 current-turn-grounded semantic event만 쓰기 권한을 갖게 하면 다음을 동시에 달성하는가?

1. raw Candidate 및 carryover-to-update FP 감소
2. 신규 잘못된 belief의 Final State 유입 감소
3. 실제 correction, scope change, confirmation, retraction recall 보존
4. strict schema 및 turn completion 보존

## 네 평가 계층

```text
Raw Proposed State Events
→ Deterministically Authorized Candidates
→ Applied Material / Metadata Delta
→ Accumulated Dialogue State
```

`Raw Proposed Events`와 `Authorized Candidates`를 분리한다. Evidence/type validator가 event를
제거한 효과를 LLM extraction 개선이라고 부르지 않는다.

Gold 이름도 `gold_candidate`가 아니라 `gold_current_turn_event`를 사용한다. Candidate는 시스템
산출물이고 gold는 사용자 발화가 표현한 의미 사건이기 때문이다.

## LLM과 State Manager 경계

LLM은 다음 사용자 의미행위만 제안한다.

| act | 의미 |
| --- | --- |
| `assert` | 새 사실 또는 대체 값을 현재 발화에서 주장 |
| `confirm` | active prior fact를 명시적으로 유지·확인 |
| `retract` | active prior fact를 명시적으로 철회 |
| `refine` | soft/facet 의미를 새 현재 근거로 구체화 |

LLM은 `ADD`, `UPDATE_VALUE`, `UPDATE_SCOPE`, `no-op`을 결정하지 않는다. State Manager가 이전
상태와 비교하여 이를 결정한다. Hard numeric correction은 `assert`, qualitative enrichment는
`refine`이다. Untouched state는 출력하지 않으며 event 부재가 carryover다.

## Evidence와 reference

- `trigger_evidence_text`는 current user utterance의 exact substring이다.
- `assert/refine`의 value source는 `current_utterance`다.
- `confirm/retract`는 `prior_state_reference`와 같은 canonical ID의 `source_ref`를 요구한다.
- 이전 state의 과거 `evidence_text`는 LLM context에 제공하지 않는다.
- hard fact만 correction resolution을 위해 value를 제공한다.
- soft/facet은 ID, scope, status만 제공한다.
- 현재 workflow에는 last-system-proposal value가 영속화되지 않는다. v1은 이를 지원한다고
  주장하지 않으며, 해당 source는 workflow가 referent를 기록한 뒤 별도 버전에서 추가한다.

## Authority와 facet

`state_events[]`만 preference/facet candidate authority다. LLM은 별도 facet 객체를 출력하지 않는다.
검증을 통과한 event로부터 application이 convenience facet을 결정론적으로 생성한다.

Item action과 explicit trade-off는 기존 전용 구조를 유지한다. 이들은 별도 action 계약이며,
현재 근거 없는 preference/facet candidate를 생성할 권한은 없다.

## Deterministic authorization

각 raw event는 다음을 모두 만족해야 한다.

```text
Current trigger evidence
AND legal semantic act payload
AND canonical ID / scope / relation compatibility
AND resolvable state precondition
AND trade-off direction consistency
```

개별 event가 실패하면 전체 turn을 schema failure로 만들지 않고 event만 reject하며 reason을 남긴다.
다만 route/action 같은 top-level 구조 불일치는 기존 structured repair 대상이다.

현재 closed state가 positive requirement/preference만 표현하므로 `not Windows` 같은 EXCLUDE state는
v1에서 positive OS로 뒤집지 않고 event 없이 남긴다. `avoid/exclude/dontcare`를 schema에 넣고
downstream semantics를 구현하지 않는 방식은 사용하지 않는다.

## Material delta와 C

C의 기존 역할은 유지한다.

- 동일 hard ID/scope/정규화 값의 `assert` → semantic no-op
- 동일 soft/facet ID/scope의 반복 `assert` → semantic no-op
- hard 값 변경 → `update_value`
- scope 변경 → `update_scope`; 이전 scope record 제거
- fresh evidence가 있는 `refine` → material refinement 가능
- superseded tombstone 뒤 fresh assertion → `reactivate`

따라서 qualitative value refinement를 기존 C가 무조건 제거하던 위험을 `refine` act로 분리한다.

## Confirmation의 두 lane

Confirmation을 무조건 material 또는 무조건 no-op으로 정의하지 않는다.

```text
Belief-content lane
  ID/value/scope/relation이 동일하면 semantic State Diff 없음

Support/decision-metadata lane
  support evidence 추가
  implicit/inferred → explicit provenance promotion
  unconfirmed → confirmed status promotion
```

`status` promotion은 downstream ranking eligibility를 바꿀 수 있으므로
`decision_eligibility_changed`를 별도로 기록한다. 기존 semantic `changed_paths`에 이를 섞지 않는다.

## Retraction과 tombstone

v1 실험 adapter는 기존 state schema와 frozen evaluator를 바꾸지 않기 위해 철회된 값을 물리적으로
삭제하지 않고 기존 `superseded` status로 비활성화한다. 이는 기능적으로 tombstone 역할을 하지만
`retracted`와 일반 supersession을 구분하는 최종 schema는 아니다.

재활성화에는 새로운 current-turn evidence를 가진 `assert`가 필요하다. Previous-state copying만으로
자동 부활할 수 없다.

## 개발 fixture

`data/tablet_domain_segse_dev_v1.json`은 12개 contrast family, family당 2개씩 총 24개 case다.

- 신규 assertion / unrelated memory
- confirmation / real change
- provenance confirmation / confirmation 거부
- retract / reactivate
- negative polarity
- no minimum / positive minimum
- trade-off direction
- microSD / internal storage
- refine / repeat
- hard-soft scope change
- unsupported-category excursion / recovery
- recommendation action / preference boundary

기존 tablet-domain dev, official holdout, M0/C confirmatory holdout과 exact utterance 중복이 없다.
이 fixture는 prompt/schema 개발용이며 untouched 성능으로 보고하지 않는다.

## 개발 비교 순서

가능하면 다음 ablation을 development fixture에서만 비교한다.

1. `B0`: frozen v2.3 Understanding + C
2. `D1`: authoritative state events, full previous context
3. `D2`: D1 + deterministic evidence/type authorization
4. `D3`: D2 + semantic acts and event-aware materiality
5. `D4`: D3 + compact read-only context (`SEGSE-lite v1`)

현재 코드는 D4 통합 treatment를 구현했다. 구성요소별 인과 주장을 하려면 D1–D3 adapter를 별도로
실행해야 한다. 최종 confirmatory에서는 development에서 선택·동결한 package 하나만 B0와 비교한다.

### 최초 live development 실행 단위

첫 실행은 같은 24개 독립 1턴 case에 대해 다음 고정 순서로 수행한다.

1. `B0`: frozen v2.3 Understanding + C
2. `D4`: SEGSE-lite v1

각 arm/case는 동일한 선언적 prior state에서 시작하며 Understanding을 정확히 한 번 호출한다. 전체
예상 logical call은 48회다. 이 fixture의 질문은 Understanding과 state transition이므로 catalog,
retrieval, ranking, Response Composer는 호출하지 않는다. 출력 누락과 terminal schema failure는 빈
예측으로 모든 분모에 남긴다. B0와 D4 호출은 bitwise paired output이 아니므로 component-causal
효과가 아니라 동일 development fixture에서의 method-selection 비교로만 해석한다.

Raw report와 LLM trace는 Git 제외 경로인 `reports/`, `logs/`에 저장하고, 실행이 모두 끝난 뒤에만
compact result를 `data/results/tablet_domain_segse_dev_v1.json`에 쓴다. 기존 결과가 있으면 runner는
덮어쓰지 않는다.

## Development gate

confirmatory holdout을 만들기 전에 다음 기준을 고정한다.

- C2U FP 50% 이상 감소
- raw proposed Candidate FP 30% 이상 감소
- raw event recall 감소 2 percentage points 이내
- correction/retract recall 감소 각각 2 pp 이내
- Final State F1 감소 0.01 absolute 이내
- hard-filter completion 감소 1 pp 이내
- schema/turn completion 감소 0.5 pp 이내

Raw event와 authorized candidate 지표를 모두 보고한다. Validator rejection으로 raw extraction
오류를 숨기지 않는다.

현재 24개 module fixture에서는 hard-filter completion을 평가하지 않는다. 따라서 나머지 gate를
통과해도 판정은 `module_gate_passed_downstream_pending`이며, 별도 end-to-end development check 전에는
confirmatory 진입을 확정하지 않는다.

## 새 confirmatory 전 필수 작업

1. development fixture를 최소 12 family의 paraphrase/reference variant로 확장
2. raw event, authorized event, semantic material delta, metadata delta evaluator 구현
3. B0와 SEGSE-lite를 동일 development 입력에서 실행
4. method와 threshold freeze
5. 별도 작성자 또는 literal template이 겹치지 않는 새 24–30 scenario holdout 작성
6. scenario 단위 10,000회 paired bootstrap 및 missing-output-as-failure 유지

새 confirmatory 결과를 본 뒤 prompt/schema/validator를 수정하면 새 버전과 새 untouched holdout이
필요하다.

## 현재 주장 가능한 범위

현재는 strict schema 생성과 결정론적 offline contract만 검증했다. 다음은 검증됐다.

- exact current-evidence 없는 stale event rejection
- negated OS의 positive requirement 승격 차단
- authoritative event → candidate projection
- add, hard correction, semantic no-op, qualitative refine, retract, reactivate
- confirmation의 semantic diff와 metadata delta 분리
- compact context의 prior evidence/value dump 제거

LLM Candidate/Final State 성능 개선, schema completion, correction recall, downstream hard-filter 개선은
아직 live development run 전이므로 주장하지 않는다.
