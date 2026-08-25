# 포스터 시나리오 Luxia live replay v1

상태: 첫 실행 동결, production system 재튜닝 전 기준선

실행일: 2026-08-08

## 무엇을 실행했는가

연구자가 작성하고 미리 동결한 영어 4턴 시나리오 20개를 시나리오별 새 대화 세션으로 만들고,
총 80개 사용자 발화를 실제 Luxia GPT-4o-mini 기반 workflow에 순서대로 입력했다.

```text
Luxia Understanding
→ deterministic State Manager
→ deterministic Policy
→ local Amazon catalog query/review retrieval/ranking
→ Luxia Response Composer
```

기존 `poster_annotation_v1`처럼 정답 상태를 직접 주입하지 않았다. production prompt·retriever·ranker는
결과를 보는 동안 변경하지 않았다. 원본 turn·trace는 Git 제외 report에, 입력·설정·report hash와
요약은 `data/manifests/poster_live_replay_luxia_v1.json`에 보존한다.

## 첫 실행 결과

| 지표 | 결과 |
| --- | ---: |
| 시나리오 완주 | 19/20 = 0.950 |
| 턴 완주 | 79/80 = 0.988 |
| scenario-level strict schema failure | 1/20 = 0.050 |
| recommend-lane reach / end-to-end completion | 8/20 = 0.400 |
| 완료 시나리오 기준 recommend-lane reach | 8/19 = 0.421 |
| end-to-end hard-filter completion | 8/20 = 0.400 |
| conditional hard-filter correctness | 8/8 = 1.000 |
| canonical ID exact | 0/19 = 0.000 |
| canonical ID micro-F1 | 0.658 |
| 추천된 상품 수 | 24개 |
| 기대 hard-filter 위반 | 0/24 = 0.000 |
| 표시 리뷰–점수 근거 일치율 | 1.000 |
| LLM response template fallback | 0.000 |
| semantic review retrieval fallback | 0.000 |
| 턴 latency 중앙값 / p95 | 4.55초 / 27.01초 |

`8/20`은 추천 관련성이나 필터 정확도가 아니라 추천 단계까지 도달한 비율이다. 추천 lane에
들어간 8개에서는 기대 hard filter가 모두 정확했다. 파생 지표와 phase latency의 정본은
`data/manifests/poster_live_replay_v1_analysis.json`이다.

완료된 19개 기준 category recall은 8/19 = 0.421, hard-constraint slot F1은 1.000,
soft-constraint slot F1은 0.333이었다. 18/19 시나리오에서 총 35개 extra ID가 있었고,
그중 `subjective_property` false positive가 21개였다. 따라서 canonical exact는 보조 지표로 둔다.

phase latency 중앙값은 Understanding LLM 2.50초, State+Policy 0.21ms,
retrieval+Cross-Encoder 2.22초, ranking 3.11ms, Response Composer LLM 1.41초였다.
v1 trace는 retrieval과 Cross-Encoder를 한 구간으로 기록했으므로 둘을 분리한 값은 주장하지 않는다.

최종 recommend에 도달한 시나리오는 `ph01`, `ph02`, `ph06`, `ph07`, `ph08`, `ph12`, `ph13`,
`ph19`다. 완료됐지만 최종 clarify에 머문 11개는 `ph03`, `ph04`, `ph05`, `ph09`, `ph10`,
`ph11`, `ph15`, `ph16`, `ph17`, `ph18`, `ph20`이다.

## 실패가 발생한 위치

최종 clarify 11개는 모두 Policy가 `supported category`를 다시 물었다. 각 시나리오의 첫 발화에는
tablet이 명시되어 있었지만, Luxia Understanding이 `category_tablet` 후보를 누락했다. 이후 턴의
예산·용도·속성 일부가 추출돼도 State Manager에는 category가 없으므로 Policy가 추천 lane으로
진입하지 않았다.

`ph14`는 네 번째 턴 Understanding에서 두 번 모두 `canonical_id`와 constraint key가 일치하지 않는
출력을 내 strict schema가 거부했다. 이 실패를 덮어쓰지 않고 첫 실행 결과에 남겼다. 별도 재시도에서는
4턴을 완주했지만 `category_tablet` 누락으로 여전히 clarify lane이었다.

canonical ID exact가 0인 것은 category 누락 외에도 예상 soft preference 대신 추가 subjective ID를
추출한 차이를 함께 반영한다. 따라서 exact 하나만으로 추천 품질을 해석하지 않고 micro-F1과
hard-filter·lane·추천 결과를 분리해 본다.

## 해석

추천까지 도달한 8개에서는 기대 hard filter가 정확했고, 24개 추천 모두 제약 위반이 없었으며 리뷰
근거 ID도 일치했다. 현재 가장 큰 병목은 상품 랭킹 안전성이 아니라 upstream Understanding의 category
추출과 그 결과를 사용하는 Policy 분기다.

이 결과는 실제 사용자 대화 연구가 아니라 통제 시나리오의 live replay다. 또한 사람 관련성 판정이
없으므로 추천된 상품이 사용자에게 얼마나 좋은지는 아직 NDCG로 주장할 수 없다. 기존 3인용 packet은
gold-state retrieval/ranking 평가에만 사용하고 full-system 결과라고 부르지 않는다.

## 다음 실험 규칙

1. 이 20개와 첫 결과에 production prompt를 다시 맞추지 않는다.
2. v2는 태블릿 환경 상태를 고정하고 명시적 타 카테고리만 `unsupported_category`로 차단한다.
3. 별도 dev set에서 선택한 v2.3을 동결한 뒤 새 untouched holdout을 작성한다.
4. 같은 holdout을 Full/No-memory/No-review로 한 번씩 실행한다.
5. 세 조건 top-k 합집합의 별도 후보 pool을 만들고 human relevance를 blind 판정한다.
