# SEGSE-lite v1.2 개발 계약

## 지위

v1.2는 v1.1의 의미 설계를 바꾸기 위한 버전이 아니라 provider schema compatibility를 수정한
development 버전이다. v1.1의 discriminated event union이 생성한 `oneOf`를 Luxia/OpenAI strict
response schema가 거부해 2개 case가 동일하게 실패했고, 나머지 22 calls는 중단했다. 해당 preflight
실패는 `tablet_domain_segse_dev_v11_preflight_failure.json`에 보존한다.

동일 24-case fixture를 반복 사용하므로 v1.2도 confirmatory evidence가 아니다.

## v1.1에서 바뀐 유일한 구조적 핵심

Event를 `oneOf` union 대신 provider-compatible flat object로 출력한다.

```text
canonical_id
act = assert | confirm | retract | refine
scope_after = hard | soft | null
value_after = string | null
trigger_evidence_text
origin
confidence
```

Prompt는 assert/refine에 scope/value를 채우고 confirm/retract에는 null을 요구한다. 모델이
confirm/retract에 payload를 채워도 application이 제거하고 normalization audit을 남긴다.

다음 v1.1 원칙은 유지한다.

- source_ref/value_source/relation deterministic derivation
- invalid item-action control partial rejection
- active prior가 없는 refine → assert normalization과 raw audit 보존
- expansion storage/internal storage type gate
- numeric hard fact의 numeric evidence gate
- raw event와 authorized event 분리

## Provider preflight

실행 전 Pydantic validation JSON Schema 전체에 `oneOf` 문자열이 없음을 verifier가 확인한다. 이 검사는
provider의 모든 제약을 대신하지 않지만 v1.1에서 실제 발생한 결정적 실패를 재발시키지 않는 최소
preflight다.

## 실행과 판정

- B0: 최초 v1 result hash를 고정해 재사용, 추가 호출 0
- v1.2 treatment: 24 logical Understanding calls
- 독립 1턴 prior state, missing output as empty prediction
- catalog/retrieval/ranking/response 호출 없음
- v1과 동일 module gate 적용
- hard-filter completion은 pending

실행 중 동일한 provider schema rejection이 두 번 반복되면 남은 호출을 중단하고 performance result로
해석하지 않는다. 정상 완료하더라도 v1 결과를 본 뒤 설계된 development 결과라는 한계를 유지한다.
