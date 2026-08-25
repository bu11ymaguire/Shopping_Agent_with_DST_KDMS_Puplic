# Tablet-domain v2.3 freeze

상태: untouched multi-turn holdout 작성 전 동결

## 연구 범위

시스템은 범용 쇼핑 에이전트가 아니라 Amazon Reviews 2023에서 가공한 상품 117개와 실제 리뷰
7,552개를 사용하는 **태블릿 도메인 대화형 쇼핑 에이전트**다.

- 모든 조건의 환경 상태는 `category_tablet`이다.
- 카테고리를 말하지 않은 태블릿 매장 내 요청은 `in_domain`이다.
- laptop, phone 등 다른 상품군을 명시하면 `unsupported_category`로 검색 전에 차단한다.
- v2 평가에서는 latent `subjective_*` 생성을 비활성화했다. InterQuest/latent 모듈은 이번 평가에
  추가하지 않는다.
- LLM은 closed canonical ID와 hard/soft scope를 출력한다. 중복된 target/key는 ID에서 코드가
  결정론적으로 유도한다.

## 개발 결과와 선택

기존 v1 live replay 20개는 재튜닝에 사용하지 않았다. 별도의 영어 dev case 26개에서만 v2를
개발했다. 선택된 v2.3 결과는 다음과 같다.

| dev 지표 | 결과 |
| --- | ---: |
| strict validation | 26/26 = 1.000 |
| domain route exact | 26/26 = 1.000 |
| canonical ID exact | 18/26 = 0.692 |
| canonical ID micro-F1 | 0.807 |
| intent exact | 25/26 = 0.962 |
| item action exact | 26/26 = 1.000 |
| trade-off exact | 25/26 = 0.962 |

이 수치는 prompt를 선택한 **개발 결과**이며 일반화 성능이나 포스터의 최종 holdout 결과가 아니다.
남은 오류에는 부정 OS 표현, 기존 조건 유지 문구, trade-off 경계가 포함된다. 이 26건에 더
맞추지 않는다.

정확한 prompt/schema/model/retriever/ranker/weight/source hash는
`data/manifests/tablet_domain_v2_freeze.json`에 있다. 동결 검증은 다음으로 수행한다.

```powershell
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_v2.py
.\.venv\Scripts\python.exe scripts\verify_tablet_domain_dev.py
```

## 세 비교 조건

| 조건 | 환경 category | 대화 상태 | 리뷰 기여 |
| --- | --- | --- | --- |
| Full | 유지 | persistent | 유지 |
| No-memory | 유지 | 현재 turn만 | 유지 |
| No-review | 유지 | persistent | 제거 |

No-memory는 매 turn `category_tablet` 환경 상태에서 다시 시작하고 이전 상태·이전 순위를 입력하지
않는다. No-review는 Full과 같은 상태·후보·retrieval을 사용하되 상품 총점의 review evidence와
evidence reliability 항을 0으로 두고 리뷰를 표시하지 않는다. 가중치는 재분배하지 않는다.

## 다음 holdout 계약

holdout 실행 전에 고정할 것:

- 사용자 발화
- turn별 Gold State Diff
- Gold policy/action
- 기대 hard constraints

미리 고정하지 않을 것:

- 추천 상품
- 실제 순위
- Full/No-memory/No-review의 출력
- human relevance grade

동일 holdout을 세 조건에 한 번씩 실행한 뒤 top-k 합집합을 blind annotation pool로 만든다. 기존
472건 packet은 gold-state module-level retrieval/ranking 평가로 그대로 보존하며 새 end-to-end
pool과 섞지 않는다.

이 계약에 따라 `data/tablet_domain_multiturn_holdout_v1.json`의 20개·81턴을 작성했고 첫 system
run 전에 `data/manifests/tablet_domain_multiturn_holdout_v1.json`으로 동결했다. product ID와 gold
ranking은 없다.
