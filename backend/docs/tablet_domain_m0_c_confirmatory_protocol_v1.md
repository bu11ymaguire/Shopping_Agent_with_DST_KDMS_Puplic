# M0 vs C Confirmatory Protocol v1

## 연구 질문

새로운 untouched multi-turn holdout에서, Full-Memory의 기존 State Manager(M0)보다
deterministic semantic no-op suppression(C)이 누적 상태와 진짜 교정을 보존하면서
material State Diff false positive를 줄이는가?

이 문서는 새 dataset의 어떤 system output도 보기 전에 실행 순서, primary estimand,
보존 gate와 판정 규칙을 고정한다. 이전 20개·81턴 holdout은 방법 선택에 사용됐으므로
이번 확증 데이터에 포함하지 않는다.

## 고정된 비교 방법

- **M0**: `m0_baseline`, frozen v2.3 Understanding 후보를 기존 Full-Memory merge로 적용
- **C**: `c_semantic_noop`, 같은 후보 중 현재 state와 canonical ID·scope·정규화 값·status가
  동일한 후보를 merge 전에 제거

C는 LLM 후보 추출기를 바꾸지 않는다. 따라서 “Understanding candidate FP를 줄인다”가 아니라
“후보가 실제 상태 변화가 아닌 경우 material State Diff/provenance 갱신을 막는다”를 검증한다.

## 새 holdout

- 파일: `data/tablet_domain_m0_c_confirmatory_holdout_v1.json`
- 20 scenario, scenario당 4턴, 총 80턴
- ID: `th21`–`th40`
- 이전 official holdout, tablet-domain dev, recommendation v1과 exact utterance 중복 없음
- Gold: current-turn candidate ID/scope, material State Diff, route, Policy/action,
  trade-off, 턴별 hard filter, final active state ID
- 제외: product ID, system output, ranking, review relevance, 인간 상품 적합성

교정 보존용 턴은 실행 전에 다음 8개로 고정한다.

```text
th21:4 budget          $310 → $345
th22:4 storage         64 GB → 128 GB
th23:4 RAM             4 GB → 6 GB
th24:4 rating          4.0 → 4.4
th25:4 weight          700 g → 520 g
th26:4 display         10 in → 11 in
th27:4 operating system Android → Windows
th28:4 budget          $230 → $275
```

## 두 개의 비교 계층

### 1. Primary: fixed-upstream causal replay

M0 live run에서 얻은 각 턴의 validated Understanding 객체를 그대로 저장한다. 두 live arm이
모두 끝난 뒤, 그 객체의 `experimental_strategy`만 C로 지정해 C State Manager와 동일한
Policy→Query→catalog→review retrieval→Cross-Encoder→Rank 경로로 재생한다. 추가 LLM 호출은
없다.

이 비교에서는 M0와 C의 candidate extraction이 완전히 동일하므로 차이는 State Manager의
no-op 규칙에서만 발생한다. M0가 schema failure로 끝난 지점 이후는 replay도 출력하지 않고
동일하게 실패 처리한다.

### 2. Operational replication: independent live M0 vs C

`M0 → C` 순서로 같은 80턴을 조건당 한 번 실행한다. 각 attempted turn은 frozen
GPT-4o-mini Understanding을 한 번 호출하고, schema validation failure에 대한 기존 repair 1회와
infrastructure error에 대한 outer retry만 허용한다. Response Composer는 deterministic template다.

두 live arm이 모두 끝나기 전에는 지표를 집계하거나 결과를 판정하지 않는다. 독립 호출은
temperature 0이어도 bitwise 동일하지 않을 수 있으므로 primary causal 결과와 분리한다.

## 공통 경로

- `spn-understanding-tablet-domain-en-v2.3-frozen`
- `understanding-v3.1-tablet-domain-en` strict schema
- 동일 `DialogueState`, Policy, Query Generator
- 동일 117개 상품·7,552개 리뷰
- 동일 semantic bi-encoder → Cross-Encoder → Rank
- 동일 scenario/turn 순서
- template response composer
- missing output은 남은 Gold candidate/State Diff의 FN과 hard-filter failure로 계산

## Primary estimand와 통계

각 scenario에서 4턴 전체의 State Diff TP/FP/FN으로 F1을 계산한다. missing turn의 Gold
operation은 FN이다. Primary estimand는 다음 paired difference다.

```text
mean_scenario_F1(C fixed-upstream replay)
− mean_scenario_F1(M0 live)
```

scenario를 단위로 10,000회 paired bootstrap하고 seed는 `20260817`로 고정한다. 양측 percentile
95% CI를 보고한다.

Micro State Diff F1, material FP, Candidate F1/FP, C2U FP, Final State ID F1,
correction recall, hard-filter completion, turn/schema completion은 함께 보고한다.

## 사전 판정 규칙

다음 조건을 **fixed-upstream primary와 independent live replication에서 각각 모두** 만족할
때만 `confirmed=true`로 판정한다.

1. paired mean scenario State Diff F1 difference가 0보다 큼
2. paired bootstrap 95% CI의 lower bound가 0보다 큼
3. material State Diff FP가 M0보다 적음
4. Final State micro-F1이 M0보다 0.02 넘게 낮지 않음
5. correction candidate recall이 M0보다 0.05 넘게 낮지 않음
6. hard-filter completion이 M0보다 0.02 넘게 낮지 않음
7. turn-output completion이 M0보다 0.02 넘게 낮지 않음

하나라도 실패하면 `not_confirmed`이며 prompt, C 규칙, Gold 또는 threshold를 이번 holdout에
맞춰 수정하지 않는다. exploratory E와 C+E 결합은 이번 확증 실험에 포함하지 않는다.

## 주장 범위

성공 시 주장할 수 있는 범위:

> 동일한 frozen tablet-domain 파이프라인과 새 20-scenario holdout에서 semantic no-op
> suppression은 기존 Full-Memory보다 material State Diff false positive를 줄였고,
> 사전 정의한 누적 상태·교정·hard-filter 보존 기준을 충족했다.

성공해도 candidate extraction 자체가 개선됐다고 주장하지 않는다. Gold-State Oracle 상품,
인간 relevance, 추천 품질, 자연어 응답 품질도 이번 endpoint가 아니다.

## 실행 산출물

- raw/checkpoint(Git 제외): `reports/tablet_domain_m0_c_confirmatory_v1.json`
- live trace(Git 제외): `logs/tablet_confirmatory_<run-id>_<strategy>.jsonl`
- compact tracked result: `data/results/tablet_domain_m0_c_confirmatory_v1.json`
- frozen result manifest와 분석 보고서는 실행 후 별도 생성

Raw와 trace는 덮어쓰지 않는다. 실행 commit, dataset/source hash, reported model, schema mode,
logical call, repair, HTTP attempt, retry, token 수를 보존한다.
